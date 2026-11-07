"""Reactive defense — answer a CORE_THREAT by placing a *verified* turret
near the threat, and an ECONOMY_THREAT by repairing / clearing the belt.

Never trust ``facing_toward()`` alone: a wall between the obvious tile and the
threat looks identical to it, so every placement is confirmed with the
engine's own ``can_fire_from`` before committing. Sentinels cannot rotate, so
a placement that cannot verify a hit is worthless — we skip it and try another
tile, aim at the blocking barrier, or advance into melee range instead.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

from constants import (
    CARDINALS,
    DEFEND_BUILD_MAX_FROM_TRIGGER,
    DEFEND_THREAT_APPROACH_RANGE_SQ,
    DEFENSE_RING_SQ,
    MAX_GUNNERS,
    TITANIUM_RESERVE,
)
import condition_state
from geometry import builder_can_act, facing_away_from, facing_toward, orth_adjacent, xy
from navigator import step_to
import pathfind
from team_store import bump_gunner_count, read_gunner_count

_ARMED_TURRETS = (EntityType.GUNNER, EntityType.SENTINEL)


# ---------------------------------------------------------------------------
# Threat sizing
# ---------------------------------------------------------------------------


def core_threat_direction(core: Position, threat: Position) -> Direction:
    """Cardinal bucket of a threat relative to the Core."""
    return facing_toward(core, threat)


def target_turret_count(ct: Controller, core: Position) -> int:
    """How many turrets the current armed core threats warrant.

    One per armed threat tile (gunner/sentinel/builder) — a clustered rush
    from one side still needs a turret's worth of firepower each, so this is
    *not* bucketed by direction. Passive barriers do not count.
    """
    threats = condition_state.get_core_threats(ct, core)
    return sum(1 for t in threats if t.kind != condition_state.THREAT_BARRIER)


def turrets_near_core(ct: Controller, core: Position) -> int:
    """Friendly gunners/sentinels standing within the Core ring."""
    my_team = ct.get_team()
    n = 0
    for uid in condition_state.get_cached_buildings(ct):
        try:
            if ct.get_team(uid) != my_team:
                continue
            if ct.get_entity_type(uid) not in _ARMED_TURRETS:
                continue
            if ct.get_position(uid).distance_squared(core) <= DEFENSE_RING_SQ:
                n += 1
        except Exception:
            continue
    return n


def _turrets_covering_threats(ct: Controller, core: Position) -> int:
    """Friendly ring turrets that can *verify* a hit on at least one threat.

    A turret still standing is not automatically useful — a friendly unit can
    wander into a sentinel's fixed line and blind it permanently. Only count
    turrets whose ``can_fire_from`` still validates against a live threat.
    """
    threats = condition_state.get_core_threats(ct, core)
    if not threats:
        return 0
    can_fire_from = getattr(ct, "can_fire_from", None)
    if not callable(can_fire_from):
        return turrets_near_core(ct, core)

    my_team = ct.get_team()
    covered = 0
    for uid in condition_state.get_cached_buildings(ct):
        try:
            if ct.get_team(uid) != my_team:
                continue
            et = ct.get_entity_type(uid)
            if et not in _ARMED_TURRETS:
                continue
            tpos = ct.get_position(uid)
            if tpos.distance_squared(core) > DEFENSE_RING_SQ:
                continue
            facing = ct.get_direction(uid)
        except Exception:
            continue
        for threat in threats:
            try:
                if can_fire_from(tpos, facing, et, threat.pos):
                    covered += 1
                    break
            except Exception:
                continue
    return covered


def builders_needed_for_defense(ct: Controller, core: Position) -> int:
    """Extra builders to dispatch: unmet armed-threat coverage, capped."""
    want = target_turret_count(ct, core)
    have = _turrets_covering_threats(ct, core)
    return max(0, min(want - have, DEFEND_BUILD_MAX_FROM_TRIGGER))


# ---------------------------------------------------------------------------
# Verified placement
# ---------------------------------------------------------------------------


def best_verified_placement(
    ct: Controller,
    target: Position,
    turret_type: EntityType,
    *,
    exclude: set[tuple[int, int]] | None = None,
) -> tuple[Position, Direction] | None:
    """Buildable tile whose facing can actually hit ``target`` per the
    engine's own ``can_fire_from``.

    Candidates are the four tiles orthogonally adjacent to ``target``
    itself first — one tile from the threat, so nothing (a wall, a unit, a
    belt) can end up between turret and target — falling back to the
    builder's own orthogonal neighbours (buildable immediately even before
    fully closing the distance to the threat). A real match showed
    sentinels placed near wherever the defending builder happened to be
    standing rather than pinned on the threat, leaving gaps something
    could stand in between.

    ``exclude`` skips specific tiles even if currently empty and otherwise
    legal — used by the attacker to avoid rebuilding a sentinel in the
    exact spot the enemy just destroyed one, which a real match showed
    makes it a predictable, easy target to keep sniping.
    """
    can_fire_from = getattr(ct, "can_fire_from", None)
    if not callable(can_fire_from):
        return None
    pos = ct.get_position()
    seen: set[tuple[int, int]] = set()
    candidates: list[Position] = []
    for t in [target.add(d) for d in CARDINALS] + [pos.add(d) for d in CARDINALS]:
        key = xy(t)
        if key == xy(target) or key in seen or (exclude and key in exclude):
            continue
        seen.add(key)
        candidates.append(t)
    candidates.sort(
        key=lambda t: (t.distance_squared(target), t.distance_squared(pos), t.x, t.y)
    )

    is_sentinel = turret_type == EntityType.SENTINEL
    for tile in candidates:
        facing = facing_toward(tile, target)
        try:
            if is_sentinel:
                buildable = ct.can_build_sentinel(tile, facing)
            else:
                buildable = ct.can_build_gunner(tile, facing)
            if not buildable:
                continue
            if can_fire_from(tile, facing, turret_type, target):
                return (tile, facing)
        except Exception:
            continue
    return None


def _blocking_barrier(ct: Controller, threat: Position) -> Position | None:
    """Nearest visible enemy barrier to the threat (a lane blocker to clear)."""
    my_team = ct.get_team()
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for uid in condition_state.get_cached_buildings(ct):
        try:
            if ct.get_team(uid) == my_team:
                continue
            if ct.get_entity_type(uid) != EntityType.BARRIER:
                continue
            bpos = ct.get_position(uid)
        except Exception:
            continue
        key = (bpos.distance_squared(threat), bpos.x, bpos.y)
        if best_key is None or key < best_key:
            best_key = key
            best = bpos
    return best


def try_build_sentinel(ct: Controller, tile: Position, facing: Direction) -> bool:
    if not builder_can_act(ct):
        return False
    if ct.get_global_resources() < ct.get_sentinel_cost():
        return False
    if not ct.can_build_sentinel(tile, facing):
        return False
    ct.build_sentinel(tile, facing)
    return True


def try_build_gunner(ct: Controller, core: Position) -> bool:
    """Build one outward-facing gunner on an orthogonal neighbour tile.

    Legacy static-ring helper — kept for the old ``defend_step`` path; the
    reactive path uses ``best_verified_placement`` + ``try_build_sentinel``.
    """
    if not builder_can_act(ct):
        return False
    if read_gunner_count(ct) >= MAX_GUNNERS:
        return False
    cost = ct.get_gunner_cost()
    if ct.get_global_resources() < cost + TITANIUM_RESERVE:
        return False

    pos = ct.get_position()
    facing = facing_away_from(pos, core)
    if facing == Direction.CENTRE:
        facing = Direction.EAST

    tiles = sorted(
        (pos.add(d) for d in CARDINALS),
        key=lambda t: (-t.distance_squared(core), t.x, t.y),
    )
    for tile in tiles:
        if ct.can_build_gunner(tile, facing):
            ct.build_gunner(tile, facing)
            bump_gunner_count(ct)
            return True
    return False


# ---------------------------------------------------------------------------
# Melee helpers
# ---------------------------------------------------------------------------


def _try_melee(ct: Controller, tile: Position) -> bool:
    if not builder_can_act(ct):
        return False
    if ct.get_global_resources() < 2:
        return False
    try:
        if not ct.can_fire(tile):
            return False
    except Exception:
        return False
    ct.fire(tile)
    return True


# ---------------------------------------------------------------------------
# Dispatch decisions
# ---------------------------------------------------------------------------


def _walk_distance(ct: Controller, start: Position, goal: Position) -> int:
    """Known-terrain walking distance, falling back to Manhattan distance
    (same step-count units as BFS, unlike squared Euclidean) when BFS can't
    resolve — start==goal, goal in fog, or walled off in known terrain.

    Raw distance_squared is a poor proxy for "who can get there fastest" on
    a maze-like map: a builder numerically close but behind a wall band can
    take far longer to arrive than one slightly farther with a direct
    corridor. A real match picked exactly the wrong (walled-off) builder as
    sole defender this way while genuinely closer ones sat idle
    (2026-08-22, "paths" map — walled corridors throughout).
    """
    try:
        d = pathfind.bfs_path_length(ct, start, goal)
    except Exception:
        d = None
    if d is not None:
        return d
    return abs(start.x - goal.x) + abs(start.y - goal.y)


def _rank_defenders_by_threat_distance(
    ct: Controller, threats: list[condition_state.CoreThreat]
) -> list[tuple[int, int]]:
    """(distance-to-nearest-threat, builder_id) for self + every visible
    friendly builder, nearest first. Distance is real known-terrain walking
    distance where resolvable (see _walk_distance), not straight-line."""

    def to_threat(p: Position) -> int:
        return min(_walk_distance(ct, p, t.pos) for t in threats)

    my_id = ct.get_id()
    ranked: list[tuple[int, int]] = [(to_threat(ct.get_position()), my_id)]

    my_team = ct.get_team()
    for uid in condition_state.get_cached_units(ct):
        try:
            if uid == my_id:
                continue
            if ct.get_team(uid) != my_team:
                continue
            if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
                continue
            ranked.append((to_threat(ct.get_position(uid)), uid))
        except Exception:
            continue

    ranked.sort()
    return ranked


def should_enter_defend_build(ct: Controller, state: object, core: Position) -> bool:
    """This free builder is one of the nearest to a live core threat."""
    assignment = getattr(state, "assignment", None)
    if assignment is not None and getattr(assignment, "ore_pos", None) is not None:
        return False

    needed = builders_needed_for_defense(ct, core)
    if needed <= 0:
        return False

    threats = condition_state.get_core_threats(ct, core)
    if not threats:
        return False

    my_id = ct.get_id()
    for i, (_d, uid) in enumerate(_rank_defenders_by_threat_distance(ct, threats)):
        if uid == my_id:
            return i < needed
    return False


def core_in_immediate_danger(ct: Controller, core: Position) -> bool:
    """A real (non-barrier) threat is in range and *nothing* friendly
    currently covers it — the Core can be hit this turn with zero response.
    Severe enough to justify interrupting an actively-mining builder (see
    ``should_interrupt_for_core_defense`` — a merely-present threat with
    at least one turret already answering it is not this).

    Includes THREAT_BUILDER, not just gunner/sentinel — a pure builder-only
    rush destroys the Core exactly the same way (melee), and previously
    slipped past this check entirely: with every builder busy mining, the
    interrupt path (the only one that can pull a *busy* builder off task)
    never triggered for it, only the free-builder path could respond, and
    a rush timed to catch us with no free builder walked in unopposed
    (real match, 2026-08-23 — "opponent target my core only with his
    builders and I had no defense").
    """
    threats = condition_state.get_core_threats(ct, core)
    if not any(t.kind != condition_state.THREAT_BARRIER for t in threats):
        return False
    return _turrets_covering_threats(ct, core) == 0


def should_interrupt_for_core_defense(
    ct: Controller, state: object, core: Position
) -> bool:
    """This builder — even mid-mining-task — is one of the nearest to a Core
    left completely undefended against an armed threat.

    A real ladder match lost the Core in 60-105 rounds with zero counter
    response: every builder was busy mining (``should_enter_defend_build``
    only ever considers *free* builders), so a fast rush that plants an
    armed threat before any builder happens to be free went entirely
    unanswered. Losing the Core ends the match regardless of economy, so
    this is the one case a mining task may be interrupted — gated tightly
    (``core_in_immediate_danger``: armed threat + zero coverage) so the
    common case (a threat something is already answering) never yanks
    miners and the economy benchmark stays intact.
    """
    if not core_in_immediate_danger(ct, core):
        return False

    needed = builders_needed_for_defense(ct, core)
    if needed <= 0:
        return False

    threats = condition_state.get_core_threats(ct, core)
    my_id = ct.get_id()
    for i, (_d, uid) in enumerate(_rank_defenders_by_threat_distance(ct, threats)):
        if uid == my_id:
            return i < needed
    return False


def defend_build_step(
    ct: Controller, state: object, core: Position, *, avoid: Position | None
) -> bool:
    """Place a verified turret at the nearest core threat, or melee/advance.

    Returns True while still working the threat with a weapon (built/advancing
    toward one), False when it fell through to melee (adjacency-only response,
    used for weak enemy builders and as the last resort for armed threats).
    """
    threats = condition_state.get_core_threats(ct, core)
    if not threats:
        return True  # threat cleared — exit the phase

    pos = ct.get_position()
    threat = min(threats, key=lambda t: pos.distance_squared(t.pos))
    tpos, kind = threat.pos, threat.kind
    adjacent = orth_adjacent(pos, tpos)

    # Enemy builder rush → melee only, never a sentinel. A sentinel's
    # facing is fixed at build time and verified against wherever the
    # threat happens to be *that instant* — for a mobile target this is
    # already stale by the time the sentinel is actually standing, so it
    # ends up aimed at empty ground while the builder walks past it (real
    # match, 2026-08-23: two sentinels built near an approaching builder
    # rush, both verified at build time, neither ever fired — the Core was
    # destroyed anyway). Melee is adjacency-only, so it's never misaligned
    # the way a fixed-facing sentinel can be, and it can't be blinded by a
    # friendly unit/belt in the line either.
    if kind == condition_state.THREAT_BUILDER:
        if adjacent and _try_melee(ct, tpos):
            return False
        step_to(ct, state, tpos, avoid=avoid)
        return True

    in_range = adjacent or pos.distance_squared(tpos) <= DEFEND_THREAT_APPROACH_RANGE_SQ
    if in_range:
        placement = best_verified_placement(ct, tpos, EntityType.SENTINEL)
        if placement is None:
            barrier = _blocking_barrier(ct, tpos)
            if barrier is not None:
                placement = best_verified_placement(ct, barrier, EntityType.SENTINEL)
        if placement is not None:
            tile, facing = placement
            if try_build_sentinel(ct, tile, facing):
                return True
        # No verified/legal weapon this turn — melee if we can reach, else
        # advance so we eventually fall into melee range (no line-of-fire).
        if adjacent and _try_melee(ct, tpos):
            return False
        step_to(ct, state, tpos, avoid=avoid)
        return True

    step_to(ct, state, tpos, avoid=avoid)
    return True


# ---------------------------------------------------------------------------
# Economy defense
# ---------------------------------------------------------------------------


def try_destroy_adjacent_blocking_barrier(ct: Controller) -> bool:
    """Melee an orthogonally adjacent enemy barrier (belt-route blocker)."""
    my_team = ct.get_team()
    pos = ct.get_position()
    for d in CARDINALS:
        tile = pos.add(d)
        try:
            bid = ct.get_tile_building_id(tile)
            if bid is None:
                continue
            if ct.get_team(bid) == my_team:
                continue
            if ct.get_entity_type(bid) != EntityType.BARRIER:
                continue
        except Exception:
            continue
        if _try_melee(ct, tile):
            return True
    return False


def try_repair_adjacent(ct: Controller) -> bool:
    """Heal an orthogonally adjacent damaged friendly structure."""
    if not builder_can_act(ct):
        return False
    pos = ct.get_position()
    for d in CARDINALS:
        tile = pos.add(d)
        try:
            if ct.can_heal(tile):
                ct.heal(tile)
                return True
        except Exception:
            continue
    return False


def economy_defend_step(
    ct: Controller, state: object, *, avoid: Position | None
) -> bool:
    """Repair/clear a threatened belt instead of expanding. False if nothing
    is threatened (caller resumes normal economy)."""
    threatened = condition_state.get_threatened_structures(ct)
    if not threatened:
        return False

    if try_destroy_adjacent_blocking_barrier(ct):
        return True
    if try_repair_adjacent(ct):
        return True

    pos = ct.get_position()
    target = min(threatened, key=lambda s: pos.distance_squared(s))
    step_to(ct, state, target, avoid=avoid)
    return True


# ---------------------------------------------------------------------------
# Legacy static ring (kept for back-compat; no longer the primary path)
# ---------------------------------------------------------------------------


def count_visible_friendly_gunners(ct: Controller) -> int:
    my_team = ct.get_team()
    n = 0
    for uid in condition_state.get_cached_buildings(ct):
        if ct.get_entity_type(uid) == EntityType.GUNNER and ct.get_team(uid) == my_team:
            n += 1
    return n


def defend_step(ct: Controller, state: object, *, avoid: Position | None) -> None:
    """One defensive round: place a gunner near Core, else walk toward Core."""
    core = getattr(state, "core_pos", None)
    if core is None:
        return

    pos = ct.get_position()
    near_core = pos.distance_squared(core) <= DEFENSE_RING_SQ

    if near_core and try_build_gunner(ct, core):
        return

    step_to(ct, state, core, avoid=avoid)
