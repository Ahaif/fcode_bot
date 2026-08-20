"""Map Knowledge layer — per-unit terrain memory + team-shared sightings/choke.

Persistence (validated):
  - Per-unit module globals persist cross-round for the SAME unit only
    (engine isolates module namespaces per unit).
  - Team-shared facts use comm-store slots 1-3 (sightings + choke).
  - Slot 0 team meta lives in team_store.py; claims stay in claim_manager
    (slots 6-15). Slots 4-5 reserved for Phase 4/5 — never written here.

Phase 3 produces data only — callers must not change builder behavior yet.
"""

from __future__ import annotations

from fcode import Controller, EntityType, Environment, Position

from constants import (
    SLOT_CHOKE_HINT,
    SLOT_SIGHTING_0,
    SLOT_SIGHTING_1,
)
from geometry import in_bounds
from team_store import read_enemy_core, read_own_core

# --- Per-unit module state (isolated per unit by the engine) ---
_ENV_UNKNOWN = 0
_ENV_EMPTY = 1
_ENV_WALL = 2
_ENV_ORE = 3

_width = 0
_height = 0
_known_env: bytearray | None = None
_last_update_round = -1

# Sighting type codes (store).
_TYPE_EMPTY = 0
_TYPE_HARVESTER = 1
_TYPE_CONVEYOR = 2
_TYPE_GUNNER = 3
_TYPE_SENTINEL = 4
_TYPE_LAUNCHER = 5
_TYPE_OTHER = 6

_TYPE_PRIORITY = {
    _TYPE_HARVESTER: 0,
    _TYPE_GUNNER: 1,
    _TYPE_SENTINEL: 1,
    _TYPE_LAUNCHER: 2,
    _TYPE_CONVEYOR: 3,
    _TYPE_OTHER: 4,
}


def _ensure_grid(ct: Controller) -> None:
    global _width, _height, _known_env
    w, h = ct.get_map_width(), ct.get_map_height()
    if _known_env is not None and _width == w and _height == h:
        return
    _width, _height = w, h
    _known_env = bytearray(w * h)  # all UNKNOWN


def _idx(x: int, y: int) -> int:
    return y * _width + x


def _env_code(env: Environment) -> int:
    if env == Environment.WALL:
        return _ENV_WALL
    if env == Environment.ORE_TITANIUM:
        return _ENV_ORE
    return _ENV_EMPTY


def _entity_type_code(et: EntityType) -> int:
    if et == EntityType.HARVESTER:
        return _TYPE_HARVESTER
    if et == EntityType.CONVEYOR or et == EntityType.SPLITTER:
        return _TYPE_CONVEYOR
    if et == EntityType.GUNNER:
        return _TYPE_GUNNER
    if et == EntityType.SENTINEL:
        return _TYPE_SENTINEL
    if et == EntityType.LAUNCHER:
        return _TYPE_LAUNCHER
    if et == EntityType.CORE:
        return _TYPE_EMPTY  # cores go through team_store, not sightings
    return _TYPE_OTHER


def pack_sighting(x: int, y: int, type_code: int, last_seen: int) -> int:
    return (
        (x & 31)
        | ((y & 31) << 5)
        | ((type_code & 15) << 10)
        | ((last_seen & 1023) << 14)
    )


def unpack_sighting(value: int) -> tuple[int, int, int, int] | None:
    v = int(value) & 0xFFFFFFFF
    if v == 0:
        return None
    type_code = (v >> 10) & 15
    if type_code == _TYPE_EMPTY:
        return None
    x = v & 31
    y = (v >> 5) & 31
    last_seen = (v >> 14) & 1023
    return x, y, type_code, last_seen


def pack_choke(x: int, y: int, width_code: int = 0) -> int:
    return (x & 31) | ((y & 31) << 5) | ((width_code & 3) << 10) | (1 << 12)


def unpack_choke(value: int) -> tuple[int, int, int] | None:
    v = int(value) & 0xFFFFFFFF
    if v == 0 or ((v >> 12) & 1) == 0:
        return None
    return v & 31, (v >> 5) & 31, (v >> 10) & 3


def known_env_at(x: int, y: int) -> int:
    """Return per-unit env code or UNKNOWN if out of grid / unset."""
    if _known_env is None or not (0 <= x < _width and 0 <= y < _height):
        return _ENV_UNKNOWN
    return _known_env[_idx(x, y)]


def read_sightings(ct: Controller) -> list[tuple[Position, int, int]]:
    """Team-shared sightings: (pos, type_code, last_seen_round)."""
    out: list[tuple[Position, int, int]] = []
    for slot in (SLOT_SIGHTING_0, SLOT_SIGHTING_1):
        try:
            parsed = unpack_sighting(ct.read_store(slot))
        except Exception:
            continue
        if parsed is None:
            continue
        x, y, tc, rnd = parsed
        out.append((Position(x, y), tc, rnd))
    return out


def read_choke(ct: Controller) -> Position | None:
    try:
        parsed = unpack_choke(ct.read_store(SLOT_CHOKE_HINT))
    except Exception:
        return None
    if parsed is None:
        return None
    return Position(parsed[0], parsed[1])


def _sighting_rank(type_code: int, last_seen: int, round_now: int) -> tuple[int, int]:
    """Lower is better: type priority, then age (newer better → smaller age)."""
    pri = _TYPE_PRIORITY.get(type_code, 9)
    age = max(0, round_now - last_seen)
    return (pri, age)


def _publish_sightings(ct: Controller, candidates: list[tuple[Position, int]]) -> None:
    """Merge local sightings into team slots 1-2 by priority + age."""
    rnd = ct.get_current_round()
    merged: list[tuple[Position, int, int]] = []
    for pos, tc, seen in read_sightings(ct):
        merged.append((pos, tc, seen))
    for pos, tc in candidates:
        merged.append((pos, tc, rnd))

    # Dedupe by tile — keep best rank.
    best: dict[tuple[int, int], tuple[Position, int, int]] = {}
    for pos, tc, seen in merged:
        key = (pos.x, pos.y)
        cur = best.get(key)
        if cur is None or _sighting_rank(tc, seen, rnd) < _sighting_rank(
            cur[1], cur[2], rnd
        ):
            best[key] = (pos, tc, seen)

    ranked = sorted(
        best.values(),
        key=lambda t: (_sighting_rank(t[1], t[2], rnd), t[0].x, t[0].y),
    )[:2]
    packed = [0, 0]
    for i, (pos, tc, seen) in enumerate(ranked):
        packed[i] = pack_sighting(pos.x, pos.y, tc, seen)
    try:
        ct.write_store(SLOT_SIGHTING_0, packed[0])
        ct.write_store(SLOT_SIGHTING_1, packed[1] if len(ranked) > 1 else 0)
    except Exception:
        pass
    _audit_intel(
        ct,
        f"sightings | n={len(ranked)} | "
        + "; ".join(f"({p.x},{p.y}):t{tc}:r{s}" for p, tc, s in ranked),
    )


def _publish_choke(ct: Controller) -> None:
    """Cheap choke hint: midpoint on own→enemy core line with few passable neighbours."""
    own = read_own_core(ct)
    enemy = read_enemy_core(ct)
    if enemy is None:
        return
    # Already have a choke — keep it (Phase 3: no churn).
    if read_choke(ct) is not None:
        return

    mx = (own.x + enemy.x) // 2
    my = (own.y + enemy.y) // 2
    best: Position | None = None
    best_open = 10**9
    # Small search window around midpoint (±2).
    for dx in range(-2, 3):
        for dy in range(-2, 3):
            p = Position(mx + dx, my + dy)
            if not in_bounds(ct, p):
                continue
            try:
                if not ct.is_in_vision(p):
                    continue
                if ct.get_tile_env(p) == Environment.WALL:
                    continue
                if not ct.is_tile_passable(p):
                    continue
            except Exception:
                continue
            open_n = 0
            for ox, oy in ((0, -1), (1, 0), (0, 1), (-1, 0)):
                n = Position(p.x + ox, p.y + oy)
                if not in_bounds(ct, n):
                    continue
                try:
                    if ct.is_in_vision(n) and ct.is_tile_passable(n):
                        open_n += 1
                except Exception:
                    continue
            # Narrow corridor: 1–2 open orth neighbours.
            if open_n <= 2 and open_n < best_open:
                best_open = open_n
                best = p
    if best is None:
        return
    width_code = 0 if best_open <= 1 else 1
    try:
        ct.write_store(SLOT_CHOKE_HINT, pack_choke(best.x, best.y, width_code))
        _audit_intel(ct, f"choke | ({best.x},{best.y}) width={width_code}")
    except Exception:
        pass


def update(ct: Controller) -> None:
    """Incremental map update for this unit. Safe to call every turn."""
    global _last_update_round
    rnd = ct.get_current_round()
    # Still update env every call (vision differs per unit); skip redundant
    # team-store writes within the same round after first pass if desired.
    _ensure_grid(ct)
    assert _known_env is not None

    # Terrain from vision.
    try:
        tiles = ct.get_nearby_tiles()
    except Exception:
        tiles = []
    for tile in tiles:
        try:
            if not ct.is_in_vision(tile):
                continue
            code = _env_code(ct.get_tile_env(tile))
            _known_env[_idx(tile.x, tile.y)] = code
        except Exception:
            continue

    # Enemy building sightings (not Core — that is team_store).
    my_team = ct.get_team()
    candidates: list[tuple[Position, int]] = []
    try:
        buildings = ct.get_nearby_buildings()
    except Exception:
        buildings = []
    for uid in buildings:
        try:
            if ct.get_team(uid) == my_team:
                continue
            et = ct.get_entity_type(uid)
            if et == EntityType.CORE:
                continue
            tc = _entity_type_code(et)
            if tc == _TYPE_EMPTY:
                continue
            pos = ct.get_position(uid)
            candidates.append((pos, tc))
        except Exception:
            continue
    if candidates:
        _publish_sightings(ct, candidates)

    # Choke hint once enemy core known and midpoint visible.
    if rnd != _last_update_round:
        _publish_choke(ct)
        _last_update_round = rnd

    _maybe_debug_draw(ct)


def _maybe_debug_draw(ct: Controller) -> None:
    """Optional replay dots: MAP_INTEL_DEBUG=1."""
    import os

    if os.environ.get("MAP_INTEL_DEBUG", "") != "1":
        return
    try:
        for pos, _tc, _seen in read_sightings(ct):
            ct.draw_indicator_dot(pos, 255, 80, 80)
        choke = read_choke(ct)
        if choke is not None:
            ct.draw_indicator_dot(choke, 80, 200, 255)
        enemy = read_enemy_core(ct)
        if enemy is not None:
            ct.draw_indicator_dot(enemy, 255, 200, 0)
    except Exception:
        pass


def _audit_intel(ct: Controller, msg: str) -> None:
    import os

    if os.environ.get("MAP_INTEL_AUDIT", "") != "1":
        return
    path = os.environ.get("MAP_INTEL_AUDIT_LOG", "map_intel_audit.log")
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"r={ct.get_current_round()} | id={ct.get_id()} | {msg}\n")
    except Exception:
        pass


def reset_for_tests() -> None:
    global _width, _height, _known_env, _last_update_round
    _width = 0
    _height = 0
    _known_env = None
    _last_update_round = -1
