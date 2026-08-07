"""Builder-role helpers use Assignment for ore ownership."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import Position

from assignment import claim_ore, get_assignment, record_harvester
from constants import (
    PHASE_BUILD_HARVESTER,
    PHASE_EXPLORE,
    PHASE_MOVE_TO_STAND,
    PHASE_PLAN_CONVEYOR,
)
from roles import builder_role


def _bare_state() -> SimpleNamespace:
    return SimpleNamespace(
        phase=PHASE_EXPLORE,
        core_pos=Position(1, 1),
        assignment=None,
        explore_target=None,
        approach_stand=None,
        build_plan=None,
        build_index=0,
        replan_count=0,
        last_replan_reason=None,
        stand_paved=False,
        stuck=0,
        harvests_done=0,
    )


def test_cancel_pre_harvester_clears_assignment_and_resets_phase() -> None:
    state = _bare_state()
    claim_ore(state, Position(4, 4), owner_id=1)
    state.phase = PHASE_MOVE_TO_STAND
    state.current_waypoint = Position(0, 0)
    state.current_waypoint_index = 2
    state.explore_target = Position(0, 0)
    state.approach_stand = Position(4, 5)
    state.stuck = 2

    class FakeCt:
        def get_id(self) -> int:
            return 1

        def get_position(self) -> Position:
            return Position(0, 0)

        def get_nearby_tiles(self):
            return []

        def get_nearby_units(self):
            return []

        def get_team(self, _uid: int | None = None) -> int:
            return 0

        def read_store(self, _slot: int) -> int:
            return 0

        def write_store(self, _slot: int, _val: int) -> None:
            return None

    builder_role._cancel_pre_harvester(FakeCt(), state, reason="test")  # type: ignore[arg-type]
    a = get_assignment(state)
    assert a.ore_pos is None
    assert a.harvester_pos is None
    assert state.phase == PHASE_EXPLORE
    # Sector waypoint must survive economy → explore resume.
    assert state.current_waypoint == Position(0, 0)
    assert state.current_waypoint_index == 2
    assert state.approach_stand is None
    assert state.stuck == 0


def test_enter_late_game_clears_assignment() -> None:
    state = _bare_state()
    claim_ore(state, Position(8, 8), owner_id=1)

    class FakeCt:
        def get_id(self) -> int:
            return 1

        def read_store(self, _slot: int) -> int:
            return 0

        def write_store(self, _slot: int, _val: int) -> None:
            return None

    builder_role._enter_late_game(FakeCt(), state)  # type: ignore[arg-type]
    assert get_assignment(state).ore_pos is None
    assert state.phase == "defend"


def test_move_to_stand_keeps_harvester_pos_none() -> None:
    """Approach must not record ownership — conveyors stay gated."""
    state = _bare_state()
    ore = Position(5, 5)
    stand = Position(5, 6)
    claim_ore(state, ore)
    state.approach_stand = stand
    state.phase = PHASE_MOVE_TO_STAND

    class FakeCt:
        def get_position(self) -> Position:
            return Position(5, 8)

        def get_move_cooldown(self) -> int:
            return 1  # force early return after reading stand

    builder_role._state_move_to_stand(FakeCt(), state, avoid=None)  # type: ignore[arg-type]
    assert get_assignment(state).harvester_pos is None
    assert get_assignment(state).ore_pos == ore
    assert not get_assignment(state).has_harvester


def test_build_records_harvester_then_plans() -> None:
    """Successful build sets harvester_pos and enters PLAN_CONVEYOR."""
    state = _bare_state()
    ore = Position(5, 5)
    claim_ore(state, ore)
    state.approach_stand = Position(5, 6)
    state.phase = PHASE_BUILD_HARVESTER

    class FakeCt:
        def get_position(self) -> Position:
            return Position(5, 6)

        def get_tile_building_id(self, _p: Position):
            return None

        def get_action_cooldown(self) -> int:
            return 0

        def get_global_resources(self) -> int:
            return 999

        def get_harvester_cost(self) -> int:
            return 50

        def can_build_harvester(self, _p: Position) -> bool:
            return True

        def build_harvester(self, _p: Position) -> None:
            return None

    builder_role._state_build_harvester(FakeCt(), state, avoid=None)  # type: ignore[arg-type]
    a = get_assignment(state)
    assert a.harvester_pos == ore
    assert a.has_harvester
    assert state.phase == PHASE_PLAN_CONVEYOR
    assert state.harvests_done == 1
