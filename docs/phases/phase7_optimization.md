# Phase 7 — Optimization

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-7--optimization)
(this file does not duplicate the checkboxes — update them there, not here).

## Goal
Remove waste: builder idle %, walking distance, duplicate work, store
usage, CPU usage.

## Architecture reference
Cross-cutting — no single doc owns this. Re-check the "Non-negotiable
engine constraints" section of `docs/00_ARCHITECTURE.md` (10ms CPU budget
per unit per round) and revisit whether `WorldState`/`Map` caching rules
from `docs/01_WORLDSTATE.md` / `docs/02_MAP.md` are actually in place if
those layers exist by this point.

## Where to look
- Idle % / duplicate work: `roles/builder_role.py`'s phase machine — any
  builder that repeatedly re-enters `PHASE_EXPLORE` without progress, or two
  builders converging on the same ore (`economy.ore_claimed_by_other`
  should already prevent this — verify it still does under load).
- Walking distance: `geometry.try_move_toward`'s fallback ranking, and
  `explore.step_explore`'s waypoint distribution across builders.
- Store usage: every `SLOT_*` in `constants.py` — confirm no slot is written
  redundantly every round when it hasn't changed.
- CPU usage: `ct.get_cpu_time_elapsed()` sampled per role; look for
  per-unit recomputation of anything that's actually team-wide-constant
  this round (the exact premature-optimization trap `docs/01_WORLDSTATE.md`
  warns about — don't cache prematurely, but do check for accidental
  O(units × tiles) scans in `economy.scan_ore` / `explore.nearest_frontier`).

## Verification
Faster economy ramp-up and more effective actions per round vs. the Phase 6
baseline, without regressing win rate.
