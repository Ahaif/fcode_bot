"""ClaimManager — single ownership protocol."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import EntityType, Environment, Position

import claim_manager
import map_knowledge
from assignment import get_assignment


def setup_function() -> None:
    claim_manager.reset_for_tests()
    map_knowledge.reset_for_tests()


def _state(**kwargs: object) -> SimpleNamespace:
    s = SimpleNamespace(
        assignment=None,
        claim_turns_alive=1,
        free_turns=0,
        claim_owner_id=-1,
        approach_stand=None,
        build_plan=None,
        build_index=0,
    )
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


class _BaseCt:
    def __init__(self, my_id: int, pos: Position, units: dict[int, Position] | None = None):
        self._id = my_id
        self._pos = pos
        self._units = units or {my_id: pos}
        self._store = [0] * 16

    def get_id(self) -> int:
        return self._id

    def get_position(self, uid: int | None = None) -> Position:
        if uid is None:
            return self._pos
        return self._units[uid]

    def get_team(self, _uid: int | None = None) -> int:
        return 0

    def get_nearby_units(self):
        return list(self._units)

    def get_entity_type(self, uid: int = 0):
        return EntityType.BUILDER_BOT

    def read_store(self, slot: int) -> int:
        return self._store[slot]

    def write_store(self, slot: int, val: int) -> None:
        self._store[slot] = val

    def get_nearby_tiles(self):
        return [Position(4, 4), Position(5, 5)]

    def get_tile_env(self, _p: Position):
        return Environment.ORE_TITANIUM

    def get_tile_building_id(self, _p: Position):
        return None


def test_acquire_atomic_cache_and_table() -> None:
    ct = _BaseCt(3, Position(0, 0), {3: Position(0, 0)})
    state = _state()
    claim_manager.begin_turn(ct, state)  # type: ignore[arg-type]
    state.claim_turns_alive = 1
    res = claim_manager.acquire(ct, state, Position(4, 4))  # type: ignore[arg-type]
    assert res.ok
    assert get_assignment(state).ore_pos == Position(4, 4)
    assert claim_manager.owner_of(Position(4, 4), ct) == 3  # type: ignore[arg-type]


class _OrphanCt:
    """A friendly harvester at ``harvester_tile``, optionally with a belt
    tile at one of its orthogonal neighbours."""

    def __init__(
        self,
        my_id: int,
        pos: Position,
        *,
        harvester_tile: Position,
        belt_tile: Position | None = None,
    ) -> None:
        self._id = my_id
        self._pos = pos
        self._store = [0] * 16
        self._harvester_tile = harvester_tile
        self._belt_tile = belt_tile

    def get_id(self) -> int:
        return self._id

    def get_position(self, uid: int | None = None) -> Position:
        return self._pos

    def get_team(self, _uid: int | None = None) -> int:
        return 0

    def get_nearby_units(self):
        return [self._id]

    def get_entity_type(self, uid: int = 0):
        if uid == 100:
            return EntityType.HARVESTER
        if uid == 101:
            return EntityType.CONVEYOR
        return EntityType.BUILDER_BOT

    def read_store(self, slot: int) -> int:
        return self._store[slot]

    def write_store(self, slot: int, val: int) -> None:
        self._store[slot] = val

    def get_nearby_tiles(self):
        return [self._harvester_tile]

    def get_tile_env(self, _p: Position):
        return Environment.ORE_TITANIUM

    def get_tile_building_id(self, p: Position):
        if (p.x, p.y) == (self._harvester_tile.x, self._harvester_tile.y):
            return 100
        if self._belt_tile is not None and (p.x, p.y) == (self._belt_tile.x, self._belt_tile.y):
            return 101
        return None


def test_orphan_harvester_visible_when_no_belt_started() -> None:
    ct = _OrphanCt(3, Position(4, 5), harvester_tile=Position(4, 4))
    assert claim_manager.visible_orphan_harvesters(ct) == [Position(4, 4)]  # type: ignore[arg-type]


def test_harvester_with_belt_neighbour_is_not_orphan() -> None:
    ct = _OrphanCt(
        3, Position(4, 6), harvester_tile=Position(4, 4), belt_tile=Position(5, 4)
    )
    assert claim_manager.visible_orphan_harvesters(ct) == []  # type: ignore[arg-type]


def test_adopt_orphan_harvester_claims_it_and_sets_has_harvester() -> None:
    ct = _OrphanCt(3, Position(4, 5), harvester_tile=Position(4, 4))
    state = _state(claim_turns_alive=1)
    res = claim_manager.try_adopt_orphan_harvester(ct, state)  # type: ignore[arg-type]
    assert res.ok
    assert res.ore == Position(4, 4)
    assert get_assignment(state).ore_pos == Position(4, 4)
    claim_manager.note_harvester(state, Position(4, 4))
    assert get_assignment(state).has_harvester


def test_adopt_orphan_harvester_respects_exclude() -> None:
    ct = _OrphanCt(3, Position(4, 5), harvester_tile=Position(4, 4))
    state = _state(claim_turns_alive=1)
    res = claim_manager.try_adopt_orphan_harvester(  # type: ignore[arg-type]
        ct, state, exclude=Position(4, 4)
    )
    assert not res.ok
    assert res.reason == "no_orphan_harvester"


def test_second_acquire_fails_while_owning() -> None:
    ct = _BaseCt(3, Position(0, 0))
    state = _state(claim_turns_alive=1)
    assert claim_manager.acquire(ct, state, Position(4, 4)).ok  # type: ignore[arg-type]
    res = claim_manager.acquire(ct, state, Position(5, 5))  # type: ignore[arg-type]
    assert not res.ok
    assert res.reason == "already_owning"
    assert claim_manager.owner_of(Position(4, 4), ct) == 3  # type: ignore[arg-type]
    assert claim_manager.owner_of(Position(5, 5), ct) is None  # type: ignore[arg-type]


def test_release_clears_both_sides() -> None:
    ct = _BaseCt(3, Position(0, 0))
    state = _state(claim_turns_alive=1)
    claim_manager.acquire(ct, state, Position(4, 4))  # type: ignore[arg-type]
    claim_manager.release(ct, state, reason="test")  # type: ignore[arg-type]
    assert get_assignment(state).ore_pos is None
    assert claim_manager.owner_of(Position(4, 4), ct) is None  # type: ignore[arg-type]


def test_busy_builder_does_not_win_contest_for_other_ore() -> None:
    claim_manager._TABLE[(1, 11)] = 4
    ct = _BaseCt(
        6,
        Position(0, 11),
        {6: Position(0, 11), 4: Position(2, 10)},
    )
    state = _state(claim_turns_alive=1)
    assert claim_manager.why_not_acquirable(ct, state, Position(4, 4)) is None  # type: ignore[arg-type]
    assert claim_manager.acquire(ct, state, Position(4, 4)).ok  # type: ignore[arg-type]


def test_closer_free_rival_wins_contest() -> None:
    ct = _BaseCt(
        8,
        Position(0, 11),
        {8: Position(0, 11), 5: Position(4, 9)},
    )
    state = _state(claim_turns_alive=1)
    why = claim_manager.why_not_acquirable(ct, state, Position(4, 10))  # type: ignore[arg-type]
    assert why == "contest_lost_to_5"


def test_spawn_turn_gate() -> None:
    ct = _BaseCt(9, Position(2, 3))
    state = _state(claim_turns_alive=0)
    why = claim_manager.why_not_acquirable(ct, state, Position(4, 4))  # type: ignore[arg-type]
    assert why == "spawn_turn_gate"


def test_hungry_builder_beats_closer_fresh_rival() -> None:
    """Anti-starvation: long-idle builder wins over closer free rival."""
    ct = _BaseCt(
        5,
        Position(0, 0),
        {5: Position(0, 0), 8: Position(4, 4)},
    )
    state = _state(claim_turns_alive=1, free_turns=claim_manager.HUNGER_TURNS)
    # Publish rival as free with low age.
    claim_manager._FREE_AGES[8] = 0
    why = claim_manager.why_not_acquirable(ct, state, Position(4, 4))  # type: ignore[arg-type]
    assert why is None
    assert claim_manager.acquire(ct, state, Position(4, 4)).ok  # type: ignore[arg-type]


def test_lower_id_wins_table_race() -> None:
    """Higher id cannot overwrite lower id's claim for the same ore."""
    ct8 = _BaseCt(8, Position(0, 0), {8: Position(0, 0), 3: Position(1, 1)})
    ct8._store = [0] * 16
    # Pre-seed lower id claim on shared store image.
    claim_manager._TABLE[(4, 4)] = 3
    ct8.write_store(claim_manager._slot(3), claim_manager._pack_claim(3, Position(4, 4)))
    state = _state(claim_turns_alive=1)
    res = claim_manager.acquire(ct8, state, Position(4, 4))  # type: ignore[arg-type]
    assert not res.ok
    assert get_assignment(state).ore_pos is None
    assert claim_manager.owner_of(Position(4, 4), ct8) == 3  # type: ignore[arg-type]


def test_try_acquire_logs_rejects() -> None:
    claim_manager._TABLE[(4, 4)] = 3
    ct = _BaseCt(
        8,
        Position(0, 0),
        {8: Position(0, 0), 5: Position(5, 5)},
    )
    state = _state(claim_turns_alive=1)
    res = claim_manager.try_acquire_work(ct, state)  # type: ignore[arg-type]
    assert res.rejects or res.ok
    log = claim_manager.last_acquire_log(state)
    assert isinstance(log, list)


def test_same_ore_two_free_only_winner_acquires() -> None:
    """Simultaneous competition: only contest winner may hold Assignment."""
    store = [0] * 16
    units = {8: Position(0, 11), 5: Position(4, 9)}

    class Ct(_BaseCt):
        def __init__(self, my_id: int):
            super().__init__(my_id, units[my_id], units)
            self._store = store

    s5 = _state(claim_turns_alive=1)
    s8 = _state(claim_turns_alive=1)
    r5 = claim_manager.acquire(Ct(5), s5, Position(4, 10))  # type: ignore[arg-type]
    r8 = claim_manager.acquire(Ct(8), s8, Position(4, 10))  # type: ignore[arg-type]
    assert r5.ok
    assert not r8.ok
    assert get_assignment(s5).ore_pos == Position(4, 10)
    assert get_assignment(s8).ore_pos is None
