# Phase 3 — Map Intelligence

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-3--map-intelligence)
(this file does not duplicate the checkboxes — update them there only after
human review of Stage A + Stage B benchmarks).

## Implemented (code)

- `team_store.py` — slot 0 packed team meta; all former `SLOT_CORE_*` /
  `SLOT_ENEMY_*` / `SLOT_GUNNER_*` call sites migrated to helpers.
- `map_knowledge.py` — per-unit `known_env` + team sightings (slots 1–2) +
  choke hint (slot 3). Slots 4–5 reserved unused for Phase 4/5.
- Wired: `map_knowledge.update(ct)` from core / builder / gunner roles.
  **No builder behavior change** — data production only.
- `claim_manager.py` remains sole ore-ownership authority (slots 6–15).

## Persistence facts (validated in-match)

- Module-level state: **same-unit cross-round only** (isolated per unit).
- Comm store: only durable **team-shared** channel (u32 values).

## Verification aids

- Idle suite: confirms no mining regression (Phase 3 must match Stage A).
- Vs starter + `MAP_INTEL_DEBUG=1` / `MAP_INTEL_AUDIT=1`: confirm
  sightings (red dots) and choke (cyan) populate once enemy economy /
  core is scouted. Idle opponents have no buildings → sightings stay empty.

## Goal (unchanged)

Use accumulated map knowledge for better decisions in Phase 4/5 — not yet
consumed for targeting/placement in this phase.
