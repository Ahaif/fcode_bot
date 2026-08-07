"""Shared navigation layer — HOW to move, never WHERE / WHY.

State machine:
  NORMAL      — try primary toward aim; traffic waits briefly then side-steps;
                persistent unit blocks (>3 turns) escalate like walls
  WALL_FOLLOW — Bug2 left/right-hand around an obstacle hit point

Leave WALL_FOLLOW when Manhattan < hit_distance OR a direct step succeeds.

Fog safety: never treat unseen tiles as WALL.

No A*, no BFS.

Public API:
    step_to(ct, state, target, *, avoid=None) -> bool
    ensure_nav_state(state) -> None
    reset_nav(state) -> None
    shift_passable(ct, tile, toward, *, state=None) -> Position
"""

from __future__ import annotations

import os

from fcode import Controller, Direction, Position

from constants import CARDINALS
from geometry import in_bounds, manhattan, passable_env, xy

NAV_NORMAL = "normal"
NAV_WALL_FOLLOW = "wall_follow"

_RECENT_LIMIT = 10
_BLOCK_PERSIST = 3
_FOLLOW_STEPS_MAX = 25
_FAILURE_MAX = 10
_LOCAL_WAYPOINT_STEP = 4
_NAV_DEBUG = os.environ.get("NAV_DEBUG", "") == "1"

_LEFT: dict[Direction, Direction] = {
    Direction.NORTH: Direction.WEST,
    Direction.WEST: Direction.SOUTH,
    Direction.SOUTH: Direction.EAST,
    Direction.EAST: Direction.NORTH,
}
_RIGHT: dict[Direction, Direction] = {
    Direction.NORTH: Direction.EAST,
    Direction.EAST: Direction.SOUTH,
    Direction.SOUTH: Direction.WEST,
    Direction.WEST: Direction.NORTH,
}
_BACK: dict[Direction, Direction] = {
    Direction.NORTH: Direction.SOUTH,
    Direction.SOUTH: Direction.NORTH,
    Direction.EAST: Direction.WEST,
    Direction.WEST: Direction.EAST,
}


def _nav_log(state: object, event: str, **fields: object) -> None:
    parts = " ".join(f"{k}={v}" for k, v in fields.items())
    line = f"{event} {parts}".rstrip()
    log: list[str] = getattr(state, "nav_debug_log", None) or []
    log.append(line)
    del log[:-64]
    state.nav_debug_log = log  # type: ignore[attr-defined]
    if _NAV_DEBUG:
        print(f"[NAV] {line}", flush=True)


def ensure_nav_state(state: object) -> None:
    defaults: dict[str, object] = {
        "nav_mode": NAV_NORMAL,
        "nav_hit_distance": 10**9,
        "nav_hit_position": None,
        "nav_follow_steps": 0,
        "nav_last_distance": 10**9,
        "nav_wall_side": "left",
        "nav_side_reversed": False,
        "nav_blocked": Direction.CENTRE,
        "nav_block_dir": Direction.CENTRE,
        "nav_block_counter": 0,
        "nav_failure_counter": 0,
        "nav_recent_positions": None,
        "nav_escape_counter": 0,
        "nav_target": None,
        "nav_local_waypoint": None,
        "nav_follow_dir": Direction.NORTH,
        "nav_debug_log": None,
    }
    for key, value in defaults.items():
        if getattr(state, key, None) is None:
            if key in ("nav_recent_positions", "nav_debug_log"):
                setattr(state, key, [])
            else:
                setattr(state, key, value)


def reset_nav(state: object) -> None:
    """Full navigator reset (mode NORMAL, counters cleared)."""
    ensure_nav_state(state)
    state.nav_mode = NAV_NORMAL  # type: ignore[attr-defined]
    state.nav_hit_distance = 10**9  # type: ignore[attr-defined]
    state.nav_hit_position = None  # type: ignore[attr-defined]
    state.nav_follow_steps = 0  # type: ignore[attr-defined]
    state.nav_escape_counter = 0  # type: ignore[attr-defined]
    state.nav_side_reversed = False  # type: ignore[attr-defined]
    state.nav_block_counter = 0  # type: ignore[attr-defined]
    state.nav_block_dir = Direction.CENTRE  # type: ignore[attr-defined]
    state.nav_failure_counter = 0  # type: ignore[attr-defined]
    state.nav_local_waypoint = None  # type: ignore[attr-defined]
    state.nav_recent_positions = []  # type: ignore[attr-defined]


def _flip_side(state: object) -> None:
    side = getattr(state, "nav_wall_side", "left")
    new_side = "right" if side == "left" else "left"
    state.nav_wall_side = new_side  # type: ignore[attr-defined]
    _nav_log(state, "SWITCH_SIDE", side=new_side)


def _escape_reset(state: object, *, reason: str) -> None:
    """Reset wall-follow, clear recent memory, flip follow side."""
    state.nav_mode = NAV_NORMAL  # type: ignore[attr-defined]
    state.nav_hit_distance = 10**9  # type: ignore[attr-defined]
    state.nav_hit_position = None  # type: ignore[attr-defined]
    state.nav_follow_steps = 0  # type: ignore[attr-defined]
    state.nav_escape_counter = 0  # type: ignore[attr-defined]
    state.nav_block_counter = 0  # type: ignore[attr-defined]
    state.nav_block_dir = Direction.CENTRE  # type: ignore[attr-defined]
    state.nav_failure_counter = 0  # type: ignore[attr-defined]
    state.nav_recent_positions = []  # type: ignore[attr-defined]
    _flip_side(state)
    _nav_log(state, "ABORT_WALL_FOLLOW", reason=reason)


def _visible(ct: Controller, tile: Position) -> bool:
    """True if tile is in vision. Missing API (unit tests) → assume visible."""
    fn = getattr(ct, "is_in_vision", None)
    if not callable(fn):
        return True
    try:
        return bool(fn(tile))
    except Exception:
        return False


def _known_impassable(ct: Controller, tile: Position) -> bool:
    if not in_bounds(ct, tile):
        return True
    if not _visible(ct, tile):
        return False
    return not passable_env(ct, tile)


def _terrain_blocks(ct: Controller, pos: Position, d: Direction) -> bool:
    if d == Direction.CENTRE:
        return False
    nxt = pos.add(d)
    # Fog-safe: unseen neighbours are not walls.
    return _known_impassable(ct, nxt)


# ---------------------------------------------------------------------------
# Passable waypoint shifting (vision-safe)
# ---------------------------------------------------------------------------

def shift_passable(
    ct: Controller,
    tile: Position,
    toward: Position,
    *,
    state: object | None = None,
) -> Position:
    """Shift ``tile`` toward ``toward`` until a known-passable tile is found.

    Unseen tiles are never treated as walls (fog ≠ WALL).
    """
    if not in_bounds(ct, tile):
        cur = tile
        for _ in range(max(ct.get_map_width(), ct.get_map_height()) + 2):
            if in_bounds(ct, cur):
                tile = cur
                break
            step = cur.cardinal_direction_to(toward)
            if step == Direction.CENTRE:
                return toward
            cur = cur.add(step)
        else:
            return toward

    if not _known_impassable(ct, tile):
        return tile

    cur = tile
    for _ in range(max(ct.get_map_width(), ct.get_map_height()) + 2):
        if not in_bounds(ct, cur):
            step = cur.cardinal_direction_to(toward)
            if step == Direction.CENTRE:
                break
            cur = cur.add(step)
            continue
        if not _visible(ct, cur):
            return cur
        if passable_env(ct, cur):
            if state is not None and xy(cur) != xy(tile):
                _nav_log(
                    state, "WAYPOINT_SHIFTED", from_xy=xy(tile), to_xy=xy(cur)
                )
            return cur
        step = cur.cardinal_direction_to(toward)
        if step == Direction.CENTRE:
            break
        cur = cur.add(step)

    if in_bounds(ct, toward):
        if state is not None:
            _nav_log(
                state, "WAYPOINT_SHIFTED", from_xy=xy(tile), to_xy=xy(toward)
            )
        return toward
    return tile


# ---------------------------------------------------------------------------
# Core-return intermediate waypoint
# ---------------------------------------------------------------------------

def _is_core_goal(state: object, goal: Position) -> bool:
    core = getattr(state, "core_pos", None)
    if core is None:
        return False
    for dx in (0, 1):
        for dy in (0, 1):
            if goal.x == core.x + dx and goal.y == core.y + dy:
                return True
    return False


def _step_n(origin: Position, goal: Position, n: int) -> Position:
    cur = origin
    for _ in range(n):
        d = cur.cardinal_direction_to(goal)
        if d == Direction.CENTRE:
            break
        cur = cur.add(d)
    return cur


def _refresh_local_waypoint(
    ct: Controller, state: object, pos: Position, ultimate: Position
) -> Position:
    local: Position | None = getattr(state, "nav_local_waypoint", None)
    if manhattan(pos, ultimate) <= _LOCAL_WAYPOINT_STEP:
        state.nav_local_waypoint = ultimate  # type: ignore[attr-defined]
        return ultimate

    need_new = local is None or xy(pos) == xy(local)
    if not need_new and local is not None:
        if manhattan(local, ultimate) > manhattan(pos, ultimate):
            need_new = True
        elif _known_impassable(ct, local):
            need_new = True
    if not need_new and local is not None:
        return local

    raw = _step_n(pos, ultimate, _LOCAL_WAYPOINT_STEP)
    local = shift_passable(ct, raw, ultimate, state=state)
    if xy(local) == xy(pos):
        local = shift_passable(
            ct,
            _step_n(pos, ultimate, _LOCAL_WAYPOINT_STEP + 2),
            ultimate,
            state=state,
        )
    state.nav_local_waypoint = local  # type: ignore[attr-defined]
    return local


# ---------------------------------------------------------------------------
# Move memory — predicted destination after ct.move()
# ---------------------------------------------------------------------------

def _remember(state: object, predicted: Position) -> None:
    recent: list[tuple[int, int]] = list(getattr(state, "nav_recent_positions", []))
    key = xy(predicted)
    if not recent or recent[-1] != key:
        recent.append(key)
        del recent[:-_RECENT_LIMIT]
        state.nav_recent_positions = recent  # type: ignore[attr-defined]


def _in_recent(state: object, tile: Position) -> bool:
    return xy(tile) in getattr(state, "nav_recent_positions", [])


def _note_success(state: object) -> None:
    state.nav_failure_counter = 0  # type: ignore[attr-defined]
    state.nav_block_counter = 0  # type: ignore[attr-defined]
    state.nav_block_dir = Direction.CENTRE  # type: ignore[attr-defined]


def _note_failure(state: object) -> bool:
    """Increment failure counter. True if exceeded limit (caller should bail)."""
    n = getattr(state, "nav_failure_counter", 0) + 1
    state.nav_failure_counter = n  # type: ignore[attr-defined]
    return n > _FAILURE_MAX


def _bump_block(state: object, d: Direction) -> int:
    if getattr(state, "nav_block_dir", Direction.CENTRE) == d:
        state.nav_block_counter = getattr(state, "nav_block_counter", 0) + 1  # type: ignore[attr-defined]
    else:
        state.nav_block_dir = d  # type: ignore[attr-defined]
        state.nav_block_counter = 1  # type: ignore[attr-defined]
    return int(getattr(state, "nav_block_counter", 0))


def _issue_move(ct: Controller, state: object, d: Direction) -> bool:
    """Issue ``ct.move(d)`` and remember the *predicted* next tile."""
    pos = ct.get_position()
    nxt = pos.add(d)
    ct.move(d)
    _remember(state, nxt)
    _note_success(state)
    return True


# ---------------------------------------------------------------------------
# WALL_FOLLOW
# ---------------------------------------------------------------------------

def _enter_wall_follow(
    state: object, pos: Position, target: Position, blocked: Direction
) -> None:
    hit = manhattan(pos, target)
    state.nav_mode = NAV_WALL_FOLLOW  # type: ignore[attr-defined]
    state.nav_hit_distance = hit  # type: ignore[attr-defined]
    state.nav_hit_position = xy(pos)  # type: ignore[attr-defined]
    state.nav_last_distance = hit  # type: ignore[attr-defined]
    state.nav_follow_steps = 0  # type: ignore[attr-defined]
    state.nav_blocked = blocked  # type: ignore[attr-defined]
    state.nav_follow_dir = (  # type: ignore[attr-defined]
        _LEFT[blocked] if blocked in _LEFT else Direction.NORTH
    )
    state.nav_escape_counter = 0  # type: ignore[attr-defined]
    state.nav_block_counter = 0  # type: ignore[attr-defined]
    _nav_log(
        state,
        "ENTER_WALL_FOLLOW",
        hit=hit,
        hit_pos=xy(pos),
        blocked=str(blocked),
        side=getattr(state, "nav_wall_side", "left"),
        target=xy(target),
    )


def _leave_wall_follow(state: object, *, reason: str) -> None:
    state.nav_mode = NAV_NORMAL  # type: ignore[attr-defined]
    state.nav_follow_steps = 0  # type: ignore[attr-defined]
    state.nav_escape_counter = 0  # type: ignore[attr-defined]
    state.nav_hit_distance = 10**9  # type: ignore[attr-defined]
    state.nav_hit_position = None  # type: ignore[attr-defined]
    _nav_log(state, "LEAVE_WALL_FOLLOW", reason=reason)


def _wall_dirs(state: object) -> list[Direction]:
    heading: Direction = getattr(state, "nav_follow_dir", Direction.NORTH)
    if heading not in _LEFT:
        heading = Direction.NORTH
    if getattr(state, "nav_wall_side", "left") == "right":
        return [_RIGHT[heading], heading, _LEFT[heading], _BACK[heading]]
    return [_LEFT[heading], heading, _RIGHT[heading], _BACK[heading]]


def _try_follow_dir(
    ct: Controller,
    state: object,
    d: Direction,
    *,
    avoid: Position | None,
    allow_recent: bool,
) -> bool:
    pos = ct.get_position()
    if d == Direction.CENTRE or _terrain_blocks(ct, pos, d) or not ct.can_move(d):
        return False
    nxt = pos.add(d)
    if avoid is not None and xy(nxt) == xy(avoid):
        return False
    if not allow_recent and _in_recent(state, nxt):
        return False
    return _issue_move(ct, state, d)


def _step_wall_follow(
    ct: Controller, state: object, target: Position, *, avoid: Position | None
) -> bool:
    pos = ct.get_position()
    dist = manhattan(pos, target)
    hit = getattr(state, "nav_hit_distance", 10**9)
    primary = pos.cardinal_direction_to(target)

    # Leave when closer than the hit distance (Bug2 progress condition).
    if dist < hit:
        _leave_wall_follow(state, reason="closer_than_hit")
        return _navigate_normal(ct, state, target, avoid=avoid)

    # Prevent infinite wall hugging (before accepting a direct step).
    if getattr(state, "nav_follow_steps", 0) > _FOLLOW_STEPS_MAX:
        _escape_reset(state, reason="follow_steps_exceeded")
        return _navigate_normal(ct, state, target, avoid=avoid)

    # Leave when a direct step toward the target succeeds.
    if (
        primary != Direction.CENTRE
        and not _terrain_blocks(ct, pos, primary)
        and ct.can_move(primary)
    ):
        nxt = pos.add(primary)
        if avoid is None or xy(nxt) != xy(avoid):
            _leave_wall_follow(state, reason="direct_succeeds")
            return _issue_move(ct, state, primary)

    # Prefer non-recent tiles strongly; only relax if fully stuck.
    for allow_recent in (False, True):
        for d in _wall_dirs(state):
            if _try_follow_dir(ct, state, d, avoid=avoid, allow_recent=allow_recent):
                state.nav_follow_dir = d  # type: ignore[attr-defined]
                state.nav_follow_steps = (  # type: ignore[attr-defined]
                    getattr(state, "nav_follow_steps", 0) + 1
                )
                state.nav_escape_counter = (  # type: ignore[attr-defined]
                    getattr(state, "nav_escape_counter", 0) + 1
                )
                return True

    if _note_failure(state):
        _escape_reset(state, reason="failure_exceeded")
        return False
    return False


# ---------------------------------------------------------------------------
# NORMAL
# ---------------------------------------------------------------------------

def _try_side_steps(
    ct: Controller,
    state: object,
    aim: Position,
    *,
    avoid: Position | None,
    skip: Direction | None,
    force: bool,
) -> bool:
    """Legal side-steps ranked by distance to aim. Prefer non-recent tiles."""
    pos = ct.get_position()
    ranked = sorted(CARDINALS, key=lambda d: pos.add(d).distance_squared(aim))
    for allow_recent in (False, True):
        if not force and allow_recent:
            continue
        for d in ranked:
            if skip is not None and d == skip:
                continue
            if _terrain_blocks(ct, pos, d):
                continue
            nxt = pos.add(d)
            if avoid is not None and xy(nxt) == xy(avoid):
                continue
            if not allow_recent and _in_recent(state, nxt):
                continue
            if ct.can_move(d):
                return _issue_move(ct, state, d)
    # Avoid fallback when force-sidestepping under traffic.
    if force and avoid is not None:
        for d in ranked:
            if skip is not None and d == skip:
                continue
            if _terrain_blocks(ct, pos, d):
                continue
            if ct.can_move(d):
                return _issue_move(ct, state, d)
    return False


def _navigate_normal(
    ct: Controller,
    state: object,
    aim: Position,
    *,
    avoid: Position | None,
) -> bool:
    """One NORMAL step. Enters wall-follow on terrain / persistent blocks."""
    pos = ct.get_position()
    primary = pos.cardinal_direction_to(aim)
    if primary == Direction.CENTRE:
        return False

    # Adjacent: prefer direct; side-step only after persistent traffic.
    if manhattan(pos, aim) == 1:
        if _terrain_blocks(ct, pos, primary):
            if _note_failure(state):
                _escape_reset(state, reason="failure_exceeded")
            return False
        if ct.can_move(primary):
            nxt = pos.add(primary)
            if avoid is not None and xy(nxt) == xy(avoid):
                return False
            return _issue_move(ct, state, primary)
        # Traffic on the stand.
        blocks = _bump_block(state, primary)
        if blocks > _BLOCK_PERSIST:
            # Side-step out of the jam rather than wait forever.
            if _try_side_steps(
                ct, state, aim, avoid=avoid, skip=primary, force=True
            ):
                return True
            _enter_wall_follow(state, pos, aim, primary)
            return _step_wall_follow(ct, state, aim, avoid=avoid)
        if blocks >= _BLOCK_PERSIST:
            if _try_side_steps(
                ct, state, aim, avoid=avoid, skip=primary, force=True
            ):
                return True
        if _note_failure(state):
            _escape_reset(state, reason="failure_exceeded")
            return False
        return False

    # Primary into terrain → Bug2 immediately (do not oscillate via side-steps).
    if _terrain_blocks(ct, pos, primary):
        _enter_wall_follow(state, pos, aim, primary)
        return _step_wall_follow(ct, state, aim, avoid=avoid)

    # Primary open.
    if ct.can_move(primary):
        nxt = pos.add(primary)
        if avoid is None or xy(nxt) != xy(avoid):
            return _issue_move(ct, state, primary)
        # Avoid tile occupies primary — side-step.
        if _try_side_steps(ct, state, aim, avoid=avoid, skip=primary, force=True):
            return True
        return False

    # Primary blocked by a unit — temporary traffic.
    blocks = _bump_block(state, primary)

    # Always try a legal side-step so a single failed move never freezes us.
    if _try_side_steps(
        ct,
        state,
        aim,
        avoid=avoid,
        skip=primary,
        force=blocks >= _BLOCK_PERSIST,
    ):
        return True

    # Persistent unit block → treat exactly like a wall.
    if blocks > _BLOCK_PERSIST:
        _enter_wall_follow(state, pos, aim, primary)
        return _step_wall_follow(ct, state, aim, avoid=avoid)

    if _note_failure(state):
        _escape_reset(state, reason="failure_exceeded")
        return False
    return False


# ---------------------------------------------------------------------------
# Public entry
# ---------------------------------------------------------------------------

def step_to(
    ct: Controller,
    state: object,
    target: Position,
    *,
    avoid: Position | None = None,
) -> bool:
    """One cardinal step toward ``target``. True if a move happened."""
    ensure_nav_state(state)

    if ct.get_move_cooldown() != 0:
        return False

    pos = ct.get_position()
    core = getattr(state, "core_pos", None)
    toward = core if core is not None else pos

    goal = target
    if not in_bounds(ct, goal) or _known_impassable(ct, goal):
        goal = shift_passable(ct, goal, toward, state=state)

    if xy(pos) == xy(goal):
        state.nav_mode = NAV_NORMAL  # type: ignore[attr-defined]
        state.nav_local_waypoint = None  # type: ignore[attr-defined]
        _note_success(state)
        return False

    prev = getattr(state, "nav_target", None)
    if prev is None or xy(prev) != xy(goal):
        state.nav_target = goal  # type: ignore[attr-defined]
        state.nav_mode = NAV_NORMAL  # type: ignore[attr-defined]
        state.nav_follow_steps = 0  # type: ignore[attr-defined]
        state.nav_escape_counter = 0  # type: ignore[attr-defined]
        state.nav_block_counter = 0  # type: ignore[attr-defined]
        state.nav_failure_counter = 0  # type: ignore[attr-defined]
        state.nav_local_waypoint = None  # type: ignore[attr-defined]
        _nav_log(state, "TARGET_CHANGED", to=xy(goal))

    if _is_core_goal(state, goal) and manhattan(pos, goal) > 1:
        aim = _refresh_local_waypoint(ct, state, pos, goal)
    else:
        aim = goal
        state.nav_local_waypoint = None  # type: ignore[attr-defined]

    if getattr(state, "nav_mode", NAV_NORMAL) == NAV_WALL_FOLLOW:
        moved = _step_wall_follow(ct, state, aim, avoid=avoid)
    else:
        moved = _navigate_normal(ct, state, aim, avoid=avoid)

    if not moved and getattr(state, "nav_failure_counter", 0) > _FAILURE_MAX:
        # Ensure role-level stuck logic can react.
        _escape_reset(state, reason="failure_exceeded")
        return False
    return moved


__all__ = [
    "NAV_NORMAL",
    "NAV_WALL_FOLLOW",
    "ensure_nav_state",
    "reset_nav",
    "shift_passable",
    "step_to",
]
