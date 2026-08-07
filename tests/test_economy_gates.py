"""Hard gate: conveyors only after this builder recorded a harvester."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import EntityType, Position

from assignment import claim_ore, get_assignment, record_harvester
from constants import (
    PHASE_BUILD_CONVEYOR,
    PHASE_EXPLORE,
    PHASE_FAILED_TASK,
)
from economy import compute_conveyor_plan, friendly_harvester_on
from geometry import on_or_orth_adjacent_to_core
from roles import builder_role


def test_friendly_harvester_on_false_when_empty() -> None:
    class Ct:
        def get_tile_building_id(self, _p: Position):
            return None

    assert friendly_harvester_on(Ct(), Position(0, 0)) is False  # type: ignore[arg-type]


def test_friendly_harvester_on_true_for_team_harvester() -> None:
    class Ct:
        def get_tile_building_id(self, _p: Position):
            return 7

        def get_entity_type(self, _uid: int = 0):
            return EntityType.HARVESTER

        def get_team(self, _uid: int | None = None):
            return 0

    assert friendly_harvester_on(Ct(), Position(3, 3)) is True  # type: ignore[arg-type]


def test_build_conveyor_aborts_without_recorded_harvester() -> None:
    """Ownership gate: BUILD_CONVEYOR without harvester_pos → FAILED, no pave."""
    state = SimpleNamespace(
        phase=PHASE_BUILD_CONVEYOR,
        core_pos=Position(1, 1),
        assignment=None,
        explore_target=None,
        approach_stand=Position(5, 6),
        build_plan=[Position(5, 6), Position(5, 5), Position(5, 4)],
        build_index=0,
        replan_count=0,
        last_replan_reason=None,
        stand_paved=False,
        stuck=0,
        harvests_done=0,
    )
    claim_ore(state, Position(5, 5))
    assert not get_assignment(state).has_harvester

    class FakeCt:
        def get_id(self) -> int:
            return 1

        def get_position(self) -> Position:
            return Position(5, 7)

        def is_in_vision(self, _p: Position) -> bool:
            return True

        def get_tile_building_id(self, _p: Position):
            return None

        def get_action_cooldown(self) -> int:
            return 0

        def get_global_resources(self) -> int:
            return 9999

        def get_conveyor_cost(self) -> int:
            return 10

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

        def can_build_conveyor(self, *_a, **_k) -> bool:
            raise AssertionError("must not build conveyor without ownership")

        def build_conveyor(self, *_a, **_k) -> None:
            raise AssertionError("must not build conveyor without ownership")

    builder_role._state_build_conveyor(FakeCt(), state, avoid=None)  # type: ignore[arg-type]
    assert state.phase == PHASE_FAILED_TASK
    assert get_assignment(state).ore_pos is None


def test_build_conveyor_keeps_going_when_ore_out_of_vision() -> None:
    """Once owned, out-of-vision ore must not abort the home belt."""
    stand = Position(5, 6)
    ahead = Position(5, 5)
    state = SimpleNamespace(
        phase=PHASE_BUILD_CONVEYOR,
        core_pos=Position(1, 1),
        assignment=None,
        explore_target=None,
        approach_stand=stand,
        build_plan=[stand, ahead, Position(5, 4), Position(5, 3), Position(5, 2)],
        build_index=0,
        replan_count=0,
        last_replan_reason=None,
        stand_paved=False,
        stuck=0,
        harvests_done=1,
    )
    claim_ore(state, Position(5, 7))
    record_harvester(state, Position(5, 7))

    class FakeCt:
        def __init__(self) -> None:
            self.built: Position | None = None

        def get_position(self) -> Position:
            # Standing on stand — executor must build ahead, not abandon.
            return stand

        def is_in_vision(self, _p: Position) -> bool:
            return False

        def get_action_cooldown(self) -> int:
            return 0

        def get_global_resources(self) -> int:
            return 9999

        def get_conveyor_cost(self) -> int:
            return 10

        def get_map_width(self) -> int:
            return 32

        def get_map_height(self) -> int:
            return 32

        def get_tile_env(self, _p: Position):
            from fcode import Environment

            return Environment.EMPTY

        def get_tile_building_id(self, _p: Position):
            return None

        def get_tile_builder_bot_id(self, _p: Position):
            return None

        def can_build_conveyor(self, *_a, **_k) -> bool:
            return True

        def build_conveyor(self, p: Position, *_a, **_k) -> None:
            self.built = p

        def get_move_cooldown(self) -> int:
            return 1

    ct = FakeCt()
    builder_role._state_build_conveyor(ct, state, avoid=None)  # type: ignore[arg-type]
    assert state.phase == PHASE_BUILD_CONVEYOR
    assert get_assignment(state).has_harvester
    assert ct.built == ahead


def test_compute_conveyor_plan_axis_to_core() -> None:
    """Planner produces a deterministic stand→Core-approach path."""

    class Ct:
        def get_map_width(self) -> int:
            return 32

        def get_map_height(self) -> int:
            return 32

        def is_in_vision(self, _p: Position) -> bool:
            return True

        def get_tile_env(self, _p: Position):
            from fcode import Environment

            return Environment.EMPTY

        def get_tile_building_id(self, _p: Position):
            return None

    stand = Position(5, 5)
    core = Position(1, 1)  # footprint (1,1)-(2,2)
    plan = compute_conveyor_plan(Ct(), stand, core)  # type: ignore[arg-type]
    assert plan is not None
    assert plan[0] == stand
    assert on_or_orth_adjacent_to_core(plan[-1], core)
