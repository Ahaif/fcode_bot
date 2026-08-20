"""Deterministic sector-ray exploration (Phase 1).

Each builder owns one angular sector forever and walks permanent outward
waypoint sequences until that sector is fully swept to the map edge.

Primary ray = sector centre (Builder0 North, then clockwise).
After the centre ray hits the edge, ±1/3-width rays finish the owned
sector. Only then may the builder rotate to the next sector.

Rules (summary):
  - Global objective = ``current_waypoint`` on the active sector ray.
  - On reach / stuck-after-avoidance → next farther waypoint, same ray.
  - ``visited`` is permanent (anti-oscillation only).
  - Economy may interrupt; waypoint index is never reset by economy.
"""

from __future__ import annotations

import math

from fcode import Controller, Position

from constants import MAX_BUILDERS, STUCK_LIMIT
from geometry import passable_env, xy
from navigator import shift_passable, step_to

# Outward spacing along each sector ray (Rule 3: 6, 12, 18, … to the edge).
WAYPOINT_STEP = 6
# Unit traffic near Core is common — require sustained immobility before skip.
EXPLORE_STUCK_LIMIT = max(STUCK_LIMIT * 4, 12)
# Centre + ±1/3 width — stay inside the owned sector before rotating onward.
SECTOR_SWEEPS = 3
# Oscillation breaker: moving A↔B resets ``stuck``, so detect hot-zone loops.
EXPLORE_TRAIL_LEN = 24
EXPLORE_LOOP_MIN_LEN = 16
EXPLORE_LOOP_UNIQUE_MAX = 8
# No new tiles for this many explore turns → force ray/sector rotate.
EXPLORE_STAGNANT_LIMIT = 40


def _num_sectors() -> int:
    return max(4, MAX_BUILDERS)


def sector_angle(sector_id: int, num_sectors: int, sweep: int = 0) -> float:
    """Angle for ``sector_id`` / ``sweep``.

    Builder0 centre = North (-π/2), then clockwise around the Core.
    sweep 0 = centre, 1 = −width/3, 2 = +width/3 (still inside the sector).
    """
    n = max(1, num_sectors)
    idx = sector_id % n
    width = (2 * math.pi) / n
    center = -math.pi / 2 + width * idx
    # Stay strictly inside the sector cone (never cross into a neighbour).
    offsets = (0.0, -width / 3.0, width / 3.0)
    return center + offsets[sweep % len(offsets)]


def waypoint_on_ray(
    core: Position,
    sector_id: int,
    num_sectors: int,
    radius: int,
    map_w: int,
    map_h: int,
    *,
    sweep: int = 0,
) -> Position:
    """Clamp the ray sample at ``radius`` to the map bounds."""
    angle = sector_angle(sector_id, num_sectors, sweep)
    tx = int(round(core.x + radius * math.cos(angle)))
    ty = int(round(core.y + radius * math.sin(angle)))
    tx = max(0, min(map_w - 1, tx))
    ty = max(0, min(map_h - 1, ty))
    return Position(tx, ty)


def _on_core_footprint(core: Position, tile: Position) -> bool:
    return (
        core.x <= tile.x <= core.x + 1 and core.y <= tile.y <= core.y + 1
    )


def sector_waypoints(
    ct: Controller,
    core: Position,
    builder_index: int,
    num_sectors: int,
    *,
    sweep: int = 0,
    state: object | None = None,
) -> list[Position]:
    """Distinct outward waypoints along one ray until the map edge.

    Waypoints are map-clamped only here. Passable shifting happens later when
    the tile is in vision (see ``shift_passable`` / ``_bind_waypoint``).

    Rays that point off a corner map (e.g. NW-core sector pointing SW) clamp
    every sample onto the core — those return ``[]`` so the explorer rotates
    instead of soft-locking on an unreachable edge.
    """
    del state  # reserved for future; shifting needs vision
    w, h = ct.get_map_width(), ct.get_map_height()
    max_r = max(w, h) + WAYPOINT_STEP
    out: list[Position] = []
    r = WAYPOINT_STEP
    while r <= max_r:
        p = waypoint_on_ray(
            core, builder_index, num_sectors, r, w, h, sweep=sweep
        )
        # Off-map angles collapse to the near corner / core footprint.
        if _on_core_footprint(core, p) or p.distance_squared(core) < (r * r) // 4:
            r += WAYPOINT_STEP
            continue
        if out and xy(out[-1]) == xy(p):
            break
        if out and p.distance_squared(core) <= out[-1].distance_squared(core):
            break
        out.append(p)
        r += WAYPOINT_STEP
    return out


def init_sector(ct: Controller, state: object) -> None:
    """Assign a permanent sector once. Safe to call every turn."""
    if getattr(state, "sector_init", False):
        return
    n = _num_sectors()
    state.sector_id = ct.get_id() % n  # type: ignore[attr-defined]
    state.num_sectors = n  # type: ignore[attr-defined]
    state.builder_index = state.sector_id  # type: ignore[attr-defined]
    state.current_waypoint_index = 0  # type: ignore[attr-defined]
    state.current_waypoint = None  # type: ignore[attr-defined]
    state.sector_sweep = 0  # type: ignore[attr-defined]
    state.sectors_finished = 0  # type: ignore[attr-defined]
    state.exploration_complete = False  # type: ignore[attr-defined]
    state._sector_waypoints = None  # type: ignore[attr-defined]
    state.sector_init = True  # type: ignore[attr-defined]


def _waypoints(ct: Controller, state: object) -> list[Position]:
    """Cached distinct ray waypoints for the active sector sweep."""
    cached = getattr(state, "_sector_waypoints", None)
    if cached is not None:
        return cached  # type: ignore[return-value]
    core = getattr(state, "core_pos", None)
    if core is None:
        return []
    sector_id = getattr(state, "sector_id", 0)
    num_sectors = getattr(state, "num_sectors", _num_sectors())
    sweep = getattr(state, "sector_sweep", 0)
    wps = sector_waypoints(ct, core, sector_id, num_sectors, sweep=sweep, state=state)
    state._sector_waypoints = wps  # type: ignore[attr-defined]
    return wps


def _rotate_explore_ray(ct: Controller, state: object) -> bool:
    """Next within-sector sweep, or next sector. Clears waypoint cache only."""
    del ct  # symmetry with other helpers; rotation is state-only
    sweep = getattr(state, "sector_sweep", 0) + 1
    if sweep < SECTOR_SWEEPS:
        state.sector_sweep = sweep  # type: ignore[attr-defined]
        state.current_waypoint_index = 0  # type: ignore[attr-defined]
        state._sector_waypoints = None  # type: ignore[attr-defined]
        state.stuck = 0  # type: ignore[attr-defined]
        return True

    finished = getattr(state, "sectors_finished", 0) + 1
    state.sectors_finished = finished  # type: ignore[attr-defined]
    n = getattr(state, "num_sectors", _num_sectors())
    if finished >= n:
        return False
    state.sector_id = (getattr(state, "sector_id", 0) + 1) % n  # type: ignore[attr-defined]
    state.builder_index = state.sector_id  # type: ignore[attr-defined]
    state.sector_sweep = 0  # type: ignore[attr-defined]
    state.current_waypoint_index = 0  # type: ignore[attr-defined]
    state._sector_waypoints = None  # type: ignore[attr-defined]
    state.stuck = 0  # type: ignore[attr-defined]
    return True


def _bind_waypoint(ct: Controller, state: object) -> Position | None:
    """Set ``current_waypoint`` from index; skip empty/collapsed rays."""
    n = getattr(state, "num_sectors", _num_sectors())
    for _ in range(n * SECTOR_SWEEPS + 1):
        wps = _waypoints(ct, state)
        if wps:
            index = getattr(state, "current_waypoint_index", 0)
            wp = wps[-1] if index >= len(wps) else wps[index]
            core = getattr(state, "core_pos", None)
            if core is not None:
                wp = shift_passable(ct, wp, core, state=state)
            state.current_waypoint = wp  # type: ignore[attr-defined]
            state.explore_target = wp  # type: ignore[attr-defined]
            return wp
        if not _rotate_explore_ray(ct, state):
            state.exploration_complete = True  # type: ignore[attr-defined]
            return None
    state.exploration_complete = True  # type: ignore[attr-defined]
    return None


def _start_next_sweep(ct: Controller, state: object) -> bool:
    """Next within-sector ray, or the next sector after this one is exhausted.

    Rule 1: stay in the owned sector until it is fully swept. After that, the
    builder may take another sector (all of its own work is done).
    """
    if not _rotate_explore_ray(ct, state):
        return False
    return _bind_waypoint(ct, state) is not None


def advance_waypoint(ct: Controller, state: object) -> Position | None:
    """Next farther waypoint on the active ray (same sector)."""
    if getattr(state, "exploration_complete", False):
        return getattr(state, "current_waypoint", None)

    wps = _waypoints(ct, state)
    if not wps:
        return _bind_waypoint(ct, state)

    index = getattr(state, "current_waypoint_index", 0) + 1
    state.current_waypoint_index = index  # type: ignore[attr-defined]
    state.waypoints_advanced = (
        getattr(state, "sector_sweep", 0) * 64 + index
    )  # type: ignore[attr-defined]
    state.stuck = 0  # type: ignore[attr-defined]

    if index < len(wps):
        return _bind_waypoint(ct, state)

    # Ray exhausted (reached or skipped). Never soft-lock waiting on a
    # wall-blocked edge tile — sweden's west column (0,11)/(0,14) is the
    # classic case from a NW core.
    if _start_next_sweep(ct, state):
        return getattr(state, "current_waypoint", None)

    state.exploration_complete = True  # type: ignore[attr-defined]
    return wps[-1]


def _on_reached(ct: Controller, state: object, waypoint: Position) -> Position | None:
    """Handle standing on the current waypoint."""
    wps = _waypoints(ct, state)
    if not wps:
        return _bind_waypoint(ct, state)
    index = getattr(state, "current_waypoint_index", 0)
    # Reached last tile of this ray → sweep or complete.
    if index >= len(wps) - 1 and xy(waypoint) == xy(wps[-1]):
        if _start_next_sweep(ct, state):
            return getattr(state, "current_waypoint", None)
        state.exploration_complete = True  # type: ignore[attr-defined]
        return waypoint
    return advance_waypoint(ct, state)


def _note_explore_trail(state: object, pos: Position) -> None:
    trail: list[tuple[int, int]] = list(getattr(state, "explore_trail", None) or [])
    trail.append(xy(pos))
    del trail[:-EXPLORE_TRAIL_LEN]
    state.explore_trail = trail  # type: ignore[attr-defined]


def _explore_hot_loop(state: object) -> bool:
    """True when recent explore positions collapse to a tiny tile set."""
    trail: list[tuple[int, int]] = list(getattr(state, "explore_trail", None) or [])
    if len(trail) < EXPLORE_LOOP_MIN_LEN:
        return False
    return len(set(trail)) <= EXPLORE_LOOP_UNIQUE_MAX


def _note_explore_progress(state: object, visited_size: int) -> None:
    if visited_size > getattr(state, "_explore_visited_mark", -1):
        state._explore_visited_mark = visited_size  # type: ignore[attr-defined]
        state._explore_stagnant = 0  # type: ignore[attr-defined]
    else:
        state._explore_stagnant = getattr(state, "_explore_stagnant", 0) + 1  # type: ignore[attr-defined]


def _break_explore_loop(ct: Controller, state: object) -> Position | None:
    """Force progress when builders orbit the same corridor forever.

    ``stuck`` only counts immobility; pairs that keep stepping A↔B never trip
    EXPLORE_STUCK_LIMIT (sweden after ~r200). Skip waypoints whose shifted
    targets still lie in the hot zone; escalate to the next sweep/sector.
    """
    hot = set(getattr(state, "explore_trail", None) or [])
    state.explore_trail = []  # type: ignore[attr-defined]
    state.stuck = 0  # type: ignore[attr-defined]
    state._explore_stagnant = 0  # type: ignore[attr-defined]

    for _ in range(8):
        wp = advance_waypoint(ct, state)
        if getattr(state, "exploration_complete", False):
            return wp
        if wp is None:
            break
        bound = getattr(state, "current_waypoint", wp)
        if bound is not None and xy(bound) not in hot:
            return bound
    if _start_next_sweep(ct, state):
        return getattr(state, "current_waypoint", None)
    state.exploration_complete = True  # type: ignore[attr-defined]
    return getattr(state, "current_waypoint", None)


def step_explore(
    ct: Controller,
    state: object,
    *,
    avoid: Position | None,
) -> None:
    """Pick the sector waypoint; Navigator owns the actual step."""
    init_sector(ct, state)
    if getattr(state, "exploration_complete", False):
        return

    if getattr(state, "core_pos", None) is None:
        return
    if getattr(state, "_sector_waypoints", None) is None:
        _waypoints(ct, state)

    pos = ct.get_position()
    visited: set[tuple[int, int]] = state.visited  # type: ignore[attr-defined]
    visited.add(xy(pos))
    _note_explore_trail(state, pos)
    _note_explore_progress(state, len(visited))

    waypoint = getattr(state, "current_waypoint", None)
    if waypoint is None:
        waypoint = _bind_waypoint(ct, state)
    if waypoint is None:
        return

    # Orbiting a wall pocket / collapsed off-map ray — stuck never fires.
    if (
        _explore_hot_loop(state)
        or getattr(state, "_explore_stagnant", 0) >= EXPLORE_STAGNANT_LIMIT
    ):
        waypoint = _break_explore_loop(ct, state)
        if getattr(state, "exploration_complete", False) or waypoint is None:
            return

    # Rule 6: reached → next farther waypoint (or next sweep / complete).
    if xy(pos) == xy(waypoint):
        waypoint = _on_reached(ct, state, waypoint)
        if getattr(state, "exploration_complete", False) or waypoint is None:
            return

    # Rule 4: sustained block → skip to next waypoint in the SAME sector.
    if getattr(state, "stuck", 0) >= EXPLORE_STUCK_LIMIT:
        waypoint = advance_waypoint(ct, state)
        if getattr(state, "exploration_complete", False) or waypoint is None:
            return

    if ct.is_in_vision(waypoint) and not passable_env(ct, waypoint):
        waypoint = advance_waypoint(ct, state)
        if getattr(state, "exploration_complete", False) or waypoint is None:
            return

    # Explorer chooses WHERE; Navigator chooses HOW.
    step_to(ct, state, waypoint, avoid=avoid)