# Phase 2 — Economy Optimization

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-2--economy-optimization)
(this file does not duplicate the checkboxes — update them there, not here).

## Goal
Increase titanium income once Phase 1's economy is reliable — better ore
choice, shared logistics, smarter return paths, less idle time.

## Architecture reference
- `docs/03_MISSION.md` — "choose best ore instead of nearest" is a Mission-
  layer scoring upgrade to `nearest_unclaimed_ore`; don't build it until
  Phase 1's dumb nearest-ore version is verified end to end (see that doc's
  warning about this being where the last rebuild broke).
- `docs/02_MAP.md` — ore cluster detection is spatial knowledge that belongs
  in `Map` once it exists; today it has to live as extra logic in
  `economy.scan_ore` (see the gap table in `docs/OVERVIEW.md`).

## Where this lives in code today
- `economy.py` — `scan_ore` is the ore-selection function to extend (nearest
  → best-scored). `ore_claimed_by_other` is the shared-logistics dedup to
  revisit if you add multi-ore-per-builder logic.
- `roles/builder_role.py` — `_economy_exhausted`, `WAYPOINTS_BEFORE_COMBAT`-
  driven fallback is the "avoid long-distance mining" cutoff; tune here
  before adding new heuristics.
- `constants.py` — tuning knobs for any new threshold (keep them here, not
  inline, per the project convention).

## Verification
Confirm mining increases, first titanium arrives earlier, and income per
round is higher than the Phase 1 baseline in the Benchmark History table at
the bottom of `PROGRESS_TRACKER.md` — a regression here blocks promotion
even if the code "looks smarter."
