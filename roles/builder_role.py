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
from combat import attack_step, note_enemy_core, try_attack_adjacent
from constants import (
    PHASE_ATTACK,
    PHASE_BUILD_CONVEYOR,
    PHASE_BUILD_HARVESTER,
    PHASE_COMPLETE_TASK,
    PHASE_DEFEND,
    PHASE_EXPLORE,
    PHASE_FAILED_TASK,
    PHASE_MOVE_TO_STAND,
    PHASE_PLAN_CONVEYOR,
    PHASE_RETURN_HOME,
    SLOT_CORE_X,
    SLOT_CORE_Y,
    STUCK_LIMIT,
)
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
    nearest_core_tile,
    on_or_orth_adjacent_to_core,
    orth_adjacent,
    xy,
)
import idle_investigate
from navigator import step_to

_BUILDER_AUDIT = os.environ.get("BUILDER_AUDIT", "") == "1"
_AUDIT_PATH = os.environ.get(
    "BUILDER_AUDIT_LOG",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "builder_audit.log"),
)
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
    for uid in ct.get_nearby_buildings():
        if ct.get_entity_type(uid) == EntityType.CORE and ct.get_team(uid) == my_team:
            state.core_pos = ct.get_position(uid)  # type: ignore[attr-defined]
            return
    if ct.get_current_round() >= 2:
        state.core_pos = Position(  # type: ignore[attr-defined]
            ct.read_store(SLOT_CORE_X),
            ct.read_store(SLOT_CORE_Y),
        )


def _try_acquire_mining_task(
    ct: Controller,
    state: object,
    *,
    exclude: Position | None = None,
) -> bool:
    """Sole acquisition entry — ClaimManager.try_acquire_work only."""
    res = claim_manager.try_acquire_work(ct, state, exclude=exclude)
    idle_investigate.note_acquire_attempt(ct, state, res)
    if not res.ok or res.ore is None:
        return False
    clear_build_plan(state)
    state.approach_stand = None  # type: ignore[attr-defined]
    _set_phase(state, PHASE_MOVE_TO_STAND, reason=f"ore_found_{xy(res.ore)}")
    return True


def _fail_task(ct: Controller, state: object, *, reason: str) -> None:
    idle_investigate.note_abandon(state, reason=f"fail:{reason}", builder_id=ct.get_id())
    clear_build_plan(state)
    claim_manager.release(ct, state, reason=f"fail:{reason}")
    if _try_acquire_mining_task(ct, state):
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
        elif getattr(state, "stuck", 0) >= STUCK_LIMIT:
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

    # Exactly one acquisition path for free builders every turn.
    if get_assignment(state).ore_pos is None:
        if _try_acquire_mining_task(ct, state):
            phase = PHASE_MOVE_TO_STAND
            acted_hint = (
                f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
            )
        elif phase == PHASE_DEFEND:
            pass
        elif phase == PHASE_EXPLORE and getattr(state, "exploration_complete", False):
            _enter_late_game(ct, state)
            phase = PHASE_DEFEND
    else:
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
    if phase == PHASE_DEFEND:
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
        if get_assignment(state).ore_pos is None and _try_acquire_mining_task(ct, state):
            acted_hint = acted_hint or (
                f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
            )
        _finish()
        return
    if phase == PHASE_ATTACK:
        if get_assignment(state).ore_pos is None and _try_acquire_mining_task(ct, state):
            acted_hint = acted_hint or (
                f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
            )
            _state_move_to_stand(ct, state, avoid=prev)
            _finish()
            return
        attack_step(ct, state, avoid=prev)
        if get_assignment(state).ore_pos is None and _try_acquire_mining_task(ct, state):
            acted_hint = acted_hint or (
                f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
            )
        _finish()
        return

    step_explore(ct, state, avoid=prev)
    if get_assignment(state).ore_pos is None and _try_acquire_mining_task(ct, state):
        acted_hint = acted_hint or (
            f"claim_ore_{xy(get_assignment(state).ore_pos)}"  # type: ignore[arg-type]
        )
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
        elif st in ("wall", "ore", "building", "oob"):
            # Hard block on a planned tile — replan from here.
            _replan_or_fail(
                ct, state, core, from_index=i, reason=f"blocked:{st}", chosen=t
            )
            return

    if adj_incomplete:
        idx = min(adj_incomplete)
        state.build_index = idx  # type: ignore[attr-defined]
        tile = plan[idx]
        nxt = plan[idx + 1] if idx + 1 < len(plan) else nearest_core_tile(tile, core)
        # Face along the belt, not at our own feet (common when paving stand
        # from plan[1] after build-ahead).
        if xy(nxt) == xy(pos):
            nxt = (
                plan[idx + 2]
                if idx + 2 < len(plan)
                else nearest_core_tile(tile, core)
            )
        result = try_build_plan_tile(ct, tile, nxt, core)
        if result == "can_build_false":
            # Facing along-plan failed; try Core-facing as a last local attempt.
            result = try_build_plan_tile(
                ct, tile, nearest_core_tile(tile, core), core
            )
        _audit_fsm(ct, state, event="build_plan_tile", reason=result, chosen=tile)
        if result == "built" and idx == 0:
            state.stand_paved = True  # type: ignore[attr-defined]
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
            if result.startswith("blocked:"):
                _replan_or_fail(
                    ct, state, core, from_index=idx + 1, reason=result, chosen=ahead
                )
            return
        if ahead_st == "conveyor":
            step_to(ct, state, ahead, avoid=avoid)
            _audit_fsm(ct, state, event="step_ahead_on_plan", chosen=ahead)
            return
        _audit_fsm(ct, state, event="build_wait", reason=f"ahead_{ahead_st}", chosen=ahead)
        return

    if status == "unit":
        _audit_fsm(ct, state, event="build_wait", reason="occupied_by_unit", chosen=tile)
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
