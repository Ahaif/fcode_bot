"""sentinel_role.py — must never fire on a friendly-occupied tile, and must
pick a legal enemy target from get_attackable_tiles() (no get_gunner_target()
equivalent exists for sentinels)."""

from __future__ import annotations

from fcode import EntityType, Position

from roles import sentinel_role


class FakeCt:
    def __init__(
        self,
        *,
        my_pos: Position = Position(5, 5),
        my_team: int = 0,
        attackable: list[Position] | None = None,
        tile_buildings: dict[tuple[int, int], tuple[int, int]] | None = None,  # (id, team)
    ) -> None:
        self._pos = my_pos
        self._team = my_team
        self._attackable = attackable or []
        self._tile_buildings = tile_buildings or {}
        self.fired_at: list[Position] = []

    def get_position(self, uid: int | None = None) -> Position:
        return self._pos

    def get_team(self, uid: int | None = None) -> int:
        if uid is None:
            return self._team
        for (_x, _y), (bid, team) in self._tile_buildings.items():
            if bid == uid:
                return team
        return self._team

    def get_attackable_tiles(self) -> list[Position]:
        return self._attackable

    def get_tile_building_id(self, pos: Position) -> int | None:
        key = (pos.x, pos.y)
        if key in self._tile_buildings:
            return self._tile_buildings[key][0]
        return None

    def can_fire(self, target: Position) -> bool:
        return True

    def fire(self, target: Position) -> None:
        self.fired_at.append(target)


def test_does_not_fire_on_friendly() -> None:
    ct = FakeCt(
        attackable=[Position(6, 5)],
        tile_buildings={(6, 5): (9, 0)},  # our own team
    )
    sentinel_role.run_sentinel(ct, None)  # type: ignore[arg-type]
    assert ct.fired_at == []


def test_fires_on_enemy() -> None:
    ct = FakeCt(
        attackable=[Position(6, 5)],
        tile_buildings={(6, 5): (9, 1)},  # enemy team
    )
    sentinel_role.run_sentinel(ct, None)  # type: ignore[arg-type]
    assert ct.fired_at == [Position(6, 5)]


def test_picks_nearest_enemy_among_multiple() -> None:
    ct = FakeCt(
        my_pos=Position(0, 0),
        attackable=[Position(10, 0), Position(3, 0)],
        tile_buildings={(10, 0): (9, 1), (3, 0): (8, 1)},
    )
    sentinel_role.run_sentinel(ct, None)  # type: ignore[arg-type]
    assert ct.fired_at == [Position(3, 0)]


def test_no_attackable_tiles_does_not_fire() -> None:
    ct = FakeCt(attackable=[])
    sentinel_role.run_sentinel(ct, None)  # type: ignore[arg-type]
    assert ct.fired_at == []


def test_empty_tile_in_range_is_ignored() -> None:
    ct = FakeCt(attackable=[Position(6, 5)])  # no building there at all
    sentinel_role.run_sentinel(ct, None)  # type: ignore[arg-type]
    assert ct.fired_at == []
