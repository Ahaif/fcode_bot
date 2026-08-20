"""Ore finding + harvester-first logistics with a persistent conveyor plan.

Hard rules:
  - NEVER build conveyors before the owning builder recorded ``harvester_pos``.
  - Only that owner may execute the build plan (enforced in builder_role).
  - Conveyors follow a stored plan from stand → Core (replanned only on blocks).
  - No conveyors during explore / approach / build_harvester.
  - Out-of-vision ore does not revoke ownership while belting home.
"""

from __future__ import annotations

from collections import deque

from fcode import Controller, EntityType, Environment, Position

import claim_manager
from constants import CARDINALS, MAX_BELT_REPLANS
from geometry import (
    builder_can_act,
    facing_toward,
    in_bounds,
    manhattan,
    nearest_core_tile,
    on_or_orth_adjacent_to_core,
    orth_adjacent,
    passable_env,
    tile_feeds_enemy,
    tile_has_conveyor,
    tile_has_enemy_logistics,
    xy,
)


def ore_owned_by_other(ct: Controller, ore: Position) -> bool:
    """True iff the claim table says a different living builder owns ``ore``."""
    return claim_manager.owned_by_other(ct, ore)


def ore_claimed_by_other(
    ct: Controller,
    ore: Position,
    state: object | None = None,
) -> bool:
    """True if Acquire would fail for ownership/contest reasons (read-only)."""
    my_id = ct.get_id()
    if claim_manager.owner_of(ore, ct) == my_id:
        return False
    if claim_manager.owned_by_other(ct, ore):
        return True
    if state is None:
        from assignment import empty_assignment

        state = type(
            "S",
            (),
            {
                "assignment": empty_assignment(),
                "claim_turns_alive": 1,
                "free_turns": 0,
            },
        )()
    return claim_manager._contest_winner(ct, state, ore) != my_id  # noqa: SLF001


def friendly_harvester_on(ct: Controller, ore: Position) -> bool:
    """True if our team has a harvester building on ``ore``."""
    vis_fn = getattr(ct, "is_in_vision", None)
    if callable(vis_fn):
        try:
            if not vis_fn(ore):
                return False
        except Exception:
            return False
    try:
        bid = ct.get_tile_building_id(ore)
        if bid is None:
            return False
        if ct.get_entity_type(bid) != EntityType.HARVESTER:
            return False
        return ct.get_team(bid) == ct.get_team()
    except Exception:
        return False


def scan_ore(
    ct: Controller,
    state: object | None = None,
    *,
    exclude: Position | None = None,
) -> Position | None:
    """Closest visible uncovered ore this unit would win under ClaimManager."""
    from assignment import empty_assignment

    if state is None:
        state = type(
            "S",
            (),
            {
                "assignment": empty_assignment(),
                "claim_turns_alive": 1,
                "free_turns": 0,
            },
        )()
    pos = ct.get_position()
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for tile in claim_manager.visible_uncovered_ores(ct):
        if exclude is not None and tile.x == exclude.x and tile.y == exclude.y:
            continue
        if claim_manager.why_not_acquirable(ct, state, tile, exclude=exclude):
            continue
        key = (pos.distance_squared(tile), tile.x, tile.y)
        if best_key is None or key < best_key:
            best_key = key
            best = tile
    return best


def pick_ore_stand(
    ct: Controller,
    ore: Position,
    core: Position | None,
) -> Position | None:
    """Orthogonal neighbour of ore — prefer the one closest to Core."""
    options: list[Position] = []
    for d in CARDINALS:
        n = ore.add(d)
        if passable_env(ct, n):
            options.append(n)
    if not options:
        return None
    if core is None:
        pos = ct.get_position()
        options.sort(key=lambda t: (pos.distance_squared(t), t.x, t.y))
    else:
        goal = nearest_core_tile(ore, core)
        options.sort(key=lambda t: (manhattan(t, goal), t.x, t.y))
    return options[0]


def try_build_harvester(ct: Controller, ore: Position) -> bool:
    if not builder_can_act(ct):
        return False
    if ct.get_global_resources() < ct.get_harvester_cost():
        return False
    if not orth_adjacent(ct.get_position(), ore):
        return False
    if ct.can_build_harvester(ore):
        ct.build_harvester(ore)
        return True
    return False


def is_core_tile(tile: Position, core: Position) -> bool:
    for dx in (0, 1):
        for dy in (0, 1):
            if tile.x == core.x + dx and tile.y == core.y + dy:
                return True
    return False


def _visible(ct: Controller, tile: Position) -> bool:
    try:
        return bool(ct.is_in_vision(tile))
    except Exception:
        return False


def _known_blocked(ct: Controller, tile: Position, core: Position) -> bool:
    """True if tile must not receive a conveyor (visible wall/ore/core/building).

    Enemy conveyors/splitters are *not* hard-blocked: the only choke home may
    run through them. The executor clears those tiles (melee) then paves.
    """
    if not in_bounds(ct, tile):
        return True
    if is_core_tile(tile, core):
        return True
    if not _visible(ct, tile):
        return False  # fog — assume freable until proven otherwise
    env = ct.get_tile_env(tile)
    if env in (Environment.WALL, Environment.ORE_TITANIUM):
        return True
    bid = ct.get_tile_building_id(tile)
    if bid is not None:
        try:
            et = ct.get_entity_type(bid)
            if et == EntityType.CONVEYOR and ct.get_team(bid) == ct.get_team():
                return False
            # Clearable enemy logistics — plan through, smash later.
            if et in (EntityType.CONVEYOR, EntityType.SPLITTER) and (
                ct.get_team(bid) != ct.get_team()
            ):
                return False
        except Exception:
            pass
        return True
    return False


def _axis_path(start: Position, goal: Position, *, horizontal_first: bool) -> list[Position]:
    """Inclusive Manhattan path start→goal, deterministic axis order."""
    tiles = [Position(start.x, start.y)]
    x, y = start.x, start.y
    gx, gy = goal.x, goal.y
    if horizontal_first:
        while x != gx:
            x += 1 if gx > x else -1
            tiles.append(Position(x, y))
        while y != gy:
            y += 1 if gy > y else -1
            tiles.append(Position(x, y))
    else:
        while y != gy:
            y += 1 if gy > y else -1
            tiles.append(Position(x, y))
        while x != gx:
            x += 1 if gx > x else -1
            tiles.append(Position(x, y))
    return tiles


def _strip_core_suffix(tiles: list[Position], core: Position) -> list[Position]:
    """Drop Core footprint tiles — conveyors stop on the last approach tile."""
    while tiles and is_core_tile(tiles[-1], core):
        tiles.pop()
    return tiles


def _path_clear(ct: Controller, tiles: list[Position], core: Position) -> bool:
    """True if every tile is visible and legal for a conveyor (no fog cheating)."""
    for t in tiles:
        if not _visible(ct, t):
            # Axis shortcuts must not pave through fog — fog may be WALL
            # (sweden closed pockets). BFS may still cross fog later.
            return False
        if tile_has_conveyor(ct, t) or tile_has_enemy_logistics(ct, t):
            continue
        if _known_blocked(ct, t, core):
            return False
    return True


def _core_approach_tiles(ct: Controller, core: Position, stand: Position) -> list[Position]:
    """Tiles orth-adjacent to the Core footprint, nearest to ``stand`` first."""
    seen: set[tuple[int, int]] = set()
    out: list[Position] = []
    for dx in (0, 1):
        for dy in (0, 1):
            c = Position(core.x + dx, core.y + dy)
            for d in CARDINALS:
                t = c.add(d)
                key = xy(t)
                if key in seen or not in_bounds(ct, t):
                    continue
                if is_core_tile(t, core):
                    continue
                seen.add(key)
                out.append(t)
    out.sort(key=lambda t: (manhattan(stand, t), t.x, t.y))
    return out


def _bfs_path(
    ct: Controller,
    start: Position,
    goal: Position,
    core: Position,
    *,
    allow_fog: bool = True,
) -> list[Position] | None:
    """Shortest grid path start→goal avoiding known blocked tiles. Planner only.

    When ``allow_fog`` is False, only expand onto visible tiles (plus start).
    Prefer that first so we never route a belt through an unseen wall into a
    closed pocket (sweden). Fog-tolerant BFS remains as a fallback for long
    belts across unexplored ground.
    """
    if xy(start) == xy(goal):
        return [Position(start.x, start.y)]
    limit = manhattan(start, goal) + max(ct.get_map_width(), ct.get_map_height())
    q: deque[Position] = deque([start])
    prev: dict[tuple[int, int], tuple[int, int] | None] = {xy(start): None}
    while q:
        cur = q.popleft()
        if manhattan(start, cur) > limit:
            continue
        for d in CARDINALS:
            nxt = cur.add(d)
            key = xy(nxt)
            if key in prev or not in_bounds(ct, nxt):
                continue
            if is_core_tile(nxt, core):
                continue
            if not allow_fog and not _visible(ct, nxt):
                continue
            if _known_blocked(ct, nxt, core):
                if not (_visible(ct, nxt) and tile_has_conveyor(ct, nxt)):
                    continue
            prev[key] = xy(cur)
            if key == xy(goal):
                out: list[Position] = [Position(goal.x, goal.y)]
                walk: tuple[int, int] | None = xy(cur)
                while walk is not None:
                    out.append(Position(walk[0], walk[1]))
                    walk = prev[walk]
                out.reverse()
                return out
            q.append(nxt)
    return None


def compute_conveyor_plan(
    ct: Controller,
    stand: Position,
    core: Position,
) -> list[Position] | None:
    """Full conveyor plan: stand … last tile adjacent to Core (no Core tiles)."""
    goal = nearest_core_tile(stand, core)
    for horizontal_first in (True, False):
        raw = _axis_path(stand, goal, horizontal_first=horizontal_first)
        raw = _strip_core_suffix(raw, core)
        if not raw:
            continue
        if _path_clear(ct, raw, core):
            if xy(raw[0]) != xy(stand):
                raw = [stand] + [t for t in raw if xy(t) != xy(stand)]
            return raw

    # Prefer BFS on known tiles only, then fog-tolerant fallback.
    for allow_fog in (False, True):
        for approach in _core_approach_tiles(ct, core, stand):
            if _known_blocked(ct, approach, core) and not (
                _visible(ct, approach) and tile_has_conveyor(ct, approach)
            ):
                continue
            raw = _bfs_path(ct, stand, approach, core, allow_fog=allow_fog)
            if raw is None:
                continue
            if xy(raw[0]) != xy(stand):
                raw = [stand] + raw
            return raw
    return None

def replan_conveyor_suffix(
    ct: Controller,
    state: object,
    core: Position,
    *,
    from_index: int,
    reason: str,
) -> bool:
    """Replace plan[from_index:] with a new path from the previous tile to Core.

    Already-built prefix is preserved. Returns False if impossible / over budget.
    """
    plan: list[Position] = list(getattr(state, "build_plan", None) or [])
    if not plan:
        return False
    replans = int(getattr(state, "replan_count", 0))
    if replans >= MAX_BELT_REPLANS:
        return False

    if from_index <= 0:
        start = plan[0]
        prefix: list[Position] = []
    else:
        start = plan[from_index - 1]
        prefix = plan[:from_index]

    suffix = compute_conveyor_plan(ct, start, core)
    if suffix is None:
        return False
    # suffix starts with ``start``; drop it when attaching to prefix that ends there.
    if prefix and xy(suffix[0]) == xy(prefix[-1]):
        suffix = suffix[1:]
    new_plan = prefix + suffix
    # Deduplicate consecutive tiles.
    cleaned: list[Position] = []
    for t in new_plan:
        if not cleaned or xy(cleaned[-1]) != xy(t):
            cleaned.append(t)
    state.build_plan = cleaned  # type: ignore[attr-defined]
    state.build_index = from_index  # type: ignore[attr-defined]
    state.replan_count = replans + 1  # type: ignore[attr-defined]
    state.last_replan_reason = reason  # type: ignore[attr-defined]
    return True


def plan_tile_status(
    ct: Controller, tile: Position, core: Position
) -> str:
    """Classify a planned tile for the executor."""
    if is_core_tile(tile, core):
        return "core"
    if not in_bounds(ct, tile):
        return "oob"
    if not _visible(ct, tile):
        return "fog"
    if tile_has_conveyor(ct, tile):
        return "conveyor"
    if tile_has_enemy_logistics(ct, tile):
        return "enemy_belt"
    env = ct.get_tile_env(tile)
    if env == Environment.WALL:
        return "wall"
    if env == Environment.ORE_TITANIUM:
        return "ore"
    bid = ct.get_tile_building_id(tile)
    if bid is not None:
        return "building"
    try:
        if ct.get_tile_builder_bot_id(tile) is not None:
            return "unit"
    except Exception:
        pass
    return "empty"


def try_build_plan_tile(
    ct: Controller,
    tile: Position,
    next_tile: Position | None,
    core: Position,
) -> str:
    """Attempt to place a conveyor on ``tile``. Returns status string."""
    if not builder_can_act(ct):
        return "cooldown"
    if ct.get_global_resources() < ct.get_conveyor_cost():
        return "insufficient_resources"
    if not orth_adjacent(ct.get_position(), tile):
        return "not_adjacent"
    if xy(ct.get_position()) == xy(tile):
        return "standing_on_tile"
    status = plan_tile_status(ct, tile, core)
    if status == "conveyor":
        return "already_conveyor"
    if status == "enemy_belt":
        return "blocked:enemy_belt"
    if status in ("wall", "ore", "building", "core", "oob"):
        return f"blocked:{status}"
    if status == "unit":
        return "occupied_by_unit"
    # Face along the plan toward the next tile, else toward Core.
    face_target = next_tile if next_tile is not None else nearest_core_tile(tile, core)
    if next_tile is not None and is_core_tile(next_tile, core):
        face_target = nearest_core_tile(tile, core)

    preferred = facing_toward(tile, face_target)
    # Never dump ore into enemy belts/core — try other cardinals toward target.
    candidates = [preferred]
    for d in CARDINALS:
        if d != preferred:
            candidates.append(d)
    # Prefer facings that still reduce Manhattan distance to the face target.
    candidates.sort(
        key=lambda d: (
            0 if d == preferred else 1,
            manhattan(tile.add(d), face_target),
            d.name,
        )
    )
    for facing in candidates:
        if tile_feeds_enemy(ct, tile, facing):
            continue
        if ct.can_build_conveyor(tile, facing):
            ct.build_conveyor(tile, facing)
            return "built"
    if any(tile_feeds_enemy(ct, tile, d) for d in CARDINALS):
        return "blocked:enemy_sink"
    return "can_build_false"


def plan_is_complete(
    ct: Controller,
    state: object,
    core: Position,
) -> bool:
    """True when every planned conveyor exists and the last tile touches Core."""
    plan: list[Position] = list(getattr(state, "build_plan", None) or [])
    if not plan:
        return False
    # All plan tiles must be conveyors (stand included).
    for t in plan:
        if is_core_tile(t, core):
            continue
        if not _visible(ct, t):
            # Trust prefix already built behind us; require visible remaining.
            idx = getattr(state, "build_index", 0)
            # If we've advanced past this tile, trust it.
            try:
                ti = next(i for i, p in enumerate(plan) if xy(p) == xy(t))
            except StopIteration:
                return False
            if ti >= idx:
                return False
            continue
        if not tile_has_conveyor(ct, t):
            return False
    last = plan[-1]
    if not on_or_orth_adjacent_to_core(last, core):
        return False
    return True


def clear_build_plan(state: object) -> None:
    state.build_plan = None  # type: ignore[attr-defined]
    state.build_index = 0  # type: ignore[attr-defined]
    state.replan_count = 0  # type: ignore[attr-defined]
    state.last_replan_reason = None  # type: ignore[attr-defined]
    state.stand_paved = False  # type: ignore[attr-defined]
