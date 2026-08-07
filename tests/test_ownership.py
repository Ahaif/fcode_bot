"""Explicit ClaimManager ownership — not proximity."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import EntityType, Environment, Position

import claim_manager
from assignment import claim_ore, clear_assignment, ore_owner, reset_ownership_registry_for_tests
from economy import ore_claimed_by_other, scan_ore


def setup_function() -> None:
    reset_ownership_registry_for_tests()


def test_claim_registers_owner() -> None:
    state = SimpleNamespace()
    ore = Position(4, 10)
    claim_ore(state, ore, owner_id=4)
    assert ore_owner(ore) == 4
    clear_assignment(state)
    assert ore_owner(ore) is None


def test_busy_builder_does_not_block_unowned_ore() -> None:
    s4 = SimpleNamespace()
    claim_ore(s4, Position(1, 11), owner_id=4)

    class Ct:
        def get_id(self) -> int:
            return 6

        def get_position(self, uid: int | None = None) -> Position:
            if uid == 4:
                return Position(2, 10)
            return Position(0, 11)

        def get_team(self, _uid: int | None = None) -> int:
            return 0

        def get_nearby_units(self):
            return [4, 6]

        def get_entity_type(self, uid: int = 0):
            return EntityType.BUILDER_BOT

        def read_store(self, _slot: int) -> int:
            return 0

        def write_store(self, _slot: int, _val: int) -> None:
            return None

    ore = Position(4, 10)
    assert ore_owner(ore) is None
    assert ore_claimed_by_other(Ct(), ore) is False  # type: ignore[arg-type]


def test_free_closer_builder_wins_tiebreak() -> None:
    class Ct:
        def get_id(self) -> int:
            return 8

        def get_position(self, uid: int | None = None) -> Position:
            if uid == 5:
                return Position(4, 9)
            return Position(0, 11)

        def get_team(self, _uid: int | None = None) -> int:
            return 0

        def get_nearby_units(self):
            return [5, 8]

        def get_entity_type(self, uid: int = 0):
            return EntityType.BUILDER_BOT

        def read_store(self, _slot: int) -> int:
            return 0

        def write_store(self, _slot: int, _val: int) -> None:
            return None

    assert ore_claimed_by_other(Ct(), Position(4, 10)) is True  # type: ignore[arg-type]


def test_owner_ignores_free_rival_tiebreak() -> None:
    s = SimpleNamespace()
    claim_ore(s, Position(5, 5), owner_id=5)

    class Ct:
        def get_id(self) -> int:
            return 5

        def get_position(self, uid: int | None = None) -> Position:
            if uid == 8:
                return Position(5, 6)
            return Position(3, 7)

        def get_team(self, _uid: int | None = None) -> int:
            return 0

        def get_nearby_units(self):
            return [5, 8]

        def get_entity_type(self, uid: int = 0):
            return EntityType.BUILDER_BOT

        def read_store(self, _slot: int) -> int:
            return 0

        def write_store(self, _slot: int, _val: int) -> None:
            return None

    assert ore_claimed_by_other(Ct(), Position(5, 5)) is False  # type: ignore[arg-type]


def test_owned_ore_blocks_others() -> None:
    s = SimpleNamespace()
    claim_ore(s, Position(6, 6), owner_id=3)

    class Ct:
        def get_id(self) -> int:
            return 5

        def get_position(self, uid: int | None = None) -> Position:
            if uid == 3:
                return Position(4, 5)
            return Position(3, 7)

        def get_team(self, _uid: int | None = None) -> int:
            return 0

        def get_nearby_units(self):
            return [3, 5]

        def get_entity_type(self, uid: int = 0):
            return EntityType.BUILDER_BOT

        def read_store(self, _slot: int) -> int:
            return 0

        def write_store(self, _slot: int, _val: int) -> None:
            return None

    assert ore_claimed_by_other(Ct(), Position(6, 6)) is True  # type: ignore[arg-type]


def test_scan_ore_skips_owned_keeps_unowned() -> None:
    s = SimpleNamespace()
    claim_ore(s, Position(5, 5), owner_id=3)

    class Ct:
        def get_id(self) -> int:
            return 10

        def get_position(self, uid: int | None = None) -> Position:
            if uid == 3:
                return Position(4, 5)
            if uid == 4:
                return Position(2, 10)
            return Position(2, 6)

        def get_team(self, _uid: int | None = None) -> int:
            return 0

        def get_nearby_units(self):
            return [3, 4, 10]

        def get_entity_type(self, uid: int = 0):
            return EntityType.BUILDER_BOT

        def get_nearby_tiles(self):
            return [Position(5, 5), Position(6, 6), Position(4, 10)]

        def get_tile_env(self, _p: Position):
            return Environment.ORE_TITANIUM

        def get_tile_building_id(self, _p: Position):
            return None

        def read_store(self, _slot: int) -> int:
            return 0

        def write_store(self, _slot: int, _val: int) -> None:
            return None

    claim_ore(SimpleNamespace(), Position(1, 11), owner_id=4)
    found = scan_ore(Ct())  # type: ignore[arg-type]
    assert found is not None
    assert (found.x, found.y) in {(6, 6), (4, 10)}
    assert (found.x, found.y) != (5, 5)


def test_manager_rejects_duplicate_owner_intent() -> None:
    ct_units = {8: Position(1, 4), 9: Position(2, 3)}

    class Ct:
        def __init__(self, my_id: int):
            self._id = my_id
            self._store = [0] * 16

        def get_id(self) -> int:
            return self._id

        def get_position(self, uid: int | None = None) -> Position:
            if uid is None:
                return ct_units[self._id]
            return ct_units[uid]

        def get_team(self, _uid: int | None = None) -> int:
            return 0

        def get_nearby_units(self):
            return [8, 9]

        def get_entity_type(self, uid: int = 0):
            return EntityType.BUILDER_BOT

        def get_tile_env(self, _p: Position):
            return Environment.ORE_TITANIUM

        def get_tile_building_id(self, _p: Position):
            return None

        def read_store(self, slot: int) -> int:
            return self._store[slot]

        def write_store(self, slot: int, val: int) -> None:
            self._store[slot] = val

    # Closer id=9 wins; id=8 must fail contest for (4,4).
    s8 = SimpleNamespace(assignment=None, claim_turns_alive=1)
    s9 = SimpleNamespace(assignment=None, claim_turns_alive=1)
    why8 = claim_manager.why_not_acquirable(Ct(8), s8, Position(4, 4))  # type: ignore[arg-type]
    why9 = claim_manager.why_not_acquirable(Ct(9), s9, Position(4, 4))  # type: ignore[arg-type]
    assert why8 == "contest_lost_to_9"
    assert why9 is None
