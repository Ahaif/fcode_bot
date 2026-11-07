"""Builder role — explicit FSM executing a persistent mining task.

Task sequence (never left until SUCCESS / FAILURE / explicit cancel):

  EXPLORE → MOVE_TO_STAND → BUILD_HARVESTER → PLAN_CONVEYOR
         → BUILD_CONVEYOR → RETURN_HOME → COMPLETE_TASK → EXPLORE
                             ↘ FAILED_TASK → EXPLORE

Conveyors come only from ``state.build_plan`` (consumed by index).
No per-turn greedy neighbour selection for belt direction.
"""

from __future__ import annotations

import os

from fcode import Controller, EntityType, Position

import claim_manager
from assignment import get_assignment
import combat
from combat import attack_step, note_enemy_core, try_attack_adjacent
import condition_state
from constants import (
    PHASE_ATTACK,
    PHASE_BUILD_CONVEYOR,
    PHASE_BUILD_HARVESTER,
    PHASE_COMPLETE_TASK,
    PHASE_DEFEND,
    PHASE_DEFEND_BUILD,
    PHASE_EXPLORE,
    PHASE_FAILED_TASK,
    PHASE_MOVE_TO_STAND,
    PHASE_PLAN_CONVEYOR,
    PHASE_RETURN_HOME,
    DEFEND_BUILD_COOLDOWN_ROUNDS,
    DEFEND_BUILD_TIMEOUT_ROUNDS,
    ECONOMY_PAUSE_MAX_ROUNDS,
    ORPHAN_HARVESTER_RETRY_CAP,
    STUCK_LIMIT,
)
import defense
from defense import defend_step
from economy import (
    clear_build_plan,
    compute_conveyor_plan,
    friendly_harvester_on,
    is_core_tile,
    pick_ore_stand,
    plan_is_complete,
    plan_tile_status,
    replan_conveyor_suffix,
    try_build_harvester,
    try_build_plan_tile,
)
from explore import init_sector, step_explore
from geometry import (
    builder_can_act,
    nearest_core_tile,
    on_or_orth_adjacent_to_core,
    orth_adjacent,
    xy,
)
import idle_investigate
from navigator import step_to
from team_store import read_own_core
import map_knowledge

_BUILDER_AUDIT = os.environ.get("BUILDER_AUDIT", "") == "1"
_AUDIT_PATH = os.environ.get("BUILDER_AUDIT_LOG", "builder_audit.log")
_AUDIT_FP = None


def _audit_write(text: str) -> None:
    global _AUDIT_FP
    if not _BUILDER_AUDIT:
        return
    if _AUDIT_FP is None:
        _AUDIT_FP = open(_AUDIT_PATH, "a", buffering=1)  # noqa: SIM115
    _AUDIT_FP.write(text + "\n")


def _audit_fsm(
    ct: Controller,
    state: object,
    *,
    event: str,
    reason: str = "",
    chosen: Position | None = None,
) -> None:
    if not _BUILDER_AUDIT:
        return
    a = get_assignment(state)
    plan = getattr(state, "build_plan", None) or []
    idx = int(getattr(state, "build_index", 0))
    remaining = [xy(t) for t in plan[idx : idx + 6]]
    nxt = plan[idx] if 0 <= idx < len(plan) else None
    parts = [
        f"r={ct.get_current_round()}",
        f"id={ct.get_id()}",
        f"state={getattr(state, 'phase', '?')}",
        f"pos={xy(ct.get_position())}",
        f"ore={xy(a.ore_pos) if a.ore_pos else None}",
        f"has_h={a.has_harvester}",
        f"stand={xy(getattr(state, 'approach_stand', None)) if getattr(state, 'approach_stand', None) else None}",
        f"build_index={idx}",
        f"plan_len={len(plan)}",
        f"next={xy(nxt) if nxt else None}",
        f"chosen={xy(chosen) if chosen else None}",
        f"remaining={remaining}",
        f"replans={getattr(state, 'replan_count', 0)}",
        f"stuck={getattr(state, 'stuck', 0)}",
        f"event={event}",
        f"reason={reason}",
    ]
    _audit_write(" | ".join(parts))


def _set_phase(state: object, phase: str, *, reason: str = "") -> None:
    prev = getattr(state, "phase", None)
    state.phase = phase  # type: ignore[attr-defined]
    if _BUILDER_AUDIT and prev != phase:
        _audit_write(f"TRANSITION | from={prev} | to={phase} | reason={reason}")


def _ensure_core_pos(ct: Controller, state: object) -> None:
    if getattr(state, "core_pos", None) is not None:
        return
    my_team = ct.get_team()
    for uid in condition_state.get_cached_buildings(ct):
        if ct.get_entity_type(uid) == EntityType.CORE and ct.get_team(uid) == my_team:
            state.core_pos = ct.get_position(uid)  # type: ignore[attr-defined]
            return
    if ct.get_current_round() >= 2:
        state.core_pos = read_own_core(ct)  # type: ignore[attr-defined]


def _try_acquire_mining_task(
    ct: Controller,
    state: object,
    *,
    exclude: Position | None = None,
) -> bool:
    """Acquisition entry — a fresh ore first, else an orphaned harvester."""
    res = claim_manager.try_acquire_work(ct, state, exclude=exclude)
    idle_investigate.note_acquire_attempt(ct, state, res)
    if res.ok and res.ore is not None:
        clear_build_plan(state)
        state.approach_stand = None  # type: ignore[attr-defined]
        _set_phase(state, PHASE_MOVE_TO_STAND, reason=f"ore_found_{xy(res.ore)}")
        return True

    orphan = claim_manager.try_adopt_orphan_harvester(ct, state, exclude=exclude)
    if not orphan.ok or orphan.ore is None:
        return False
    clear_build_plan(state)
    state.approach_stand = None  # type: ignore[attr-defined]
    claim_manager.note_harvester(state, orphan.ore)
    _set_phase(state, PHASE_PLAN_CONVEYOR, reason=f"orphan_adopted_{xy(orphan.ore)}")
    return True


def _fail_task(ct: Controller, state: object, *, reason: str) -> None:
    idle_investigate.note_abandon(state, reason=f"fail:{reason}", builder_id=ct.get_id())
    assignment = get_assignment(state)
    failed_ore = assignment.ore_pos
    had_harvester = assignment.has_harvester
    clear_build_plan(state)
    claim_manager.release(ct, state, reason=f"fail:{reason}")

    exclude: Position | None = None
    if had_harvester and failed_ore is not None:
        # A harvester already exists at failed_ore — never let it go
        # unclaimed forever (see try_adopt_orphan_harvester), but cap
        # same-tile retries so a genuinely unreachable one doesn't trap
        # this builder in an every-turn fail/re-adopt loop.
        last = getattr(state, "belt_fail_ore", None)
        if last is not None and (last.x, last.y) == (failed_ore.x, failed_ore.y):
            state.belt_fail_streak = getattr(state, "belt_fail_streak", 0) + 1  # type: ignore[attr-defined]
        else:
            state.belt_fail_ore = failed_ore  # type: ignore[attr-defined]
            state.belt_fail_streak = 1  # type: ignore[attr-defined]
        if state.belt_fail_streak >= ORPHAN_HARVESTER_RETRY_CAP:  # type: ignore[attr-defined]
            exclude = failed_ore

    if _try_acquire_mining_task(ct, state, exclude=exclude):
        return
    _set_phase(state, PHASE_FAILED_TASK, reason=reason)


def _complete_task(ct: Controller, state: object, *, reason: str) -> None:
    claim_manager.complete(ct, state)
    clear_build_plan(state)
    state.approach_stand = None  # type: ignore[attr-defined]
    state.stuck = 0  # type: ignore[attr-defined]
    if _try_acquire_mining_task(ct, state):
        return
    _set_phase(state, PHASE_EXPLORE, reason=reason)


def _cancel_pre_harvester(ct: Controller, state: object, *, reason: str) -> None:
    """Cancel only before harvester — explore may resume."""
    idle_investigate.note_abandon(
        state, reason=f"cancel_pre_h:{reason}", builder_id=ct.get_id()
    )
    failed_ore = get_assignment(state).ore_pos
    clear_build_plan(state)
    claim_manager.release(ct, state, reason=f"cancel_pre_h:{reason}")
    state.approach_stand = None  # type: ignore[attr-defined]
    state.stuck = 0  # type: ignore[attr-defined]
    exclude = failed_ore if reason == "stuck_before_harvester" else None
    if _try_acquire_mining_task(ct, state, exclude=exclude):
        return
    _set_phase(state, PHASE_EXPLORE, reason=reason)


def _enter_late_game(ct: Controller, state: object) -> None:
    idle_investigate.note_abandon(state, reason="enter_late_game", builder_id=ct.get_id())
    clear_build_plan(state)
    claim_manager.release(ct, state, reason="enter_late_game")
    state.approach_stand = None  # type: ignore[attr-defined]
    state.stuck = 0  # type: ignore[attr-defined]
    _set_phase(state, PHASE_DEFEND, reason="sector_exhausted")


def run_builder(ct: Controller, state: object) -> None:
    """One builder turn — stuck bookkeeping, then the active FSM state."""
    idle_ctx = idle_investigate.begin_turn(ct, state)
    acted_hint = ""

    def _finish(hint: str = "") -> None:
        # Audit first — bookkeeping bumps claim_turns_alive / free_turns and
        # would make spawn-gated ores look falsely claimable at end-of-turn.
        idle_investigate.end_turn(ct, state, idle_ctx, acted_hint=hint or acted_hint)
        claim_manager.end_turn_bookkeeping(state)

    _ensure_core_pos(ct, state)
    note_enemy_core(ct)
    map_knowledge.update(ct)
    # Sole ownership reconcile + publish/clear for this unit.
    claim_manager.begin_turn(ct, state)
    init_sector(ct, state)

    pos = ct.get_position()
    state.visited.add(xy(pos))  # type: ignore[attr-defined]

    last = getattr(state, "last_pos", None)
    if last is not None and xy(last) == xy(pos):
        state.stuck = getattr(state, "stuck", 0) + 1  # type: ignore[attr-defined]
    else:
        state.stuck = 0  # type: ignore[attr-defined]
    prev = last
    state.last_pos = pos  # type: ignore[attr-defined]

    phase = getattr(state, "phase", PHASE_EXPLORE)
    assignment = get_assignment(state)

    if phase == PHASE_COMPLETE_TASK:
        _set_phase(state, PHASE_EXPLORE, reason="task_released")
        phase = PHASE_EXPLORE
    elif phase == PHASE_FAILED_TASK:
        _set_phase(state, PHASE_EXPLORE, reason="task_failed_released")
        phase = PHASE_EXPLORE

    # begin_turn may clear Assignment while phase still names a mining state —
    # never dispatch into assert/GameError loops (0μs freezes).
    _MINING_PHASES = (
        PHASE_MOVE_TO_STAND,
        PHASE_BUILD_HARVESTER,
        PHASE_PLAN_CONVEYOR,
        PHASE_BUILD_CONVEYOR,
        PHASE_RETURN_HOME,
    )
    if assignment.ore_pos is None and phase in _MINING_PHASES:
        _set_phase(state, PHASE_EXPLORE, reason="assignment_cleared")
        phase = PHASE_EXPLORE

    ore = assignment.ore_pos
    if ore is not None and phase in (PHASE_MOVE_TO_STAND, PHASE_BUILD_HARVESTER):
        try:
            ore_visible = bool(ct.is_in_vision(ore))
            ore_built = (
                ore_visible and ct.get_tile_building_id(ore) is not None
            )
        except Exception:
            ore_visible = False
            ore_built = False
        if ore_built and not assignment.has_harvester:
            _cancel_pre_harvester(ct, state, reason="ore_taken")
            phase = getattr(state, "phase", PHASE_EXPLORE)
        elif ore_visible and claim_manager.owned_by_other(ct, ore):
            _cancel_pre_harvester(ct, state, reason="ore_owned_by_other")
            phase = getattr(state, "phase", PHASE_EXPLORE)
        elif phase == PHASE_MOVE_TO_STAND and getattr(state, "stuck", 0) >= STUCK_LIMIT:
            # Movement-only: genuinely not making progress toward the stand
            # tile. Never applies in PHASE_BUILD_HARVESTER — there, standing
            # still is *correct* (already in position), so the same signal
            # would mean "waiting to afford the harvester," not "stuck." A
            # tight 3-round limit there abandoned a perfectly good claim
            # after a brief titanium shortage (2026-08-22 report); a
            # genuinely permanent blocker still recovers via the same
            # lenient stuck-recovery path build_conveyor/return_home use.
            _cancel_pre_harvester(ct, state, reason="stuck_before_harvester")
            phase = getattr(state, "phase", PHASE_EXPLORE)
        elif phase == PHASE_BUILD_HARVESTER and getattr(state, "stuck", 0) >= max(
            STUCK_LIMIT * 3, 9
        ):
            _cancel_pre_harvester(ct, state, reason="stuck_before_harvester")
            phase = getattr(state, "phase", PHASE_EXPLORE)
    elif ore is not None and phase in (
        PHASE_PLAN_CONVEYOR,
        PHASE_BUILD_CONVEYOR,
        PHASE_RETURN_HOME,
    ):
        # Mid-belt stuck: recover in place. Never abandon an owned mine just
        # because walk-back / fog made us idle — that left orphan Ti belts.
        if getattr(state, "stuck", 0) >= max(STUCK_LIMIT * 3, 9):
            core = getattr(state, "core_pos", None)
            plan = list(getattr(state, "build_plan", None) or [])
            if (
                core is not None
                and plan
                and phase in (PHASE_BUILD_CONVEYOR, PHASE_RETURN_HOME)
            ):
                state.stuck = 0  # type: ignore[attr-defined]
                idx = _first_incomplete_index(ct, plan, core)
                if idx >= len(plan):
                    _set_phase(state, PHASE_RETURN_HOME, reason="stuck_but_plan_done")
                    phase = PHASE_RETURN_HOME
                else:
                    _set_phase(
                        state, PHASE_BUILD_CONVEYOR, reason="stuck_recover_replan"
                    )
                    phase = PHASE_BUILD_CONVEYOR
                    _replan_or_fail(
                        ct,
                        state,
                        core,
                        from_index=idx,
                        reason="stuck_during_belt",
                    )
            else:
                _fail_task(ct, state, reason="stuck_during_belt")
                phase = getattr(state, "phase", PHASE_EXPLORE)

    # Reactive layer: threat sensing drives free builders before economy.
    # Busy builders (an owned ore task) are never yanked for a routine
    # threat — should_enter_* and should_dispatch_* both refuse when a
    # mining assignment is active, which keeps the economy benchmark
    # intact. The one exception is should_interrupt_for_core_defense below:
    # an armed threat already in range with *zero* friendly coverage means
    # the Core can die this turn with nobody answering, which outweighs a
    # single mining task (any harvester it orphans is picked back up later
    # via try_adopt_orphan_harvester).
    cond = condition_state.get_condition_state(ct)
    core = getattr(state, "core_pos", None)
    in_combat_phase = phase in (PHASE_ATTACK, PHASE_DEFEND_BUILD)
    # Off cooldown after a DEFEND_BUILD_TIMEOUT_ROUNDS give-up — see
    # DEFEND_BUILD_COOLDOWN_ROUNDS's own comment for why this builder must
    # not be immediately pulled back into the same unresolved threat.
    defend_build_ready = ct.get_current_round() >= getattr(
        state, "defend_build_cooldown_until", 0
    )

    if (
        get_assignment(state).ore_pos is not None
        and not in_combat_phase
        and core is not None
        and cond == condition_state.CORE_THREAT
        and defend_build_ready
        and defense.should_interrupt_for_core_defense(ct, state, core)
    ):
        claim_manager.release(ct, state, reason="core_in_immediate_danger")
        _set_phase(state, PHASE_DEFEND_BUILD, reason="core_in_immediate_danger")
        phase = PHASE_DEFEND_BUILD
        in_combat_phase = True

    if get_assignment(state).ore_pos is None and not in_combat_phase:
        if (
            core is not None
            and cond == condition_state.CORE_THREAT
            and defend_build_ready
            and defense.should_enter_defend_build(ct, state, core)
        ):
            _set_phase(state, PHASE_DEFEND_BUILD, reason="core_threat")
            phase = PHASE_DEFEND_BUILD
        elif (
            core is not None
            and cond not in (condition_state.STARVATION, condition_state.CORE_THREAT)
            and combat.should_dispatch_attacker(ct, state, core)
        ):
            # Never commit a fresh builder to offense while the Core is
            # under active threat — defense (should_enter_defend_build
            # above) already gets first refusal on this same free builder;
            # if it wasn't needed for defense specifically, it still
            # shouldn't leave for a brand-new attack mission while the
            # team is under pressure elsewhere. An already-dispatched
            # attacker is unaffected (in_combat_phase already skips this
            # whole block for it). Real match evidence, 2026-08-22: the
            # Core died in 80 rounds to an enemy sentinel with "no counter
            # attack from my side" — every spare builder should be
            # available for defense/economy during an active rush, not
            # peeled off to start a new offensive.
            combat.claim_attack_slot(ct, state)
            _set_phase(state, PHASE_ATTACK, reason="dispatch_attacker")
            phase = PHASE_ATTACK
        elif cond == condition_state.ECONOMY_THREAT and defense.economy_defend_step(
            ct, state, avoid=prev
        ):
            _finish("economy_defend")
            return
        elif (
            core is not None
            and cond == condition_state.CORE_THREAT
            and defense.core_in_immediate_danger(ct, core)
            and getattr(state, "eco_pause_streak", 0) < ECONOMY_PAUSE_MAX_ROUNDS
        ):
            # Titanium priority: the Core has zero coverage against an armed
            # threat right now — every bit of Ti spent starting a brand-new
            # harvester is Ti not available for the sentinel that actually
            # saves the Core. Already-claimed tasks are untouched (handled
            # above via _MINING_PHASES); this only withholds *starting* a
            # new one — the builder falls through to explore/wait instead.
            # Bounded (ECONOMY_PAUSE_MAX_ROUNDS) — see its own comment for
            # why an uncapped pause deadlocks the whole economy against a
            # threat that's never fully covered.
            state.eco_pause_streak = getattr(state, "eco_pause_streak", 0) + 1  # type: ignore[attr-defined]
        elif _try_acquire_mining_task(ct, state):
            state.eco_pause_streak = 0  # type: ignore[attr-defined]
            phase = getattr(state, "phase", PHASE_MOVE_TO_STAND)
            acted_hint = (
                f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
            )
    elif not in_combat_phase:
        phase = getattr(state, "phase", phase)

    _audit_fsm(ct, state, event="dispatch")

    if phase == PHASE_MOVE_TO_STAND:
        _state_move_to_stand(ct, state, avoid=prev)
        _finish()
        return
    if phase == PHASE_BUILD_HARVESTER:
        _state_build_harvester(ct, state, avoid=prev)
        _finish()
        return
    if phase == PHASE_PLAN_CONVEYOR:
        _state_plan_conveyor(ct, state)
        _finish()
        return
    if phase == PHASE_BUILD_CONVEYOR:
        _state_build_conveyor(ct, state, avoid=prev)
        _finish()
        return
    if phase == PHASE_RETURN_HOME:
        _state_return_home(ct, state, avoid=prev)
        _finish()
        return
    if phase == PHASE_DEFEND_BUILD:
        if core is None:
            state.defend_build_rounds = 0  # type: ignore[attr-defined]
            _set_phase(state, PHASE_EXPLORE, reason="no_core_for_defend")
            step_explore(ct, state, avoid=prev)
            _finish()
            return
        threats = condition_state.get_core_threats(ct, core)
        timed_out = (
            bool(threats)
            and getattr(state, "defend_build_rounds", 0) >= DEFEND_BUILD_TIMEOUT_ROUNDS
        )
        if not threats or timed_out:
            # timed_out: give up on this specific threat-response attempt —
            # a threat that never clears in DEFEND_BUILD_TIMEOUT_ROUNDS
            # rounds means either no legal placement exists here or the
            # enemy is replenishing faster than we can cover it. Staying
            # stuck here forever (PHASE_DEFEND_BUILD has no other exit)
            # guarantees total economy starvation across up to
            # DEFEND_BUILD_MAX_FROM_TRIGGER builders at once — a real match
            # loss (2026-08-22, jotunheim) — which is worse than accepting
            # the risk and resuming economy.
            state.defend_build_rounds = 0  # type: ignore[attr-defined]
            if timed_out:
                # Ineligible to be pulled back into PHASE_DEFEND_BUILD for a
                # while — an unresolved threat would otherwise re-trigger
                # should_enter_defend_build / should_interrupt_for_core_
                # defense the very next round, cycling so fast a mining task
                # never gets far enough to matter.
                state.defend_build_cooldown_until = (  # type: ignore[attr-defined]
                    ct.get_current_round() + DEFEND_BUILD_COOLDOWN_ROUNDS
                )
            reason = "defend_build_timeout" if timed_out else "core_threat_cleared"
            _set_phase(state, PHASE_EXPLORE, reason=reason)
            if get_assignment(state).ore_pos is None and _try_acquire_mining_task(
                ct, state
            ):
                _state_move_to_stand(ct, state, avoid=prev)
            else:
                step_explore(ct, state, avoid=prev)
            _finish()
            return
        state.defend_build_rounds = getattr(state, "defend_build_rounds", 0) + 1  # type: ignore[attr-defined]
        defense.defend_build_step(ct, state, core, avoid=prev)
        _finish()
        return
    if phase == PHASE_ATTACK:
        # Confirm we actually won the slot the round after claiming — a
        # same-round race could have handed it to another builder.
        if not combat.attack_slot_claim_confirmed(ct, state):
            combat.release_attack_slot(ct, state)
            _set_phase(state, PHASE_EXPLORE, reason="lost_attack_slot")
            step_explore(ct, state, avoid=prev)
            _finish()
            return
        if combat.should_recall_attacker(ct, state):
            combat.release_attack_slot(ct, state)
            _set_phase(state, PHASE_EXPLORE, reason="attack_recalled")
            step_explore(ct, state, avoid=prev)
            _finish()
            return
        attack_step(ct, state, avoid=prev)
        _finish("attack")
        return
    if phase == PHASE_DEFEND:
        # Legacy static path — no longer auto-entered, kept for safety.
        if get_assignment(state).ore_pos is None and _try_acquire_mining_task(ct, state):
            acted_hint = acted_hint or (
                f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
            )
            _state_move_to_stand(ct, state, avoid=prev)
            _finish()
            return
        if try_attack_adjacent(ct):
            _finish("attack_adjacent")
            return
        defend_step(ct, state, avoid=prev)
        _finish()
        return

    step_explore(ct, state, avoid=prev)
    if get_assignment(state).ore_pos is None and _try_acquire_mining_task(ct, state):
        acted_hint = acted_hint or (
            f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
        )
    elif defense.try_repair_adjacent(ct):
        # Opportunistic — a builder that's merely exploring/idle and finds
        # nothing to mine still heals a damaged adjacent structure it
        # happens to pass, instead of leaving it broken until the next
        # active ECONOMY_THREAT response. step_explore already spent this
        # round's movement; a heal/fire uses the separate action cooldown,
        # so this costs nothing extra.
        acted_hint = acted_hint or "explore_repair_adjacent"
    elif combat.try_attack_adjacent_economy(ct):
        # Opportunistic — likewise damage an adjacent enemy harvester/
        # conveyor/splitter merely passed while exploring (user-requested,
        # 2026-08-22: "economy builder should attack enemy harvesters or
        # conveyors they meet in their way of exploring"). Adjacency-only
        # scoping (same helper the dedicated attacker uses) means this is
        # never a deliberate detour, only a reaction to what's already
        # right next to the builder's own exploration path.
        acted_hint = acted_hint or "explore_attack_adjacent_economy"
    _finish()


def _state_move_to_stand(
    ct: Controller, state: object, *, avoid: Position | None
) -> None:
    assignment = get_assignment(state)
    ore = assignment.ore_pos
    if ore is None:
        _set_phase(state, PHASE_EXPLORE, reason="no_ore_in_move_to_stand")
        return
    core = getattr(state, "core_pos", None)
    stand = getattr(state, "approach_stand", None)
    if stand is None:
        stand = pick_ore_stand(ct, ore, core)
        state.approach_stand = stand  # type: ignore[attr-defined]
    if stand is None:
        _cancel_pre_harvester(ct, state, reason="no_stand")
        if getattr(state, "phase", None) == PHASE_MOVE_TO_STAND:
            # Re-acquired a different ore same turn.
            return
        return

    pos = ct.get_position()
    if xy(pos) == xy(stand) or orth_adjacent(pos, ore):
        state.approach_stand = (  # type: ignore[attr-defined]
            pos if orth_adjacent(pos, ore) else stand
        )
        state.stuck = 0  # type: ignore[attr-defined]
        _set_phase(state, PHASE_BUILD_HARVESTER, reason="arrived_at_stand")
        _state_build_harvester(ct, state, avoid=avoid)
        return

    step_to(ct, state, stand, avoid=avoid)
    _audit_fsm(ct, state, event="move_to_stand", chosen=stand)


def _state_build_harvester(
    ct: Controller, state: object, *, avoid: Position | None
) -> None:
    assignment = get_assignment(state)
    ore = assignment.ore_pos
    if ore is None:
        _set_phase(state, PHASE_EXPLORE, reason="no_ore_in_build_harvester")
        return
    pos = ct.get_position()

    if not orth_adjacent(pos, ore):
        _set_phase(state, PHASE_MOVE_TO_STAND, reason="lost_adjacency")
        _state_move_to_stand(ct, state, avoid=avoid)
        return

    try:
        built = ct.is_in_vision(ore) and ct.get_tile_building_id(ore) is not None
    except Exception:
        built = False
    if built and not assignment.has_harvester:
        _cancel_pre_harvester(ct, state, reason="ore_already_built")
        return

    if assignment.has_harvester:
        _set_phase(state, PHASE_PLAN_CONVEYOR, reason="harvester_already_owned")
        _state_plan_conveyor(ct, state)
        return

    if try_build_harvester(ct, ore):
        claim_manager.note_harvester(state, ore)
        state.harvests_done = getattr(state, "harvests_done", 0) + 1  # type: ignore[attr-defined]
        state.stuck = 0  # type: ignore[attr-defined]
        _set_phase(state, PHASE_PLAN_CONVEYOR, reason="harvester_built")
        _audit_fsm(ct, state, event="harvester_built", reason="act_spent")
        return

    _audit_fsm(ct, state, event="build_harvester_wait", reason="cannot_build")


def _state_plan_conveyor(ct: Controller, state: object) -> None:
    core = getattr(state, "core_pos", None)
    assignment = get_assignment(state)
    ore = assignment.ore_pos
    stand = getattr(state, "approach_stand", None)

    if core is None or ore is None or not assignment.has_harvester:
        _fail_task(ct, state, reason="missing_ownership_for_plan")
        return
    if stand is None or not orth_adjacent(stand, ore):
        stand = pick_ore_stand(ct, ore, core)
        state.approach_stand = stand  # type: ignore[attr-defined]
    if stand is None:
        _fail_task(ct, state, reason="no_stand_for_plan")
        return

    if ct.is_in_vision(ore) and not friendly_harvester_on(ct, ore):
        _fail_task(ct, state, reason="harvester_missing")
        return

    plan = compute_conveyor_plan(ct, stand, core)
    if plan is None or not plan:
        _fail_task(ct, state, reason="plan_impossible")
        return

    state.build_plan = plan  # type: ignore[attr-defined]
    state.build_index = 0  # type: ignore[attr-defined]
    state.replan_count = 0  # type: ignore[attr-defined]
    state.last_replan_reason = None  # type: ignore[attr-defined]
    state.stuck = 0  # type: ignore[attr-defined]
    _set_phase(state, PHASE_BUILD_CONVEYOR, reason=f"plan_ready_len={len(plan)}")
    _audit_fsm(ct, state, event="plan_created", reason=f"len={len(plan)}")


def _first_incomplete_index(
    ct: Controller, plan: list[Position], core: Position
) -> int:
    """Index of first plan tile that is not yet a conveyor (or len if done).

    Fog behind the builder is not treated as incomplete when a later plan tile
    is already a visible conveyor — otherwise we try to "walk back" to pave
    tiles that left vision and freeze mid-belt.
    """
    for i, t in enumerate(plan):
        st = plan_tile_status(ct, t, core)
        if st == "conveyor" or st == "core":
            continue
        if st == "fog":
            later_conveyor = any(
                plan_tile_status(ct, plan[j], core) == "conveyor"
                for j in range(i + 1, len(plan))
            )
            if later_conveyor:
                continue
        return i
    return len(plan)


def _replan_or_fail(
    ct: Controller,
    state: object,
    core: Position,
    *,
    from_index: int,
    reason: str,
    chosen: Position | None = None,
) -> None:
    ok = replan_conveyor_suffix(
        ct, state, core, from_index=from_index, reason=reason
    )
    _audit_fsm(
        ct,
        state,
        event="replan",
        reason=getattr(state, "last_replan_reason", reason) or reason,
        chosen=chosen,
    )
    if not ok:
        _fail_task(ct, state, reason=f"replan_failed_{reason}")


def _clear_enemy_belt_on_plan(
    ct: Controller,
    state: object,
    tile: Position,
    *,
    avoid: Position | None,
) -> None:
    """Smash enemy conveyor/splitter on a plan tile — do not replan around it.

    On choke maps the only path home may run through enemy belts. Replanning
    away often dumps the builder into a walled dead-end; clear then pave.
    """
    pos = ct.get_position()
    if orth_adjacent(pos, tile):
        if builder_can_act(ct) and ct.get_global_resources() >= 2 and ct.can_fire(tile):
            ct.fire(tile)
            _audit_fsm(ct, state, event="clear_enemy_belt", reason="attack", chosen=tile)
            return
        _audit_fsm(ct, state, event="clear_enemy_belt", reason="wait_attack", chosen=tile)
        return

    if xy(pos) == xy(tile):
        # Standing on enemy belt — step off orthogonally so we can melee it.
        core = getattr(state, "core_pos", None)
        goal = nearest_core_tile(pos, core) if core is not None else pos
        step_to(ct, state, goal, avoid=avoid)
        _audit_fsm(ct, state, event="clear_enemy_belt", reason="step_off", chosen=tile)
        return

    step_to(ct, state, tile, avoid=avoid)
    _audit_fsm(ct, state, event="clear_enemy_belt", reason="approach", chosen=tile)


def _state_build_conveyor(
    ct: Controller, state: object, *, avoid: Position | None
) -> None:
    """Consume ``build_plan`` only — never pick a greedy neighbour.

    Choreography (move XOR act):
      • Standing on empty plan[i]: build plan[i+1] ahead, then step onto it.
      • Adjacent to any empty plan tile: pave the lowest-index one first
        (so stand is paved immediately after step-off, before marching home).
      • Otherwise step along already-built plan conveyors / Core toward the
        first incomplete tile.
    """
    core = getattr(state, "core_pos", None)
    assignment = get_assignment(state)
    ore = assignment.ore_pos
    plan: list[Position] = list(getattr(state, "build_plan", None) or [])

    if core is None or ore is None or not assignment.has_harvester or not plan:
        _fail_task(ct, state, reason="invalid_build_state")
        return

    if ct.is_in_vision(ore) and not friendly_harvester_on(ct, ore):
        _fail_task(ct, state, reason="harvester_missing")
        return

    if plan_is_complete(ct, state, core):
        _set_phase(state, PHASE_RETURN_HOME, reason="all_plan_tiles_built")
        _state_return_home(ct, state, avoid=avoid)
        return

    pos = ct.get_position()

    # Prefer paving the earliest incomplete tile we can already reach.
    # Build-ahead leaves plan[0] empty behind us — fill it before marching on.
    adj_incomplete: list[int] = []
    for i, t in enumerate(plan):
        st = plan_tile_status(ct, t, core)
        if st == "empty" and orth_adjacent(pos, t):
            adj_incomplete.append(i)
        elif st == "fog" and orth_adjacent(pos, t):
            # Only chase fog if nothing further along the plan is built yet.
            later_conveyor = any(
                plan_tile_status(ct, plan[j], core) == "conveyor"
                for j in range(i + 1, len(plan))
            )
            if not later_conveyor:
                adj_incomplete.append(i)

    if adj_incomplete:
        idx = min(adj_incomplete)
        state.build_index = idx  # type: ignore[attr-defined]
        tile = plan[idx]
        # Always face the immediate next plan tile — even if we stand on it.
        # Skipping to plan[idx+2] aims at a diagonal on corners; cardinal
        # tie-break then picks the wrong axis (WEST instead of SOUTH), and
        # ore never follows the belt (valkyrie stand/corner pave).
        nxt = plan[idx + 1] if idx + 1 < len(plan) else nearest_core_tile(tile, core)
        result = try_build_plan_tile(ct, tile, nxt, core)
        _audit_fsm(ct, state, event="build_plan_tile", reason=result, chosen=tile)
        if result in ("insufficient_resources", "cooldown"):
            # Correctly positioned, just waiting to afford the next tile (or
            # off action-cooldown) — never let this read as "stuck." The
            # stuck counter otherwise climbs every round spent waiting and
            # eventually burns the replan budget (MAX_BELT_REPLANS) purely
            # from titanium being temporarily tight, not a routing problem
            # (2026-08-22 report: a harvester+belt task abandoned mid-link
            # after a resource dry spell).
            state.stuck = 0  # type: ignore[attr-defined]
            return
        if result == "built" and idx == 0:
            state.stand_paved = True  # type: ignore[attr-defined]
        if result == "blocked:enemy_belt":
            _clear_enemy_belt_on_plan(ct, state, tile, avoid=avoid)
            return
        if result == "blocked:enemy_sink":
            # Adjacent enemy belt is stealing the facing — smash it first.
            if try_attack_adjacent(ct):
                _audit_fsm(ct, state, event="clear_enemy_sink", chosen=tile)
                return
            _replan_or_fail(
                ct, state, core, from_index=idx, reason=result, chosen=tile
            )
            return
        if result.startswith("blocked:"):
            _replan_or_fail(
                ct, state, core, from_index=idx, reason=result, chosen=tile
            )
        return

    idx = _first_incomplete_index(ct, plan, core)
    state.build_index = idx  # type: ignore[attr-defined]
    if idx >= len(plan):
        _set_phase(state, PHASE_RETURN_HOME, reason="index_past_end")
        _state_return_home(ct, state, avoid=avoid)
        return

    tile = plan[idx]
    status = plan_tile_status(ct, tile, core)

    if status == "enemy_belt":
        _clear_enemy_belt_on_plan(ct, state, tile, avoid=avoid)
        return

    if status in ("wall", "ore", "building", "oob"):
        _replan_or_fail(
            ct, state, core, from_index=idx, reason=f"blocked:{status}", chosen=tile
        )
        return

    # Standing on the incomplete tile — cannot pave underfoot; build ahead.
    if xy(pos) == xy(tile) and status in ("empty", "fog", "unit"):
        if idx + 1 >= len(plan):
            goal = nearest_core_tile(pos, core)
            for dx in (0, 1):
                for dy in (0, 1):
                    c = Position(core.x + dx, core.y + dy)
                    if orth_adjacent(pos, c):
                        step_to(ct, state, c, avoid=avoid)
                        _audit_fsm(ct, state, event="step_core_to_pave_last", chosen=c)
                        return
            _audit_fsm(ct, state, event="build_wait", reason="on_last_empty", chosen=tile)
            return

        ahead = plan[idx + 1]
        ahead_st = plan_tile_status(ct, ahead, core)
        if ahead_st == "enemy_belt":
            _clear_enemy_belt_on_plan(ct, state, ahead, avoid=avoid)
            return
        if ahead_st in ("wall", "ore", "building", "oob"):
            _replan_or_fail(
                ct,
                state,
                core,
                from_index=idx + 1,
                reason=f"ahead_blocked:{ahead_st}",
                chosen=ahead,
            )
            return
        if ahead_st in ("empty", "fog"):
            if not orth_adjacent(pos, ahead):
                _audit_fsm(ct, state, event="build_wait", reason="ahead_not_adj", chosen=ahead)
                return
            face = plan[idx + 2] if idx + 2 < len(plan) else nearest_core_tile(ahead, core)
            result = try_build_plan_tile(ct, ahead, face, core)
            _audit_fsm(ct, state, event="build_ahead", reason=result, chosen=ahead)
            if result in ("insufficient_resources", "cooldown"):
                # See the adj_incomplete branch above — waiting to afford
                # the tile is not "stuck."
                state.stuck = 0  # type: ignore[attr-defined]
                return
            if result == "blocked:enemy_belt":
                _clear_enemy_belt_on_plan(ct, state, ahead, avoid=avoid)
                return
            if result == "blocked:enemy_sink":
                if try_attack_adjacent(ct):
                    _audit_fsm(ct, state, event="clear_enemy_sink", chosen=ahead)
                    return
                _replan_or_fail(
                    ct, state, core, from_index=idx + 1, reason=result, chosen=ahead
                )
                return
            if result.startswith("blocked:"):
                _replan_or_fail(
                    ct, state, core, from_index=idx + 1, reason=result, chosen=ahead
                )
            return
        if ahead_st in ("conveyor", "unit"):
            # "unit": a friendly/enemy unit standing on the next plan tile
            # — never just sit still waiting for it to move (see the other
            # occupied_by_unit fix above for why: real match evidence of a
            # 250+ round freeze from exactly this no-op).
            step_to(ct, state, ahead, avoid=avoid)
            _audit_fsm(ct, state, event="step_ahead_on_plan", chosen=ahead)
            return
        _audit_fsm(ct, state, event="build_wait", reason=f"ahead_{ahead_st}", chosen=ahead)
        return

    if status == "unit":
        # A friendly (or enemy) unit is standing on the plan tile itself —
        # never just sit still waiting for it to move on its own. That was
        # a pure no-op: no movement attempted, nothing to trigger the
        # navigator's own traffic-avoidance, so two builders that each
        # happened to be waiting on a tile the other occupied could
        # deadlock on each other forever (real match, 2026-08-23: two
        # builders frozen, zero action/move cooldown, from round 47 to
        # 300+). step_to lets the navigator sidestep or wait-with-timeout
        # like every other phase already does.
        _audit_fsm(ct, state, event="build_wait", reason="occupied_by_unit", chosen=tile)
        step_to(ct, state, tile, avoid=avoid)
        return

    # Walk the full built belt (not only prefix up to idx) so we can return to
    # pave stand / fill gaps after build-ahead marched west.
    if _step_along_built_plan(ct, state, plan, core, avoid=avoid, toward=tile):
        return
    _audit_fsm(ct, state, event="build_wait", reason=status, chosen=tile)


def _step_along_built_plan(
    ct: Controller,
    state: object,
    plan: list[Position],
    core: Position,
    *,
    avoid: Position | None,
    toward: Position,
) -> bool:
    """One step on existing plan conveyors / Core toward ``toward`` (invariant A)."""
    pos = ct.get_position()
    best: Position | None = None
    best_d = 10**9

    candidates: list[Position] = []
    for t in plan:
        st = plan_tile_status(ct, t, core)
        if st == "conveyor" or is_core_tile(t, core):
            candidates.append(t)
    for dx in (0, 1):
        for dy in (0, 1):
            candidates.append(Position(core.x + dx, core.y + dy))

    for t in candidates:
        if not orth_adjacent(pos, t):
            continue
        if xy(pos) == xy(t):
            continue
        # May only step onto conveyor / Core.
        if not is_core_tile(t, core) and plan_tile_status(ct, t, core) != "conveyor":
            continue
        d = t.distance_squared(toward)
        if d < best_d:
            best_d = d
            best = t
    if best is None:
        return False
    step_to(ct, state, best, avoid=avoid)
    _audit_fsm(ct, state, event="step_along_plan", chosen=best)
    return True


def _state_return_home(
    ct: Controller, state: object, *, avoid: Position | None
) -> None:
    """Plan conveyors exist — walk home on plan conveyors / Core, then complete."""
    core = getattr(state, "core_pos", None)
    assignment = get_assignment(state)
    plan: list[Position] = list(getattr(state, "build_plan", None) or [])

    if core is None or not assignment.has_harvester or not plan:
        _fail_task(ct, state, reason="invalid_return_state")
        return

    if not plan_is_complete(ct, state, core):
        _set_phase(state, PHASE_BUILD_CONVEYOR, reason="plan_incomplete_resume_build")
        _state_build_conveyor(ct, state, avoid=avoid)
        return

    pos = ct.get_position()
    if on_or_orth_adjacent_to_core(pos, core):
        _complete_task(ct, state, reason="arrived_core_plan_done")
        if getattr(state, "phase", None) == PHASE_MOVE_TO_STAND:
            _state_move_to_stand(ct, state, avoid=avoid)
        return

    goal = nearest_core_tile(pos, core)
    for dx in (0, 1):
        for dy in (0, 1):
            c = Position(core.x + dx, core.y + dy)
            if orth_adjacent(pos, c):
                step_to(ct, state, c, avoid=avoid)
                _audit_fsm(ct, state, event="step_to_core", chosen=c)
                return

    if _step_along_built_plan(ct, state, plan, core, avoid=avoid, toward=goal):
        return
    _audit_fsm(ct, state, event="return_wait", reason="no_adjacent_plan_conveyor")
