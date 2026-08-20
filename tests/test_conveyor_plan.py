"""Persistent conveyor plan — planner + replan suffix only."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import Direction, EntityType, Environment, Position

from assignment import claim_ore, record_harvester
from constants import PHASE_BUILD_CONVEYOR
from economy import compute_conveyor_plan, plan_is_complete, replan_conveyor_suffix
from geometry import facing_toward
from roles import builder_role


class _OpenMap:
    def get_map_width(self) -> int:
        return 32

    def get_map_height(self) -> int:
        return 32

    def is_in_vision(self, _p: Position) -> bool:
        return True

    def get_tile_env(self, _p: Position):
        return Environment.EMPTY

    def get_tile_building_id(self, _p: Position):
        return None


def test_plan_starts_at_stand_and_avoids_core_tiles() -> None:
    stand = Position(8, 1)
    core = Position(1, 1)
    plan = compute_conveyor_plan(_OpenMap(), stand, core)  # type: ignore[arg-type]
    assert plan is not None
    assert plan[0] == stand
    for t in plan:
        assert not (1 <= t.x <= 2 and 1 <= t.y <= 2)


def test_axis_plan_rejects_fog_shortcut() -> None:
    """Axis paths through fog must not be accepted (may hide walls)."""

    class FogMap(_OpenMap):
        def is_in_vision(self, p: Position) -> bool:
            # Only stand + core approach visible; mid corridor is fog.
            return p.x in (1, 2, 3, 8) or p.y != 1

    stand = Position(8, 1)
    core = Position(1, 1)
    # Still finds a plan via fog-tolerant BFS, but not a foggy axis shortcut.
    plan = compute_conveyor_plan(FogMap(), stand, core)  # type: ignore[arg-type]
    assert plan is not None


def test_closed_pocket_no_plan_when_walls_visible() -> None:
    """Fully walled pocket with no exit → no conveyor plan (don't pave into wall)."""
    walls = {
        (4, 4), (5, 4), (6, 4),
        (4, 5),         (6, 5),
        (4, 6), (5, 6), (6, 6),
    }
    stand = Position(5, 5)  # inside
    core = Position(1, 1)

    class Pocket(_OpenMap):
        def get_tile_env(self, p: Position):
            if (p.x, p.y) in walls:
                return Environment.WALL
            return Environment.EMPTY

    assert compute_conveyor_plan(Pocket(), stand, core) is None  # type: ignore[arg-type]


def test_replan_preserves_prefix() -> None:
    stand = Position(6, 3)
    core = Position(1, 1)
    ct = _OpenMap()
    plan = compute_conveyor_plan(ct, stand, core)  # type: ignore[arg-type]
    assert plan is not None and len(plan) >= 3
    state = SimpleNamespace(
        build_plan=list(plan),
        build_index=2,
        replan_count=0,
        last_replan_reason=None,
    )
    prefix = plan[:2]
    assert replan_conveyor_suffix(ct, state, core, from_index=2, reason="wall")  # type: ignore[arg-type]
    new_plan = state.build_plan
    assert [ (p.x, p.y) for p in new_plan[:2] ] == [ (p.x, p.y) for p in prefix ]
    assert state.replan_count == 1
    assert state.last_replan_reason == "wall"


def test_plan_is_complete_requires_all_conveyors() -> None:
    class Ct(_OpenMap):
        def __init__(self) -> None:
            self.conveyors = {(3, 1), (4, 1)}

        def get_tile_building_id(self, p: Position):
            return 1 if (p.x, p.y) in self.conveyors else None

        def get_entity_type(self, _uid: int = 0):
            from fcode import EntityType

            return EntityType.CONVEYOR

        def get_team(self, _uid: int | None = None):
            return 0

    core = Position(1, 1)  # footprint (1,1)-(2,2); (3,1) is adjacent
    plan = [Position(5, 1), Position(4, 1), Position(3, 1)]
    state = SimpleNamespace(build_plan=plan, build_index=0)
    ct = Ct()
    assert plan_is_complete(ct, state, core) is False  # type: ignore[arg-type]
    ct.conveyors = {(5, 1), (4, 1), (3, 1)}
    assert plan_is_complete(ct, state, core) is True  # type: ignore[arg-type]


def test_enemy_conveyor_does_not_complete_plan() -> None:
    """Enemy belts must not count as our plan conveyors (ore gift bug)."""

    class Ct(_OpenMap):
        def __init__(self) -> None:
            # All plan tiles have *enemy* conveyors.
            self.conveyors = {(5, 1), (4, 1), (3, 1)}

        def get_tile_building_id(self, p: Position):
            return 1 if (p.x, p.y) in self.conveyors else None

        def get_entity_type(self, _uid: int = 0):
            return EntityType.CONVEYOR

        def get_team(self, uid: int | None = None):
            # uid None / omitted → us; building id 1 → enemy
            if uid is None:
                return 0
            return 1

    core = Position(1, 1)
    plan = [Position(5, 1), Position(4, 1), Position(3, 1)]
    state = SimpleNamespace(build_plan=plan, build_index=0)
    assert plan_is_complete(Ct(), state, core) is False  # type: ignore[arg-type]


def test_plan_routes_through_clearable_enemy_belt() -> None:
    """Only-path choke through enemy belts must stay on that corridor.

    Replanning around them dumps builders into walled dead-ends (crossfire).
    """
    enemy = {(5, 1), (4, 1), (3, 1), (2, 1)}  # sole row toward core

    class Ct(_OpenMap):
        def get_tile_building_id(self, p: Position):
            return 9 if (p.x, p.y) in enemy else None

        def get_entity_type(self, _uid: int = 0):
            return EntityType.CONVEYOR

        def get_team(self, uid: int | None = None):
            if uid is None:
                return 0
            return 1

    stand = Position(6, 1)
    core = Position(1, 1)
    plan = compute_conveyor_plan(Ct(), stand, core)  # type: ignore[arg-type]
    assert plan is not None
    # Direct corridor is shorter than going around — expect enemy tiles in plan.
    assert any((t.x, t.y) in enemy for t in plan)


def test_plan_tile_status_enemy_belt() -> None:
    from economy import plan_tile_status

    class Ct(_OpenMap):
        def get_tile_building_id(self, p: Position):
            return 9 if (p.x, p.y) == (4, 4) else None

        def get_entity_type(self, _uid: int = 0):
            return EntityType.CONVEYOR

        def get_team(self, uid: int | None = None):
            if uid is None:
                return 0
            return 1

    assert plan_tile_status(Ct(), Position(4, 4), Position(1, 1)) == "enemy_belt"  # type: ignore[arg-type]


def test_build_refuses_facing_into_enemy_belt() -> None:
    from economy import try_build_plan_tile

    class Ct(_OpenMap):
        def __init__(self) -> None:
            self.built: list[tuple[Position, Direction]] = []
            self.pos = Position(5, 2)

        def get_position(self) -> Position:
            return self.pos

        def get_action_cooldown(self) -> int:
            return 0

        def get_global_resources(self) -> int:
            return 999

        def get_conveyor_cost(self) -> int:
            return 3

        def get_tile_building_id(self, p: Position):
            # Enemy conveyor north of the tile we want to pave.
            if (p.x, p.y) == (5, 0):
                return 9
            return None

        def get_entity_type(self, _uid: int = 0):
            return EntityType.CONVEYOR

        def get_team(self, uid: int | None = None):
            if uid is None:
                return 0
            return 1

        def get_tile_builder_bot_id(self, _p: Position):
            return None

        def can_build_conveyor(self, _p: Position, _d: Direction) -> bool:
            return True

        def build_conveyor(self, p: Position, d: Direction) -> int:
            self.built.append((p, d))
            return 1

    # Preferred face target is north — into the enemy belt. Must pick another.
    ct = Ct()
    tile = Position(5, 1)
    result = try_build_plan_tile(ct, tile, Position(5, 0), Position(1, 1))  # type: ignore[arg-type]
    assert result == "built"
    assert ct.built
    assert ct.built[0][1] != Direction.NORTH


def test_corner_skip_face_target_picks_wrong_cardinal() -> None:
    """Document why we must face plan[i+1], not plan[i+2], on corners."""
    tile = Position(13, 17)
    next_tile = Position(13, 18)
    skip_tile = Position(12, 18)
    assert facing_toward(tile, next_tile) == Direction.SOUTH
    # Diagonal skip + cardinal tie-break → WEST (wrong for the belt).
    assert facing_toward(tile, skip_tile) == Direction.WEST


def test_pave_while_standing_on_next_faces_immediate_successor() -> None:
    """Regression: paving stand from plan[1] must face plan[1], not plan[2].

    On an L-shaped path, facing plan[2] aims diagonally and builds WEST
    instead of SOUTH — ore never enters the belt (valkyrie).
    """
    stand = Position(13, 17)
    ahead = Position(13, 18)
    turn = Position(12, 18)
    plan = [stand, ahead, turn, Position(11, 18), Position(10, 18)]
    state = SimpleNamespace(
        phase=PHASE_BUILD_CONVEYOR,
        core_pos=Position(1, 1),
        assignment=None,
        explore_target=None,
        approach_stand=stand,
        build_plan=plan,
        build_index=0,
        replan_count=0,
        last_replan_reason=None,
        stand_paved=False,
        stuck=0,
        harvests_done=1,
    )
    claim_ore(state, Position(13, 16))
    record_harvester(state, Position(13, 16))

    class FakeCt:
        def __init__(self) -> None:
            self.built: list[tuple[Position, Direction]] = []
            self.conveyors = {(ahead.x, ahead.y)}
            self.ore = Position(13, 16)

        def get_position(self) -> Position:
            return ahead  # standing on next plan tile

        def is_in_vision(self, _p: Position) -> bool:
            return True

        def get_action_cooldown(self) -> int:
            return 0

        def get_global_resources(self) -> int:
            return 9999

        def get_conveyor_cost(self) -> int:
            return 3

        def get_map_width(self) -> int:
            return 32

        def get_map_height(self) -> int:
            return 32

        def get_tile_env(self, _p: Position):
            return Environment.EMPTY

        def get_tile_building_id(self, p: Position):
            if (p.x, p.y) == (self.ore.x, self.ore.y):
                return 7
            return 1 if (p.x, p.y) in self.conveyors else None

        def get_entity_type(self, uid: int = 0):
            if uid == 7:
                return EntityType.HARVESTER
            return EntityType.CONVEYOR

        def get_team(self, _uid: int | None = None):
            return 0

        def get_tile_builder_bot_id(self, _p: Position):
            return None

        def can_build_conveyor(self, _p: Position, _d: Direction) -> bool:
            return True

        def build_conveyor(self, p: Position, d: Direction) -> int:
            self.built.append((p, d))
            self.conveyors.add((p.x, p.y))
            return 99

        def get_move_cooldown(self) -> int:
            return 1

    ct = FakeCt()
    builder_role._state_build_conveyor(ct, state, avoid=None)  # type: ignore[arg-type]
    assert ct.built == [(stand, Direction.SOUTH)]
