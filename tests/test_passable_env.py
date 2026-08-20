"""passable_env must match engine walkability (buildings, not only walls)."""

from __future__ import annotations

from fcode import EntityType, Environment, Position

from geometry import passable_env


def test_wall_impassable() -> None:
    class Ct:
        def get_map_width(self) -> int:
            return 10

        def get_map_height(self) -> int:
            return 10

        def is_in_vision(self, _p: Position) -> bool:
            return True

        def get_tile_env(self, _p: Position):
            return Environment.WALL

        def get_tile_building_id(self, _p: Position):
            return None

    assert passable_env(Ct(), Position(1, 1)) is False  # type: ignore[arg-type]


def test_harvester_impassable_conveyor_passable() -> None:
    class Ct:
        def __init__(self) -> None:
            self.buildings = {
                (2, 2): EntityType.HARVESTER,
                (3, 2): EntityType.CONVEYOR,
            }

        def get_map_width(self) -> int:
            return 10

        def get_map_height(self) -> int:
            return 10

        def is_in_vision(self, _p: Position) -> bool:
            return True

        def get_tile_env(self, _p: Position):
            return Environment.EMPTY

        def get_tile_building_id(self, p: Position):
            return 1 if (p.x, p.y) in self.buildings else None

        def get_entity_type(self, _uid: int = 0):
            # Caller only asks after building_id — use last queried pos via hack
            return EntityType.HARVESTER

        def get_team(self, _uid=None):
            return 0

    ct = Ct()

    # Patch get_entity_type to use building map
    def _etype(_uid: int = 0):
        # geometry calls get_entity_type(bid) after get_tile_building_id
        # We need per-tile type — store last building id lookup
        return ct._last_et

    def _bid(p: Position):
        et = ct.buildings.get((p.x, p.y))
        if et is None:
            return None
        ct._last_et = et
        return 1

    ct.get_tile_building_id = _bid  # type: ignore[method-assign]
    ct.get_entity_type = _etype  # type: ignore[method-assign]
    ct._last_et = EntityType.HARVESTER

    assert passable_env(ct, Position(2, 2)) is False  # type: ignore[arg-type]
    assert passable_env(ct, Position(3, 2)) is True  # type: ignore[arg-type]
    assert passable_env(ct, Position(4, 2)) is True  # type: ignore[arg-type]
