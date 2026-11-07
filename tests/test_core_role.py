"""Core role — builder spawn/replenishment (roles/core_role.py)."""

from __future__ import annotations

from fcode import Direction, EntityType, Position

import map_knowledge
import team_store
from roles import core_role


class FakeCt:
    def __init__(
        self,
        *,
        my_id: int = 1,
        my_pos: Position = Position(5, 5),
        unit_count: int = 6,
        resources: int = 10_000,
        builder_cost: int = 30,
        buildable: bool = True,
        round_: int = 10,
    ) -> None:
        self._id = my_id
        self._pos = my_pos
        self._unit_count = unit_count
        self._resources = resources
        self._builder_cost = builder_cost
        self._buildable = buildable
        self._round = round_
        self._store = [0] * 16
        self.spawned: list[Position] = []

    def get_id(self) -> int:
        return self._id

    def get_position(self, uid: int | None = None) -> Position:
        return self._pos

    def get_team(self, uid: int | None = None) -> int:
        return 0

    def get_entity_type(self, uid: int | None = None) -> EntityType:
        return EntityType.CORE

    def get_current_round(self) -> int:
        return self._round

    def get_map_width(self) -> int:
        return 20

    def get_map_height(self) -> int:
        return 20

    def get_nearby_tiles(self, dist_sq: int | None = None) -> list[Position]:
        return []

    def get_nearby_units(self) -> list[int]:
        return []

    def get_nearby_buildings(self) -> list[int]:
        return []

    def get_unit_count(self) -> int:
        return self._unit_count

    def get_action_cooldown(self) -> int:
        return 0

    def get_global_resources(self) -> int:
        return self._resources

    def get_builder_bot_cost(self) -> int:
        return self._builder_cost

    def can_spawn(self, _pos: Position) -> bool:
        return self._buildable

    def spawn_builder(self, pos: Position) -> None:
        self.spawned.append(pos)

    def read_store(self, i: int) -> int:
        return self._store[i]

    def write_store(self, i: int, v: int) -> None:
        self._store[i] = v


def _reset() -> None:
    map_knowledge.reset_for_tests()


def test_run_core_replenishes_after_losses_below_max_builders() -> None:
    """A monotonic "spawned so far" tally never decreases when a builder
    dies, so once it reached MAX_BUILDERS the Core stopped spawning
    forever — permanently under-strength after any combat losses. Live
    unit_count must drive this instead, so losses get replenished (real
    match, 2026-08-23: "when builder is died... the attacker or other
    builder never spawned")."""
    _reset()
    ct = FakeCt(unit_count=3)  # below MAX_BUILDERS=6 — e.g. after losses
    state = type("S", (), {})()
    core_role.run_core(ct, state)  # type: ignore[arg-type]
    assert len(ct.spawned) == 1


def test_run_core_does_not_spawn_once_at_max_builders() -> None:
    _reset()
    ct = FakeCt(unit_count=core_role.MAX_BUILDERS)
    state = type("S", (), {})()
    core_role.run_core(ct, state)  # type: ignore[arg-type]
    assert ct.spawned == []
