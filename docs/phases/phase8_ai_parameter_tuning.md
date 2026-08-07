# Phase 8 — AI Parameter Tuning

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-8--ai-parameter-tuning)
(this file does not duplicate the checkboxes — update them there, not here).

## Goal
Optimize parameters automatically (self-play / param sweeps) once Phase 6's
Policy layer is fully wired and benchmarked.

## Rules (from `PROGRESS_TRACKER.md`, repeated here because they're
non-negotiable for this phase specifically)
- AI edits **parameters only**.
- AI **never** edits runtime code.
- Every generation must beat baseline before promotion.

## Where this lives in code today
- `constants.py` — the entire surface area this phase is allowed to touch:
  `MAX_BUILDERS`, `MAX_GUNNERS`, `AMMO_CONVERT_CHUNK`, `TITANIUM_RESERVE`,
  `STUCK_LIMIT`, `WAYPOINTS_BEFORE_COMBAT`, `DEFENSE_RING_SQ`, and whatever
  `docs/06_POLICY.md` thresholds get added in Phase 6
  (`BOOTSTRAP_ROUNDS`, `ATTACK_THRESHOLD`, threat radius).
- `scripts/run_vs_starter.sh` — the benchmark harness a sweep would call
  repeatedly per generation; there is no multi-run sweep/self-play driver
  script yet — building one (outside this repo's runtime bot code, e.g. in
  `scripts/`) is part of what this phase requires.

## Verification
Benchmark passed and generation promoted only if it beats the current
baseline in `PROGRESS_TRACKER.md`'s Benchmark History table — never promote
on a single favorable run; the tracker's core rule (compiles ≠ complete)
applies doubly hard here since parameter sweeps can overfit to one map/seed.
