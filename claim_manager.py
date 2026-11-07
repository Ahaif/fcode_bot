"""Centralized mining ownership protocol — sole authority for ore ownership.

Authority
---------
The team claim table (comm-store slots 6..15) is the single source of truth.
``Assignment.ore_pos`` is a per-unit *cache* maintained only by this module.

All ownership transitions go through:

  begin_turn / acquire / release / transfer / complete / try_acquire_work

No other module may write ``Assignment.ore_pos`` or claim-store slots.

Same-round races
----------------
Store writes are visible next round. Contests among *visible free* builders
are therefore decided by a pure function of (hunger, distance, id) so every
observer agrees who may Acquire without reading each other's writes.
Lower builder id always wins store conflicts for the same ore.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from fcode import Controller, EntityType, Environment, Position

from constants import MAX_ORE_CLAIMS, SLOT_CLAIM_BASE
from geometry import xy
import map_knowledge

# Round-scoped cache — see condition_state.py's own note on this: calling
# ct.get_nearby_buildings()/ct.get_nearby_units() directly bypasses
# map_knowledge's per-round cache and reintroduces the redundant
# Controller-API calls that caused real TLE timeouts.
get_cached_units = map_knowledge.get_cached_units

# Free builders with this many consecutive free turns win contests over
# freshly-freed builders even when slightly farther (anti-starvation).
HUNGER_TURNS = 30
# Packed "free heartbeat" uses x=255 (invalid map coord) in the claim slot.
_FREE_SENTINEL_X = 255
_INF_DIST = 10**9

# In-process mirror for unit tests only (fcode units do not share module memory).
_TABLE: dict[tuple[int, int], int] = {}
_FREE_AGES: dict[int, int] = {}

_ACQUIRE_LOG_ATTR = "_claim_acquire_log"


def _asn():
    import assignment as asn

    return asn


def reset_for_tests() -> None:
    _TABLE.clear()
    _FREE_AGES.clear()


def _ore_key(ore: Position) -> tuple[int, int]:
    return (ore.x, ore.y)


def _pack_claim(builder_id: int, ore: Position) -> int:
    return builder_id * 100000 + ore.x * 256 + ore.y + 1


def _pack_free(builder_id: int, free_turns: int) -> int:
    return builder_id * 100000 + _FREE_SENTINEL_X * 256 + min(max(free_turns, 0), 255) + 1


def _unpack(packed: int) -> tuple[int, tuple[int, int]] | None:
    if packed <= 0:
        return None
    v = packed - 1
    bid = v // 100000
    rest = v % 100000
    return bid, (rest // 256, rest % 256)


def _slot(builder_id: int) -> int:
    return SLOT_CLAIM_BASE + (builder_id % MAX_ORE_CLAIMS)


def _builder_alive(ct: Controller, uid: int) -> bool:
    try:
        if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
            return False
        return ct.get_team(uid) == ct.get_team()
    except Exception:
        return False


def read_table(ct: Controller | None = None) -> dict[tuple[int, int], int]:
    """Authoritative ore→owner map. Duplicate ores resolve to lower builder id."""
    out: dict[tuple[int, int], int] = {
        k: v for k, v in _TABLE.items() if k[0] != _FREE_SENTINEL_X
    }
    if ct is None:
        return out
    for i in range(MAX_ORE_CLAIMS):
        try:
            parsed = _unpack(int(ct.read_store(SLOT_CLAIM_BASE + i)))
        except Exception:
            continue
        if parsed is None:
            continue
        bid, key = parsed
        if key[0] == _FREE_SENTINEL_X:
            continue
        prev = out.get(key)
        if prev is None or bid < prev:
            out[key] = bid
    return out


def read_free_ages(ct: Controller | None = None) -> dict[int, int]:
    """builder_id → free_turns from heartbeats (+ test mirror)."""
    out = dict(_FREE_AGES)
    if ct is None:
        return out
    for i in range(MAX_ORE_CLAIMS):
        try:
            parsed = _unpack(int(ct.read_store(SLOT_CLAIM_BASE + i)))
        except Exception:
            continue
        if parsed is None:
            continue
        bid, key = parsed
        if key[0] != _FREE_SENTINEL_X:
            continue
        out[bid] = key[1]
    return out


def owner_of(ore: Position, ct: Controller | None = None) -> int | None:
    return read_table(ct).get(_ore_key(ore))


def builder_claim(ct: Controller, builder_id: int) -> tuple[int, int] | None:
    for key, oid in read_table(ct).items():
        if oid == builder_id:
            return key
    return None


@dataclass
class AcquireResult:
    ok: bool
    ore: Position | None = None
    reason: str = ""
    rejects: list[tuple[tuple[int, int], str]] = field(default_factory=list)


def _clear_task_fields(state: object) -> None:
    for attr, value in (
        ("build_plan", None),
        ("build_index", 0),
        ("replan_count", 0),
        ("last_replan_reason", None),
        ("stand_paved", False),
        ("approach_stand", None),
    ):
        if hasattr(state, attr):
            setattr(state, attr, value)


def _set_assignment_ore(state: object, ore: Position | None) -> None:
    """Cache-only write — always paired with a table write in the same call."""
    asn = _asn()
    if ore is None:
        state.assignment = asn.empty_assignment()  # type: ignore[attr-defined]
        return
    a = asn.get_assignment(state)
    if a.ore_pos is None or (a.ore_pos.x, a.ore_pos.y) != (ore.x, ore.y):
        state.assignment = asn.Assignment(  # type: ignore[attr-defined]
            ore_pos=ore,
            harvester_pos=None,
            connected=False,
            completed=False,
        )
    else:
        a.ore_pos = ore
        a.completed = False


def _write_claim(ct: Controller, builder_id: int, ore: Position) -> bool:
    """Publish claim. Fails (no write) if a lower-id owner already holds ``ore``."""
    key = _ore_key(ore)
    table = read_table(ct)
    existing = table.get(key)
    if existing is not None and existing != builder_id and existing < builder_id:
        return False

    # Mirror
    for k, oid in list(_TABLE.items()):
        if oid == builder_id or k == key:
            del _TABLE[k]
    _TABLE[key] = builder_id
    _FREE_AGES.pop(builder_id, None)

    my_slot = _slot(builder_id)
    # Evict higher-id duplicates of this ore; never evict lower id.
    for i in range(MAX_ORE_CLAIMS):
        slot = SLOT_CLAIM_BASE + i
        if slot == my_slot:
            continue
        try:
            parsed = _unpack(int(ct.read_store(slot)))
        except Exception:
            continue
        if parsed is None:
            continue
        other_id, other_key = parsed
        if other_key == key and other_id != builder_id:
            if builder_id < other_id:
                ct.write_store(slot, 0)
            else:
                return False

    ct.write_store(my_slot, _pack_claim(builder_id, ore))
    return True


def _write_free_or_clear(ct: Controller, builder_id: int, free_turns: int) -> None:
    """Publish free heartbeat (or clear slot). Removes any ore claim for builder."""
    for k, oid in list(_TABLE.items()):
        if oid == builder_id:
            del _TABLE[k]
    _FREE_AGES[builder_id] = free_turns

    my_slot = _slot(builder_id)
    try:
        parsed = _unpack(int(ct.read_store(my_slot)))
    except Exception:
        parsed = None
    # Only touch our slot (or empty / our free heartbeat).
    if parsed is not None and parsed[0] != builder_id:
        # Slot collision with another builder — do not clobber their claim.
        # Still update mirror ages for tests.
        return
    ct.write_store(my_slot, _pack_free(builder_id, free_turns))


def _is_busy_on_table(ct: Controller, uid: int) -> bool:
    return builder_claim(ct, uid) is not None


def _free_turns_of(ct: Controller, state: object, uid: int) -> int:
    if uid == ct.get_id():
        return int(getattr(state, "free_turns", 0))
    return int(read_free_ages(ct).get(uid, 0))


def _contest_winner(ct: Controller, state: object, ore: Position) -> int:
    """Deterministic winner among self + visible free builders."""
    my_id = ct.get_id()
    ages = read_free_ages(ct)
    ages[my_id] = int(getattr(state, "free_turns", 0))

    def sort_key(uid: int, pos: Position) -> tuple[int, int, int]:
        turns = int(ages.get(uid, 0))
        hunger = 0 if turns >= HUNGER_TURNS else 1
        return (hunger, pos.distance_squared(ore), uid)

    best_id = my_id
    best = sort_key(my_id, ct.get_position())
    my_team = ct.get_team()
    for uid in get_cached_units(ct):
        if uid == my_id:
            continue
        try:
            if ct.get_entity_type(uid) != EntityType.BUILDER_BOT:
                continue
            if ct.get_team(uid) != my_team:
                continue
        except Exception:
            continue
        if _is_busy_on_table(ct, uid):
            continue
        pos = ct.get_position(uid)
        key = sort_key(uid, pos)
        if key < best:
            best = key
            best_id = uid
    return best_id


def why_not_acquirable(
    ct: Controller,
    state: object,
    ore: Position,
    *,
    exclude: Position | None = None,
) -> str | None:
    """None if acquire(ore) would succeed; else rejection reason."""
    my_id = ct.get_id()
    if _asn().get_assignment(state).ore_pos is not None:
        return "already_owning"
    if int(getattr(state, "claim_turns_alive", 0)) < 1:
        return "spawn_turn_gate"
    if exclude is not None and ore.x == exclude.x and ore.y == exclude.y:
        return "excluded_this_turn"
    try:
        vis_fn = getattr(ct, "is_in_vision", None)
        if callable(vis_fn) and not vis_fn(ore):
            return "out_of_vision"
        if ct.get_tile_env(ore) != Environment.ORE_TITANIUM:
            return "not_ore"
        if ct.get_tile_building_id(ore) is not None:
            return "occupied"
    except Exception:
        return "tile_unreadable"

    table = read_table(ct)
    oid = table.get(_ore_key(ore))
    if oid is not None and oid != my_id:
        if _builder_alive(ct, oid):
            return f"owned_by_{oid}"

    winner = _contest_winner(ct, state, ore)
    if winner != my_id:
        return f"contest_lost_to_{winner}"
    return None


def begin_turn(ct: Controller, state: object) -> None:
    """Reconcile cache with table. Guarantees no local Assignment/table split."""
    my_id = ct.get_id()
    state.claim_owner_id = my_id  # type: ignore[attr-defined]
    if not hasattr(state, "claim_turns_alive"):
        state.claim_turns_alive = 0  # type: ignore[attr-defined]
    if not hasattr(state, "free_turns"):
        state.free_turns = 0  # type: ignore[attr-defined]
    setattr(state, _ACQUIRE_LOG_ATTR, [])

    # Drop dead owners' claims when we can identify their slot.
    table = read_table(ct)
    for key, oid in list(table.items()):
        if oid == my_id:
            continue
        if not _builder_alive(ct, oid):
            slot = _slot(oid)
            try:
                parsed = _unpack(int(ct.read_store(slot)))
            except Exception:
                parsed = None
            if parsed is not None and parsed[0] == oid:
                ct.write_store(slot, 0)
            _TABLE.pop(key, None)

    table = read_table(ct)
    board_key = builder_claim(ct, my_id)
    a = _asn().get_assignment(state)
    cache_key = _ore_key(a.ore_pos) if a.ore_pos is not None else None

    # Table prefers lower id — if we lost the ore, drop cache immediately.
    if cache_key is not None:
        oid = table.get(cache_key)
        if oid is not None and oid != my_id:
            _set_assignment_ore(state, None)
            _clear_task_fields(state)
            cache_key = None
            board_key = builder_claim(ct, my_id)

    if board_key is not None:
        ore = Position(board_key[0], board_key[1])
        # Another living builder with lower id owns it on merged table.
        oid = table.get(board_key)
        if oid is not None and oid != my_id:
            _set_assignment_ore(state, None)
            _clear_task_fields(state)
            _write_free_or_clear(ct, my_id, int(getattr(state, "free_turns", 0)))
            return
        prev_h = a.harvester_pos if cache_key == board_key else None
        if cache_key != board_key or a.ore_pos is None:
            _set_assignment_ore(state, ore)
            if prev_h is not None:
                _asn().get_assignment(state).harvester_pos = prev_h
        # Republish claim (defends against higher-id clobber attempts).
        if not _write_claim(ct, my_id, ore):
            _set_assignment_ore(state, None)
            _clear_task_fields(state)
            _write_free_or_clear(ct, my_id, int(getattr(state, "free_turns", 0)))
        else:
            state.free_turns = 0  # type: ignore[attr-defined]
    else:
        if cache_key is not None:
            _set_assignment_ore(state, None)
            _clear_task_fields(state)
        _write_free_or_clear(ct, my_id, int(getattr(state, "free_turns", 0)))


def _acquire_committed(ct: Controller, state: object, ore: Position) -> AcquireResult:
    """Contest + table write, shared by ``acquire`` and orphan adoption."""
    my_id = ct.get_id()
    winner = _contest_winner(ct, state, ore)
    if winner != my_id:
        return AcquireResult(ok=False, reason=f"contest_lost_to_{winner}")

    # Commit cache + table together; roll back cache if table write loses.
    _set_assignment_ore(state, ore)
    _clear_task_fields(state)
    if not _write_claim(ct, my_id, ore):
        _set_assignment_ore(state, None)
        _write_free_or_clear(ct, my_id, int(getattr(state, "free_turns", 0)))
        return AcquireResult(ok=False, reason="lost_table_race")

    # Re-read merged table — yield if a lower id is authoritative.
    oid = read_table(ct).get(_ore_key(ore))
    if oid is not None and oid != my_id:
        _set_assignment_ore(state, None)
        _clear_task_fields(state)
        _write_free_or_clear(ct, my_id, int(getattr(state, "free_turns", 0)))
        return AcquireResult(ok=False, reason=f"yielded_to_{oid}")

    state.free_turns = 0  # type: ignore[attr-defined]
    return AcquireResult(ok=True, ore=ore, reason="acquired")


def acquire(ct: Controller, state: object, ore: Position) -> AcquireResult:
    """Atomically reserve ``ore`` or fail — cache and table updated together."""
    reason = why_not_acquirable(ct, state, ore)
    if reason is not None:
        return AcquireResult(ok=False, reason=reason)
    return _acquire_committed(ct, state, ore)


def release(ct: Controller, state: object, *, reason: str = "") -> None:
    """Atomically clear table claim and Assignment cache."""
    my_id = ct.get_id()
    _set_assignment_ore(state, None)
    _clear_task_fields(state)
    _write_free_or_clear(ct, my_id, int(getattr(state, "free_turns", 0)))
    state._last_release_reason = reason  # type: ignore[attr-defined]


def complete(ct: Controller, state: object) -> None:
    release(ct, state, reason="complete")


def transfer(ct: Controller, state: object, new_ore: Position) -> AcquireResult:
    if _asn().get_assignment(state).ore_pos is not None:
        release(ct, state, reason="transfer")
    return acquire(ct, state, new_ore)


def note_harvester(state: object, ore: Position) -> None:
    a = _asn().get_assignment(state)
    if a.ore_pos is None:
        return
    if (a.ore_pos.x, a.ore_pos.y) != (ore.x, ore.y):
        return
    a.harvester_pos = ore
    a.connected = False
    a.completed = False


def owned_by_other(ct: Controller, ore: Position) -> bool:
    my_id = ct.get_id()
    oid = owner_of(ore, ct)
    if oid is None or oid == my_id:
        return False
    return _builder_alive(ct, oid)


def visible_uncovered_ores(ct: Controller) -> list[Position]:
    out: list[Position] = []
    for tile in ct.get_nearby_tiles():
        try:
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            if ct.get_tile_building_id(tile) is not None:
                continue
            out.append(tile)
        except Exception:
            continue
    return out


_CARD = ((0, -1), (1, 0), (0, 1), (-1, 0))


def visible_orphan_harvesters(ct: Controller) -> list[Position]:
    """Friendly harvesters with no belt tile started at all.

    ``visible_uncovered_ores`` skips any ore tile with a building on it, so
    once a belt plan fails/releases *after* the harvester was already built,
    that tile is filtered out of claimable work forever — a real match
    showed harvesters left permanently unlinked to Core because of exactly
    this (2026-08-22). Reopen a harvester as claimable "finish the belt"
    work when none of its orthogonal neighbours is a friendly
    conveyor/splitter, i.e. no belt was ever started from it.
    """
    my_team = ct.get_team()
    out: list[Position] = []
    for tile in ct.get_nearby_tiles():
        try:
            if ct.get_tile_env(tile) != Environment.ORE_TITANIUM:
                continue
            bid = ct.get_tile_building_id(tile)
            if bid is None:
                continue
            if ct.get_team(bid) != my_team:
                continue
            if ct.get_entity_type(bid) != EntityType.HARVESTER:
                continue
        except Exception:
            continue
        has_belt = False
        for dx, dy in _CARD:
            try:
                nbid = ct.get_tile_building_id(Position(tile.x + dx, tile.y + dy))
                if nbid is None:
                    continue
                if ct.get_team(nbid) != my_team:
                    continue
                if ct.get_entity_type(nbid) in (EntityType.CONVEYOR, EntityType.SPLITTER):
                    has_belt = True
                    break
            except Exception:
                continue
        if not has_belt:
            out.append(tile)
    return out


def try_adopt_orphan_harvester(
    ct: Controller, state: object, *, exclude: Position | None = None
) -> AcquireResult:
    """Claim a friendly orphaned harvester to finish linking it to Core.

    Same claim-table protocol as ore acquisition (so at most one builder
    adopts a given tile), but skips the "occupied" rejection in
    ``why_not_acquirable`` since the building here is our own harvester,
    not something blocking a fresh claim. ``exclude`` lets a builder that
    just gave up on this exact tile (see ``_fail_task``'s retry cap) skip
    re-adopting it this call, so a genuinely unreachable harvester doesn't
    trap a builder in an every-turn fail/re-adopt loop forever.
    """
    if _asn().get_assignment(state).ore_pos is not None:
        return AcquireResult(ok=False, reason="already_owning")
    if int(getattr(state, "claim_turns_alive", 0)) < 1:
        return AcquireResult(ok=False, reason="spawn_turn_gate")

    my_id = ct.get_id()
    pos = ct.get_position()
    orphans = visible_orphan_harvesters(ct)
    if exclude is not None:
        orphans = [t for t in orphans if (t.x, t.y) != (exclude.x, exclude.y)]
    ranked = sorted(orphans, key=lambda t: (pos.distance_squared(t), t.x, t.y))
    table = read_table(ct)
    for ore in ranked:
        oid = table.get(_ore_key(ore))
        if oid is not None and oid != my_id and _builder_alive(ct, oid):
            continue
        res = _acquire_committed(ct, state, ore)
        if res.ok:
            return res
    return AcquireResult(ok=False, reason="no_orphan_harvester")


def try_acquire_work(
    ct: Controller,
    state: object,
    *,
    exclude: Position | None = None,
) -> AcquireResult:
    """Single acquisition entry point — logs every candidate rejection."""
    rejects: list[tuple[tuple[int, int], str]] = []
    if _asn().get_assignment(state).ore_pos is not None:
        res = AcquireResult(ok=False, reason="already_owning", rejects=rejects)
        setattr(state, _ACQUIRE_LOG_ATTR, [("*", "already_owning")])
        return res

    pos = ct.get_position()
    candidates = visible_uncovered_ores(ct)
    ranked = sorted(candidates, key=lambda t: (pos.distance_squared(t), t.x, t.y))
    log: list[tuple[object, str]] = []
    best: Position | None = None
    for ore in ranked:
        why = why_not_acquirable(ct, state, ore, exclude=exclude)
        if why is not None:
            rejects.append((_ore_key(ore), why))
            log.append((xy(ore), why))
            continue
        best = ore
        break

    setattr(state, _ACQUIRE_LOG_ATTR, log)

    if best is None:
        reason = "no_winnable_ore" if candidates else "no_visible_ore"
        return AcquireResult(ok=False, reason=reason, rejects=rejects)

    res = acquire(ct, state, best)
    res.rejects = rejects
    if not res.ok:
        log.append((xy(best), res.reason))
        setattr(state, _ACQUIRE_LOG_ATTR, log)
    return res


def last_acquire_log(state: object) -> list:
    return list(getattr(state, _ACQUIRE_LOG_ATTR, []) or [])


def end_turn_bookkeeping(state: object) -> None:
    """Call once at end of builder turn after all Acquire attempts."""
    state.claim_turns_alive = int(getattr(state, "claim_turns_alive", 0)) + 1  # type: ignore[attr-defined]
    if _asn().get_assignment(state).ore_pos is None:
        state.free_turns = int(getattr(state, "free_turns", 0)) + 1  # type: ignore[attr-defined]
    else:
        state.free_turns = 0  # type: ignore[attr-defined]
