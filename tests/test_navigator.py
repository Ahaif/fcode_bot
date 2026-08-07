"""Navigator layer — fault-tolerant Bug2 state machine."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import Direction, Environment, Position

from geometry import manhattan, xy
from navigator import (
    NAV_NORMAL,
    NAV_WALL_FOLLOW,
    ensure_nav_state,
    shift_passable,
    step_to,
)


def _state(**kwargs: object) -> SimpleNamespace:
    s = SimpleNamespace()
    ensure_nav_state(s)
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


class _FakeCt:
    def __init__(
        self,
        pos: Position,
        blocked: set[Direction] | None = None,
        *,
        cooldown: int = 0,
        walls: set[tuple[int, int]] | None = None,
    ) -> None:
        self._pos = pos
        self._blocked = blocked or set()
        self._cooldown = cooldown
        self._walls = walls or set()
        self.moves: list[Direction] = []

    def get_position(self) -> Position:
        return self._pos

    def get_move_cooldown(self) -> int:
        return self._cooldown

    def get_map_width(self) -> int:
        return 40

    def get_map_height(self) -> int:
        return 40

    def get_tile_env(self, p: Position) -> Environment:
        if (p.x, p.y) in self._walls:
            return Environment.WALL
        return Environment.EMPTY

    def can_move(self, d: Direction) -> bool:
        if d == Direction.CENTRE or d in self._blocked:
            return False
        nxt = self._pos.add(d)
        if (nxt.x, nxt.y) in self._walls:
            return False
        return True

    def move(self, d: Direction) -> None:
        self.moves.append(d)
        self._pos = self._pos.add(d)


def test_step_to_moves_directly_when_open() -> None:
    ct = _FakeCt(Position(5, 5))
    state = _state()
    assert step_to(ct, state, Position(5, 8), avoid=None) is True
    assert ct.moves == [Direction.SOUTH]
    assert state.nav_mode == NAV_NORMAL
    assert state.nav_recent_positions[-1] == (5, 6)
    assert state.nav_failure_counter == 0


def test_step_to_false_when_cooldown() -> None:
    ct = _FakeCt(Position(1, 1), cooldown=1)
    state = _state()
    assert step_to(ct, state, Position(1, 5)) is False


def test_traffic_waits_first_turns() -> None:
    """Unit-blocked primary with no side-step → wait; no Bug2 yet."""
    ct = _FakeCt(
        Position(5, 5),
        blocked={
            Direction.SOUTH,
            Direction.EAST,
            Direction.WEST,
            Direction.NORTH,
        },
    )
    state = _state()
    assert step_to(ct, state, Position(5, 12)) is False
    assert state.nav_mode == NAV_NORMAL
    assert state.nav_block_counter == 1


def test_persistent_traffic_enters_wall_follow() -> None:
    """Same direction blocked >3 turns → obstacle avoidance."""
    state = _state()
    for _ in range(4):
        ct = _FakeCt(
            Position(5, 5),
            blocked={
                Direction.SOUTH,
                Direction.EAST,
                Direction.WEST,
                Direction.NORTH,
            },
        )
        step_to(ct, state, Position(5, 12))
    assert state.nav_mode == NAV_WALL_FOLLOW
    assert state.nav_hit_position == (5, 5)
    assert any(e.startswith("ENTER_WALL_FOLLOW") for e in state.nav_debug_log)


def test_primary_terrain_enters_wall_follow_even_if_side_open() -> None:
    """Hitting a wall must start Bug2 — do not oscillate via side-steps."""
    walls = {(5, 6)}  # south wall only
    ct = _FakeCt(Position(5, 5), walls=walls)
    state = _state()
    step_to(ct, state, Position(5, 12))
    assert state.nav_mode == NAV_WALL_FOLLOW
    assert state.nav_hit_distance == 7
    assert state.nav_hit_position == (5, 5)


def test_adjacent_target_no_side_step_on_first_block() -> None:
    ct = _FakeCt(Position(5, 5), blocked={Direction.SOUTH})
    state = _state()
    assert step_to(ct, state, Position(5, 6)) is False
    assert ct.moves == []


def test_leave_when_closer_than_hit() -> None:
    """Leave immediately when Manhattan < hit_distance."""
    state = _state(
        nav_mode=NAV_WALL_FOLLOW,
        nav_hit_distance=10,
        nav_hit_position=(5, 15),
        nav_follow_dir=Direction.EAST,
        nav_wall_side="left",
        nav_target=Position(5, 12),
        nav_follow_steps=3,
    )
    # dist to (5,12) from (5,8) = 4 < 10; south open → leave and move.
    ct = _FakeCt(Position(5, 8))
    assert step_to(ct, state, Position(5, 12)) is True
    assert state.nav_mode == NAV_NORMAL
    assert any(
        e.startswith("LEAVE_WALL_FOLLOW reason=closer_than_hit")
        for e in state.nav_debug_log
    )


def test_leave_when_direct_succeeds() -> None:
    state = _state(
        nav_mode=NAV_WALL_FOLLOW,
        nav_hit_distance=2,  # dist from (5,8) to (5,12) = 4, not closer
        nav_hit_position=(5, 10),
        nav_follow_dir=Direction.EAST,
        nav_wall_side="left",
        nav_follow_steps=2,
        nav_target=Position(5, 12),
    )
    ct = _FakeCt(Position(5, 8))
    assert step_to(ct, state, Position(5, 12)) is True
    assert ct.moves == [Direction.SOUTH]
    assert any(
        e.startswith("LEAVE_WALL_FOLLOW reason=direct_succeeds")
        for e in state.nav_debug_log
    )


def test_follow_steps_resets_and_flips() -> None:
    state = _state(
        nav_mode=NAV_WALL_FOLLOW,
        nav_hit_distance=2,
        nav_hit_position=(10, 10),
        nav_follow_dir=Direction.NORTH,
        nav_wall_side="left",
        nav_follow_steps=26,
        nav_target=Position(20, 20),
    )
    ct = _FakeCt(Position(10, 10))
    step_to(ct, state, Position(20, 20))
    assert state.nav_wall_side == "right"
    assert any(e.startswith("SWITCH_SIDE") for e in state.nav_debug_log)
    assert state.nav_recent_positions == [] or state.nav_mode == NAV_NORMAL


def test_failure_counter_resets_nav() -> None:
    state = _state(
        nav_mode=NAV_WALL_FOLLOW,
        nav_hit_distance=2,
        nav_follow_dir=Direction.NORTH,
        nav_wall_side="right",
        nav_follow_steps=1,
        nav_failure_counter=10,
        nav_target=Position(20, 20),
    )
    ct = _FakeCt(
        Position(10, 10),
        blocked=set(Direction) - {Direction.CENTRE},
        walls={(10, 9), (10, 11), (9, 10), (11, 10)},
    )
    assert step_to(ct, state, Position(20, 20)) is False
    assert state.nav_mode == NAV_NORMAL
    assert any(e.startswith("ABORT_WALL_FOLLOW") for e in state.nav_debug_log)


def test_recent_buffer_holds_ten() -> None:
    ct = _FakeCt(Position(0, 0))
    state = _state()
    # Walk east 12 times.
    for i in range(12):
        ct = _FakeCt(Position(i, 0))
        step_to(ct, state, Position(20, 0))
    assert len(state.nav_recent_positions) == 10
    assert state.nav_recent_positions[-1] == (12, 0) or state.nav_recent_positions[
        -1
    ] == (11, 0)


def test_shift_passable_moves_toward_core() -> None:
    ct = _FakeCt(Position(0, 0), walls={(8, 0), (7, 0)})
    ct.is_in_vision = lambda p: True  # type: ignore[method-assign]
    state = _state()
    core = Position(0, 0)
    shifted = shift_passable(ct, Position(8, 0), core, state=state)
    assert (shifted.x, shifted.y) == (6, 0)
    assert any(e.startswith("WAYPOINT_SHIFTED") for e in state.nav_debug_log)


def test_shift_passable_skips_unseen() -> None:
    ct = _FakeCt(Position(0, 0), walls={(8, 0), (7, 0), (6, 0)})
    ct.is_in_vision = lambda p: False  # type: ignore[method-assign]
    state = _state()
    assert shift_passable(ct, Position(8, 0), Position(0, 0), state=state) == Position(
        8, 0
    )


def test_target_changed_logged() -> None:
    ct = _FakeCt(Position(2, 2))
    state = _state(nav_target=Position(9, 9), nav_mode=NAV_WALL_FOLLOW)
    step_to(ct, state, Position(2, 8))
    assert any(e.startswith("TARGET_CHANGED") for e in state.nav_debug_log)
    assert state.nav_mode == NAV_NORMAL


def test_local_waypoint_for_core_return_only() -> None:
    ct = _FakeCt(Position(2, 2))
    state = _state(core_pos=Position(2, 20))
    step_to(ct, state, Position(20, 2))
    assert state.nav_local_waypoint is None

    state2 = _state(core_pos=Position(2, 20))
    ct2 = _FakeCt(Position(2, 2))
    step_to(ct2, state2, Position(2, 20))
    local = state2.nav_local_waypoint
    assert local is not None
    assert manhattan(Position(2, 2), local) <= 4
    ct3 = _FakeCt(ct2.get_position())
    state2.nav_recent_positions = []
    step_to(ct3, state2, Position(2, 20))
    assert xy(state2.nav_local_waypoint) == xy(local)


def test_side_step_under_traffic() -> None:
    """Primary blocked by unit, east open → side-step instead of freezing."""
    ct = _FakeCt(Position(5, 5), blocked={Direction.SOUTH})
    state = _state()
    assert step_to(ct, state, Position(5, 12)) is True
    assert ct.moves  # moved somewhere legal
    assert Direction.SOUTH not in ct.moves
