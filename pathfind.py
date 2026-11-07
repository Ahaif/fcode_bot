"""Global wall-aware routing for the dedicated attacker.

The shared ``navigator`` is a local Bug2 wall-follower — great for traffic and
short hops, but it can loop forever in a concave pocket formed by terrain walls
(observed: an attacker trapped ~650 rounds against a wall band while the enemy
Core sat a few tiles beyond it). This module adds a cheap breadth-first search
over the calling unit's *known* terrain so the attacker gets a waypoint that
actually routes around wall bands.

Fog safety: unknown tiles are treated as PASSABLE, so BFS plans optimistically
through unseen ground and simply re-plans next turn once new walls are learned.
Only tiles the unit has personally seen to be WALL block the search.

Buildings (ours or the enemy's) are intentionally *not* treated as obstacles
here — those are dynamic and the navigator's local traffic handling (and the
attacker's demolish/melee steps) deal with them. BFS only solves the static
terrain maze.
"""

from __future__ import annotations

from collections import deque

from fcode import Controller, Position

import map_knowledge

_WALL = 2  # map_knowledge._ENV_WALL

_CARD = ((0, -1), (1, 0), (0, 1), (-1, 0))


def _bfs_search(
    ct: Controller, start: Position, goal: Position
) -> tuple[int, list[int], int, int, int] | None:
    """Shared BFS core for ``bfs_waypoint``/``bfs_path_length``.

    Returns ``(w, parent, start_idx, target_idx, found)`` where ``target_idx``
    is the goal if reached, else the reachable tile nearest the goal (so a
    walled-off goal still yields a "head this way" answer instead of None) —
    ``found`` is 1/0. ``None`` if BFS can't run at all (bad map dims) or start
    already equals goal.
    """
    try:
        w = ct.get_map_width()
        h = ct.get_map_height()
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None

    gx = min(max(goal.x, 0), w - 1)
    gy = min(max(goal.y, 0), h - 1)
    sx = min(max(start.x, 0), w - 1)
    sy = min(max(start.y, 0), h - 1)
    if (sx, sy) == (gx, gy):
        return None

    start_idx = sy * w + sx
    goal_idx = gy * w + gx

    parent = [-1] * (w * h)
    seen = bytearray(w * h)
    seen[start_idx] = 1
    q: deque[int] = deque((start_idx,))
    found = False
    # Track the reachable tile closest to the goal, so that when the goal is
    # walled off in known terrain we still head toward its nearest approach
    # (probing for the entrance) instead of returning None and handing control
    # back to the navigator, which loops in the pocket.
    best_idx = start_idx
    best_d = (sx - gx) * (sx - gx) + (sy - gy) * (sy - gy)
    while q:
        cur = q.popleft()
        if cur == goal_idx:
            found = True
            break
        cx = cur % w
        cy = cur // w
        for dx, dy in _CARD:
            nx = cx + dx
            ny = cy + dy
            if nx < 0 or nx >= w or ny < 0 or ny >= h:
                continue
            nidx = ny * w + nx
            if seen[nidx]:
                continue
            # Block only tiles personally known to be walls; the goal tile is
            # always allowed (it's a building on empty terrain).
            if nidx != goal_idx and map_knowledge.known_env_at(nx, ny) == _WALL:
                continue
            seen[nidx] = 1
            parent[nidx] = cur
            q.append(nidx)
            d = (nx - gx) * (nx - gx) + (ny - gy) * (ny - gy)
            if d < best_d:
                best_d = d
                best_idx = nidx

    target_idx = goal_idx if found else best_idx
    return (w, parent, start_idx, target_idx, 1 if found else 0)


def bfs_waypoint(
    ct: Controller,
    start: Position,
    goal: Position,
    *,
    lookahead: int = 8,
) -> Position | None:
    """Return an intermediate waypoint ~``lookahead`` steps along the shortest
    known-terrain path from ``start`` to ``goal``, or ``None`` if BFS can't be
    run or the goal is unreachable through known walls (caller should then fall
    back to the greedy navigator).
    """
    result = _bfs_search(ct, start, goal)
    if result is None:
        return None
    w, parent, start_idx, target_idx, _found = result
    if target_idx == start_idx:
        return None

    # Walk the parent chain back from the (reached or nearest) target.
    path: list[int] = []
    cur = target_idx
    while cur != start_idx and cur != -1:
        path.append(cur)
        cur = parent[cur]
    if not path:
        return None
    path.reverse()

    step = path[min(lookahead, len(path)) - 1]
    return Position(step % w, step // w)


def bfs_path_length(ct: Controller, start: Position, goal: Position) -> int | None:
    """Known-terrain walking distance (step count) from ``start`` to
    ``goal``, or ``None`` if BFS can't run, ``start == goal``, or ``goal`` is
    walled off in known terrain (no partial-credit "nearest approach" value —
    unlike ``bfs_waypoint``, a distance comparison needs an exact number or
    nothing).

    Used to rank which free builder can actually *reach* a Core threat
    fastest — raw ``distance_squared`` picks whichever tile is closest as
    the crow flies, which is a poor proxy on a maze-like map: a builder
    numerically close but behind a wall band can take far longer to arrive
    than one slightly farther with a direct corridor (real match evidence,
    2026-08-22 — a far, walled-off builder was chosen as sole defender while
    genuinely closer ones sat idle).
    """
    if start.x == goal.x and start.y == goal.y:
        return 0
    result = _bfs_search(ct, start, goal)
    if result is None:
        return None
    _w, parent, start_idx, target_idx, found = result
    if not found:
        return None

    steps = 0
    cur = target_idx
    while cur != start_idx and cur != -1:
        steps += 1
        cur = parent[cur]
    if cur != start_idx:
        return None
    return steps


__all__ = ["bfs_waypoint", "bfs_path_length"]
