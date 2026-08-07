"""Offense: smash enemy harvesters and push the enemy Core.

Builders deal 2 damage for 2 Ti on an orthogonally adjacent building
(``can_fire`` / ``fire``). Prefer enemy harvesters, then any enemy building,
then walk toward a known / guessed enemy Core.
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from constants import (
    CARDINALS,
    SLOT_ENEMY_CORE_KNOWN,
    SLOT_ENEMY_CORE_X,
    SLOT_ENEMY_CORE_Y,
)
from geometry import builder_can_act, in_bounds
from navigator import step_to


def note_enemy_core(ct: Controller) -> None:
    """If the enemy Core is in vision, publish it for the rest of the team."""
    my_team = ct.get_team()
    for uid in ct.get_nearby_buildings():
        if ct.get_entity_type(uid) != EntityType.CORE:
            continue
        if ct.get_team(uid) == my_team:
            continue
        pos = ct.get_position(uid)
        ct.write_store(SLOT_ENEMY_CORE_X, pos.x)
        ct.write_store(SLOT_ENEMY_CORE_Y, pos.y)
        ct.write_store(SLOT_ENEMY_CORE_KNOWN, 1)
        return


def enemy_core_target(ct: Controller, own_core: Position | None) -> Position | None:
    """Known enemy Core from the store, else rotational symmetry guess."""
    if ct.read_store(SLOT_ENEMY_CORE_KNOWN) == 1:
        return Position(ct.read_store(SLOT_ENEMY_CORE_X), ct.read_store(SLOT_ENEMY_CORE_Y))
    # Most league maps are rotationally symmetric: (x,y) → (W-1-x, H-1-y).
    if own_core is not None:
        w, h = ct.get_map_width(), ct.get_map_height()
        return Position(w - 1 - own_core.x, h - 1 - own_core.y)
    return None


def _enemy_building_on(ct: Controller, tile: Position) -> EntityType | None:
    if not in_bounds(ct, tile):
        return None
    try:
        if not ct.is_in_vision(tile):
            return None
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return None
        if ct.get_team(bid) == ct.get_team():
            return None
        return ct.get_entity_type(bid)
    except Exception:
        return None


def try_attack_adjacent(ct: Controller) -> bool:
    """Attack an orthogonally adjacent enemy building. Prefer harvesters."""
    if not builder_can_act(ct):
        return False
    # Builder melee costs 2 Ti — skip if broke.
    if ct.get_global_resources() < 2:
        return False

    pos = ct.get_position()
    harvester_tiles: list[Position] = []
    other_tiles: list[Position] = []
    for d in CARDINALS:
        tile = pos.add(d)
        et = _enemy_building_on(ct, tile)
        if et is None:
            continue
        if et == EntityType.HARVESTER:
            harvester_tiles.append(tile)
        elif et != EntityType.CORE:
            other_tiles.append(tile)
        else:
            other_tiles.append(tile)

    for tile in harvester_tiles + other_tiles:
        if ct.can_fire(tile):
            ct.fire(tile)
            return True
    return False


def nearest_enemy_harvester(ct: Controller) -> Position | None:
    """Closest visible enemy harvester tile, or None."""
    pos = ct.get_position()
    my_team = ct.get_team()
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for uid in ct.get_nearby_buildings():
        if ct.get_entity_type(uid) != EntityType.HARVESTER:
            continue
        if ct.get_team(uid) == my_team:
            continue
        tile = ct.get_position(uid)
        key = (pos.distance_squared(tile), tile.x, tile.y)
        if best_key is None or key < best_key:
            best_key = key
            best = tile
    return best


def attack_step(
    ct: Controller,
    state: object,
    *,
    avoid: Position | None,
) -> None:
    """One offensive round: melee if adjacent, else march toward a target."""
    note_enemy_core(ct)

    if try_attack_adjacent(ct):
        return

    target = nearest_enemy_harvester(ct)
    if target is None:
        target = enemy_core_target(ct, getattr(state, "core_pos", None))
    if target is None:
        # Last resort: map centre (often contested mid).
        target = Position(ct.get_map_width() // 2, ct.get_map_height() // 2)

    state.attack_target = target  # type: ignore[attr-defined]
    step_to(ct, state, target, avoid=avoid)
