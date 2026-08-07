"""Deterministic sector-waypoint exploration."""

from __future__ import annotations

import math
from types import SimpleNamespace

from fcode import Direction, Environment, Position

from explore import (
    EXPLORE_STUCK_LIMIT,
    WAYPOINT_STEP,
    init_sector,
    sector_angle,
    step_explore,
    waypoint_on_ray,
)


def _state(**kwargs: object) -> SimpleNamespace:
    base = dict(
        core_pos=Position(20, 20),
        visited=set(),
        sector_id=0,
        num_sectors=7,
        builder_index=0,
        current_waypoint_index=0,
        current_waypoint=None,
        explore_target=None,
        exploration_complete=False,
        sector_sweep=0,
        sectors_finished=0,
        sector_init=True,
        waypoints_advanced=0,
        stuck=0,
        blocked_dirs=set(),
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


class _FakeCt:
    def __init__(
        self,
        pos: Position,
        *,
        w: int = 60,
        h: int = 60,
        can_move_dirs: set[Direction] | None = None,
        uid: int = 0,
    ) -> None:
        self._pos = pos
        self._w = w
        self._h = h
        self._can = can_move_dirs if can_move_dirs is not None else set(Direction)
        self._uid = uid
        self.moves: list[Direction] = []

    def get_position(self) -> Position:
        return self._pos

    def get_nearby_tiles(self) -> list[Position]:
        return []

    def get_map_width(self) -> int:
        return self._w

    def get_map_height(self) -> int:
        return self._h

    def get_tile_env(self, _p: Position) -> Environment:
        return Environment.EMPTY

    def get_id(self) -> int:
        return self._uid

    def get_move_cooldown(self) -> int:
        return 0

    def can_move(self, d: Direction) -> bool:
        return d in self._can and d != Direction.CENTRE

    def move(self, d: Direction) -> None:
        self.moves.append(d)
        self._pos = self._pos.add(d)

    def is_in_vision(self, _p: Position) -> bool:
        return False


def test_builder0_sector_is_north() -> None:
    assert abs(sector_angle(0, 7) - (-math.pi / 2)) < 1e-9


def test_waypoint_sticky_while_en_route() -> None:
    core = Position(20, 20)
    wp = waypoint_on_ray(core, 0, 7, WAYPOINT_STEP, 60, 60)
    state = _state(
        current_waypoint=wp,
        current_waypoint_index=0,
        _sector_waypoints=[wp, waypoint_on_ray(core, 0, 7, 2 * WAYPOINT_STEP, 60, 60)],
    )
    pos = Position(20, 22)
    ct = _FakeCt(pos, can_move_dirs={Direction.NORTH})
    step_explore(ct, state, avoid=Position(20, 23))
    assert (state.current_waypoint.x, state.current_waypoint.y) == (wp.x, wp.y)
    ct2 = _FakeCt(ct.get_position(), can_move_dirs={Direction.NORTH})
    step_explore(ct2, state, avoid=pos)
    assert (state.current_waypoint.x, state.current_waypoint.y) == (wp.x, wp.y)
    assert state.current_waypoint_index == 0


def test_reach_advances_farther_same_sector() -> None:
    core = Position(20, 20)
    wp0 = waypoint_on_ray(core, 0, 7, WAYPOINT_STEP, 60, 60)
    wp1 = waypoint_on_ray(core, 0, 7, 2 * WAYPOINT_STEP, 60, 60)
    assert (wp0.x, wp0.y) != (wp1.x, wp1.y)
    state = _state(
        current_waypoint=wp0,
        current_waypoint_index=0,
        _sector_waypoints=[wp0, wp1, waypoint_on_ray(core, 0, 7, 3 * WAYPOINT_STEP, 60, 60)],
    )
    ct = _FakeCt(wp0, can_move_dirs={Direction.NORTH})
    step_explore(ct, state, avoid=None)
    assert state.current_waypoint_index == 1
    assert (state.current_waypoint.x, state.current_waypoint.y) == (wp1.x, wp1.y)


def test_stuck_skips_to_next_waypoint_same_sector() -> None:
    core = Position(20, 20)
    wp0 = waypoint_on_ray(core, 0, 7, WAYPOINT_STEP, 60, 60)
    wp1 = waypoint_on_ray(core, 0, 7, 2 * WAYPOINT_STEP, 60, 60)
    state = _state(
        current_waypoint=wp0,
        current_waypoint_index=0,
        stuck=EXPLORE_STUCK_LIMIT,
        _sector_waypoints=[wp0, wp1, waypoint_on_ray(core, 0, 7, 3 * WAYPOINT_STEP, 60, 60)],
    )
    ct = _FakeCt(Position(20, 20), can_move_dirs={Direction.NORTH})
    step_explore(ct, state, avoid=None)
    assert state.current_waypoint_index == 1
    assert (state.current_waypoint.x, state.current_waypoint.y) == (wp1.x, wp1.y)
    assert state.stuck == 0


def test_visited_never_cleared() -> None:
    core = Position(20, 20)
    wp0 = waypoint_on_ray(core, 0, 7, WAYPOINT_STEP, 60, 60)
    wp1 = waypoint_on_ray(core, 0, 7, 2 * WAYPOINT_STEP, 60, 60)
    visited = {(20, 20), (20, 19), (20, 18)}
    state = _state(
        current_waypoint=wp0,
        current_waypoint_index=0,
        stuck=EXPLORE_STUCK_LIMIT,
        visited=set(visited),
        _sector_waypoints=[wp0, wp1],
    )
    ct = _FakeCt(Position(20, 20), can_move_dirs={Direction.NORTH})
    step_explore(ct, state, avoid=None)
    assert visited.issubset(state.visited)


def test_failed_move_keeps_same_waypoint() -> None:
    core = Position(20, 20)
    wp = waypoint_on_ray(core, 0, 7, WAYPOINT_STEP, 60, 60)
    state = _state(
        current_waypoint=wp,
        current_waypoint_index=0,
        stuck=0,
        _sector_waypoints=[wp, waypoint_on_ray(core, 0, 7, 2 * WAYPOINT_STEP, 60, 60)],
    )
    ct = _FakeCt(Position(20, 22), can_move_dirs=set())
    step_explore(ct, state, avoid=None)
    assert state.current_waypoint_index == 0
    assert (state.current_waypoint.x, state.current_waypoint.y) == (wp.x, wp.y)


def test_edge_marks_exploration_complete() -> None:
    edge = Position(2, 0)
    mid = Position(2, 1)
    state = _state(
        core_pos=Position(2, 2),
        current_waypoint_index=1,
        current_waypoint=edge,
        exploration_complete=False,
        sector_sweep=2,
        sectors_finished=6,
        num_sectors=7,
        _sector_waypoints=[mid, edge],
    )
    ct = _FakeCt(edge, w=8, h=8)
    step_explore(ct, state, avoid=None)
    assert state.exploration_complete


def test_reach_starts_next_sector_sweep() -> None:
    edge = Position(20, 0)
    mid = Position(20, 10)
    state = _state(
        current_waypoint_index=1,
        current_waypoint=edge,
        sector_sweep=0,
        _sector_waypoints=[mid, edge],
    )
    ct = _FakeCt(edge, w=60, h=60)
    step_explore(ct, state, avoid=None)
    assert state.exploration_complete is False
    assert state.sector_sweep == 1
    assert state.current_waypoint_index == 0


def test_init_sector_assigns_permanent_id() -> None:
    state = SimpleNamespace(sector_init=False, visited=set())
    ct = _FakeCt(Position(0, 0), uid=3)
    init_sector(ct, state)
    assert state.sector_id == 3 % 7
    assert state.sector_init is True
    state.sector_id = 99
    init_sector(ct, state)
    assert state.sector_id == 99
