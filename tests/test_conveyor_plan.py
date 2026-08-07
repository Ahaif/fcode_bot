"""Persistent conveyor plan — planner + replan suffix only."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import Environment, Position

from economy import compute_conveyor_plan, plan_is_complete, replan_conveyor_suffix


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

    core = Position(1, 1)  # footprint (1,1)-(2,2); (3,1) is adjacent
    plan = [Position(5, 1), Position(4, 1), Position(3, 1)]
    state = SimpleNamespace(build_plan=plan, build_index=0)
    ct = Ct()
    assert plan_is_complete(ct, state, core) is False  # type: ignore[arg-type]
    ct.conveyors = {(5, 1), (4, 1), (3, 1)}
    assert plan_is_complete(ct, state, core) is True  # type: ignore[arg-type]
