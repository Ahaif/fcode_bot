"""Stage 2 — reactive CORE_THREAT / ECONOMY_THREAT builder dispatch (defense.py)."""

from __future__ import annotations

from fcode import Direction, EntityType, Position

import claim_manager
import condition_state
import defense
import map_knowledge
from assignment import empty_assignment


class FakeCt:
    def __init__(
        self,
        *,
        my_id: int = 1,
        my_pos: Position = Position(5, 0),
        buildings: list[tuple[int, EntityType, int, Position]] | None = None,
        units: list[tuple[int, int, Position]] | None = None,
        resources: int = 10_000,
        gunner_cost: int = 10,
        store: dict[int, int] | None = None,
        healable_tiles: set[tuple[int, int]] | None = None,
        tile_buildings: dict[tuple[int, int], tuple[int, EntityType, int]] | None = None,
        blocked_fire_from: set[tuple[Position, Direction]] | None = None,
        blocked_fire_targets: set[Position] | None = None,
        directions: dict[int, Direction] | None = None,
        buildable: bool = True,
        round_: int = 10,
    ) -> None:
        self._blocked_fire_from = blocked_fire_from or set()
        self._blocked_fire_targets = blocked_fire_targets or set()
        self._directions = directions or {}
        self._buildable = buildable
        self._round = round_
        self._id = my_id
        self._pos = my_pos
        self._buildings = buildings or []
        self._units = units or []
        self._resources = resources
        self._gunner_cost = gunner_cost
        self._store = dict(store or {})
        self._store.setdefault(0, 0)
        self._healable = healable_tiles or set()
        self._tile_buildings = tile_buildings or {}
        self.built_gunners: list[tuple[Position, Direction]] = []
        self.built_sentinels: list[tuple[Position, Direction]] = []
        self.healed: list[Position] = []
        self.fired: list[Position] = []

    def get_id(self) -> int:
        return self._id

    def get_team(self, uid: int | None = None) -> int:
        if uid is None or uid == self._id:
            return 0
        for buid, _et, team, _pos in self._buildings:
            if buid == uid:
                return team
        for buid, team, _pos in self._units:
            if buid == uid:
                return team
        return 0

    def get_position(self, uid: int | None = None) -> Position:
        if uid is None or uid == self._id:
            return self._pos
        for buid, _et, _team, pos in self._buildings:
            if buid == uid:
                return pos
        for buid, _team, pos in self._units:
            if buid == uid:
                return pos
        return Position(0, 0)

    def get_entity_type(self, uid: int):
        for buid, et, _team, _pos in self._buildings:
            if buid == uid:
                return et
        return EntityType.BUILDER_BOT

    def get_nearby_buildings(self) -> list[int]:
        return [b[0] for b in self._buildings]

    def get_nearby_units(self) -> list[int]:
        return [u[0] for u in self._units]

    def get_current_round(self) -> int:
        return self._round

    def get_global_resources(self) -> int:
        return self._resources

    def get_unit_count(self) -> int:
        return 5

    def get_gunner_cost(self) -> int:
        return self._gunner_cost

    def get_action_cooldown(self) -> int:
        return 0

    def read_store(self, i: int) -> int:
        return self._store.get(i, 0)

    def write_store(self, i: int, v: int) -> None:
        self._store[i] = v

    def can_build_gunner(self, _pos: Position, _facing: Direction) -> bool:
        return self._buildable

    def build_gunner(self, pos: Position, facing: Direction) -> None:
        self.built_gunners.append((pos, facing))

    def get_sentinel_cost(self) -> int:
        return self._gunner_cost

    def can_build_sentinel(self, _pos: Position, _facing: Direction) -> bool:
        return self._buildable

    def build_sentinel(self, pos: Position, facing: Direction) -> None:
        self.built_sentinels.append((pos, facing))

    def can_fire_from(
        self, position: Position, direction: Direction, _turret_type, target: Position
    ) -> bool:
        if target in self._blocked_fire_targets:
            return False
        return (position, direction) not in self._blocked_fire_from

    def can_fire(self, tile: Position) -> bool:
        return (tile.x, tile.y) in self._tile_buildings

    def get_direction(self, uid: int) -> Direction:
        return self._directions.get(uid, Direction.NORTH)

    def fire(self, tile: Position) -> None:
        self.fired.append(tile)

    def get_tile_building_id(self, tile: Position) -> int | None:
        key = (tile.x, tile.y)
        if key in self._tile_buildings:
            return self._tile_buildings[key][0]
        return None

    def can_heal(self, tile: Position) -> bool:
        return (tile.x, tile.y) in self._healable

    def heal(self, tile: Position) -> None:
        self.healed.append(tile)


def _reset() -> None:
    condition_state.reset_for_tests()
    map_knowledge.reset_for_tests()
    claim_manager.reset_for_tests()


def test_core_threat_direction_buckets_cardinal() -> None:
    core = Position(10, 10)
    assert defense.core_threat_direction(core, Position(15, 10)) == Direction.EAST
    assert defense.core_threat_direction(core, Position(10, 5)) == Direction.NORTH


def test_target_turret_count_scales_with_armed_threats_same_direction() -> None:
    """Multiple armed enemies from roughly the same side still each need a
    turret's worth of firepower — bucketing by direction alone (the old v1
    behavior) undercounted a clustered rush. See docs/PROGRESS_TRACKER.md
    Phase 4 — real match feedback: 3 enemy turrets built from one side only
    got a single defender dispatched."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(0, 5),
        buildings=[
            (2, EntityType.GUNNER, 1, Position(3, 0)),  # east, dist_sq=9
            (3, EntityType.GUNNER, 1, Position(3, 1)),  # east-ish, dist_sq=10
        ],
    )
    assert defense.target_turret_count(ct, core) == 2  # type: ignore[arg-type]


def test_target_turret_count_counts_distinct_directions() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        buildings=[
            (2, EntityType.GUNNER, 1, Position(3, 0)),  # east
            (3, EntityType.GUNNER, 1, Position(0, 3)),  # south
        ],
    )
    assert defense.target_turret_count(ct, core) == 2  # type: ignore[arg-type]


def test_turrets_near_core_counts_only_within_ring() -> None:
    core = Position(0, 0)
    ct = FakeCt(
        buildings=[
            (2, EntityType.GUNNER, 0, Position(2, 0)),  # dist_sq=4, in ring (<=18)
            (3, EntityType.GUNNER, 0, Position(20, 0)),  # far, out of ring
            (4, EntityType.GUNNER, 1, Position(2, 1)),  # enemy — not ours
        ],
    )
    assert defense.turrets_near_core(ct, core) == 1  # type: ignore[arg-type]


def test_builders_needed_capped_at_max_from_trigger() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        buildings=[
            (2, EntityType.GUNNER, 1, Position(3, 0)),  # east
            (3, EntityType.GUNNER, 1, Position(0, 3)),  # south
            (4, EntityType.GUNNER, 1, Position(-3, 0)),  # west
            (5, EntityType.GUNNER, 1, Position(0, -3)),  # north
            (6, EntityType.GUNNER, 1, Position(3, 3)),  # southeast — 5th, over cap
        ],
    )
    assert defense.target_turret_count(ct, core) == 5  # type: ignore[arg-type]
    assert (
        defense.builders_needed_for_defense(ct, core)  # type: ignore[arg-type]
        == defense.DEFEND_BUILD_MAX_FROM_TRIGGER
    )


def test_should_enter_defend_build_ranks_nearest_first() -> None:
    _reset()
    core = Position(0, 0)
    # One distinct threat direction -> needed=1. Two free builders visible;
    # only the nearer one should enter.
    near = FakeCt(
        my_id=1,
        my_pos=Position(2, 0),
        buildings=[(9, EntityType.GUNNER, 1, Position(3, 0))],
        units=[(2, 0, Position(10, 0))],
    )
    far = FakeCt(
        my_id=2,
        my_pos=Position(10, 0),
        buildings=[(9, EntityType.GUNNER, 1, Position(3, 0))],
        units=[(1, 0, Position(2, 0))],
    )
    near_state = type("S", (), {"assignment": empty_assignment()})()
    far_state = type("S", (), {"assignment": empty_assignment()})()
    assert defense.should_enter_defend_build(near, near_state, core) is True  # type: ignore[arg-type]
    # condition_state caches get_nearby_* per round — near/far simulate two
    # different units' perspectives at the "same" round, which never
    # actually collide in the real engine (isolated module state per unit),
    # only in this shared test process. Reset the cache between them.
    _reset()
    assert defense.should_enter_defend_build(far, far_state, core) is False  # type: ignore[arg-type]


def test_should_enter_defend_build_false_when_busy() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        buildings=[(9, EntityType.GUNNER, 1, Position(3, 0))],
    )
    state = type("S", (), {"assignment": empty_assignment()})()
    state.assignment.ore_pos = Position(9, 9)  # mid mining task
    assert defense.should_enter_defend_build(ct, state, core) is False  # type: ignore[arg-type]


def test_core_in_immediate_danger_true_when_armed_threat_uncovered() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        buildings=[(9, EntityType.SENTINEL, 1, Position(3, 0))],
    )
    assert defense.core_in_immediate_danger(ct, core) is True  # type: ignore[arg-type]


def test_core_in_immediate_danger_false_when_already_covered() -> None:
    """A friendly sentinel already answering the threat — not a dire gap."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        buildings=[
            (9, EntityType.SENTINEL, 1, Position(3, 0)),  # enemy threat
            (8, EntityType.SENTINEL, 0, Position(0, 1)),  # friendly, covering
        ],
    )
    assert defense.core_in_immediate_danger(ct, core) is False  # type: ignore[arg-type]


def test_core_in_immediate_danger_true_for_uncovered_builder_rush() -> None:
    """A pure builder-only rush counts too, not just gunner/sentinel — a
    real match showed the Core destroyed purely by enemy builders while
    every friendly builder was busy mining: core_in_immediate_danger only
    checked armed threat kinds, so the interrupt path (the only one that
    can pull a *busy* builder off task) never triggered for it
    (2026-08-23)."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        units=[(9, 1, Position(4, 0))],  # enemy builder, dist_sq=16 <= 20
    )
    assert defense.core_in_immediate_danger(ct, core) is True  # type: ignore[arg-type]


def test_core_in_immediate_danger_false_for_covered_builder_rush() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        units=[(9, 1, Position(4, 0))],  # enemy builder
        buildings=[(8, EntityType.SENTINEL, 0, Position(0, 1))],  # friendly, covering
    )
    assert defense.core_in_immediate_danger(ct, core) is False  # type: ignore[arg-type]


def test_should_interrupt_for_core_defense_true_when_busy_and_dire() -> None:
    """A mid-mining builder is still allowed to break off when the Core is
    completely undefended against an armed threat — the one exception to
    'busy builders are never yanked' (2026-08-22: real match lost the Core
    in 60 rounds because every builder was busy and none were ever free)."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        buildings=[(9, EntityType.SENTINEL, 1, Position(3, 0))],
    )
    state = type("S", (), {"assignment": empty_assignment()})()
    state.assignment.ore_pos = Position(9, 9)  # mid mining task
    assert defense.should_interrupt_for_core_defense(ct, state, core) is True  # type: ignore[arg-type]


def test_should_interrupt_for_core_defense_false_when_already_covered() -> None:
    """A routine, already-answered threat must never yank a busy builder."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        buildings=[
            (9, EntityType.SENTINEL, 1, Position(3, 0)),  # enemy threat
            (8, EntityType.SENTINEL, 0, Position(0, 1)),  # friendly, covering
        ],
    )
    state = type("S", (), {"assignment": empty_assignment()})()
    state.assignment.ore_pos = Position(9, 9)  # mid mining task
    assert defense.should_interrupt_for_core_defense(ct, state, core) is False  # type: ignore[arg-type]


def test_defend_build_step_exits_when_threat_cleared() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(my_pos=Position(0, 1))
    state = type("S", (), {})()
    assert defense.defend_build_step(ct, state, core, avoid=None) is True  # type: ignore[arg-type]
    assert ct.built_sentinels == []


def test_defend_build_step_builds_facing_threat() -> None:
    """The reactive CORE_THREAT response builds a sentinel, not a gunner."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        # Within DEFEND_THREAT_APPROACH_RANGE_SQ of the threat, diagonal
        # rather than orth-adjacent so none of the builder's 4 candidate
        # tiles coincides with the threat's own tile (which would degenerate
        # facing_toward to its CENTRE fallback instead of a real direction).
        my_pos=Position(3, -1),
        buildings=[(9, EntityType.GUNNER, 1, Position(4, 0))],  # east threat
    )
    state = type("S", (), {})()
    assert defense.defend_build_step(ct, state, core, avoid=None) is True  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1
    tile, facing = ct.built_sentinels[0]
    # Should face toward the threat (east), not away from Core.
    assert facing == Direction.EAST


def test_defend_build_step_avoids_placement_that_cannot_actually_hit() -> None:
    """facing_toward() alone only picks the geometrically closest cardinal —
    it never confirms the resulting turret could actually land a hit. A
    wall between the "obvious" tile/facing and the threat looked identical
    to facing_toward: real match evidence showed a sentinel built pointing
    at empty air while the threat sat behind an obstruction. The fix uses
    the engine's own can_fire_from() to verify before committing, so it
    must skip a geometrically-nearest tile that can't actually connect."""
    _reset()
    core = Position(0, 0)
    # Builder at (3,-1), threat east at (4,0) — the nearest/obvious tile is
    # (3,0) facing EAST (see test_defend_build_step_builds_facing_threat).
    # Block every facing from (3,0): can_fire_from must never validate that
    # tile no matter which direction, forcing a different tile.
    blocked = {(Position(3, 0), d) for d in (Direction.NORTH, Direction.EAST, Direction.SOUTH, Direction.WEST)}
    ct = FakeCt(
        my_pos=Position(3, -1),
        buildings=[(9, EntityType.GUNNER, 1, Position(4, 0))],
        blocked_fire_from=blocked,
    )
    state = type("S", (), {})()
    assert defense.defend_build_step(ct, state, core, avoid=None) is True  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1
    tile, _facing = ct.built_sentinels[0]
    assert tile != Position(3, 0), "should not settle for a placement verified unable to hit the target"


def test_defend_build_step_targets_blocking_barrier_when_threat_unreachable() -> None:
    """When every ring placement's can_fire_from fails against the real
    armed threat (e.g. it sits fully behind a barrier wall), aim at the
    wall itself instead of falling back to first_buildable_placement's
    unverified guess. A verified hit on the barrier is a real response — it
    starts clearing the lane — where the old blind fallback built a turret
    that could never actually hit anything, which real match replays showed
    as a "wrong direction" sentinel doing nothing against the threat behind
    the wall."""
    _reset()
    core = Position(0, 0)
    threat_pos = Position(4, 0)
    barrier_pos = Position(3, 0)
    ct = FakeCt(
        my_pos=Position(3, -1),
        buildings=[
            (9, EntityType.GUNNER, 1, threat_pos),
            (8, EntityType.BARRIER, 1, barrier_pos),
        ],
        blocked_fire_targets={threat_pos},
    )
    state = type("S", (), {})()
    assert defense.defend_build_step(ct, state, core, avoid=None) is True  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1


def test_defend_build_step_builds_weapon_before_meleeing_adjacent_threat() -> None:
    """A weapon (turret) is always tried before melee, even when already
    orthogonally adjacent to the threat — a builder deals only 2 damage per
    2 Ti per hit, far too slow/costly against a live 30-40 HP gunner or
    sentinel compared to just placing one of our own. Melee is a last
    resort (see the buildable=False test below), not the default."""
    _reset()
    core = Position(0, 0)
    threat_pos = Position(3, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),  # orth-adjacent to threat_pos
        buildings=[(9, EntityType.SENTINEL, 1, threat_pos)],
        tile_buildings={(3, 0): (9, EntityType.SENTINEL, 1)},
    )
    state = type("S", (), {})()
    assert defense.defend_build_step(ct, state, core, avoid=None) is True  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1
    assert ct.fired == []


def test_defend_build_step_melees_enemy_builder_before_building_weapon() -> None:
    """The opposite order from the gunner/sentinel test above, and
    deliberately so: an enemy builder is weak and melee is adjacency-only,
    so it can never be blinded by a friendly conveyor/unit in the line the
    way a fixed-facing sentinel can. Real match evidence: the Core was
    destroyed purely by an enemy builder rush across four maps in one
    match, with an already-built sentinel sitting right there unable to
    fire because a friendly conveyor was in its line — building yet
    another turret is strictly worse here than meleeing the weak threat
    immediately."""
    _reset()
    core = Position(0, 0)
    threat_pos = Position(3, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),  # orth-adjacent to threat_pos
        units=[(9, 1, threat_pos)],
        tile_buildings={(3, 0): (9, EntityType.BUILDER_BOT, 1)},
    )
    state = type("S", (), {})()
    assert defense.defend_build_step(ct, state, core, avoid=None) is False  # type: ignore[arg-type]
    assert ct.fired == [threat_pos]
    assert ct.built_sentinels == []
    assert ct.built_gunners == []


def test_defend_build_step_never_builds_sentinel_for_builder_threat_even_when_not_yet_adjacent() -> None:
    """A mobile builder threat within DEFEND_THREAT_APPROACH_RANGE_SQ but
    not yet literally adjacent used to fall through to sentinel-placement
    (verified against wherever the threat was *that instant*) — by the
    time the sentinel was actually standing, the threat had walked past its
    fixed line of fire. Real match evidence, 2026-08-23: two sentinels
    built near an approaching builder rush, both verified at build time,
    neither ever fired, Core destroyed anyway. THREAT_BUILDER must never
    reach sentinel-placement at all, only approach-then-melee."""
    _reset()
    core = Position(0, 0)
    threat_pos = Position(3, 1)  # dist_sq to (2,0) is 2 — in range, not adjacent
    ct = FakeCt(
        my_pos=Position(2, 0),
        units=[(9, 1, threat_pos)],
    )
    state = type("S", (), {})()
    # step_to() needs full navigator support this minimal FakeCt doesn't
    # provide — the AttributeError itself proves it went straight to
    # approaching the threat, never attempting sentinel placement first.
    try:
        defense.defend_build_step(ct, state, core, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert ct.built_sentinels == []
    assert ct.fired == []


def test_defend_build_step_melees_adjacent_threat_only_when_build_fails() -> None:
    """An enemy turret placed directly next to our Core used to sit there
    unanswered — the only response was trying to build our own turret
    nearby, never simply removing it. Melee is the fallback once every
    weapon option has genuinely failed (e.g. no legal tile to build from at
    all), not the first response."""
    _reset()
    core = Position(0, 0)
    threat_pos = Position(3, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),  # orth-adjacent to threat_pos
        buildings=[(9, EntityType.SENTINEL, 1, threat_pos)],
        tile_buildings={(3, 0): (9, EntityType.SENTINEL, 1)},
        buildable=False,
    )
    state = type("S", (), {})()
    assert defense.defend_build_step(ct, state, core, avoid=None) is False  # type: ignore[arg-type]
    assert ct.fired == [threat_pos]
    assert ct.built_sentinels == []
    assert ct.built_gunners == []


def test_builders_needed_returns_one_when_existing_turret_cannot_hit_threat() -> None:
    """A turret still standing doesn't mean it's still useful — a friendly
    unit can wander into its fixed line after construction and block it
    completely (sentinels can't rotate at all). turrets_near_core alone
    used to make this look like sufficient coverage forever, with nothing
    ever dispatched to actually deal with the threat again."""
    _reset()
    core = Position(0, 0)
    threat_pos = Position(3, 0)
    ct = FakeCt(
        buildings=[
            (2, EntityType.GUNNER, 1, threat_pos),
            (9, EntityType.SENTINEL, 0, Position(1, 0)),  # existing friendly turret, in ring
        ],
        blocked_fire_targets={threat_pos},  # nothing can verify a hit on it
    )
    assert defense.target_turret_count(ct, core) == 1  # type: ignore[arg-type]
    assert defense.turrets_near_core(ct, core) == 1  # type: ignore[arg-type]
    assert defense.builders_needed_for_defense(ct, core) == 1  # type: ignore[arg-type]


def test_builders_needed_stays_zero_when_existing_turret_can_hit_threat() -> None:
    """Control for the test above — an existing turret that *can* still
    verify a hit is real coverage; no redundant dispatch."""
    _reset()
    core = Position(0, 0)
    threat_pos = Position(3, 0)
    ct = FakeCt(
        buildings=[
            (2, EntityType.GUNNER, 1, threat_pos),
            (9, EntityType.SENTINEL, 0, Position(1, 0)),
        ],
    )
    assert defense.builders_needed_for_defense(ct, core) == 0  # type: ignore[arg-type]


def test_defend_build_step_advances_toward_threat_when_build_fully_fails() -> None:
    """When every ring tile fails to build (not just fails to verify a hit —
    genuinely can't build at all, e.g. no legal tile), don't just sit still
    repeating the same failed attempt forever — advance toward the threat so
    it eventually falls into melee range instead, which has no line-of-fire
    concept at all."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(
        my_pos=Position(2, 0),
        buildings=[(9, EntityType.GUNNER, 1, Position(4, 0))],
        buildable=False,
    )
    state = type("S", (), {})()
    # step_to() needs full navigator support this minimal FakeCt doesn't
    # provide — asserting the AttributeError itself is the point here: it
    # proves defend_build_step actually attempted to move instead of just
    # silently returning False and sitting still (the old, now-fixed
    # behavior would have raised nothing at all).
    raised = False
    try:
        defense.defend_build_step(ct, state, core, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        raised = True
    assert raised, "expected defend_build_step to attempt step_to() toward the threat"
    assert ct.built_sentinels == []
    assert ct.built_gunners == []


def test_economy_defend_step_returns_false_when_nothing_threatened() -> None:
    _reset()
    ct = FakeCt(my_pos=Position(0, 0))
    state = type("S", (), {})()
    assert defense.economy_defend_step(ct, state, avoid=None) is False  # type: ignore[arg-type]


def test_economy_defend_step_repairs_adjacent_damaged_structure() -> None:
    _reset()
    ct = FakeCt(
        my_pos=Position(1, 0),
        buildings=[
            (5, EntityType.HARVESTER, 0, Position(0, 0)),
            (6, EntityType.GUNNER, 1, Position(2, 0)),  # enemy within radius
        ],
        tile_buildings={(0, 0): (5, EntityType.HARVESTER, 0)},
        healable_tiles={(0, 0)},
    )
    state = type("S", (), {})()
    assert defense.economy_defend_step(ct, state, avoid=None) is True  # type: ignore[arg-type]
    assert ct.healed == [Position(0, 0)]


def test_economy_defend_step_destroys_adjacent_blocking_barrier() -> None:
    """A real match showed the enemy silently cutting off an already-
    completed belt route by boxing in a harvester with barriers — the
    response is to clear the barrier the same way an adjacent threat's
    repair target would be, not just repair HP damage."""
    _reset()
    ct = FakeCt(
        my_pos=Position(1, 0),
        buildings=[
            (5, EntityType.HARVESTER, 0, Position(0, 0)),
            (6, EntityType.BARRIER, 1, Position(2, 0)),  # blocks the route, no return fire
        ],
        tile_buildings={
            (0, 0): (5, EntityType.HARVESTER, 0),
            (2, 0): (6, EntityType.BARRIER, 1),
        },
    )
    state = type("S", (), {})()
    assert defense.economy_defend_step(ct, state, avoid=None) is True  # type: ignore[arg-type]
    assert ct.fired == [Position(2, 0)]
    assert ct.healed == []

