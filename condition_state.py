"""Condition-state dispatcher — dynamic SAFE / CORE_THREAT / ECONOMY_THREAT /
STARVATION signal, computed fresh every round per unit.

Read-only layer on top of MapKnowledge sightings (slots 1-2) + this unit's
own vision — no new full-map scans. Nothing in this module calls a mutating
``ct.*`` method or changes builder behavior; it only answers "what's the
situation right now."

Priority when multiple are true: STARVATION > CORE_THREAT > ECONOMY_THREAT >
SAFE (see ``get_condition_state``).
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from typing import NamedTuple

from constants import (
    CORE_THREAT_RANGE_SQ_BARRIER,
    CORE_THREAT_RANGE_SQ_BUILDER,
    CORE_THREAT_RANGE_SQ_GUNNER,
    CORE_THREAT_RANGE_SQ_SENTINEL,
    ECONOMY_THREAT_RADIUS_SQ,
    STARVATION_PER_UNIT_BUFFER,
    STARVATION_RESERVE,
    STARVATION_ROUNDS,
)
from geometry import nearest_core_tile
import map_knowledge
from team_store import read_own_core

SAFE = "safe"
CORE_THREAT = "core_threat"
ECONOMY_THREAT = "economy_threat"
STARVATION = "starvation"

# Threat kinds — armed threats (can actually deal damage) rank above passive
# ones (boxing-in walls) for both target-selection priority (defend_build_step
# / gunner rotation should shoot the thing that can hurt you first) and
# turret-count sizing (a wall doesn't need more coverage, an armed enemy does).
THREAT_GUNNER = "gunner"
THREAT_SENTINEL = "sentinel"
THREAT_BUILDER = "builder"
THREAT_BARRIER = "barrier"

THREAT_PRIORITY = {
    THREAT_SENTINEL: 0,
    THREAT_GUNNER: 0,
    THREAT_BUILDER: 1,
    THREAT_BARRIER: 2,
}


class CoreThreat(NamedTuple):
    pos: Position
    kind: str

# Mirrors map_knowledge.py's private sighting type codes (1=harvester,
# 2=conveyor, 3=gunner, 4=sentinel, 5=launcher, 6=other) — not imported
# directly since they're module-private there; keep numerically in sync.
_SIGHT_GUNNER = 3
_SIGHT_SENTINEL = 4

_SIGHT_RANGE_SQ = {
    _SIGHT_GUNNER: CORE_THREAT_RANGE_SQ_GUNNER,
    _SIGHT_SENTINEL: CORE_THREAT_RANGE_SQ_SENTINEL,
}

# --- Per-unit module state — same-unit cross-round only (engine isolates
# module namespaces per living unit; see map_knowledge.py / claim_manager.py
# for the same pattern). Team-wide consensus is therefore approximate: a
# freshly spawned builder starts its own streak at 0. Acceptable per Stage 1
# spec — this only delays a defense-spend gate by a few rounds, never opens
# an idle window.
_low_resource_streak = 0
_last_streak_round = -1

# get_nearby_buildings()/get_nearby_units() are cached round-scoped in
# map_knowledge.py (the lowest layer — its own update() already calls both
# unconditionally every round for every unit). Reuse that cache here instead
# of issuing separate calls: under real TLE (10ms/unit/round), the redundant
# calls this module used to make on top of map_knowledge's own were enough
# to cause timeouts that skipped a unit's turn at the worst possible moment
# — found via `fcode match test` with real TLE enforcement (local runs with
# --tle 0 never catch this; always validate perf-sensitive changes with
# `--tle 10` before trusting a `--tle 0` benchmark).
get_cached_buildings = map_knowledge.get_cached_buildings
get_cached_units = map_knowledge.get_cached_units


_SIGHT_KIND = {
    _SIGHT_GUNNER: THREAT_GUNNER,
    _SIGHT_SENTINEL: THREAT_SENTINEL,
}


def _can_actually_hit_core(
    ct: Controller, uid: int, pos: Position, et: EntityType, core_tile: Position
) -> bool:
    """Authoritative fallback beyond the CORE_THREAT_RANGE_SQ_* proximity
    guess: the engine publishes no numeric attack-range constant separate
    from vision (AGENTS.md: "turrets... have an attack range for firing,
    separate from vision" — no value given; GameConstants only exposes
    *_VISION_RADIUS_SQ). CORE_THREAT_RANGE_SQ_GUNNER/_SENTINEL above just
    reuse those vision numbers as the closest available proxy — a real match
    replay showed the Core destroyed by an enemy sentinel sitting outside
    that proxy radius, entirely invisible to our own threat detection. Use
    the engine's own can_fire_from() with the unit's real current facing to
    check directly whether it could hit the Core right now, independent of
    any distance guess. Only usable for a *currently visible* uid (need its
    live facing); stale comm-store sightings fall back to the proximity
    guess alone.
    """
    can_fire_from = getattr(ct, "can_fire_from", None)
    get_direction = getattr(ct, "get_direction", None)
    if not callable(can_fire_from) or not callable(get_direction):
        return False
    try:
        facing = get_direction(uid)
        return bool(can_fire_from(pos, facing, et, core_tile))
    except Exception:
        return False


def get_core_threats(ct: Controller, core: Position) -> list[CoreThreat]:
    """Enemy gunner/sentinel/builder/barrier within real range of Core.

    Armed threats (gunner/sentinel/builder) use their actual firing/melee
    range; barriers use a tighter proximity radius since they're passive
    (boxing-in, not damage). Deduped by tile, keeping the highest-priority
    (lowest-numbered) kind seen at that position.
    """
    hits: list[CoreThreat] = []

    for pos, type_code, _seen in map_knowledge.read_sightings(ct):
        kind = _SIGHT_KIND.get(type_code)
        if kind is None:
            continue
        range_sq = _SIGHT_RANGE_SQ[type_code]
        anchor = nearest_core_tile(pos, core)
        if anchor.distance_squared(pos) <= range_sq:
            hits.append(CoreThreat(pos, kind))

    my_team = ct.get_team()
    for uid in get_cached_buildings(ct):
        try:
            if ct.get_team(uid) == my_team:
                continue
            et = ct.get_entity_type(uid)
            if et == EntityType.GUNNER:
                range_sq, kind = CORE_THREAT_RANGE_SQ_GUNNER, THREAT_GUNNER
            elif et == EntityType.SENTINEL:
                range_sq, kind = CORE_THREAT_RANGE_SQ_SENTINEL, THREAT_SENTINEL
            elif et == EntityType.BARRIER:
                range_sq, kind = CORE_THREAT_RANGE_SQ_BARRIER, THREAT_BARRIER
            else:
                continue
            pos = ct.get_position(uid)
        except Exception:
            continue
        anchor = nearest_core_tile(pos, core)
        in_range = anchor.distance_squared(pos) <= range_sq
        if not in_range and kind in (THREAT_GUNNER, THREAT_SENTINEL):
            in_range = _can_actually_hit_core(ct, uid, pos, et, anchor)
        if in_range:
            hits.append(CoreThreat(pos, kind))

    my_id = ct.get_id()
    for uid in get_cached_units(ct):
        try:
            if uid == my_id:
                continue
            if ct.get_team(uid) == my_team:
                continue
            if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
                continue
            pos = ct.get_position(uid)
        except Exception:
            continue
        anchor = nearest_core_tile(pos, core)
        if anchor.distance_squared(pos) <= CORE_THREAT_RANGE_SQ_BUILDER:
            hits.append(CoreThreat(pos, THREAT_BUILDER))

    best: dict[tuple[int, int], CoreThreat] = {}
    for t in hits:
        key = (t.pos.x, t.pos.y)
        cur = best.get(key)
        if cur is None or THREAT_PRIORITY[t.kind] < THREAT_PRIORITY[cur.kind]:
            best[key] = t
    return list(best.values())


def get_threatened_structures(ct: Controller) -> list[Position]:
    """Friendly harvester/conveyor/splitter tiles with a visible enemy
    combat unit/turret *or barrier* within ECONOMY_THREAT_RADIUS_SQ right
    now. Public — defense.py uses this to pick a repair/clear target.

    Barriers included since a real match showed the enemy silently cutting
    off an already-completed belt route by boxing in a harvester/conveyor
    with barriers built well away from our own Core — nothing detected
    this at all before, since every other trigger here is either an armed
    unit or a turret. defense.economy_defend_step destroys an adjacent
    blocking barrier the same way it repairs adjacent damage.
    """
    friendlies = _friendly_structure_positions(ct)
    if not friendlies:
        return []

    my_team = ct.get_team()
    my_id = ct.get_id()
    enemy_positions: list[Position] = []
    for uid in get_cached_units(ct):
        try:
            if uid == my_id:
                continue
            if ct.get_team(uid) == my_team:
                continue
            # get_nearby_units() is not builder-bots-only (it also surfaces
            # e.g. the enemy Core) — filter explicitly, same as
            # get_core_threats()'s own units loop.
            if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
                continue
            enemy_positions.append(ct.get_position(uid))
        except Exception:
            continue
    for uid in get_cached_buildings(ct):
        try:
            if ct.get_team(uid) == my_team:
                continue
            et = ct.get_entity_type(uid)
            if et not in (
                EntityType.GUNNER,
                EntityType.SENTINEL,
                EntityType.LAUNCHER,
                EntityType.BARRIER,
            ):
                continue
            enemy_positions.append(ct.get_position(uid))
        except Exception:
            continue

    if not enemy_positions:
        return []
    return [
        f
        for f in friendlies
        if any(f.distance_squared(e) <= ECONOMY_THREAT_RADIUS_SQ for e in enemy_positions)
    ]


def _friendly_structure_positions(ct: Controller) -> list[Position]:
    """Friendly harvester/conveyor/splitter tiles visible to this unit right now."""
    my_team = ct.get_team()
    out: list[Position] = []
    for uid in get_cached_buildings(ct):
        try:
            if ct.get_team(uid) != my_team:
                continue
            et = ct.get_entity_type(uid)
            if et not in (
                EntityType.HARVESTER,
                EntityType.CONVEYOR,
                EntityType.SPLITTER,
            ):
                continue
            out.append(ct.get_position(uid))
        except Exception:
            continue
    return out


def _starving(ct: Controller) -> bool:
    """N consecutive rounds below a living-unit-count-scaled resource floor."""
    global _low_resource_streak, _last_streak_round
    rnd = ct.get_current_round()
    if rnd == _last_streak_round:
        return _low_resource_streak >= STARVATION_ROUNDS
    _last_streak_round = rnd

    try:
        unit_count = max(1, ct.get_unit_count())
    except Exception:
        unit_count = 1
    threshold = STARVATION_RESERVE + STARVATION_PER_UNIT_BUFFER * unit_count

    if ct.get_global_resources() < threshold:
        _low_resource_streak += 1
    else:
        _low_resource_streak = 0
    return _low_resource_streak >= STARVATION_ROUNDS


def get_condition_state(ct: Controller) -> str:
    """This unit's read of the team's current tactical situation, this round.

    Cheap: MapKnowledge sightings + this unit's own vision only, no new
    full-map scans. Priority when multiple are true: STARVATION >
    CORE_THREAT > ECONOMY_THREAT > SAFE.
    """
    if _starving(ct):
        return STARVATION

    core = read_own_core(ct)
    if get_core_threats(ct, core):
        return CORE_THREAT

    if get_threatened_structures(ct):
        return ECONOMY_THREAT

    return SAFE


def reset_for_tests() -> None:
    global _low_resource_streak, _last_streak_round
    _low_resource_streak = 0
    _last_streak_round = -1
