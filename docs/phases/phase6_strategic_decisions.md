# Phase 6 — Strategic Decisions

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-6--strategic-decisions)
(this file does not duplicate the checkboxes — update them there, not here).

## Goal
Make better long-term decisions: expand vs. defend, attack timing, builder
role balancing, resource reservation, combat production timing.

## Architecture reference
This phase **is** the `Policy` layer from `docs/06_POLICY.md` — build it
**last**, per `docs/00_ARCHITECTURE.md`'s build order, and only once Phases
1–5 reliably win the economy benchmark. Keep it a flat rule table
(`BOOTSTRAP` / `ECONOMY` / `DEFEND` / `ATTACK`) at first, not a scoring
model or search.

## Where the current approximation lives
- `roles/builder_role.py` — `_enter_late_game`: a static, non-reactive
  stand-in for the `DEFEND`/`ATTACK` split (even/odd unit id), made once per
  builder when its personal ore hunt dries up. This is the thing Phase 6
  replaces with a real, team-wide, threat-reactive `Policy.decide()`.
- `constants.py` — every threshold this phase needs (`BOOTSTRAP_ROUNDS`,
  `ATTACK_THRESHOLD`, threat radius) belongs here per `06_POLICY.md`, not
  hardcoded inline — some of today's equivalents already live here
  (`WAYPOINTS_BEFORE_COMBAT`, `TITANIUM_RESERVE`) and are candidates to
  become real Policy inputs.

## Consistency requirement
Same requirement as `docs/04_PLANNER.md`: every unit must land on the same
mode this round. Decide once (recompute locally vs. read from comm store)
and reuse that same mechanism for both `Planner` and `Policy` — don't invent
a second consistency mechanism.

## Verification
This is the finish line for v1 per `docs/06_POLICY.md`: full benchmark
suite, all four modes wired, win rate meaningfully above 50% vs. the
starter bot, before touching AI-assisted tuning or self-play sweeps
(Phase 8).
