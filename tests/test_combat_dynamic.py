"""Stage 3 — one capped attack-dedicated builder (combat.py dispatch/recall)."""

from __future__ import annotations

from fcode import EntityType, Position

import claim_manager
import combat
import condition_state
import map_knowledge
import team_store
from assignment import empty_assignment


class FakeCt:
    def __init__(
        self,
        *,
        my_id: int = 1,
        my_pos: Position = Position(5, 0),
        hp: int = 40,
        max_hp: int = 40,
        resources: int = 10_000,
        builder_cost: int = 30,
        units: list[tuple[int, int, Position]] | None = None,
        buildings: list[tuple[int, EntityType, int, Position]] | None = None,
        tile_buildings: dict[tuple[int, int], int] | None = None,
        store: dict[int, int] | None = None,
        sentinel_cost: int = 30,
        blocked_fire_targets: set[Position] | None = None,
        round_: int = 10,
        barrier_supported: bool = False,
        sentinel_buildable: bool = True,
    ) -> None:
        self._barrier_supported = barrier_supported
        self._sentinel_buildable = sentinel_buildable
        self.built_barriers: list[Position] = []
        self._round = round_
        self._id = my_id
        self._pos = my_pos
        self._hp = hp
        self._max_hp = max_hp
        self._resources = resources
        self._builder_cost = builder_cost
        self._units = units or []
        self._buildings = buildings or []
        self._tile_buildings = tile_buildings or {}
        self._store = dict(store or {})
        self._sentinel_cost = sentinel_cost
        self._blocked_fire_targets = blocked_fire_targets or set()
        self.built_sentinels: list[tuple[Position, object]] = []
        self.built_launchers: list[Position] = []
        self.fired: list[Position] = []

    def can_fire(self, _tile: Position) -> bool:
        return True

    def fire(self, tile: Position) -> None:
        self.fired.append(tile)

    def get_action_cooldown(self) -> int:
        return 0

    def get_move_cooldown(self) -> int:
        return 0

    def get_sentinel_cost(self) -> int:
        return self._sentinel_cost

    def can_build_sentinel(self, _pos: Position, _facing) -> bool:
        return self._sentinel_buildable

    def build_sentinel(self, pos: Position, facing) -> None:
        self.built_sentinels.append((pos, facing))

    def get_barrier_cost(self) -> int:
        return 3

    def can_build_barrier(self, _pos: Position) -> bool:
        return self._barrier_supported

    def build_barrier(self, pos: Position) -> None:
        self.built_barriers.append(pos)

    def get_launcher_cost(self) -> int:
        return 20

    def can_build_launcher(self, _pos: Position) -> bool:
        return True

    def build_launcher(self, pos: Position) -> None:
        self.built_launchers.append(pos)

    def can_fire_from(self, _position, _direction, _turret_type, target) -> bool:
        return target not in self._blocked_fire_targets

    def get_nearby_buildings(self) -> list[int]:
        return [b[0] for b in self._buildings]

    def is_in_vision(self, _pos: Position) -> bool:
        return True

    def get_tile_building_id(self, tile: Position) -> int | None:
        return self._tile_buildings.get((tile.x, tile.y))

    def get_map_width(self) -> int:
        return 20

    def get_map_height(self) -> int:
        return 20

    def get_id(self) -> int:
        return self._id

    def get_current_round(self) -> int:
        return self._round

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

    def get_nearby_units(self) -> list[int]:
        return [u[0] for u in self._units]

    def get_hp(self) -> int:
        return self._hp

    def get_max_hp(self) -> int:
        return self._max_hp

    def get_global_resources(self) -> int:
        return self._resources

    def get_builder_bot_cost(self) -> int:
        return self._builder_cost

    def read_store(self, i: int) -> int:
        return self._store.get(i, 0)

    def write_store(self, i: int, v: int) -> None:
        self._store[i] = v


def _reset() -> None:
    claim_manager.reset_for_tests()
    condition_state.reset_for_tests()
    map_knowledge.reset_for_tests()


def _ring_sentinel_buildings(
    core: Position, count: int, *, start_id: int = 900
) -> list[tuple[int, EntityType, int, Position]]:
    """``count`` friendly (team 0) sentinel buildings orthogonally adjacent
    to ``core`` — simulates an already-built ring for tests, matching
    combat.py's live _count_friendly_sentinels_near (not a state counter)."""
    offsets = [Position(1, 0), Position(-1, 0), Position(0, 1), Position(0, -1)]
    out = []
    for i, off in enumerate(offsets[:count]):
        pos = Position(core.x + off.x, core.y + off.y)
        out.append((start_id + i, EntityType.SENTINEL, 0, pos))
    return out


def _state():
    return type(
        "S",
        (),
        {
            "assignment": empty_assignment(),
            "attack_target": None,
            "attack_last_dist": None,
            "attack_stall_rounds": 0,
            "attack_sentinel_tiles": set(),
            "core_pos": None,
        },
    )()


def test_attack_slot_holder_alive_false_when_unclaimed() -> None:
    _reset()
    ct = FakeCt()
    assert combat.attack_slot_holder_alive(ct) is False  # type: ignore[arg-type]


def test_claim_and_read_attack_slot_roundtrip() -> None:
    _reset()
    ct = FakeCt(my_id=7)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    assert team_store.read_attack_builder_id(ct) == 7  # type: ignore[arg-type]
    assert combat.attack_slot_holder_alive(ct) is True  # type: ignore[arg-type]


def test_attack_slot_stale_true_when_unclaimed() -> None:
    _reset()
    ct = FakeCt()
    assert combat.attack_slot_stale(ct) is True  # type: ignore[arg-type]


def test_attack_slot_stale_false_immediately_after_claim() -> None:
    _reset()
    ct = FakeCt(my_id=7, round_=10)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    assert combat.attack_slot_stale(ct) is False  # type: ignore[arg-type]


def test_attack_slot_not_stale_while_heartbeat_keeps_refreshing() -> None:
    """A live attacker refreshes the claim every round it runs (attack_step
    heartbeats it) — the slot must stay non-stale indefinitely as long as
    that keeps happening, no matter how many rounds pass or whether anyone
    else can currently see the holder."""
    _reset()
    ct = FakeCt(my_id=7, round_=10)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    for r in range(11, 11 + combat.ATTACK_SLOT_STALE_ROUNDS + 20):
        ct._round = r  # type: ignore[attr-defined]
        team_store.write_attack_builder_id(ct, 7)  # type: ignore[arg-type]
        assert combat.attack_slot_stale(ct) is False  # type: ignore[arg-type]


def test_attack_slot_stale_after_timeout_with_no_heartbeat() -> None:
    """The only way heartbeats stop is the holder died without ever
    reaching release_attack_slot — after ATTACK_SLOT_STALE_ROUNDS with no
    refresh, the slot must become reclaimable."""
    _reset()
    ct = FakeCt(my_id=7, round_=10)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    ct._round = 10 + combat.ATTACK_SLOT_STALE_ROUNDS + 1  # type: ignore[attr-defined]
    assert combat.attack_slot_stale(ct) is True  # type: ignore[arg-type]


def test_should_dispatch_attacker_false_while_slot_freshly_claimed_even_if_holder_unseen() -> None:
    """The bug this replaced: a purely vision-based liveness check let a
    second free builder claim the slot the moment it simply couldn't see
    the current (still perfectly alive) holder — on a large map this
    caused a permanent ping-pong between two builders, neither ever
    reaching the enemy Core. FakeCt here never lists the holder in any
    building/unit list at all (simulating total mutual invisibility), yet
    dispatch must still correctly refuse."""
    _reset()
    core = Position(0, 0)
    holder_ct = FakeCt(my_id=7, round_=10)
    state = _state()
    combat.claim_attack_slot(holder_ct, state)  # type: ignore[arg-type]

    # A different free builder, with zero visibility of id 7 whatsoever —
    # shares the same (real-engine: team-wide) comm store, just not the
    # units/buildings list, simulating them being far apart on a large map.
    other = FakeCt(my_id=1, round_=11, store=holder_ct._store)  # type: ignore[attr-defined]
    other_state = _state()
    assert combat.should_dispatch_attacker(other, other_state, core) is False  # type: ignore[arg-type]


def test_attack_step_heartbeats_the_claim_every_round() -> None:
    _reset()
    ct = FakeCt(my_id=7, my_pos=Position(50, 50), round_=10)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    ct._round = 30  # type: ignore[attr-defined]
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass  # step_to() needs full navigator support this FakeCt lacks
    assert team_store.read_attack_slot_claim_round(ct) == 30  # type: ignore[arg-type]


def test_should_dispatch_attacker_false_when_slot_taken() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(my_id=1)
    team_store.write_attack_builder_id(ct, 99)  # type: ignore[arg-type]
    state = _state()
    assert combat.should_dispatch_attacker(ct, state, core) is False  # type: ignore[arg-type]


def test_should_dispatch_attacker_false_when_resources_low() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt(resources=50, builder_cost=30)  # 50 < 30 + 60 buffer
    state = _state()
    assert combat.should_dispatch_attacker(ct, state, core) is False  # type: ignore[arg-type]


def test_should_dispatch_attacker_picks_nearest_free_builder() -> None:
    _reset()
    core = Position(0, 0)
    near = FakeCt(my_id=1, my_pos=Position(2, 0), units=[(2, 0, Position(10, 0))])
    far = FakeCt(my_id=2, my_pos=Position(10, 0), units=[(1, 0, Position(2, 0))])
    assert combat.should_dispatch_attacker(near, _state(), core) is True  # type: ignore[arg-type]
    # condition_state caches get_nearby_* per round — near/far simulate two
    # different units' perspectives at the "same" round, which never
    # actually collide in the real engine (isolated module state per unit,
    # see condition_state.py's own docstring), only in this shared test
    # process. Reset the cache between them to match that isolation.
    _reset()
    assert combat.should_dispatch_attacker(far, _state(), core) is False  # type: ignore[arg-type]


def test_should_dispatch_attacker_false_when_busy() -> None:
    _reset()
    core = Position(0, 0)
    ct = FakeCt()
    state = _state()
    state.assignment.ore_pos = Position(3, 3)
    assert combat.should_dispatch_attacker(ct, state, core) is False  # type: ignore[arg-type]


def test_should_recall_attacker_on_low_hp() -> None:
    ct = FakeCt(hp=10, max_hp=40)  # 10 <= 40*0.35=14
    state = _state()
    assert combat.should_recall_attacker(ct, state) is True  # type: ignore[arg-type]


def test_should_recall_attacker_false_when_healthy_and_no_target() -> None:
    ct = FakeCt(hp=40, max_hp=40)
    state = _state()
    assert combat.should_recall_attacker(ct, state) is False  # type: ignore[arg-type]


def test_should_recall_attacker_after_stall_rounds() -> None:
    target = Position(50, 50)
    state = _state()
    state.attack_target = target
    # Builder never gets any closer to target across ATTACK_STALL_ROUNDS rounds.
    ct = FakeCt(my_pos=Position(0, 0), hp=40, max_hp=40)
    recalled = False
    for _ in range(combat.ATTACK_STALL_ROUNDS + 1):
        recalled = combat.should_recall_attacker(ct, state)  # type: ignore[arg-type]
        if recalled:
            break
    assert recalled is True


def test_should_recall_attacker_resets_stall_on_progress() -> None:
    target = Position(50, 0)
    state = _state()
    state.attack_target = target
    for x in range(0, combat.ATTACK_STALL_ROUNDS):
        ct = FakeCt(my_pos=Position(x, 0), hp=40, max_hp=40)
        assert combat.should_recall_attacker(ct, state) is False  # type: ignore[arg-type]
    assert state.attack_stall_rounds == 0


def test_should_recall_attacker_never_recalls_while_acting_even_if_stationary() -> None:
    """An attacker parked adjacent to the enemy Core grinding it down with
    melee never gets closer in tile-distance — the mission is progressing
    even though the distance-to-target metric is flat. Real matches
    (2026-08-22) showed a besieging attacker recalled mid-kill by the old
    distance-only stall counter, then wandering off for a harvester."""
    target = Position(10, 10)
    state = _state()
    state.attack_target = target
    ct = FakeCt(my_pos=Position(11, 10), hp=40, max_hp=40)  # never moves
    for _ in range(combat.ATTACK_STALL_ROUNDS * 2):
        state.attack_acted = True  # type: ignore[attr-defined]
        assert combat.should_recall_attacker(ct, state) is False  # type: ignore[arg-type]
        assert state.attack_stall_rounds == 0  # type: ignore[attr-defined]


def test_should_recall_attacker_resumes_stall_counting_once_acting_stops() -> None:
    """The acted-exemption only covers rounds that actually fired/built —
    a genuinely stuck attacker (no action, no distance progress) still
    recalls after ATTACK_STALL_ROUNDS."""
    target = Position(50, 50)
    state = _state()
    state.attack_target = target
    ct = FakeCt(my_pos=Position(0, 0), hp=40, max_hp=40)  # never moves
    for _ in range(5):
        state.attack_acted = True  # type: ignore[attr-defined]
        assert combat.should_recall_attacker(ct, state) is False  # type: ignore[arg-type]
    recalled = False
    for _ in range(combat.ATTACK_STALL_ROUNDS + 1):
        state.attack_acted = False  # type: ignore[attr-defined]
        recalled = combat.should_recall_attacker(ct, state)  # type: ignore[arg-type]
        if recalled:
            break
    assert recalled is True


def test_attack_slot_claim_confirmed_true_same_round_as_claim() -> None:
    """The comm-store write isn't visible until next round — nothing to
    check yet the same round claim_attack_slot was called."""
    _reset()
    ct = FakeCt(my_id=1, round_=10)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    assert combat.attack_slot_claim_confirmed(ct, state) is True  # type: ignore[arg-type]


def test_attack_slot_claim_confirmed_false_when_another_write_won() -> None:
    """Same-round race: should_dispatch_attacker's vision-limited ranking
    let two different builders both decide "I'm the winner" in the same
    round. Only one write can end up canonical — the loser must detect
    this the following round and self-correct, instead of staying wrongly
    committed to PHASE_ATTACK for up to ATTACK_STALL_ROUNDS (or longer)."""
    _reset()
    ct = FakeCt(my_id=1, round_=10)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    # Someone else's write ends up canonical (simulates the real race: a
    # different unit's claim_attack_slot call overwrote the slot).
    team_store.write_attack_builder_id(ct, 99)  # type: ignore[arg-type]
    ct._round = 11  # type: ignore[attr-defined]
    assert combat.attack_slot_claim_confirmed(ct, state) is False  # type: ignore[arg-type]


def test_attack_slot_claim_confirmed_true_when_this_builder_actually_won() -> None:
    _reset()
    ct = FakeCt(my_id=1, round_=10)
    state = _state()
    combat.claim_attack_slot(ct, state)  # type: ignore[arg-type]
    ct._round = 11  # type: ignore[attr-defined]
    assert combat.attack_slot_claim_confirmed(ct, state) is True  # type: ignore[arg-type]


def test_release_attack_slot_only_clears_own_claim() -> None:
    _reset()
    ct = FakeCt(my_id=1)
    team_store.write_attack_builder_id(ct, 99)  # type: ignore[arg-type]
    state = _state()
    combat.release_attack_slot(ct, state)  # type: ignore[arg-type]
    # Not our claim — must not have been cleared.
    assert team_store.read_attack_builder_id(ct) == 99  # type: ignore[arg-type]

    team_store.write_attack_builder_id(ct, 1)  # type: ignore[arg-type]
    combat.release_attack_slot(ct, state)  # type: ignore[arg-type]
    assert team_store.read_attack_builder_id(ct) == 0  # type: ignore[arg-type]


def test_attack_step_plants_sentinel_near_known_enemy_core() -> None:
    """Phase 5 — once the attacker is close enough to the enemy Core, plant
    a standing sentinel facing it instead of only melee-and-walk-on."""
    _reset()
    enemy_core = Position(10, 0)
    ct = FakeCt(my_pos=Position(6, 0))  # dist_sq to (10,0) = 16 <= 40
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1
    assert len(getattr(state, "attack_sentinel_tiles", ())) == 1  # type: ignore[arg-type]


def test_attack_step_keeps_planting_sentinels_up_to_the_ring_cap() -> None:
    """Ring the Core with up to ATTACK_MAX_SENTINELS sentinels — a single
    facing only ever covers one approach side, leaving melee as the only
    follow-up; user-requested redesign (2026-08-22)."""
    _reset()
    enemy_core = Position(10, 0)
    ct = FakeCt(
        my_pos=Position(6, 0),
        buildings=_ring_sentinel_buildings(enemy_core, combat.ATTACK_MAX_SENTINELS - 1),
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1


def test_attack_step_mop_up_prefers_barrier_over_melee_when_adjacent_to_target() -> None:
    """When no sentinel is buildable this round (no legal spot — e.g. a
    full ring), deny economy with a cheap barrier instead of melee when
    the attacker is right next to the target — melee stays only the
    fallback (user-requested redesign, 2026-08-22: the attacker "should
    not" get stuck fighting enemy conveyors in melee)."""
    _reset()
    enemy_core = Position(10, 0)
    conveyor_pos = Position(12, 0)
    ct = FakeCt(
        my_id=1,
        my_pos=Position(12, 1),  # orth-adjacent to conveyor_pos
        buildings=[(50, EntityType.CONVEYOR, 1, conveyor_pos)],
        tile_buildings={(12, 0): 50},
        barrier_supported=True,
        sentinel_buildable=False,
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    # Mop-up only engages once a sentinel has actually been planted this
    # mission (real match, 2026-08-23: chasing economy before that dragged
    # the attacker into a prolonged fight far from the Core).
    state.attack_sentinel_tiles = {(9, 0)}  # type: ignore[attr-defined]
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert len(ct.built_barriers) == 1
    assert ct.fired == []


def test_attack_step_mop_up_falls_back_to_melee_without_barrier_support() -> None:
    """Same setup, but the barrier isn't legal/available — melee still
    happens so the attacker never just idles."""
    _reset()
    enemy_core = Position(10, 0)
    conveyor_pos = Position(12, 0)
    ct = FakeCt(
        my_id=1,
        my_pos=Position(12, 1),
        buildings=[(50, EntityType.CONVEYOR, 1, conveyor_pos)],
        tile_buildings={(12, 0): 50},
        barrier_supported=False,
        sentinel_buildable=False,
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    state.attack_sentinel_tiles = {(9, 0)}  # type: ignore[attr-defined]
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert ct.built_barriers == []
    assert ct.fired == [conveyor_pos]


def test_attack_step_ignores_distant_economy_before_sentinel_built() -> None:
    """Before any sentinel has ever been planted this mission, the attacker
    must not detour to an adjacent-but-off-path enemy harvester — that's
    exactly what dragged it into a prolonged fight with enemy builders
    defending a distant harvester and let the Core get destroyed (real
    match, 2026-08-23, 0dea2b2c...: "once he go to harvester or conveyor
    he never comeback to built sentinel")."""
    _reset()
    enemy_core = Position(18, 0)  # in a straight line east of the attacker
    harvester_pos = Position(5, 1)  # adjacent, off that line
    ct = FakeCt(
        my_id=1,
        my_pos=Position(5, 0),
        buildings=[(51, EntityType.HARVESTER, 1, harvester_pos)],
        tile_buildings={(5, 1): 51},
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass  # step_to() needs full navigator support this FakeCt lacks
    assert ct.fired == []
    assert state.attack_target == enemy_core  # type: ignore[attr-defined]


def test_attack_step_does_not_engage_mop_up_before_reaching_core_perimeter() -> None:
    """Even with a sentinel already built this mission, the liberal
    adjacent-economy mop-up must not fire until the attacker is actually
    within one tile of the Core's own footprint — user spec: "only one
    tile around all the core should be checked"."""
    _reset()
    enemy_core = Position(18, 0)
    harvester_pos = Position(5, 1)  # adjacent to attacker, off the path, far from Core
    ct = FakeCt(
        my_id=1,
        my_pos=Position(5, 0),
        buildings=[(51, EntityType.HARVESTER, 1, harvester_pos)],
        tile_buildings={(5, 1): 51},
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    state.attack_sentinel_tiles = {(17, 0)}  # type: ignore[attr-defined]
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert ct.fired == []


def test_attack_step_does_not_plant_when_no_legal_placement_exists() -> None:
    """No hard cap on sentinel count (user-requested redesign, 2026-08-22:
    "build and point sentinels around the core as long as the economy
    allow it") — the only stop condition is no legal placement (e.g. every
    reachable side already occupied) or insufficient resources."""
    _reset()
    enemy_core = Position(10, 0)
    ct = FakeCt(my_pos=Position(6, 0), sentinel_buildable=False)
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    # Falls through to step_to() once no placement is legal, which needs
    # full navigator support this minimal FakeCt doesn't provide — only
    # the "no legal spot, don't build" guard is under test here.
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert ct.built_sentinels == []


def test_attack_step_keeps_planting_with_no_cap_when_resources_allow() -> None:
    """The complement of the above: with a legal placement available and
    resources sufficient, a sentinel is planted regardless of how many
    already exist near the Core (no ATTACK_MAX_SENTINELS gate anymore)."""
    _reset()
    enemy_core = Position(10, 0)
    ct = FakeCt(
        my_pos=Position(6, 0),
        buildings=_ring_sentinel_buildings(enemy_core, 4),  # at/beyond the old cap
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1


def test_try_build_launcher_near_core_skips_when_one_already_exists() -> None:
    """At most one launcher ever, team-wide. Without this, every dispatch/
    recall/redispatch cycle of the one capped attacker reset its own
    instance flag and built a brand new launcher near Core each time — a
    real match showed several accumulate over one game."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(my_pos=Position(2, 0))
    team_store.write_has_launcher(ct)  # type: ignore[arg-type]
    assert combat.try_build_launcher_near_core(ct, core) is False  # type: ignore[arg-type]
    assert ct.built_launchers == []


def test_attack_step_skips_launcher_for_redispatched_attacker() -> None:
    """A second (redispatched) attacker, with its own fresh instance state,
    must not build a second launcher just because it's never personally
    built one — team-wide state.attack_launcher_built resetting to False on
    each claim_attack_slot() call used to cause exactly that."""
    _reset()
    core = Position(5, 0)  # within ATTACK_LAUNCHER_BUILD_RANGE_SQ of pos, so the
    # launcher-build branch is actually reached (not skipped on distance
    # alone) — the has_launcher flag is what must stop it here.
    enemy_core = Position(10, 0)
    ct = FakeCt(my_pos=Position(6, 0))  # dist_sq to enemy_core = 16 <= 40: plants sentinel
    team_store.write_has_launcher(ct)  # type: ignore[arg-type]
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    state.core_pos = core
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert ct.built_launchers == []
    # Skipped straight past the launcher branch to normal offense logic.
    assert len(ct.built_sentinels) == 1


def test_attack_step_walks_back_to_core_when_out_of_launcher_range() -> None:
    """A real match showed the launcher taking until round 699 of a
    ~1000-round match to ever get built: the attacker was dispatched from
    just outside ATTACK_LAUNCHER_BUILD_RANGE_SQ, gave up on the launcher
    immediately, and marched toward the enemy instead — permanently moving
    farther from Core with every round, only stumbling back into range by
    chance hundreds of rounds later. Must walk back toward Core instead."""
    _reset()
    core = Position(0, 0)
    far_pos = Position(10, 0)  # dist_sq=100, well outside range_sq=18
    ct = FakeCt(my_pos=far_pos)
    state = _state()
    state.core_pos = core
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass  # step_to() needs full navigator support this FakeCt lacks
    assert ct.built_launchers == []
    assert getattr(state, "attack_launcher_built", False) is False
    assert state.attack_target == core  # type: ignore[attr-defined]


def test_attack_step_skips_walk_back_when_launcher_already_exists() -> None:
    """A redispatched attacker must not walk all the way back to Core
    chasing a launcher that already exists team-wide — proceed to normal
    offense logic instead."""
    _reset()
    core = Position(5, 0)
    enemy_core = Position(10, 0)
    far_pos = Position(10, 0)  # far from *our* core, but within
    # ATTACK_SENTINEL_RANGE_SQ of the enemy core — plants a sentinel,
    # proving it proceeded to normal logic instead of turning back.
    ct = FakeCt(my_pos=far_pos)
    team_store.write_has_launcher(ct)  # type: ignore[arg-type]
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    state.core_pos = core
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert ct.built_launchers == []
    assert len(ct.built_sentinels) == 1


def test_attack_step_builds_launcher_near_own_core_first() -> None:
    """The enemy was observed flinging their own builders toward our Core
    with a launcher — apply the same technique for our attacker: build one
    near our own Core on the way out, before anything else this round."""
    _reset()
    core = Position(0, 0)
    ct = FakeCt(my_pos=Position(2, 0))  # dist_sq=4 <= ATTACK_LAUNCHER_BUILD_RANGE_SQ=18
    state = _state()
    state.core_pos = core
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert len(ct.built_launchers) == 1  # on an orthogonal neighbour tile
    assert state.attack_launcher_built is True  # type: ignore[attr-defined]
    assert state.attack_launcher_wait_pos == Position(2, 0)  # type: ignore[attr-defined]
    # Nothing else attempted this same round — the round's action was spent
    # building the launcher.
    assert ct.built_sentinels == []


def test_attack_step_waits_for_launcher_without_moving() -> None:
    _reset()
    core = Position(0, 0)
    pos = Position(2, 0)
    ct = FakeCt(my_pos=pos)
    state = _state()
    state.core_pos = core
    state.attack_launcher_built = True  # type: ignore[attr-defined]
    state.attack_launcher_wait_rounds = 0  # type: ignore[attr-defined]
    state.attack_launcher_wait_pos = pos  # type: ignore[attr-defined]
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert state.attack_launcher_wait_rounds == 1  # type: ignore[attr-defined]
    assert getattr(state, "attack_launched_or_gave_up", False) is False
    assert ct.built_sentinels == []


def test_attack_step_detects_throw_via_position_change_and_resumes_same_round() -> None:
    """A position change while "waiting" (we never move ourselves during the
    wait) means the launcher's own turn already threw us — stop waiting
    immediately and resume normal offense logic this same round, instead of
    wasting more rounds standing still after a successful throw."""
    _reset()
    core = Position(0, 0)
    enemy_core = Position(10, 0)
    ct = FakeCt(my_pos=Position(6, 0))  # now close to enemy_core, post-throw
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    state.core_pos = core
    state.attack_launcher_built = True  # type: ignore[attr-defined]
    state.attack_launcher_wait_rounds = 1  # type: ignore[attr-defined]
    state.attack_launcher_wait_pos = Position(2, 0)  # stale pre-throw position
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert state.attack_launched_or_gave_up is True  # type: ignore[attr-defined]
    # Resumed normal logic this same round — close enough to plant the
    # sentinel immediately rather than waiting yet another round.
    assert len(ct.built_sentinels) == 1


def test_attack_step_gives_up_waiting_after_max_rounds() -> None:
    _reset()
    core = Position(0, 0)
    pos = Position(2, 0)
    ct = FakeCt(my_pos=pos)
    state = _state()
    state.core_pos = core
    state.attack_launcher_built = True  # type: ignore[attr-defined]
    state.attack_launcher_wait_rounds = combat.ATTACK_LAUNCHER_MAX_WAIT_ROUNDS  # type: ignore[attr-defined]
    state.attack_launcher_wait_pos = pos  # never moved — no throw happened
    # Falls through to step_to() once given up, which needs full navigator
    # support this minimal FakeCt doesn't provide — only the give-up guard
    # is under test here (same pattern as the sentinel-planting tests above).
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert state.attack_launched_or_gave_up is True  # type: ignore[attr-defined]


def test_attack_step_melees_adjacent_economy_building_in_its_way() -> None:
    """The dedicated attacker clears an enemy harvester/conveyor/splitter
    genuinely blocking its forward step toward the enemy Core — a real
    match showed the attacker "abandon the mission" when an enemy
    harvester sat directly in its path: with nothing telling it to just
    clear the obstacle, it stalled long enough to be recalled to normal
    economy duty. This is the pre-sentinel/pre-perimeter case
    (blocking_logistics_tile), not the liberal post-sentinel mop-up."""
    _reset()
    enemy_core = Position(8, 0)
    harvester_pos = Position(6, 0)  # directly on the path from (5,0) to (8,0)
    ct = FakeCt(
        my_id=1,
        my_pos=Position(5, 0),
        buildings=[(51, EntityType.HARVESTER, 1, harvester_pos)],
        tile_buildings={(6, 0): 51},
        sentinel_buildable=False,  # no legal spot this round
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    # No barrier-building support on this FakeCt (so try_build_barrier_
    # blocking no-ops) — isolates the melee fallback.
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass  # step_to() needs full navigator support this FakeCt lacks
    assert ct.fired == [harvester_pos]


def test_attack_step_targets_enemy_core_directly_before_sentinel_built() -> None:
    """"Go directly to core since position is known" — before the sentinel
    is placed, a conveyor spotted along the way must not divert the
    walking target off the direct path to the enemy Core; conveyor-hunting
    is priority two, only once the sentinel mission is done."""
    _reset()
    enemy_core = Position(30, 30)
    conveyor_pos = Position(31, 31)
    ct = FakeCt(
        my_id=1,
        my_pos=Position(0, 0),  # far from enemy_core — sentinel not in range
        buildings=[(50, EntityType.CONVEYOR, 1, conveyor_pos)],
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert state.attack_target == enemy_core  # type: ignore[attr-defined]


def test_attack_step_pre_sentinel_attacks_conveyor_directly_blocking_path() -> None:
    """Before the sentinel is planted, an enemy conveyor sitting exactly on
    the next step toward the enemy Core is a genuine obstacle and is
    cleared — distinct from one merely adjacent but off the path (see
    test_attack_step_pre_sentinel_ignores_conveyor_off_the_path)."""
    _reset()
    enemy_core = Position(20, 0)  # far outside ATTACK_SENTINEL_RANGE_SQ
    conveyor_pos = Position(6, 0)  # next step east from my_pos=(5, 0)
    ct = FakeCt(
        my_id=1,
        my_pos=Position(5, 0),
        buildings=[(50, EntityType.CONVEYOR, 1, conveyor_pos)],
        tile_buildings={(6, 0): 50},
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert ct.fired == [conveyor_pos]


def test_attack_step_clears_enemy_barrier_directly_blocking_path() -> None:
    """A barrier is "cheap HP wall, no other function" (AGENTS.md) — pure
    block, can't fire back. Without treating it as clearable, nothing ever
    engages one sitting directly in the attacker's path: BFS doesn't route
    around buildings, so the attacker just sits there stuck, unable to
    progress or act (real match, 2026-08-23, "paths" map — a dense maze —
    attacker reported "frozen... not even attacking conveyors")."""
    _reset()
    enemy_core = Position(20, 0)
    barrier_pos = Position(6, 0)  # next step east from my_pos=(5, 0)
    ct = FakeCt(
        my_id=1,
        my_pos=Position(5, 0),
        buildings=[(50, EntityType.BARRIER, 1, barrier_pos)],
        tile_buildings={(6, 0): 50},
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert ct.fired == [barrier_pos]


def test_attack_step_pre_sentinel_ignores_conveyor_off_the_path() -> None:
    """An enemy conveyor merely adjacent but not on the next step must not
    derail the pre-sentinel march to Core — a real match (2026-08-22)
    showed the attacker getting stuck destroying an enemy belt network it
    was only walking past, never reaching the Core."""
    _reset()
    enemy_core = Position(20, 0)  # far outside ATTACK_SENTINEL_RANGE_SQ
    conveyor_pos = Position(5, 1)  # adjacent (south) but not toward the Core
    ct = FakeCt(
        my_id=1,
        my_pos=Position(5, 0),
        buildings=[(50, EntityType.CONVEYOR, 1, conveyor_pos)],
        tile_buildings={(5, 1): 50},
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass  # step_to() needs full navigator support this FakeCt lacks
    assert ct.fired == []


def test_sticky_bfs_waypoint_persists_across_rounds_until_stale() -> None:
    """A BFS waypoint is committed to for ATTACK_BFS_STICKY_ROUNDS rounds
    instead of recomputed every round, even if known terrain changes in the
    meantime — real match evidence (2026-08-22) showed the attacker
    "spinning in the same 4 tiles" from a waypoint that flip-flopped round
    to round as fog cleared."""
    _reset()
    map_knowledge._width = 20  # noqa: SLF001
    map_knowledge._height = 20  # noqa: SLF001
    map_knowledge._known_env = bytearray(20 * 20)  # noqa: SLF001

    state = _state()
    target = Position(10, 0)
    ct1 = FakeCt(my_pos=Position(0, 0), round_=10)
    wp1 = combat._sticky_bfs_waypoint(ct1, state, Position(0, 0), target)  # type: ignore[arg-type]
    assert wp1 is not None

    # Terrain changed (would yield a different BFS result) but we're still
    # inside the sticky window and haven't reached the cached waypoint.
    map_knowledge._known_env[5] = 2  # noqa: SLF001 — wall at (5, 0)
    ct2 = FakeCt(my_pos=Position(0, 0), round_=12)
    wp2 = combat._sticky_bfs_waypoint(ct2, state, Position(0, 0), target)  # type: ignore[arg-type]
    assert wp2 == wp1

    # Past the sticky window — recomputes.
    ct3 = FakeCt(
        my_pos=Position(0, 0), round_=10 + combat.ATTACK_BFS_STICKY_ROUNDS + 1
    )
    wp3 = combat._sticky_bfs_waypoint(ct3, state, Position(0, 0), target)  # type: ignore[arg-type]
    assert wp3 is not None


def test_sticky_bfs_waypoint_recomputes_when_target_changes() -> None:
    _reset()
    map_knowledge._width = 20  # noqa: SLF001
    map_knowledge._height = 20  # noqa: SLF001
    map_knowledge._known_env = bytearray(20 * 20)  # noqa: SLF001

    state = _state()
    ct1 = FakeCt(my_pos=Position(0, 0), round_=10)
    wp1 = combat._sticky_bfs_waypoint(  # type: ignore[arg-type]
        ct1, state, Position(0, 0), Position(10, 0)
    )
    ct2 = FakeCt(my_pos=Position(0, 0), round_=11)
    wp2 = combat._sticky_bfs_waypoint(  # type: ignore[arg-type]
        ct2, state, Position(0, 0), Position(0, 10)
    )
    assert wp1 != wp2


def test_attack_step_hunts_conveyor_perimeter_once_sentinel_built() -> None:
    """Only after the sentinel mission is complete does the attacker switch
    to systematically working the enemy Core's conveyor perimeter."""
    _reset()
    enemy_core = Position(30, 30)
    conveyor_pos = Position(32, 30)
    ct = FakeCt(
        my_id=1,
        my_pos=Position(27, 28),  # within ATTACK_SENTINEL_RANGE_SQ of enemy_core
        buildings=[(50, EntityType.CONVEYOR, 1, conveyor_pos)],
        sentinel_buildable=False,  # no legal spot this round
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    state.attack_sentinel_tiles = {(29, 30)}  # type: ignore[attr-defined]
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert state.attack_target == conveyor_pos  # type: ignore[attr-defined]


def test_try_build_sentinel_at_enemy_core_uses_barrier_fallback_when_core_blocked() -> None:
    """A direct shot on the enemy Core that can never be verified (e.g. the
    enemy walled it in) used to fall straight to an unverified blind guess —
    real match evidence showed this built pointing the "obvious" direction
    through the wall, hitting nothing. Verify a shot on the blocking wall
    instead before giving up to the blind guess."""
    _reset()
    enemy_core = Position(10, 0)
    barrier_pos = Position(8, 0)
    ct = FakeCt(
        my_pos=Position(6, 0),
        buildings=[(50, EntityType.BARRIER, 1, barrier_pos)],
        blocked_fire_targets={enemy_core},
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    assert len(ct.built_sentinels) == 1
    assert len(getattr(state, "attack_sentinel_tiles", ())) == 1  # type: ignore[arg-type]


def test_attack_step_redirects_to_nearby_enemy_logistics_when_core_walled_off() -> None:
    """When the enemy Core has no visible harvester and can't be melee'd
    this round, redirect movement at a nearby enemy conveyor/splitter
    instead of marching at the Core tile itself — still real economic
    damage ("start removing conveyors ... so it loses economy" per the
    original ask), and reachable even when the Core itself is walled off."""
    _reset()
    enemy_core = Position(10, 0)
    conveyor_pos = Position(9, -1)
    ct = FakeCt(
        my_pos=Position(5, 0),  # within ATTACK_SENTINEL_RANGE_SQ of enemy_core
        buildings=[(60, EntityType.CONVEYOR, 1, conveyor_pos)],
        sentinel_buildable=False,  # no legal spot this round
    )
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    state.attack_sentinel_tiles = {(9, 0)}  # type: ignore[attr-defined]
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert state.attack_target == conveyor_pos


def test_attack_step_does_not_plant_sentinel_when_far_from_enemy_core() -> None:
    _reset()
    # (0,0)-31 is the packed comm-store's representable coordinate range —
    # stay within it while still being far outside ATTACK_SENTINEL_RANGE_SQ.
    enemy_core = Position(31, 31)
    ct = FakeCt(my_pos=Position(6, 0))
    team_store.write_enemy_core(ct, enemy_core)  # type: ignore[arg-type]
    state = _state()
    # attack_step falls through to step_to() once out of sentinel range,
    # which needs full navigator support this minimal FakeCt doesn't
    # provide — only the sentinel-planting guard is under test here.
    try:
        combat.attack_step(ct, state, avoid=None)  # type: ignore[arg-type]
    except AttributeError:
        pass
    assert ct.built_sentinels == []
    assert len(getattr(state, "attack_sentinel_tiles", ())) == 0  # type: ignore[arg-type]
