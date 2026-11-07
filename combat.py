"""Offense — one team-wide dedicated attacker that never abandons its mission.

Mission phases, strictly ordered:
  1. Optional launcher assist near our own Core (build one, wait to be thrown).
  2. March straight at the symmetric enemy Core (no mid-travel detours).
  3. Within range: plant standing sentinels facing the enemy Core from up to
     ATTACK_MAX_SENTINELS approach sides (verified, with a blocking-barrier
     fallback), for sustained ammo-fed pressure from every direction instead
     of a single facing.
  4. Once at the sentinel cap (or no more legal placements exist): deny the
     enemy economy with barriers (cheap, Ti-only, no ammo/combat exposure)
     rather than melee — melee stays only for a tile genuinely blocking this
     turn's step (see blocking_logistics_tile) or as a last resort when no
     barrier placement is legal either.

The attacker is tracked in comm-store slot 5 with a per-round heartbeat, so a
second builder can never steal the slot from a still-alive holder it simply
cannot see (a real bug that ping-ponged two builders forever on large maps).
"""

from __future__ import annotations

from fcode import Controller, EntityType, Position

from constants import (
    ATTACK_BFS_STICKY_ROUNDS,
    ATTACK_CORE_PERIMETER_RANGE_SQ,
    ATTACK_LAUNCHER_BUILD_RANGE_SQ,
    ATTACK_LAUNCHER_MAX_WAIT_ROUNDS,
    ATTACK_MAX_SENTINELS,
    ATTACK_RECALL_HP_FRACTION,
    ATTACK_RESOURCE_BUFFER,
    ATTACK_SENTINEL_RANGE_SQ,
    ATTACK_SLOT_STALE_ROUNDS,
    ATTACK_STALL_ROUNDS,
    CARDINALS,
)
import condition_state
from geometry import builder_can_act, facing_toward, in_bounds, orth_adjacent, xy
from navigator import step_to
import pathfind
import team_store
from team_store import read_enemy_core, write_enemy_core

# Includes BARRIER: a barrier is "cheap HP wall, no other function" (AGENTS.md)
# — pure block, can't fire back, so clearing one is never risky and can only
# help. Without it here, an enemy barrier sitting directly in the attacker's
# path had nothing that would ever clear it: pathfind.bfs_waypoint doesn't
# treat buildings as obstacles (only terrain walls), so BFS keeps routing
# straight at it, and neither blocking_logistics_tile nor try_attack_
# adjacent_economy would engage it — the attacker just sat there unable to
# progress or act (real match, 2026-08-23, "paths" map: a dense-maze layout,
# attacker "frozen... not even attacking conveyors").
_ENEMY_LOGISTICS = (
    EntityType.CONVEYOR,
    EntityType.SPLITTER,
    EntityType.HARVESTER,
    EntityType.BARRIER,
)


# ---------------------------------------------------------------------------
# Enemy Core knowledge
# ---------------------------------------------------------------------------


def note_enemy_core(ct: Controller) -> None:
    """If the enemy Core is in vision, publish it for the rest of the team."""
    my_team = ct.get_team()
    for uid in condition_state.get_cached_buildings(ct):
        if ct.get_entity_type(uid) != EntityType.CORE:
            continue
        if ct.get_team(uid) == my_team:
            continue
        write_enemy_core(ct, ct.get_position(uid))
        return


def enemy_core_target(ct: Controller, own_core: Position | None) -> Position | None:
    """Known enemy Core from the store, else rotational symmetry guess."""
    known = read_enemy_core(ct)
    if known is not None:
        return known
    if own_core is not None:
        w, h = ct.get_map_width(), ct.get_map_height()
        return Position(w - 1 - own_core.x, h - 1 - own_core.y)
    return None


# ---------------------------------------------------------------------------
# Attack slot (comm-store slot 5 + heartbeat)
# ---------------------------------------------------------------------------


def attack_slot_holder_alive(ct: Controller) -> bool:
    """A builder currently holds the attack slot."""
    return team_store.read_attack_builder_id(ct) > 0


def attack_slot_stale(ct: Controller) -> bool:
    """Slot is reclaimable — unclaimed, or the holder stopped heartbeating."""
    holder = team_store.read_attack_builder_id(ct)
    if holder <= 0:
        return True
    claim_round = team_store.read_attack_slot_claim_round(ct)
    return (ct.get_current_round() - claim_round) > ATTACK_SLOT_STALE_ROUNDS


def claim_attack_slot(ct: Controller, state: object) -> None:
    team_store.write_attack_builder_id(ct, ct.get_id())
    state.attack_claim_round = ct.get_current_round()  # type: ignore[attr-defined]


def attack_slot_claim_confirmed(ct: Controller, state: object) -> bool:
    """Did our claim actually win? The write is invisible until next round, so
    the same round it's assumed good; afterward, verify we own the slot."""
    claim_round = getattr(state, "attack_claim_round", None)
    if claim_round is None:
        return False
    if ct.get_current_round() <= claim_round:
        return True
    return team_store.read_attack_builder_id(ct) == ct.get_id()


def release_attack_slot(ct: Controller, state: object) -> None:
    if team_store.read_attack_builder_id(ct) == ct.get_id():
        team_store.write_attack_builder_id(ct, 0)
    state.attack_claim_round = None  # type: ignore[attr-defined]
    state.attack_sentinel_tiles = set()  # type: ignore[attr-defined]
    state.attack_stall_target = None  # type: ignore[attr-defined]
    state.attack_best_dist = None  # type: ignore[attr-defined]
    state.attack_stall_rounds = 0  # type: ignore[attr-defined]
    state.attack_acted = False  # type: ignore[attr-defined]
    state.attack_bfs_waypoint = None  # type: ignore[attr-defined]
    state.attack_bfs_target = None  # type: ignore[attr-defined]


def should_dispatch_attacker(ct: Controller, state: object, core: Position) -> bool:
    """This free builder should become the one dedicated attacker."""
    assignment = getattr(state, "assignment", None)
    if assignment is not None and getattr(assignment, "ore_pos", None) is not None:
        return False
    if ct.get_global_resources() < ct.get_builder_bot_cost() + ATTACK_RESOURCE_BUFFER:
        return False
    if not attack_slot_stale(ct):
        return False  # someone still holds it

    my_id = ct.get_id()
    my_pos = ct.get_position()
    ranked: list[tuple[int, int]] = [(my_pos.distance_squared(core), my_id)]

    my_team = ct.get_team()
    for uid in condition_state.get_cached_units(ct):
        try:
            if uid == my_id:
                continue
            if ct.get_team(uid) != my_team:
                continue
            if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
                continue
            ranked.append((ct.get_position(uid).distance_squared(core), uid))
        except Exception:
            continue

    ranked.sort()
    return ranked[0][1] == my_id


def should_recall_attacker(ct: Controller, state: object) -> bool:
    """Recall on low HP or after too many rounds with no *net* progress.

    "Progress" is either closing the distance to the mission target, or
    having successfully fired/built last round (``state.attack_acted``, set
    by ``attack_step``) — an attacker parked adjacent to the enemy Core
    grinding it down with melee, or fighting an obstacle in its way, never
    gets closer in tile-distance while doing exactly what its mission
    requires; counting that as a stall recalled a besieging attacker mid-kill
    and sent it wandering off (real matches, 2026-08-22: reached the enemy
    Core by round 70, then abandoned it for a harvester once the old
    distance-only stall counter tripped; same mechanism recalled it off an
    enemy conveyor blocking its path). Distance is still tracked against the
    best ever reached (not just the previous round's) so genuine, actionless
    wandering — e.g. stuck oscillating in a wall pocket — still recalls.
    Reset whenever the mission target itself changes (confirmed Core found,
    phase switches to conveyor-perimeter, etc.) since distances aren't
    comparable across different targets.
    """
    hp, mx = ct.get_hp(), ct.get_max_hp()
    if hp <= mx * ATTACK_RECALL_HP_FRACTION:
        return True

    if getattr(state, "attack_acted", False):
        state.attack_stall_rounds = 0  # type: ignore[attr-defined]
        return False

    target = getattr(state, "attack_target", None)
    if target is None:
        return False

    d = ct.get_position().distance_squared(target)
    prev_target = getattr(state, "attack_stall_target", None)
    if prev_target is None or xy(prev_target) != xy(target):
        state.attack_stall_target = target  # type: ignore[attr-defined]
        state.attack_best_dist = d  # type: ignore[attr-defined]
        state.attack_stall_rounds = 0  # type: ignore[attr-defined]
        return False

    best = getattr(state, "attack_best_dist", d)
    if d < best:
        state.attack_best_dist = d  # type: ignore[attr-defined]
        state.attack_stall_rounds = 0  # type: ignore[attr-defined]
    else:
        state.attack_stall_rounds = getattr(state, "attack_stall_rounds", 0) + 1  # type: ignore[attr-defined]
    return state.attack_stall_rounds >= ATTACK_STALL_ROUNDS  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Melee helpers
# ---------------------------------------------------------------------------


def _enemy_building_on(ct: Controller, tile: Position) -> EntityType | None:
    if not in_bounds(ct, tile):
        return None
    try:
        if not ct.is_in_vision(tile):
            return None
        bid = ct.get_tile_building_id(tile)
        if bid is None:
            return None
        if ct.get_team(bid) == ct.get_team():
            return None
        return ct.get_entity_type(bid)
    except Exception:
        return None


def try_attack_adjacent(ct: Controller) -> bool:
    """Attack an orthogonally adjacent enemy building. Prefer harvesters."""
    if not builder_can_act(ct):
        return False
    if ct.get_global_resources() < 2:
        return False

    pos = ct.get_position()
    harvester_tiles: list[Position] = []
    other_tiles: list[Position] = []
    for d in CARDINALS:
        tile = pos.add(d)
        et = _enemy_building_on(ct, tile)
        if et is None:
            continue
        if et == EntityType.HARVESTER:
            harvester_tiles.append(tile)
        else:
            other_tiles.append(tile)

    for tile in harvester_tiles + other_tiles:
        if ct.can_fire(tile):
            ct.fire(tile)
            return True
    return False


def _try_attack_tile(ct: Controller, tile: Position) -> bool:
    if not builder_can_act(ct):
        return False
    if ct.get_global_resources() < 2:
        return False
    try:
        if not ct.can_fire(tile):
            return False
    except Exception:
        return False
    ct.fire(tile)
    return True


def try_attack_adjacent_economy(ct: Controller) -> bool:
    """Clear any adjacent enemy harvester/conveyor/splitter.

    Never a Core/gunner/sentinel/barrier. Used for the post-sentinel
    perimeter mop-up phase, where being liberal about any adjacent target is
    the point (see ``blocking_logistics_tile`` for the pre-sentinel march,
    which only ever engages the one tile actually blocking the next step).
    """
    pos = ct.get_position()
    for d in CARDINALS:
        tile = pos.add(d)
        et = _enemy_building_on(ct, tile)
        if et not in _ENEMY_LOGISTICS:
            continue
        if _try_attack_tile(ct, tile):
            return True
    return False


def blocking_logistics_tile(ct: Controller, pos: Position, goal: Position) -> Position | None:
    """The enemy logistics building directly on this turn's step toward
    ``goal``, if any.

    Used during the pre-sentinel march so the attacker only ever fights an
    obstacle genuinely blocking forward progress — an enemy conveyor sitting
    adjacent but off the planned path is left alone rather than derailing
    the march (a real match showed the attacker getting stuck destroying an
    enemy belt network it was merely walking past, never reaching the Core).
    """
    if pos.x == goal.x and pos.y == goal.y:
        return None
    primary = pos.cardinal_direction_to(goal)
    tile = pos.add(primary)
    et = _enemy_building_on(ct, tile)
    if et in _ENEMY_LOGISTICS:
        return tile
    return None


def _try_melee_enemy_core(ct: Controller, enemy_core: Position) -> bool:
    """Melee an orthogonally adjacent enemy Core footprint tile (2 dmg/2 Ti)."""
    if not builder_can_act(ct):
        return False
    if ct.get_global_resources() < 2:
        return False
    pos = ct.get_position()
    my_team = ct.get_team()
    for dx in (0, 1):
        for dy in (0, 1):
            tile = Position(enemy_core.x + dx, enemy_core.y + dy)
            if abs(pos.x - tile.x) + abs(pos.y - tile.y) != 1:
                continue
            try:
                bid = ct.get_tile_building_id(tile)
                if bid is None or ct.get_team(bid) == my_team:
                    continue
                if ct.get_entity_type(bid) != EntityType.CORE:
                    continue
                if ct.can_fire(tile):
                    ct.fire(tile)
                    return True
            except Exception:
                continue
    return False


def nearest_enemy_harvester(ct: Controller) -> Position | None:
    """Closest visible enemy harvester tile, or None."""
    pos = ct.get_position()
    my_team = ct.get_team()
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for uid in condition_state.get_cached_buildings(ct):
        if ct.get_entity_type(uid) != EntityType.HARVESTER:
            continue
        if ct.get_team(uid) == my_team:
            continue
        tile = ct.get_position(uid)
        key = (pos.distance_squared(tile), tile.x, tile.y)
        if best_key is None or key < best_key:
            best_key = key
            best = tile
    return best


def _dist_sq_to_core_footprint(pos: Position, core: Position) -> int:
    """Squared distance from ``pos`` to the *nearest* tile of the Core's
    2x2 footprint, not just its reference corner — for judging genuine
    "immediately around the Core" proximity precisely."""
    best = pos.distance_squared(core)
    for dx in (0, 1):
        for dy in (0, 1):
            d = pos.distance_squared(Position(core.x + dx, core.y + dy))
            if d < best:
                best = d
    return best


def nearest_enemy_logistics_near(
    ct: Controller, enemy_core: Position, *, range_sq: int = ATTACK_SENTINEL_RANGE_SQ
) -> Position | None:
    """Closest visible enemy conveyor/splitter/harvester/barrier within
    ``range_sq`` of the enemy Core's own footprint (not its reference
    corner — see ``_dist_sq_to_core_footprint``)."""
    my_team = ct.get_team()
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for uid in condition_state.get_cached_buildings(ct):
        try:
            if ct.get_team(uid) == my_team:
                continue
            if ct.get_entity_type(uid) not in _ENEMY_LOGISTICS:
                continue
            tpos = ct.get_position(uid)
        except Exception:
            continue
        d = _dist_sq_to_core_footprint(tpos, enemy_core)
        if d > range_sq:
            continue
        key = (d, tpos.x, tpos.y)
        if best_key is None or key < best_key:
            best_key = key
            best = tpos
    return best


# ---------------------------------------------------------------------------
# Offensive builds
# ---------------------------------------------------------------------------


def try_build_launcher_near_core(ct: Controller, core: Position) -> bool:
    """Build the single team-wide launcher on an orthogonal neighbour tile."""
    if team_store.read_has_launcher(ct):
        return False
    if not builder_can_act(ct):
        return False
    if ct.get_global_resources() < ct.get_launcher_cost():
        return False
    pos = ct.get_position()
    for d in CARDINALS:
        tile = pos.add(d)
        try:
            if ct.can_build_launcher(tile):
                ct.build_launcher(tile)
                team_store.write_has_launcher(ct)
                return True
        except Exception:
            continue
    return False


def try_build_sentinel_at_enemy_core(
    ct: Controller, enemy_core: Position, *, exclude: set[tuple[int, int]] | None = None
) -> tuple[bool, Position | None]:
    """Plant a verified sentinel facing the enemy Core (barrier fallback).

    Returns (built, tile) — the caller records ``tile`` so a later loss can
    be excluded from future attempts instead of predictably rebuilding in
    the exact spot the enemy just destroyed.
    """
    from defense import best_verified_placement, try_build_sentinel

    placement = best_verified_placement(ct, enemy_core, EntityType.SENTINEL, exclude=exclude)
    if placement is None:
        barrier = _nearest_enemy_barrier(ct, enemy_core)
        if barrier is not None:
            placement = best_verified_placement(
                ct, barrier, EntityType.SENTINEL, exclude=exclude
            )
    if placement is None:
        return False, None
    tile, facing = placement
    return try_build_sentinel(ct, tile, facing), tile


def _count_friendly_sentinels_near(ct: Controller, anchor: Position) -> int:
    """Live count of our own sentinels within ATTACK_SENTINEL_RANGE_SQ of
    the enemy Core — recomputed fresh every round so a sentinel the enemy
    destroys is noticed immediately and the ring-building logic retries it,
    instead of a monotonic build counter that never accounts for losses
    (real match evidence, 2026-08-22 — a destroyed sentinel was never
    replaced once the nominal "ring complete" count had ever been reached).
    """
    my_team = ct.get_team()
    n = 0
    for uid in condition_state.get_cached_buildings(ct):
        try:
            if ct.get_team(uid) != my_team:
                continue
            if ct.get_entity_type(uid) != EntityType.SENTINEL:
                continue
            if ct.get_position(uid).distance_squared(anchor) > ATTACK_SENTINEL_RANGE_SQ:
                continue
        except Exception:
            continue
        n += 1
    return n


def try_build_barrier_blocking(ct: Controller, near: Position) -> bool:
    """Deny economy: wall off an empty tile adjacent to an enemy economy
    target (never the target tile itself — that just wastes the barrier
    where nothing needs blocking). Barriers cost titanium only (3 Ti), no
    ammo, no combat exposure — the preferred mop-up action once the
    sentinel ring is up, ahead of melee.
    """
    if not builder_can_act(ct):
        return False
    cost_fn = getattr(ct, "get_barrier_cost", None)
    can_fn = getattr(ct, "can_build_barrier", None)
    build_fn = getattr(ct, "build_barrier", None)
    if not callable(cost_fn) or not callable(can_fn) or not callable(build_fn):
        return False
    if ct.get_global_resources() < cost_fn():
        return False
    pos = ct.get_position()
    for d in CARDINALS:
        tile = pos.add(d)
        if xy(tile) == xy(near):
            continue
        try:
            if can_fn(tile):
                build_fn(tile)
                return True
        except Exception:
            continue
    return False


def _nearest_enemy_barrier(ct: Controller, anchor: Position) -> Position | None:
    my_team = ct.get_team()
    best: Position | None = None
    best_key: tuple[int, int, int] | None = None
    for uid in condition_state.get_cached_buildings(ct):
        try:
            if ct.get_team(uid) == my_team:
                continue
            if ct.get_entity_type(uid) != EntityType.BARRIER:
                continue
            bpos = ct.get_position(uid)
        except Exception:
            continue
        key = (bpos.distance_squared(anchor), bpos.x, bpos.y)
        if best_key is None or key < best_key:
            best_key = key
            best = bpos
    return best


# ---------------------------------------------------------------------------
# Main step
# ---------------------------------------------------------------------------


def _launcher_phase(ct: Controller, state: object, *, avoid: Position | None) -> bool:
    """Handle the pre-march launcher assist. Returns True if it consumed the
    round (built / waiting / walking back); False to fall through to offense.
    """
    if team_store.read_has_launcher(ct):
        return False
    # Once the launcher business is settled (built+thrown, or given up), never
    # re-enter — otherwise a Core ringed by our own belts (no free tile to
    # build a launcher on) oscillates the attacker near home forever.
    if getattr(state, "attack_launched_or_gave_up", False):
        return False

    pos = ct.get_position()
    core = getattr(state, "core_pos", None)

    if getattr(state, "attack_launcher_built", False):
        wait_pos = getattr(state, "attack_launcher_wait_pos", None)
        if wait_pos is not None and xy(pos) != xy(wait_pos):
            # We moved without moving ourselves — the launcher threw us.
            state.attack_launched_or_gave_up = True  # type: ignore[attr-defined]
            return False
        if getattr(state, "attack_launcher_wait_rounds", 0) >= ATTACK_LAUNCHER_MAX_WAIT_ROUNDS:
            state.attack_launched_or_gave_up = True  # type: ignore[attr-defined]
            return False
        state.attack_launcher_wait_rounds = (  # type: ignore[attr-defined]
            getattr(state, "attack_launcher_wait_rounds", 0) + 1
        )
        return True

    if core is None:
        return False

    if pos.distance_squared(core) <= ATTACK_LAUNCHER_BUILD_RANGE_SQ:
        if try_build_launcher_near_core(ct, core):
            state.attack_launcher_built = True  # type: ignore[attr-defined]
            state.attack_launcher_wait_pos = pos  # type: ignore[attr-defined]
            state.attack_launcher_wait_rounds = 0  # type: ignore[attr-defined]
            return True
        # In range but no free tile (Core boxed in by belts) — skip the
        # launcher rather than oscillate; the sentinel push is what matters.
        state.attack_launched_or_gave_up = True  # type: ignore[attr-defined]
        return False

    # Out of build range — walk back toward Core once to try building there.
    state.attack_target = core  # type: ignore[attr-defined]
    step_to(ct, state, core, avoid=avoid)
    return True


def _core_candidates(ct: Controller, own_core: Position) -> list[Position]:
    """The three symmetric positions the enemy Core could occupy — rotational
    (point) and the two axis reflections. Maps are symmetric by reflection or
    rotation (AGENTS.md), and we can't tell which until we see enemy structures.
    """
    w, h = ct.get_map_width(), ct.get_map_height()
    x, y = own_core.x, own_core.y
    return [
        Position(w - 1 - x, h - 1 - y),  # rotational (point) symmetry
        Position(w - 1 - x, y),          # horizontal reflection (x flips)
        Position(x, h - 1 - y),          # vertical reflection (y flips)
    ]


def _sticky_bfs_waypoint(
    ct: Controller, state: object, pos: Position, target: Position
) -> Position | None:
    """BFS waypoint toward ``target``, committed to for
    ATTACK_BFS_STICKY_ROUNDS rounds instead of recomputed every round.

    BFS on partial (fog-limited) terrain knowledge can legitimately choose
    a *different* shortest path as new tiles are revealed each round even
    though the underlying situation barely changed, producing a waypoint
    that flip-flops between neighbouring tiles round after round — the
    attacker never commits to any one route long enough to actually clear
    a wall pocket (a real match, 2026-08-22: "keeps spinning in the same 4
    tiles"). Recomputes early if the target changed or the cached waypoint
    was reached.
    """
    cached = getattr(state, "attack_bfs_waypoint", None)
    cached_target = getattr(state, "attack_bfs_target", None)
    cached_round = getattr(state, "attack_bfs_round", -(10**9))
    rnd = ct.get_current_round()

    if (
        cached is not None
        and cached_target is not None
        and xy(cached_target) == xy(target)
        and xy(pos) != xy(cached)
        and rnd - cached_round < ATTACK_BFS_STICKY_ROUNDS
    ):
        return cached

    try:
        wp = pathfind.bfs_waypoint(ct, pos, target)
    except Exception:
        wp = None
    state.attack_bfs_waypoint = wp  # type: ignore[attr-defined]
    state.attack_bfs_target = target  # type: ignore[attr-defined]
    state.attack_bfs_round = rnd  # type: ignore[attr-defined]
    return wp


def _next_core_candidate(
    ct: Controller, state: object, own_core: Position | None, pos: Position
) -> Position:
    """March target while the enemy Core is unseen: sweep the candidate
    corners, advancing once we're close enough to have revealed one."""
    if own_core is None:
        return Position(ct.get_map_width() // 2, ct.get_map_height() // 2)
    candidates = _core_candidates(ct, own_core)
    idx = getattr(state, "attack_candidate_idx", 0) % len(candidates)
    # Advance only once we're inside builder vision of the candidate (r²=20):
    # if the real Core were there we'd have seen it and locked on already, so
    # an empty corner means try the next symmetry candidate.
    if pos.distance_squared(candidates[idx]) <= 9:
        idx = (idx + 1) % len(candidates)
        state.attack_candidate_idx = idx  # type: ignore[attr-defined]
    return candidates[idx]


def attack_step(ct: Controller, state: object, *, avoid: Position | None) -> None:
    """One offensive round for the dedicated attacker."""
    # Heartbeat first so the slot never goes stale while we're alive.
    team_store.write_attack_builder_id(ct, ct.get_id())
    note_enemy_core(ct)

    if _launcher_phase(ct, state, avoid=avoid):
        return

    pos = ct.get_position()
    own_core = getattr(state, "core_pos", None)
    # Only a *confirmed* (actually seen) enemy Core is trustworthy to aim a
    # fixed sentinel at. The symmetry guess is fine for walking toward, but on
    # reflection-symmetric maps it points at empty space beside the real Core,
    # so a sentinel planted on the guess sits idle forever (verified in a
    # sweden replay: 2000+ idle sentinel turns, Core untouched).
    confirmed = read_enemy_core(ct)

    used_tiles: set[tuple[int, int]] = set(getattr(state, "attack_sentinel_tiles", ()))
    close_to_core = (
        confirmed is not None and pos.distance_squared(confirmed) <= ATTACK_SENTINEL_RANGE_SQ
    )
    try:
        sentinel_afford = ct.get_global_resources() >= ct.get_sentinel_cost()
    except Exception:
        sentinel_afford = False

    # 1. Plant a standing sentinel — no cap: keep planting as long as the
    # economy affords it and a legal spot exists near the Core, this round
    # and every round after (user-requested redesign, 2026-08-22: "build
    # and point sentinels around the core as long as the economy allow
    # it"). best_verified_placement's candidates are the Core's own
    # orthogonal neighbours, so an occupied tile is naturally skipped and a
    # fresh side tried — a destroyed sentinel is noticed (this check is
    # re-attempted unconditionally every round, not gated on a build tally)
    # and retried automatically; used_tiles excludes every tile ever tried
    # this mission so a loss is never predictably rebuilt in the exact spot
    # the enemy just sniped.
    if close_to_core and sentinel_afford:
        built, tile = try_build_sentinel_at_enemy_core(ct, confirmed, exclude=used_tiles)
        if built:
            if tile is not None:
                used_tiles.add(xy(tile))
                state.attack_sentinel_tiles = used_tiles  # type: ignore[attr-defined]
            state.attack_acted = True  # type: ignore[attr-defined]
            # Tell the Core to start banking ammo to feed these sentinels'
            # grind on the enemy Core (a single 20-ammo cap starves it).
            team_store.write_attack_committed(ct, True)
            return

    # Mop-up (conveyor-cutting) only ever kicks in once at least one
    # sentinel has actually been planted this mission — not merely "can't
    # afford one this round" — and is scoped to literally one tile around
    # the Core's own footprint (ATTACK_CORE_PERIMETER_RANGE_SQ), never the
    # wide sentinel-placement bubble. Before that, chasing a distant
    # harvester/conveyor dragged the attacker into a prolonged fight with
    # enemy builders defending it and it never came back to press the Core
    # (real match, 2026-08-23, lost 0-5 by Core destroyed).
    sentinel_ever_built = bool(used_tiles)
    near_core_perimeter = (
        confirmed is not None
        and _dist_sq_to_core_footprint(pos, confirmed) <= ATTACK_CORE_PERIMETER_RANGE_SQ
    )

    # Pick a march target.
    if confirmed is not None:
        logistics = (
            nearest_enemy_logistics_near(ct, confirmed, range_sq=ATTACK_CORE_PERIMETER_RANGE_SQ)
            if sentinel_ever_built
            else None
        )
        target = logistics if logistics is not None else confirmed
    else:
        # Core not seen yet — sweep the symmetry candidates so we actually
        # reach and reveal it instead of committing to one (maybe wrong) guess.
        target = _next_core_candidate(ct, state, own_core, pos)

    state.attack_target = target  # type: ignore[attr-defined]

    # Route around known terrain walls with BFS — the local Bug2 navigator
    # loops forever in a concave wall pocket, which stranded the attacker a
    # few tiles short of the enemy Core for hundreds of rounds. Keep the true
    # goal in attack_target (for stall/recall bookkeeping); only the movement
    # waypoint is BFS-routed, and sticky (see _sticky_bfs_waypoint) so it
    # doesn't flip-flop round to round as fog clears. Fog-safe: unknown
    # tiles count as passable.
    move_goal = target
    wp = _sticky_bfs_waypoint(ct, state, pos, target)
    if wp is not None:
        move_goal = wp

    # 2. Economy denial — only reached when a sentinel wasn't built this
    # round (no budget or no legal spot right now, not a permanent giving
    # up). Once a sentinel exists and the attacker is within one tile of
    # the Core's own footprint, deny economy by attacking the conveyor/
    # splitter/harvester perimeter (barrier first — cheap, Ti-only, no
    # combat exposure — then melee), preferred over meleeing the Core
    # directly (user-requested: "attacks conveyors surrounding core...
    # only go for [that] if there is no ammo to fire" — i.e. no sentinel
    # buildable this round). Before that (no sentinel yet, or not yet at
    # the Core's perimeter), only ever engage the one tile actually
    # blocking this turn's step, so the attacker doesn't get dragged into
    # destroying a whole belt network — or fighting builders defending a
    # distant harvester — instead of reaching the Core.
    if sentinel_ever_built and near_core_perimeter:
        target_here = (
            nearest_enemy_logistics_near(ct, confirmed, range_sq=ATTACK_CORE_PERIMETER_RANGE_SQ)
            if confirmed
            else None
        )
        # Only attempt the barrier once actually adjacent to the target —
        # try_build_barrier_blocking places on a tile next to the attacker's
        # own position, so attempting it from far away would just wall off
        # empty ground near wherever the attacker happens to be walking,
        # wasting Ti on a barrier that blocks nothing relevant.
        if (
            target_here is not None
            and orth_adjacent(pos, target_here)
            and try_build_barrier_blocking(ct, target_here)
        ):
            state.attack_acted = True  # type: ignore[attr-defined]
            return
        if try_attack_adjacent_economy(ct):
            state.attack_acted = True  # type: ignore[attr-defined]
            return
    else:
        blocking = blocking_logistics_tile(ct, pos, move_goal)
        if blocking is not None and _try_attack_tile(ct, blocking):
            state.attack_acted = True  # type: ignore[attr-defined]
            return

    # 3. Melee the Core directly — last resort only, when neither a
    # sentinel nor an economy target was available this round (demoted
    # from its previous early priority per the same redesign request).
    aim = confirmed if confirmed is not None else enemy_core_target(ct, own_core)
    if aim is not None and _try_melee_enemy_core(ct, aim):
        state.attack_acted = True  # type: ignore[attr-defined]
        return

    # Nothing fired this round — only actual movement counts as progress
    # from here on (see should_recall_attacker's use of attack_acted).
    state.attack_acted = False  # type: ignore[attr-defined]

    import os as _os
    if _os.environ.get("ATTACK_DEBUG") == "1":
        prim = pos.cardinal_direction_to(target)
        nxt = pos.add(prim)
        try:
            env = ct.get_tile_env(nxt)
            bid = ct.get_tile_building_id(nxt)
            bteam = ct.get_team(bid) if bid is not None else None
            cm = ct.can_move(prim)
        except Exception:
            env = bid = bteam = cm = "?"
        with open("attack_debug.log", "a", encoding="utf-8") as _f:
            _f.write(
                f"r={ct.get_current_round()} pos=({pos.x},{pos.y}) "
                f"confirmed={confirmed} target=({target.x},{target.y}) "
                f"prim={prim} nxt=({nxt.x},{nxt.y}) env={env} "
                f"bldg={bid} bteam={bteam} can_move={cm} "
                f"move_goal=({move_goal.x},{move_goal.y})\n"
            )
    step_to(ct, state, move_goal, avoid=avoid)
