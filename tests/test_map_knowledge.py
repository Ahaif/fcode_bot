"""MapKnowledge pack helpers + update smoke tests."""

from __future__ import annotations

from fcode import Environment, Position

import map_knowledge


def test_sighting_pack_roundtrip() -> None:
    v = map_knowledge.pack_sighting(12, 7, map_knowledge._TYPE_HARVESTER, 41)
    parsed = map_knowledge.unpack_sighting(v)
    assert parsed == (12, 7, map_knowledge._TYPE_HARVESTER, 41)


def test_choke_pack_roundtrip() -> None:
    v = map_knowledge.pack_choke(9, 10, 1)
    assert map_knowledge.unpack_choke(v) == (9, 10, 1)
    assert map_knowledge.unpack_choke(0) is None


def test_update_records_env() -> None:
    map_knowledge.reset_for_tests()

    class Ct:
        def get_map_width(self) -> int:
            return 8

        def get_map_height(self) -> int:
            return 8

        def get_current_round(self) -> int:
            return 3

        def get_nearby_tiles(self):
            return [Position(2, 2), Position(3, 2)]

        def is_in_vision(self, _p: Position) -> bool:
            return True

        def get_tile_env(self, p: Position):
            if (p.x, p.y) == (2, 2):
                return Environment.WALL
            if (p.x, p.y) == (3, 2):
                return Environment.ORE_TITANIUM
            return Environment.EMPTY

        def get_nearby_buildings(self):
            return []

        def get_team(self, _id=None):
            return 0

        def read_store(self, _i: int) -> int:
            return 0

        def write_store(self, _i: int, _v: int) -> None:
            pass

        def is_tile_passable(self, _p: Position) -> bool:
            return True

    map_knowledge.update(Ct())  # type: ignore[arg-type]
    assert map_knowledge.known_env_at(2, 2) == map_knowledge._ENV_WALL
    assert map_knowledge.known_env_at(3, 2) == map_knowledge._ENV_ORE
    assert map_knowledge.known_env_at(0, 0) == map_knowledge._ENV_UNKNOWN
