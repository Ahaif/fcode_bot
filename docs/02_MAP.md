# Layer: Map

## Responsibility

Derived, cumulative spatial knowledge built as vision reveals the map.
Phase 3 implements this as `map_knowledge.py` (data only — Phase 4/5
consume it; builder behavior is unchanged).

## Two-tier design (validated against the real engine)

**Comm store values are u32** (`0..2^32-1`). Module-level Python state
**does not share across units** (each unit gets an isolated module
namespace) but **does persist cross-round for the same unit**. Therefore:

1. **Per-unit tier** (module globals in `map_knowledge.py`): `known_env`
   terrain grid sized to `get_map_width()` × `get_map_height()`, updated
   incrementally from `get_nearby_tiles()` + `get_tile_env`.
2. **Team-shared tier** (comm store): packed facts other units can read
   next round.

Ore **ownership** stays solely in `claim_manager.py` (slots 6–15) —
MapKnowledge does not duplicate claims. Terrain “this tile is ore” is
just `known_env`, not a second claim board.

## Comm store layout (current)

| Slot | Contents |
|------|----------|
| 0 | `SLOT_TEAM_META` — packed own/enemy core + known + gunner count (`team_store.py`) |
| 1–2 | Enemy building sightings (pos + type + last-seen), max 2 |
| 3 | Choke hint (single tile) |
| 4 | **RESERVED** Phase 4 (defense) — leave 0 |
| 5 | **RESERVED** Phase 5 (offense) — leave 0 |
| 6–15 | Claim board (`claim_manager.py`) — unchanged |

### Slot 0 bit layout (`team_store.pack_team_meta`)

| Bits | Field |
|------|--------|
| 0–4 | own_core_x |
| 5–9 | own_core_y |
| 10–14 | enemy_core_x |
| 15–19 | enemy_core_y |
| 20 | enemy_core_known |
| 21–23 | gunner_count |
| 24 | `FLAG_DEFENSE_ACTIVE` (Phase 4; unset until then) |
| 25 | `FLAG_ATTACK_COMMITTED` (Phase 5; unset until then) |
| 26 | `FLAG_TEAM_SPARE` (Phase 4/5 spare boolean) |
| 27–31 | reserved `0` |

Helpers: `read_own_core` / `write_own_core` / `read_enemy_core` /
`write_enemy_core` / `read_gunner_count` / `write_gunner_count` /
`bump_gunner_count` / `read_defense_active` / `write_defense_active` /
`read_attack_committed` / `write_attack_committed` / `read_team_flag` /
`write_team_flag`. Do not decode slot 0 raw at call sites. Slots 6–15
claim board is unchanged (do not shrink).

### Sighting pack (slots 1–2)

| Bits | Field |
|------|--------|
| 0–4 | x |
| 5–9 | y |
| 10–13 | type (HARVESTER=1 … OTHER=6; 0=empty) |
| 14–23 | last_seen_round |

### Choke pack (slot 3)

| Bits | Field |
|------|--------|
| 0–4 | x |
| 5–9 | y |
| 10–11 | width_code |
| 12 | valid |

## Public API (`map_knowledge.py`)

- `update(ct)` — call once per unit turn (wired from core/builder/gunner roles)
- `known_env_at(x, y)` — per-unit terrain code
- `read_sightings(ct)` / `read_choke(ct)` — team-shared reads
- Debug: `MAP_INTEL_DEBUG=1` draws replay dots; `MAP_INTEL_AUDIT=1` logs writes

## What does NOT belong here

- Decisions / missions / changing builder targets (Phase 4+)
- Full team-fused wall maps or >2 enemy building lists
- Expansion scoring (deferred; recompute live later if needed)
- Slots 4–5 pack layouts (Phase 4 / 5)

## Ephemeral (recompute from vision)

Nearby enemy units for melee/fire, local passability, symmetry guess for
enemy Core when `enemy_core_known` is false (`combat.enemy_core_target`).
