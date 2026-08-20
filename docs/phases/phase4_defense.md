# Phase 4 — Defense

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-4--defense)
(this file does not duplicate the checkboxes — update them there, not here).

Runs after Phase 3 (Map Intelligence) — enemy-pressure detection and turret
placement both benefit from knowing the map before reacting to it.

## Goal
Protect the economy: detect enemy pressure, place/orient turrets correctly,
protect harvesters and conveyors, repair damage.

## Architecture reference
- `docs/05_ROLES.md` — gunner/sentinel turret placement and firing is a
  `Roles`-layer concern; note `sentinel_role.py` doesn't exist yet (only
  `gunner_role.py`) — adding sentinels is in scope for this phase if the
  checklist calls for it.
- `docs/06_POLICY.md` — "detect enemy pressure" → `DEFEND` mode is the
  target reactive Policy switch. Today this doesn't exist as a team-wide
  mode; see the gap noted in `docs/OVERVIEW.md`'s "Strategy — current"
  section (odd/even unit-id split is a static approximation, not reactive
  threat detection).

## Where this lives in code today
- `defense.py` — `try_build_gunner` (placement, outward-facing, ring logic),
  `defend_step` (per-round defensive behavior), `count_visible_friendly_gunners`.
- `roles/gunner_role.py` — `run_gunner` (auto-fire on `get_gunner_target()`).
- `roles/builder_role.py` — `PHASE_DEFEND` branch in `run_builder`
  (opportunistic melee via `combat.try_attack_adjacent` before falling back
  to `defend_step`).
- `constants.py` — `MAX_GUNNERS`, `DEFENSE_RING_SQ`, `TITANIUM_RESERVE`.

## Verification
Confirm the economy survives attacks and win rate increases vs. the Phase 3
(Map Intelligence) baseline. Use the Replay Review Checklist's "Defense"
section in `PROGRESS_TRACKER.md` — were turrets useful, did they face
enemies, was titanium wasted on bad placements.
