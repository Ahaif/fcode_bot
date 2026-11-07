"""gunner_role.py — must never fire on a friendly-occupied tile.

get_gunner_target() returns the closest *targetable tile* on the facing
line with no team filter (confirmed against the engine's own stub docs) —
a friendly conveyor/harvester in the line is just as "valid" a target as an
enemy. run_gunner must check team before ever calling fire().
"""

from __future__ import annotations

from fcode import Direction, EntityType, Position

import condition_state
import map_knowledge
from roles import gunner_role


class FakeCt:
    def __init__(
        self,
        *,
        my_id: int = 1,
        my_pos: Position = Position(5, 5),
        my_team: int = 0,
        direction: Direction = Direction.EAST,
        gunner_target: Position | None = None,
        tile_building: tuple[int, int, EntityType] | None = None,  # (id, team, type)
        tile_builder_bot: tuple[int, int] | None = None,  # (id, team)
        round_: int = 10,
        own_core: Position = Position(0, 0),
        store: dict[int, int] | None = None,
    ) -> None:
        self._id = my_id
        self._pos = my_pos
        self._team = my_team
        self._direction = direction
        self._gunner_target = gunner_target
        self._tile_building = tile_building
        self._tile_builder_bot = tile_builder_bot
        self._round = round_
        self._own_core = own_core
        self._store = dict(store or {})
        self._store.setdefault(0, 0)
        self.fired_at: list[Position] = []
        self.rotated_to: list[Direction] = []

    def get_id(self) -> int:
        return self._id

    def get_position(self, uid: int | None = None) -> Position:
        return self._pos

    def get_team(self, uid: int | None = None) -> int:
        if uid is None:
            return self._team
        if self._tile_building is not None and uid == self._tile_building[0]:
            return self._tile_building[1]
        if self._tile_builder_bot is not None and uid == self._tile_builder_bot[0]:
            return self._tile_builder_bot[1]
        return self._team

    def get_direction(self, uid: int | None = None) -> Direction:
        return self._direction

    def get_current_round(self) -> int:
        return self._round

    def get_gunner_target(self) -> Position | None:
        return self._gunner_target

    def get_tile_building_id(self, pos: Position) -> int | None:
        if self._tile_building is not None:
            return self._tile_building[0]
        return None

    def get_entity_type(self, uid: int):
        if self._tile_building is not None and uid == self._tile_building[0]:
            return self._tile_building[2]
        return EntityType.BUILDER_BOT

    def get_tile_builder_bot_id(self, pos: Position) -> int | None:
        if self._tile_builder_bot is not None:
            return self._tile_builder_bot[0]
        return None

    def can_fire(self, target: Position) -> bool:
        return True

    def fire(self, target: Position) -> None:
        self.fired_at.append(target)

    def can_rotate(self, direction: Direction) -> bool:
        return True

    def rotate(self, direction: Direction) -> None:
        self.rotated_to.append(direction)
        self._direction = direction

    def get_map_width(self) -> int:
        return 20

    def get_map_height(self) -> int:
        return 20

    def get_nearby_tiles(self):
        return []

    def is_in_vision(self, _p: Position) -> bool:
        return True

    def get_tile_env(self, _p: Position):
        from fcode import Environment

        return Environment.EMPTY

    def get_nearby_buildings(self) -> list[int]:
        return []

    def get_nearby_units(self) -> list[int]:
        return []

    def read_store(self, i: int) -> int:
        return self._store.get(i, 0)

    def write_store(self, i: int, v: int) -> None:
        self._store[i] = v


def _reset() -> None:
    condition_state.reset_for_tests()
    map_knowledge.reset_for_tests()


def _state():
    return type("S", (), {"idle_rounds": 0})()


def test_does_not_fire_on_friendly_building() -> None:
    _reset()
    ct = FakeCt(
        gunner_target=Position(6, 5),
        tile_building=(9, 0, EntityType.CONVEYOR),  # our own team (0)
    )
    gunner_role.run_gunner(ct, _state())  # type: ignore[arg-type]
    assert ct.fired_at == []


def test_does_not_fire_on_friendly_builder_bot() -> None:
    _reset()
    ct = FakeCt(
        gunner_target=Position(6, 5),
        tile_builder_bot=(9, 0),  # our own team
    )
    gunner_role.run_gunner(ct, _state())  # type: ignore[arg-type]
    assert ct.fired_at == []


def test_fires_on_enemy_building() -> None:
    _reset()
    ct = FakeCt(
        gunner_target=Position(6, 5),
        tile_building=(9, 1, EntityType.HARVESTER),  # enemy team
    )
    gunner_role.run_gunner(ct, _state())  # type: ignore[arg-type]
    assert ct.fired_at == [Position(6, 5)]


def test_fires_on_enemy_builder_bot() -> None:
    _reset()
    ct = FakeCt(
        gunner_target=Position(6, 5),
        tile_builder_bot=(9, 1),  # enemy team
    )
    gunner_role.run_gunner(ct, _state())  # type: ignore[arg-type]
    assert ct.fired_at == [Position(6, 5)]


def test_no_target_does_not_fire() -> None:
    _reset()
    ct = FakeCt(gunner_target=None)
    gunner_role.run_gunner(ct, _state())  # type: ignore[arg-type]
    assert ct.fired_at == []


def test_rotates_toward_known_threat_after_idle_streak() -> None:
    _reset()
    from team_store import write_own_core

    core = Position(0, 0)
    state = _state()
    ct = FakeCt(
        my_pos=Position(-3, 0),
        direction=Direction.WEST,  # facing away from the threat (wrong way)
        gunner_target=None,
        own_core=core,
    )
    write_own_core(ct, core)  # type: ignore[arg-type]
    # Enemy gunner at (2,0) — within CORE_THREAT_RANGE_SQ_GUNNER of the Core
    # footprint tile (1,0): dist_sq=1. Threat is EAST of our gunner at (-3,0).
    ct.get_nearby_buildings = lambda: [42]  # type: ignore[method-assign]
    ct.get_entity_type = lambda uid: EntityType.GUNNER  # type: ignore[method-assign]
    ct.get_team = lambda uid=None: (1 if uid == 42 else 0)  # type: ignore[method-assign]
    ct.get_position = lambda uid=None: (Position(2, 0) if uid == 42 else ct._pos)  # type: ignore[method-assign]

    for _ in range(3):
        gunner_role.run_gunner(ct, state)  # type: ignore[arg-type]
    assert ct.rotated_to, "expected a rotate() call after the idle streak"
    assert ct.rotated_to[-1] == Direction.EAST
