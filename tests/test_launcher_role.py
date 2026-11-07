"""launcher_role.py — throws only the comm-store-tracked dedicated attacker
builder, only when it's adjacent, at the attackable tile closest to the
(known) enemy Core."""

from __future__ import annotations

from fcode import EntityType, Position

import team_store
from roles import launcher_role


class FakeCt:
    def __init__(
        self,
        *,
        my_id: int = 1,
        my_pos: Position = Position(5, 0),
        my_team: int = 0,
        units: dict[int, tuple[int, EntityType, Position]] | None = None,
        attackable: list[Position] | None = None,
        launchable: set[tuple[Position, Position]] | None = None,
        store: dict[int, int] | None = None,
    ) -> None:
        self._id = my_id
        self._pos = my_pos
        self._team = my_team
        # uid -> (team, entity_type, pos)
        self._units = units or {}
        self._attackable = attackable or []
        self._launchable = launchable or set()
        self._store = dict(store or {})
        self._store.setdefault(0, 0)
        self.launched: list[tuple[Position, Position]] = []

    def get_id(self) -> int:
        return self._id

    def get_team(self, uid: int | None = None) -> int:
        if uid is None or uid == self._id:
            return self._team
        info = self._units.get(uid)
        return info[0] if info else self._team

    def get_position(self, uid: int | None = None) -> Position:
        if uid is None or uid == self._id:
            return self._pos
        info = self._units.get(uid)
        return info[2] if info else Position(0, 0)

    def get_entity_type(self, uid: int):
        info = self._units.get(uid)
        return info[1] if info else EntityType.BUILDER_BOT

    def get_attackable_tiles(self) -> list[Position]:
        return self._attackable

    def can_launch(self, bot_pos: Position, target: Position) -> bool:
        return (bot_pos, target) in self._launchable

    def launch(self, bot_pos: Position, target: Position) -> None:
        self.launched.append((bot_pos, target))

    def read_store(self, i: int) -> int:
        return self._store.get(i, 0)

    def write_store(self, i: int, v: int) -> None:
        self._store[i] = v


def _write_attacker(ct: FakeCt, uid: int) -> None:
    team_store.write_attack_builder_id(ct, uid)  # type: ignore[arg-type]


def _write_enemy_core(ct: FakeCt, pos: Position) -> None:
    team_store.write_enemy_core(ct, pos)  # type: ignore[arg-type]


def test_throws_adjacent_dedicated_attacker_toward_enemy_core() -> None:
    attacker_pos = Position(4, 0)  # orth-adjacent to launcher at (5,0)
    enemy_core = Position(20, 0)
    near_tile = Position(6, 0)  # closer to enemy_core than far_tile
    far_tile = Position(4, 0)
    ct = FakeCt(
        my_pos=Position(5, 0),
        units={7: (0, EntityType.BUILDER_BOT, attacker_pos)},
        attackable=[far_tile, near_tile],
        launchable={(attacker_pos, near_tile)},
    )
    _write_attacker(ct, 7)
    _write_enemy_core(ct, enemy_core)
    launcher_role.run_launcher(ct, None)  # type: ignore[arg-type]
    assert ct.launched == [(attacker_pos, near_tile)]


def test_does_not_throw_when_attacker_not_adjacent() -> None:
    ct = FakeCt(
        my_pos=Position(5, 0),
        units={7: (0, EntityType.BUILDER_BOT, Position(50, 50))},
        attackable=[Position(6, 0)],
        launchable={(Position(50, 50), Position(6, 0))},
    )
    _write_attacker(ct, 7)
    _write_enemy_core(ct, Position(20, 0))
    launcher_role.run_launcher(ct, None)  # type: ignore[arg-type]
    assert ct.launched == []


def test_does_not_throw_when_no_attacker_claimed() -> None:
    ct = FakeCt(
        my_pos=Position(5, 0),
        units={7: (0, EntityType.BUILDER_BOT, Position(4, 0))},
        attackable=[Position(6, 0)],
        launchable={(Position(4, 0), Position(6, 0))},
    )
    _write_enemy_core(ct, Position(20, 0))
    launcher_role.run_launcher(ct, None)  # type: ignore[arg-type]
    assert ct.launched == []


def test_does_not_throw_a_non_attacker_or_enemy_unit() -> None:
    """Only ever throws the comm-store-tracked dedicated attacker — never an
    arbitrary adjacent friendly (e.g. a mining builder just passing by) or
    an enemy unit."""
    attacker_pos = Position(4, 0)
    ct = FakeCt(
        my_pos=Position(5, 0),
        units={7: (1, EntityType.BUILDER_BOT, attacker_pos)},  # enemy team
        attackable=[Position(6, 0)],
        launchable={(attacker_pos, Position(6, 0))},
    )
    _write_attacker(ct, 7)
    _write_enemy_core(ct, Position(20, 0))
    launcher_role.run_launcher(ct, None)  # type: ignore[arg-type]
    assert ct.launched == []


def test_does_not_throw_when_illegal() -> None:
    attacker_pos = Position(4, 0)
    ct = FakeCt(
        my_pos=Position(5, 0),
        units={7: (0, EntityType.BUILDER_BOT, attacker_pos)},
        attackable=[Position(6, 0)],
        launchable=set(),  # can_launch always False
    )
    _write_attacker(ct, 7)
    _write_enemy_core(ct, Position(20, 0))
    launcher_role.run_launcher(ct, None)  # type: ignore[arg-type]
    assert ct.launched == []
