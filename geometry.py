"""Pure geometry + cheap Controller helpers used by every role.

Builder movement is cardinal-only. Build/attack/heal targets must be
orthogonally adjacent (never own tile, never diagonal) — see CONTROLLER_API.md.
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, Environment, Position

from constants import CARDINALS


def xy(pos: Position) -> tuple[int, int]:
    return (pos.x, pos.y)


def in_bounds(ct: Controller, pos: Position) -> bool:
    return 0 <= pos.x < ct.get_map_width() and 0 <= pos.y < ct.get_map_height()


def orth_adjacent(a: Position, b: Position) -> bool:
    return abs(a.x - b.x) + abs(a.y - b.y) == 1


def manhattan(a: Position, b: Position) -> int:
    return abs(a.x - b.x) + abs(a.y - b.y)


def nearest_core_tile(pos: Position, core: Position) -> Position:
    """Closest tile of the Core's 2×2 footprint to ``pos``."""
    best = Position(core.x, core.y)
    best_d = manhattan(pos, best)
    for dx in (0, 1):
        for dy in (0, 1):
            if dx == 0 and dy == 0:
                continue
            t = Position(core.x + dx, core.y + dy)
            d = manhattan(pos, t)
            if d < best_d:
                best = t
                best_d = d
    return best


def on_shortest_path(tile: Position, start: Position, goal: Position) -> bool:
    """True if ``tile`` lies on some Manhattan shortest path start→goal."""
    return manhattan(start, tile) + manhattan(tile, goal) == manhattan(start, goal)


def builder_can_act(ct: Controller) -> bool:
    """Authoritative action gate (dev26). Prefer ``can_act`` when present."""
    fn = getattr(ct, "can_act", None)
    if callable(fn):
        return bool(fn())
    return ct.get_action_cooldown() == 0


def _in_vision(ct: Controller, pos: Position) -> bool | None:
    """True/False if vision known; None if API unavailable (unit tests)."""
    fn = getattr(ct, "is_in_vision", None)
    if not callable(fn):
        return None
    try:
        return bool(fn(pos))
    except Exception:
        return False


def passable_env(ct: Controller, pos: Position) -> bool:
    """True if a builder could stand here (ignoring other bots).

    Matches engine ``is_tile_passable`` terrain rules: walls and non-walkable
    buildings (harvesters, gunners, barriers, …) block; conveyors, splitters,
    and the allied Core do not. Fog-safe: unseen tiles are treated as
    passable (fog ≠ WALL). Querying ``get_tile_env`` out of vision raises
    GameError and aborts the turn.
    """
    if not in_bounds(ct, pos):
        return False
    vis = _in_vision(ct, pos)
    if vis is False:
        return True
    try:
        if ct.get_tile_env(pos) == Environment.WALL:
            return False
        bid = ct.get_tile_building_id(pos)
        if bid is None:
            return True
        et = ct.get_entity_type(bid)
        if et in (EntityType.CONVEYOR, EntityType.SPLITTER):
            return True
        if et == EntityType.CORE and ct.get_team(bid) == ct.get_team():
            return True
        return False
    except Exception:
        return True


def facing_toward(origin: Position, target: Position) -> Direction:
    """Best cardinal facing from ``origin`` toward ``target``."""
    d = origin.cardinal_direction_to(target)
    if d == Direction.CENTRE:
        return Direction.WEST
    return d


def facing_away_from(origin: Position, away_from: Position) -> Direction:
    """Cardinal facing pointing away from ``away_from`` (for outward gunners)."""
    d = origin.cardinal_direction_to(away_from)
    if d == Direction.CENTRE:
        return Direction.EAST
    return d.opposite()


def on_or_orth_adjacent_to_core(pos: Position, core: Position) -> bool:
    """True if ``pos`` is on the Core 2×2 footprint or orthogonally adjacent."""
    for dx in (0, 1):
        for dy in (0, 1):
            cx, cy = core.x + dx, core.y + dy
            if pos.x == cx and pos.y == cy:
                return True
            if abs(pos.x - cx) + abs(pos.y - cy) == 1:
                return True
    return False


def tile_has_conveyor(ct: Controller, tile: Position) -> bool:
    """True if a *friendly* conveyor is visible on ``tile``. Fog → False.

    Enemy belts must not count — treating them as ours made planners reuse
    them and builders face into them, dumping titanium into the enemy Core.
    """
    vis = _in_vision(ct, tile)
    if vis is False:
        return False
    try:
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return False
        if ct.get_entity_type(bid) != EntityType.CONVEYOR:
            return False
        return ct.get_team(bid) == ct.get_team()
    except Exception:
        return False


def tile_has_enemy_logistics(ct: Controller, tile: Position) -> bool:
    """True if a visible enemy conveyor or splitter occupies ``tile``."""
    vis = _in_vision(ct, tile)
    if vis is False:
        return False
    try:
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return False
        if ct.get_team(bid) == ct.get_team():
            return False
        return ct.get_entity_type(bid) in (EntityType.CONVEYOR, EntityType.SPLITTER)
    except Exception:
        return False


def tile_feeds_enemy(ct: Controller, tile: Position, facing: Direction) -> bool:
    """True if a conveyor on ``tile`` facing ``facing`` would output into enemy logistics."""
    if facing == Direction.CENTRE or not facing.is_cardinal():
        return False
    out = tile.add(facing)
    if not in_bounds(ct, out):
        return False
    vis = _in_vision(ct, out)
    if vis is False:
        return False
    try:
        bid = ct.get_tile_building_id(out)
        if bid is None:
            return False
        if ct.get_team(bid) == ct.get_team():
            return False
        et = ct.get_entity_type(bid)
        return et in (
            EntityType.CONVEYOR,
            EntityType.SPLITTER,
            EntityType.CORE,
            EntityType.HARVESTER,
        )
    except Exception:
        return False


def try_move_toward(
    ct: Controller,
    target: Position,
    *,
    avoid: Position | None = None,
) -> bool:
    """One cardinal step toward ``target``. Never builds in the same call."""
    pos = ct.get_position()
    step = pos.cardinal_direction_to(target)
    if step == Direction.CENTRE:
        return False
    if ct.can_move(step):
        ct.move(step)
        return True

    ranked = sorted(CARDINALS, key=lambda d: pos.add(d).distance_squared(target))
    for d in ranked:
        if d == step:
            continue
        nxt = pos.add(d)
        if avoid is not None and xy(nxt) == xy(avoid):
            continue
        if ct.can_move(d):
            ct.move(d)
            return True

    if avoid is not None:
        for d in ranked:
            if d == step:
                continue
            if ct.can_move(d):
                ct.move(d)
                return True
    return False


def orthogonal_neighbors(ct: Controller, pos: Position) -> list[Position]:
    """In-bounds N/E/S/W tiles around ``pos``."""
    out: list[Position] = []
    for d in CARDINALS:
        tile = pos.add(d)
        if in_bounds(ct, tile):
            out.append(tile)
    return out
