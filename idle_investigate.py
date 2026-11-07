"""Idle taxonomy investigation (read-only). Enable with IDLE_AUDIT=1.

Logs per-builder facts every turn. Post-hoc analysis classifies idle into
exactly one of 7 categories and answers ownership invariant questions.

Does not change builder decisions.
"""

from __future__ import annotations

import os

from fcode import Controller, EntityType, Environment, Position

from assignment import get_assignment, ore_owner, read_claim_board
from economy import ore_claimed_by_other, ore_owned_by_other, scan_ore
from geometry import builder_can_act, xy

_ENABLED = os.environ.get("IDLE_AUDIT", "") == "1"
# Plain relative path — do not anchor this to the module's own file path.
# The platform's sandboxed loader leaves that dunder undefined for bot
# modules, and this default is evaluated eagerly at import time even when
# audit logging is disabled.
_PATH = os.environ.get("IDLE_AUDIT_LOG", "idle_audit.log")
_FP = None

_IDLE_ATTR = "_idle_turns"
_PREV_PHASE_ATTR = "_idle_prev_phase"
_LAST_CHANGE_ATTR = "_idle_last_change_round"
_LAST_ACTION_ATTR = "_idle_last_action"


def enabled() -> bool:
    return _ENABLED


def _write(line: str) -> None:
    global _FP
    if not _ENABLED:
        return
    if _FP is None:
        _FP = open(_PATH, "a", buffering=1)  # noqa: SIM115
    _FP.write(line + "\n")


def _kv(**kwargs: object) -> str:
    parts = []
    for k, v in kwargs.items():
        parts.append(f"{k}={v}")
    return " | ".join(parts)


def _visible_uncovered_ore(ct: Controller) -> list[Position]:
    out: list[Position] = []
    for tile in ct.get_nearby_tiles():
        try:
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            out.append(tile)
        except Exception:
            continue
    return out


def _nearest_other_builder(ct: Controller, ore: Position) -> tuple[int | None, int | None]:
    """Return (uid, dist_sq) of nearest other friendly builder to ore."""
    my_id = ct.get_id()
    my_team = ct.get_team()
    best: int | None = None
    best_d: int | None = None
    for uid in ct.get_nearby_units():
        if uid == my_id:
            continue
        try:
            if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
                continue
            if ct.get_team(uid) != my_team:
                continue
            d = ct.get_position(uid).distance_squared(ore)
            if best_d is None or d < best_d or (d == best_d and best is not None and uid < best):
                best_d = d
                best = uid
        except Exception:
            continue
    return best, best_d


def _ore_block_facts(
    ct: Controller,
    ores: list[Position],
    state: object | None = None,
) -> list[dict[str, object]]:
    """Per visible uncovered ore: ownership board vs free-rival tie-break."""
    my_pos = ct.get_position()
    board = read_claim_board(ct)
    facts: list[dict[str, object]] = []
    for o in ores:
        nearer_id, nearer_d = _nearest_other_builder(ct, o)
        my_d = my_pos.distance_squared(o)
        owner = ore_owner(o, ct)
        owned_by_other = ore_owned_by_other(ct, o)
        blocked = ore_claimed_by_other(ct, o, state=state)
        facts.append(
            {
                "ore": xy(o),
                "my_dist": my_d,
                "nearer_id": nearer_id,
                "nearer_dist": nearer_d,
                "board_owner": owner,
                "owned_by_other": owned_by_other,
                "claim_blocks_me": blocked,
                # Legacy alias for post-hoc parsers.
                "proximity_blocks_me": blocked,
                "board": dict(board),
            }
        )
    return facts


def _current_target(state: object, phase: str, a) -> object:
    if a.ore_pos is not None and phase in (
        "move_to_stand",
        "build_harvester",
        "plan_conveyor",
        "build_conveyor",
        "return_home",
    ):
        stand = getattr(state, "approach_stand", None)
        plan = getattr(state, "build_plan", None) or []
        idx = int(getattr(state, "build_index", 0))
        if phase == "move_to_stand" and stand is not None:
            return ("stand", xy(stand))
        if phase == "build_conveyor" and 0 <= idx < len(plan):
            return ("plan_tile", xy(plan[idx]))
        if phase == "return_home" and getattr(state, "core_pos", None) is not None:
            return ("core", xy(state.core_pos))  # type: ignore[arg-type]
        return ("ore", xy(a.ore_pos))
    wp = getattr(state, "current_waypoint", None)
    if wp is not None and phase == "explore":
        return ("waypoint", xy(wp))
    if phase == "defend" and getattr(state, "core_pos", None) is not None:
        return ("core", xy(state.core_pos))  # type: ignore[arg-type]
    return None


def _assignment_still_valid(ct: Controller, a) -> str:
    if a.ore_pos is None:
        return "n/a_no_assignment"
    ore = a.ore_pos
    try:
        if not ct.is_in_vision(ore):
            return "valid_out_of_vision_trusted"
        bid = ct.get_tile_building_id(ore)
        if a.has_harvester:
            if bid is None:
                return "invalid_harvester_missing"
            try:
                if ct.get_entity_type(bid) != EntityType.HARVESTER:
                    return "invalid_not_harvester"
                if ct.get_team(bid) != ct.get_team():
                    return "invalid_enemy_building"
            except Exception:
                return "valid_building_opaque"
            return "valid_owned_harvester"
        # Pre-harvester claim
        if bid is not None:
            return "invalid_ore_occupied_pre_harvester"
        if ct.get_tile_env(ore) != Environment.ORE_TITANIUM:
            return "invalid_not_ore"
        return "valid_claimed_unbuilt"
    except Exception:
        return "valid_check_error"


def begin_turn(ct: Controller, state: object) -> dict[str, object]:
    if not _ENABLED:
        return {}
    a = get_assignment(state)
    ores = _visible_uncovered_ore(ct)
    return {
        "round": ct.get_current_round(),
        "id": ct.get_id(),
        "phase_before": getattr(state, "phase", "?"),
        "pos_before": xy(ct.get_position()),
        "ore": xy(a.ore_pos) if a.ore_pos else None,
        "has_h": a.has_harvester,
        "visible_ore": [xy(o) for o in ores],
        "ore_facts": _ore_block_facts(ct, ores, state),
        "claimable": (lambda p: xy(p) if p else None)(scan_ore(ct, state)),
        "explore_done": bool(getattr(state, "exploration_complete", False)),
        "can_act": builder_can_act(ct),
        "action_cd": ct.get_action_cooldown(),
        "move_cd": ct.get_move_cooldown(),
        "ti": ct.get_global_resources(),
        "stuck": getattr(state, "stuck", 0),
        "nav_mode": getattr(state, "nav_mode", None),
        "free_turns": getattr(state, "free_turns", 0),
    }


def end_turn(
    ct: Controller,
    state: object,
    ctx: dict[str, object],
    *,
    acted_hint: str = "",
) -> None:
    if not _ENABLED or not ctx:
        return

    a = get_assignment(state)
    phase = getattr(state, "phase", "?")
    pos = ct.get_position()
    moved = xy(pos) != ctx["pos_before"]

    prev_logged = getattr(state, _PREV_PHASE_ATTR, ctx["phase_before"])
    if phase != prev_logged:
        setattr(state, _LAST_CHANGE_ATTR, ctx["round"])
        setattr(state, _PREV_PHASE_ATTR, phase)
        _write(
            "STATE_CHANGE | "
            + _kv(
                r=ctx["round"],
                id=ctx["id"],
                from_phase=prev_logged,
                to_phase=phase,
                ore=xy(a.ore_pos) if a.ore_pos else None,
                has_h=a.has_harvester,
                explore_done=getattr(state, "exploration_complete", False),
            )
        )

    # Refresh ore facts at end (vision may have changed after move — use start facts
    # for claim decision; end for "could take work now").
    end_ores = _visible_uncovered_ore(ct)
    end_facts = _ore_block_facts(ct, end_ores, state)
    end_claimable = scan_ore(ct, state)
    assign_valid = _assignment_still_valid(ct, a)
    target = _current_target(state, str(phase), a)

    # Did we take a meaningful action?
    action = acted_hint or ("move" if moved else "none")
    if acted_hint:
        setattr(state, _LAST_ACTION_ATTR, acted_hint)
    elif moved:
        setattr(state, _LAST_ACTION_ATTR, f"move:{xy(pos)}")

    # Free = no mining assignment.
    free = a.ore_pos is None
    unfinished_visible = [xy(o) for o in end_ores]
    # Could legally take work under CURRENT code = claimable is not None.
    could_take_under_current_rules = end_claimable is not None
    # Could take if proximity veto ignored = any uncovered visible ore.
    could_take_if_unowned_allowed = bool(end_ores) and free

    proximity_only_blocks: list[object] = []
    for f in end_facts:
        if f["proximity_blocks_me"]:
            proximity_only_blocks.append(
                {
                    "ore": f["ore"],
                    "nearer_id": f["nearer_id"],
                    "nearer_dist": f["nearer_dist"],
                    "my_dist": f["my_dist"],
                }
            )

    # Local preliminary category (post-hoc may refine 1 vs 2).
    category = _local_category(
        phase=str(phase),
        free=free,
        moved=moved,
        action=action,
        stuck=int(getattr(state, "stuck", 0)),
        can_act=bool(ctx["can_act"]),
        action_cd=int(ctx["action_cd"]),
        claimable_start=ctx["claimable"],
        claimable_end=(xy(end_claimable) if end_claimable else None),
        unfinished=unfinished_visible,
        proximity_blocks=proximity_only_blocks,
        assign_valid=assign_valid,
        explore_done=bool(getattr(state, "exploration_complete", False)),
        has_h=a.has_harvester,
    )

    idle = category is not None
    idle_turns = int(getattr(state, _IDLE_ATTR, 0))
    idle_turns = idle_turns + 1 if idle else 0
    setattr(state, _IDLE_ATTR, idle_turns)

    why_no_action = _why_no_action(
        action=action,
        category=category,
        phase=str(phase),
        free=free,
        proximity_blocks=proximity_only_blocks,
        claimable=ctx["claimable"],
        can_act=bool(ctx["can_act"]),
        action_cd=int(ctx["action_cd"]),
        stuck=int(getattr(state, "stuck", 0)),
    )

    _write(
        "TAXONOMY | "
        + _kv(
            r=ctx["round"],
            id=ctx["id"],
            phase=phase,
            phase_before=ctx["phase_before"],
            pos=xy(pos),
            assignment_ore=xy(a.ore_pos) if a.ore_pos else None,
            assignment_harvester=a.has_harvester,
            current_target=target,
            current_action=action,
            why_no_action=why_no_action,
            assignment_valid=assign_valid,
            # True ownership of *other* builders unknown here — post-hoc joins.
            other_owns_unknown=True,
            merely_closer_blocks=proximity_only_blocks,
            unfinished_work_visible=unfinished_visible,
            could_take_under_current_rules=could_take_under_current_rules,
            could_take_if_unowned_allowed=could_take_if_unowned_allowed,
            claimable_start=ctx["claimable"],
            claimable_end=(xy(end_claimable) if end_claimable else None),
            free=free,
            idle=int(idle),
            idle_turns=idle_turns,
            category=category if category is not None else "active",
            explore_done=getattr(state, "exploration_complete", False),
            stuck=getattr(state, "stuck", 0),
            nav_mode=getattr(state, "nav_mode", None),
            can_act=ctx["can_act"],
            action_cd=ctx["action_cd"],
            move_cd=ctx["move_cd"],
            ti=ctx["ti"],
            moved=int(moved),
        )
    )

    if idle and idle_turns == 1:
        _write(
            "IDLE_ENTER | "
            + _kv(
                r=ctx["round"],
                id=ctx["id"],
                phase=phase,
                category=category,
                why=why_no_action,
                assignment_ore=xy(a.ore_pos) if a.ore_pos else None,
                unfinished=unfinished_visible,
                merely_closer=proximity_only_blocks,
            )
        )

    # Flag: free builder, unfinished visible, blocked only by proximity.
    if free and proximity_only_blocks and not could_take_under_current_rules:
        _write(
            "PROXIMITY_VETO | "
            + _kv(
                r=ctx["round"],
                id=ctx["id"],
                phase=phase,
                blocked=proximity_only_blocks,
                note="free_builder_blocked_by_closer_not_proven_owner",
            )
        )

    # Flag: assignment None and phase not explore.
    if free and phase not in ("explore", "complete_task", "failed_task"):
        # complete/failed are transient same-turn; still log defend/attack/etc.
        if phase in ("defend", "attack", "move_to_stand", "build_harvester",
                     "plan_conveyor", "build_conveyor", "return_home"):
            _write(
                "PHASE_WITHOUT_ASSIGNMENT | "
                + _kv(r=ctx["round"], id=ctx["id"], phase=phase, explore_done=getattr(state, "exploration_complete", False))
            )


def _local_category(
    *,
    phase: str,
    free: bool,
    moved: bool,
    action: str,
    stuck: int,
    can_act: bool,
    action_cd: int,
    claimable_start: object,
    claimable_end: object,
    unfinished: list,
    proximity_blocks: list,
    assign_valid: str,
    explore_done: bool,
    has_h: bool,
) -> str | None:
    """Return idle category 1–7 name, or None if actively working.

    Categories 1 vs 2 refined post-hoc when ownership of nearer builder is known.
    Locally, proximity block without claimable → category 2 provisional
    ('ownership_logic_incorrectly_blocks') — post-hoc may reclassify to 1.
    """
    # Actively progressing
    if action not in ("none",) and action != "none":
        if not free and action.startswith("claim"):
            return None
        if moved and phase in (
            "explore",
            "move_to_stand",
            "build_conveyor",
            "return_home",
            "defend",
            "attack",
        ):
            # Moving while free+blocked still violates mining invariant — idle cat 2.
            if free and proximity_blocks and unfinished:
                return "2_ownership_logic_incorrectly_blocks"
            if free and explore_done and phase == "defend" and unfinished:
                return "3_state_machine_forgot_request"
            return None
        if action.startswith("claim") or action.startswith("attack"):
            return None

    if not free:
        if assign_valid.startswith("invalid"):
            return "6_task_became_invalid"
        if phase in ("build_harvester", "build_conveyor", "plan_conveyor"):
            if action_cd != 0 or not can_act:
                return None  # waiting cooldown is valid work wait, not idle taxonomy focus
            if stuck >= 3 and not moved:
                return "4_navigation_failed"
            if not moved and action == "none":
                return "5_build_logic_failed"
            return None
        if phase in ("move_to_stand", "return_home"):
            if stuck >= 3 and not moved:
                return "4_navigation_failed"
            if not moved and action == "none":
                return "4_navigation_failed"
            return None
        return None

    # Free builder
    if phase == "complete_task":
        return "3_state_machine_forgot_request"
    if phase == "failed_task":
        return "3_state_machine_forgot_request"
    if phase == "defend":
        if unfinished or proximity_blocks:
            return "3_state_machine_forgot_request"
        # No visible unfinished ore — may be legitimately done locally
        if explore_done:
            return "3_state_machine_forgot_request"  # cannot return to mining by design
        return "7_unknown"
    if phase == "attack":
        if unfinished:
            return "3_state_machine_forgot_request"
        return None
    if phase == "explore":
        if claimable_start is not None or claimable_end is not None:
            return "3_state_machine_forgot_request"  # should have claimed
        if proximity_blocks and unfinished:
            return "2_ownership_logic_incorrectly_blocks"  # provisional; post-hoc →1 if owned
        if stuck >= 3 and not moved:
            return "4_navigation_failed"
        if not moved and action == "none":
            return "4_navigation_failed"
        # Moving toward waypoint with no unfinished visible
        if moved and not unfinished:
            return None
        if moved and unfinished and proximity_blocks:
            return "2_ownership_logic_incorrectly_blocks"
        return None

    return "7_unknown"


def _why_no_action(
    *,
    action: str,
    category: str | None,
    phase: str,
    free: bool,
    proximity_blocks: list,
    claimable: object,
    can_act: bool,
    action_cd: int,
    stuck: int,
) -> str:
    if action != "none":
        return f"acted:{action}"
    if category == "2_ownership_logic_incorrectly_blocks":
        return f"proximity_veto_blocks_unowned_candidate:{proximity_blocks}"
    if category == "1_other_builder_owns_task":
        return "all_visible_work_owned_by_others"
    if category == "3_state_machine_forgot_request":
        if phase == "defend":
            return "phase_defend_never_calls_scan_ore"
        if phase == "complete_task":
            return "complete_task_skips_scan_same_turn"
        if claimable is not None:
            return f"claimable_ignored:{claimable}"
        return f"phase_{phase}_no_claim_attempt"
    if category == "4_navigation_failed":
        return f"nav_stuck={stuck}_no_move"
    if category == "5_build_logic_failed":
        return f"build_no_progress_can_act={can_act}_cd={action_cd}"
    if category == "6_task_became_invalid":
        return "assignment_invalid"
    if not can_act and action_cd != 0:
        return f"action_cooldown={action_cd}"
    return f"no_action_phase={phase}_free={free}"


def log_scheduler_summary(ct: Controller, state: object) -> None:
    if not _ENABLED:
        return
    if ct.get_entity_type() != EntityType.CORE:
        return
    ores = _visible_uncovered_ore(ct)
    my_team = ct.get_team()
    builders = []
    for uid in ct.get_nearby_units():
        try:
            if ct.get_entity_type(uid) == EntityType.BUILDER_BOT and ct.get_team(uid) == my_team:
                builders.append((uid, xy(ct.get_position(uid))))
        except Exception:
            continue
    harvs = []
    for uid in ct.get_nearby_buildings():
        try:
            if ct.get_entity_type(uid) == EntityType.HARVESTER and ct.get_team(uid) == my_team:
                harvs.append((uid, xy(ct.get_position(uid))))
        except Exception:
            continue
    from assignment import read_claim_board

    claims = {}
    try:
        claims = {str(k): v for k, v in read_claim_board(ct).items()}
    except Exception:
        claims = {}
    _write(
        "SCHEDULER | "
        + _kv(
            r=ct.get_current_round(),
            visible_uncovered_ore=[xy(o) for o in ores],
            n_ore=len(ores),
            visible_builders=builders,
            n_builders=len(builders),
            visible_harvesters=harvs,
            claim_board=claims,
            note="explicit_claim_board_slots_6_15",
        )
    )


def note_abandon(state: object, *, reason: str, builder_id: int | None = None) -> None:
    if not _ENABLED:
        return
    a = get_assignment(state)
    _write(
        "ABANDON_CALL | "
        + _kv(
            id=builder_id,
            reason=reason,
            ore=xy(a.ore_pos) if a.ore_pos else None,
            has_h=a.has_harvester,
            phase=getattr(state, "phase", None),
        )
    )


def note_acquire_attempt(ct: Controller, state: object, res: object) -> None:
    """Log ClaimManager try_acquire_work outcome (including per-ore rejects)."""
    if not _ENABLED:
        return
    rejects = getattr(res, "rejects", None) or []
    _write(
        "ACQUIRE_ATTEMPT | "
        + _kv(
            r=ct.get_current_round(),
            id=ct.get_id(),
            ok=bool(getattr(res, "ok", False)),
            ore=xy(getattr(res, "ore", None)) if getattr(res, "ore", None) else None,
            reason=getattr(res, "reason", ""),
            rejects=list(rejects)[:12],
            turns_alive=getattr(state, "claim_turns_alive", None),
            free_turns=getattr(state, "free_turns", None),
        )
    )
