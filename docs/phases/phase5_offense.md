# Phase 5 — Offense

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-5--offense)
(this file does not duplicate the checkboxes — update them there, not here).

## Goal
Damage enemy economy once ours is secure: find and destroy enemy
harvesters/conveyors, push the enemy Core, retreat when losing.

## Architecture reference
- `docs/03_MISSION.md` — `ATTACK` mission kind.
- `docs/06_POLICY.md` — `ATTACK` mode should be triggered by
  `global_resources > ATTACK_THRESHOLD and enemy_core known`, decided by
  `Policy`. Today the attack/defend split is a static per-builder decision
  in `_enter_late_game` (odd unit id → attack), not a reactive team-wide
  policy — that upgrade belongs to Phase 6.

## Where this lives in code today
- `combat.py` — `try_attack_adjacent` (melee, prefers harvesters),
  `nearest_enemy_harvester` (targeting), `enemy_core_target` (known or
  symmetry-guessed enemy Core), `note_enemy_core` (spotting/broadcast),
  `attack_step` (the per-round offensive behavior).
- `roles/builder_role.py` — `PHASE_ATTACK` branch, `_enter_late_game`
  (decides which builders go on offense).

## Missing: retreat when losing
There is currently no "retreat" logic — `attack_step` always pushes forward.
"Retreat when losing" from the checklist has no home yet; it will likely
need per-unit HP awareness plus a team-wide signal (comm store or future
`Policy`) about whether the team is winning or losing.

## Verification
Confirm enemy mining decreases and win rate increases vs. the Phase 4
(Defense) baseline before moving to Phase 6.
