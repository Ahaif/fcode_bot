"""Defense: place outward-facing gunners in a ring around our Core.

Gunners need global ammo — the Core converts Ti→ammo via ``convert_ammo``.
Builders only place the turrets; ``roles/gunner_role.py`` handles firing.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Position

from constants import (
    CARDINALS,
    DEFENSE_RING_SQ,
    MAX_GUNNERS,
    SLOT_GUNNER_COUNT,
    TITANIUM_RESERVE,
)
from geometry import builder_can_act, facing_away_from
from navigator import step_to


def count_visible_friendly_gunners(ct: Controller) -> int:
    my_team = ct.get_team()
    n = 0
    for uid in ct.get_nearby_buildings():
        if ct.get_entity_type(uid) == EntityType.GUNNER and ct.get_team(uid) == my_team:
            n += 1
    return n


def try_build_gunner(ct: Controller, core: Position) -> bool:
    """Build one outward-facing gunner on an orthogonal neighbour tile."""
    if not builder_can_act(ct):
        return False
    if ct.read_store(SLOT_GUNNER_COUNT) >= MAX_GUNNERS:
        return False
    cost = ct.get_gunner_cost()
    if ct.get_global_resources() < cost + TITANIUM_RESERVE:
        return False

    pos = ct.get_position()
    # Face away from Core so the barrel points at incoming threats.
    facing = facing_away_from(pos, core)
    if facing == Direction.CENTRE:
        facing = Direction.EAST

    # Prefer tiles farther from Core (outer ring).
    tiles = sorted(
        (pos.add(d) for d in CARDINALS),
        key=lambda t: (-t.distance_squared(core), t.x, t.y),
    )
    for tile in tiles:
        if ct.can_build_gunner(tile, facing):
            ct.build_gunner(tile, facing)
            ct.write_store(SLOT_GUNNER_COUNT, ct.read_store(SLOT_GUNNER_COUNT) + 1)
            return True
    return False


def defend_step(
    ct: Controller,
    state: object,
    *,
    avoid: Position | None,
) -> None:
    """One defensive round: place a gunner near Core, else walk toward Core."""
    core = getattr(state, "core_pos", None)
    if core is None:
        return

    pos = ct.get_position()
    near_core = pos.distance_squared(core) <= DEFENSE_RING_SQ

    if near_core and try_build_gunner(ct, core):
        return

    # Not in the ring yet (or no legal gunner tile) — move toward Core.
    step_to(ct, state, core, avoid=avoid)
