"""condition_state.py — SAFE/CORE_THREAT/ECONOMY_THREAT/STARVATION dispatcher."""

from __future__ import annotations

from fcode import Direction, EntityType, Position

import condition_state
import map_knowledge


class FakeCt:
    def __init__(
        self,
        *,
        round_: int = 10,
        resources: int = 1000,
        unit_count: int = 5,
        own_core: Position = Position(1, 1),
        buildings: list[tuple[int, EntityType, int, Position]] | None = None,
        units: list[tuple[int, int, Position]] | None = None,
        store: dict[int, int] | None = None,
        directions: dict[int, Direction] | None = None,
        hittable_from: set[tuple[Position, Direction]] | None = None,
    ) -> None:
        self._round = round_
        self._resources = resources
        self._unit_count = unit_count
        self._own_core = own_core
        # (uid, entity_type, team, pos)
        self._buildings = buildings or []
        # (uid, team, pos) — builder bots only
        self._units = units or []
        self._store = dict(store or {})
        self._store.setdefault(0, 0)
        self._directions = directions or {}
        self._hittable_from = hittable_from or set()

    def get_direction(self, uid: int) -> Direction:
        return self._directions.get(uid, Direction.NORTH)

    def can_fire_from(self, position: Position, direction, _turret_type, _target) -> bool:
        return (position, direction) in self._hittable_from

    def get_id(self) -> int:
        return 0

    def get_team(self, uid: int | None = None) -> int:
        if uid is None:
            return 0
        for buid, _et, team, _pos in self._buildings:
            if buid == uid:
                return team
        for buid, team, _pos in self._units:
            if buid == uid:
                return team
        return 0

    def get_current_round(self) -> int:
        return self._round

    def get_global_resources(self) -> int:
        return self._resources

    def get_unit_count(self) -> int:
        return self._unit_count

    def get_nearby_buildings(self) -> list[int]:
        return [b[0] for b in self._buildings]

    def get_nearby_units(self) -> list[int]:
        return [u[0] for u in self._units]

    def get_entity_type(self, uid: int):
        for buid, et, _team, _pos in self._buildings:
            if buid == uid:
                return et
        return EntityType.BUILDER_BOT

    def get_position(self, uid: int | None = None) -> Position:
        if uid is None:
            return self._own_core
        for buid, _et, _team, pos in self._buildings:
            if buid == uid:
                return pos
        for buid, _team, pos in self._units:
            if buid == uid:
                return pos
        return Position(0, 0)

    def read_store(self, i: int) -> int:
        return self._store.get(i, 0)

    def write_store(self, i: int, v: int) -> None:
        self._store[i] = v


def _reset() -> None:
    condition_state.reset_for_tests()
    map_knowledge.reset_for_tests()


def _write_own_core(ct: FakeCt, pos: Position) -> None:
    from team_store import write_own_core

    write_own_core(ct, pos)  # type: ignore[arg-type]


def test_safe_when_nothing_nearby() -> None:
    _reset()
    core = Position(5, 5)
    ct = FakeCt(own_core=core)
    _write_own_core(ct, core)
    assert condition_state.get_condition_state(ct) == condition_state.SAFE  # type: ignore[arg-type]


def test_core_threat_from_visible_enemy_gunner() -> None:
    _reset()
    core = Position(10, 10)
    ct = FakeCt(
        own_core=core,
        buildings=[(1, EntityType.GUNNER, 1, Position(11, 10))],  # dist_sq=1 <= 13
    )
    _write_own_core(ct, core)
    assert (
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        == condition_state.CORE_THREAT
    )


def test_core_threat_from_visible_enemy_sentinel_far_but_in_range() -> None:
    _reset()
    core = Position(0, 0)
    # sentinel r_sq = 32, distance_sq(5,5) = 50 -> out of range; (5,3)=34 out; (5,2)=29 in range
    ct = FakeCt(
        own_core=core,
        buildings=[(1, EntityType.SENTINEL, 1, Position(5, 2))],
    )
    _write_own_core(ct, core)
    assert (
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        == condition_state.CORE_THREAT
    )


def test_no_core_threat_when_gunner_out_of_range() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        own_core=core,
        buildings=[(1, EntityType.GUNNER, 1, Position(20, 20))],
    )
    _write_own_core(ct, core)
    assert condition_state.get_condition_state(ct) == condition_state.SAFE  # type: ignore[arg-type]


def test_core_threat_via_authoritative_fire_check_beyond_proxy_radius() -> None:
    """CORE_THREAT_RANGE_SQ_GUNNER/_SENTINEL are only a proxy (reused
    vision-radius numbers — the engine publishes no real attack-range
    constant separate from vision, see AGENTS.md). A gunner farther than
    that proxy radius but still able to actually hit the Core (verified via
    the engine's own can_fire_from with its real current facing) must still
    register as CORE_THREAT — a match replay showed the Core destroyed by
    exactly this kind of undetected threat sitting just past the guess."""
    _reset()
    core = Position(0, 0)
    gunner_pos = Position(20, 0)  # dist_sq=400, far past CORE_THREAT_RANGE_SQ_GUNNER=13
    ct = FakeCt(
        own_core=core,
        buildings=[(1, EntityType.GUNNER, 1, gunner_pos)],
        directions={1: Direction.WEST},
        hittable_from={(gunner_pos, Direction.WEST)},
    )
    _write_own_core(ct, core)
    assert (
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        == condition_state.CORE_THREAT
    )


def test_core_threat_from_sighting_slot() -> None:
    _reset()
    core = Position(0, 0)
    # Sighting: gunner (_TYPE_GUNNER=3 in map_knowledge) at (2,2), dist_sq=8 <= 13
    packed = map_knowledge.pack_sighting(2, 2, map_knowledge._TYPE_GUNNER, 9)
    ct = FakeCt(own_core=core, store={1: packed})
    _write_own_core(ct, core)
    assert (
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        == condition_state.CORE_THREAT
    )


def test_core_threat_from_adjacent_enemy_builder() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        own_core=core,
        units=[(1, 1, Position(2, 0))],  # orth-adjacent to Core footprint tile (1,0)
    )
    _write_own_core(ct, core)
    assert (
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        == condition_state.CORE_THREAT
    )


def test_economy_threat_when_enemy_near_friendly_harvester() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        own_core=core,
        buildings=[
            (1, EntityType.HARVESTER, 0, Position(20, 20)),
            (2, EntityType.GUNNER, 1, Position(23, 20)),  # dist_sq=9 <= 50
        ],
    )
    _write_own_core(ct, core)
    assert (
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        == condition_state.ECONOMY_THREAT
    )


def test_economy_threat_when_enemy_barrier_blocks_a_belt_route() -> None:
    """A real match showed the enemy silently cutting off an already-
    completed belt route by boxing in a harvester with barriers built well
    away from our own Core — nothing detected this at all before, since
    every other trigger here was an armed unit or a turret."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        own_core=core,
        buildings=[
            (1, EntityType.HARVESTER, 0, Position(20, 20)),
            (2, EntityType.BARRIER, 1, Position(21, 20)),  # dist_sq=1 <= 50
        ],
    )
    _write_own_core(ct, core)
    assert (
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        == condition_state.ECONOMY_THREAT
    )


def test_no_economy_threat_when_enemy_far_from_structures() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        own_core=core,
        buildings=[
            (1, EntityType.HARVESTER, 0, Position(20, 20)),
            (2, EntityType.GUNNER, 1, Position(50, 50)),
        ],
    )
    _write_own_core(ct, core)
    assert condition_state.get_condition_state(ct) == condition_state.SAFE  # type: ignore[arg-type]


def test_starvation_after_n_consecutive_low_rounds() -> None:
    _reset()
    core = Position(0, 0)
    for r in range(5):
        ct = FakeCt(own_core=core, round_=r, resources=0, unit_count=1)
        _write_own_core(ct, core)
        state = condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        if r < condition_state.STARVATION_ROUNDS - 1:
            assert state != condition_state.STARVATION, f"round {r} tripped early"
        else:
            assert state == condition_state.STARVATION


def test_starvation_streak_resets_on_recovery() -> None:
    _reset()
    core = Position(0, 0)
    for r in range(4):
        ct = FakeCt(own_core=core, round_=r, resources=0, unit_count=1)
        _write_own_core(ct, core)
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]

    ct = FakeCt(own_core=core, round_=4, resources=10_000, unit_count=1)
    _write_own_core(ct, core)
    assert condition_state.get_condition_state(ct) != condition_state.STARVATION  # type: ignore[arg-type]

    for r in range(5, 9):
        ct = FakeCt(own_core=core, round_=r, resources=0, unit_count=1)
        _write_own_core(ct, core)
        state = condition_state.get_condition_state(ct)  # type: ignore[arg-type]
        assert state != condition_state.STARVATION, f"round {r} tripped too early after reset"


def test_starvation_outranks_core_threat() -> None:
    _reset()
    core = Position(0, 0)
    last_state = None
    for r in range(condition_state.STARVATION_ROUNDS):
        ct = FakeCt(
            own_core=core,
            round_=r,
            resources=0,
            unit_count=1,
            buildings=[(1, EntityType.GUNNER, 1, Position(1, 0))],
        )
        _write_own_core(ct, core)
        last_state = condition_state.get_condition_state(ct)  # type: ignore[arg-type]
    assert last_state == condition_state.STARVATION


def test_repeated_call_same_round_does_not_double_count_streak() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(own_core=core, round_=0, resources=0, unit_count=1)
    _write_own_core(ct, core)
    for _ in range(condition_state.STARVATION_ROUNDS + 2):
        condition_state.get_condition_state(ct)  # type: ignore[arg-type]
    assert condition_state._low_resource_streak == 1
