"""pathfind.bfs_path_length / bfs_waypoint — wall-aware known-terrain BFS."""

from __future__ import annotations

from fcode import Position

import map_knowledge
import pathfind

_WALL = 2
_EMPTY = 1


class FakeCt:
    def __init__(self, width: int, height: int) -> None:
        self._w = width
        self._h = height

    def get_map_width(self) -> int:
        return self._w

    def get_map_height(self) -> int:
        return self._h


def _set_known_grid(w: int, h: int, walls: set[tuple[int, int]]) -> None:
    """Directly seed map_knowledge's known-terrain grid for a test, as if
    every non-wall tile had already been personally seen as EMPTY."""
    map_knowledge._width = w  # noqa: SLF001
    map_knowledge._height = h  # noqa: SLF001
    grid = bytearray(w * h)
    for y in range(h):
        for x in range(w):
            grid[y * w + x] = _WALL if (x, y) in walls else _EMPTY
    map_knowledge._known_env = grid  # noqa: SLF001


def setup_function() -> None:
    map_knowledge.reset_for_tests()


def test_bfs_path_length_matches_manhattan_on_open_grid() -> None:
    _set_known_grid(10, 10, walls=set())
    ct = FakeCt(10, 10)
    d = pathfind.bfs_path_length(ct, Position(0, 0), Position(3, 4))  # type: ignore[arg-type]
    assert d == 7  # Manhattan — no walls to route around


def test_bfs_path_length_start_equals_goal_is_zero() -> None:
    _set_known_grid(10, 10, walls=set())
    ct = FakeCt(10, 10)
    d = pathfind.bfs_path_length(ct, Position(2, 2), Position(2, 2))  # type: ignore[arg-type]
    assert d == 0


def test_bfs_path_length_none_when_goal_walled_off() -> None:
    # Goal at (5,5) fully enclosed by a wall ring — unreachable.
    walls = {
        (4, 4), (5, 4), (6, 4),
        (4, 5), (6, 5),
        (4, 6), (5, 6), (6, 6),
    }
    _set_known_grid(10, 10, walls=walls)
    ct = FakeCt(10, 10)
    d = pathfind.bfs_path_length(ct, Position(0, 0), Position(5, 5))  # type: ignore[arg-type]
    assert d is None


def test_bfs_path_length_accounts_for_a_detour_around_a_wall_band() -> None:
    """A straight-line-close start/goal pair separated by a solid wall band
    must report the real (longer) walking distance, not the Manhattan
    shortcut through the wall — this is exactly what makes it a better
    defender-ranking signal than raw distance_squared on a maze map."""
    # Solid wall band across the whole width at y=5, one gap at x=9.
    w, h = 10, 10
    walls = {(x, 5) for x in range(w) if x != 9}
    _set_known_grid(w, h, walls=walls)
    ct = FakeCt(w, h)
    straight = Position(0, 0).distance_squared(Position(0, 9))
    manhattan = abs(0 - 0) + abs(0 - 9)
    d = pathfind.bfs_path_length(ct, Position(0, 0), Position(0, 9))  # type: ignore[arg-type]
    assert d is not None
    assert d > manhattan  # forced far around to the single gap at x=9
    assert straight < d * d  # straight-line distance drastically understates it
