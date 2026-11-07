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
  `Roles`-layer concern. `sentinel_role.py` now exists (fires at the
  nearest legal enemy tile from `get_attackable_tiles()` — sentinels have
  no `get_gunner_target()`/`rotate()` equivalent, facing is fixed at build
  time forever).
- `docs/06_POLICY.md` — "detect enemy pressure" → `DEFEND` mode is the
  target reactive Policy switch. Built ahead of schedule, narrower than
  the doc's original design: `condition_state.py`'s `CORE_THREAT` signal
  (see that doc's own "Status" note) is what actually drives this phase
  today, not a full `PolicyMode`.

## How it works today (reactive only — no standing/proactive defense)

Defense is purely **reactive**: nothing gets built unless
`condition_state.get_condition_state(ct)` reports `CORE_THREAT` for a free
builder this round. There is deliberately no persistent "dedicated
defender" role — that was tried (`PHASE_DEFEND_BASE`, a proactive
4-sentinel baseline perimeter mirroring the dedicated attacker) and
explicitly reverted per direction: *"stick with sensing threat and build
counter attack near to threat only one tile away from threat."*

1. **Detection** (`condition_state.get_core_threats`) — an enemy
   gunner/sentinel/builder/barrier within real range of Core registers as
   a `CoreThreat`. Ranges are `CORE_THREAT_RANGE_SQ_GUNNER` (13),
   `_SENTINEL` (32), `_BUILDER` (20 — widened from an original 2 after a
   real match lost its Core in 91 rounds with zero defense ever
   initiated: at range² 2 an enemy builder-rush was only detected the
   round *before* it was already melee-adjacent, nowhere near enough lead
   time for dispatch+travel+build to complete), `_BARRIER` (8, since a
   wall alone doesn't attack). A currently-visible enemy gunner/sentinel
   also gets an authoritative fallback check
   (`condition_state._can_actually_hit_core`, via `can_fire_from` with its
   real live facing) — the range numbers above are only a proxy (the
   engine publishes no real attack-range constant separate from vision;
   see `06_POLICY.md`), and a real match lost its Core to a sentinel
   sitting just outside the proxy radius that this check now also catches.
2. **Dispatch** (`defense.should_enter_defend_build` /
   `builders_needed_for_defense`) — decentralized, same claim-contest
   pattern as ore claiming: every free builder ranks candidates by
   distance to the threat and only the top N (capped at
   `DEFEND_BUILD_MAX_FROM_TRIGGER`) actually enter `PHASE_DEFEND_BUILD`.
   `target_turret_count` scales with the number of *armed* threats, not
   distinct approach directions — three enemies from one side each still
   need a turret's worth of answer. `_any_turret_can_hit` additionally
   escalates the needed count even when the raw turret count already
   looks sufficient, if none of the existing turrets can currently verify
   a hit on the real threat (a friendly unit can wander into an
   already-built sentinel's fixed line after construction — sentinels
   can't rotate at all — silently disarming it while `turrets_near_core`
   still counts it as coverage).
3. **Placement** (`defense.defend_build_step`) — gated on proximity to the
   **threat itself**, not Core's ring (`DEFEND_THREAT_APPROACH_RANGE_SQ`,
   dist² 2 — "sense the threat, build the counter near it, only one tile
   away," per explicit direction). For a `THREAT_BUILDER`-kind threat
   specifically, melees first if already adjacent — the *opposite* order
   from gunner/sentinel below, deliberately: an enemy builder is weak and
   melee is adjacency-only, immune to a *friendly* conveyor/unit blinding
   a fixed-facing sentinel's line the way a real match showed (`can_fire_
   from` blocks on any building in the line, friendly or enemy; belt
   planning has no concept of avoiding an existing turret's firing line,
   so a later conveyor build can silently blind an already-verified
   sentinel — Core was destroyed purely by a builder rush across four maps
   in one match this way). For gunner/sentinel/barrier threats, tries
   `try_build_sentinel` then `try_build_gunner`, each verifying the
   tile+facing with the engine's own `can_fire_from` before committing
   (`best_verified_placement`) —
   `facing_toward()` alone only picks the geometrically closest cardinal,
   it never confirms a hit is actually possible, which is exactly what
   caused the long string of real "sentinel facing the wrong direction"
   reports before this fix. If the direct shot can't be verified (usually
   a friendly or enemy unit in the way), it tries a verified shot at the
   nearest *blocking enemy barrier* instead (`_blocking_barrier`) — a
   real response that starts clearing the lane, rather than
   `first_buildable_placement`'s old unverified blind guess.
4. **Weapon before melee** — if both build attempts fail outright (no
   legal tile at all, not just an unverified one), melee the threat
   directly *only as the last resort*
   (`_try_melee_adjacent_threat`) if it's already adjacent, else advance
   toward it so a later round gets another chance. Melee used to run
   *first* — reverted after direct feedback: a builder deals 2 damage per
   2 Ti per hit, far too slow/costly against a live 30–40 HP gunner or
   sentinel versus just placing a real weapon (2–4 shots once built).
   Melee stays valuable specifically against a barrier (30 HP flat, no
   return fire).
5. **Never freezes** — `STUCK_LIMIT`-based escape releases back to
   `EXPLORE` if physically blocked (e.g. boxed in by an enemy barrier
   wall) instead of retrying the same blocked `step_to` forever.
6. **Economy protection** (`ECONOMY_THREAT`) — cheaper response only:
   `economy_defend_step` repairs an adjacent damaged
   harvester/conveyor/splitter, destroys an adjacent enemy barrier
   blocking the route (`try_destroy_adjacent_blocking_barrier` — a real
   match showed the enemy silently cutting off an already-completed belt
   route this way, built well away from our own Core; `condition_state.
   get_threatened_structures` used to only trigger on an armed unit or
   turret, never a passive barrier), or walks toward the nearest
   threatened structure. Never builds/spawns anything. Separately, *any*
   free builder (not just one dispatched into `ECONOMY_THREAT` response)
   opportunistically heals an adjacent damaged friendly structure while
   exploring (`try_repair_adjacent` wired into `PHASE_EXPLORE`'s
   fallback) — repair used to only ever run while an enemy was *currently*
   nearby, so damage from an earlier hit-and-run sat unrepaired
   indefinitely once the enemy moved on. This still only heals HP damage,
   not a fully-destroyed tile — see `CURSOR_HANDOFF.md` §4 for that gap.
7. **STARVATION interaction** — suppresses *additional* defense spending,
   but never leaves the Core with zero turrets against an active threat
   (`turrets_near_core(ct, core) == 0` emergency override) — early-game
   resources are almost always naturally below the STARVATION floor,
   which would otherwise disable all defense against an opening rush.
8. **Barrier-boxing** — an enemy `BARRIER` counts as a (passive, low-
   priority) `CoreThreat` too, and `economy.py`'s `_known_blocked`/
   `plan_tile_status` treat enemy barriers as clearable obstacles (30 HP,
   a few builder hits) rather than a permanent routing block, so a
   builder box-in around the Core or a harvester gets cleared instead of
   permanently rerouted around.

## Where this lives in code today
- `condition_state.py` — `get_condition_state`, `get_core_threats`,
  `_can_actually_hit_core`, `get_threatened_structures`. Round-scoped
  vision cache via `map_knowledge.get_cached_buildings`/`get_cached_units`
  (redundant `get_nearby_*` calls here were enough to cause real-TLE
  timeouts that skipped a builder's turn — see `PROGRESS_TRACKER.md`'s
  "Known issues" log).
- `defense.py` — `try_build_gunner`/`try_build_sentinel` (verified
  placement, `barrier_target` fallback), `best_verified_placement`/
  `first_buildable_placement`, `target_turret_count`, `turrets_near_core`,
  `_any_turret_can_hit`, `builders_needed_for_defense`,
  `should_enter_defend_build`, `_best_threat`/`_covered_directions`/
  `_blocking_barrier`, `_try_melee_adjacent_threat`, `defend_build_step`,
  `economy_defend_step`/`try_repair_adjacent`. `defend_step` (the old
  pre-Phase-4 static outward-ring builder) still exists but is no longer
  called from anywhere — see "Removed" below.
- `roles/sentinel_role.py` — fires at the nearest legal enemy tile from
  `get_attackable_tiles()`, filtered to enemy-occupied and `can_fire()`.
- `roles/gunner_role.py` — checks team ownership of `get_gunner_target()`'s
  result before firing (that call has no team filter per the engine's own
  docs — a real friendly-fire risk otherwise), and rotates
  (`can_rotate`/`rotate`) toward a known threat after
  `_ROTATE_AFTER_IDLE_ROUNDS` idle rounds.
- `roles/builder_role.py` — the reactive dispatch block (top of
  `run_builder`) evaluates `should_enter_defend_build` for any free
  builder when `CORE_THREAT` is active; `PHASE_DEFEND_BUILD` handler runs
  `defend_build_step` with the grace-period/stuck/starvation exits
  described above.
- `constants.py` — `CORE_THREAT_RANGE_SQ_GUNNER/_SENTINEL/_BUILDER/
  _BARRIER`, `DEFEND_THREAT_APPROACH_RANGE_SQ`, `DEFEND_BUILD_MAX_FROM_
  TRIGGER`, `DEFEND_BUILD_THREAT_GRACE_ROUNDS`, `MAX_SENTINELS_NEAR_CORE`
  (3), `MAX_GUNNERS`, `DEFENSE_RING_SQ`, `TITANIUM_RESERVE`.

## Removed (tried, then explicitly reverted)
A persistent, always-on dedicated base defender (`PHASE_DEFEND_BASE`) —
mirroring the one dedicated attacker, proactively building toward a
4-sentinel perimeter (one per cardinal direction) regardless of whether a
threat was currently active — was built, benchmarked clean, and shipped,
then reverted the same day after direct feedback watching it play: *"revoke
those modifications, and stick with sensing threat and build counter
attack near to threat only one tile away from threat."* If a standing
baseline defense is wanted again later, the removed code (comm-store slot
4 defender-id packing, `should_be_dedicated_defender`/`claim_defender_
slot`/`defender_slot_claim_confirmed`, `defend_base_step`,
`_next_uncovered_base_direction`) is preserved in git history, not
reinvented from scratch — see the commit around `PROGRESS_TRACKER.md`'s
"Reverted" entry in the Known Issues log for exactly what was removed.

## Verification
Confirm the economy survives attacks and win rate increases vs. the Phase 3
(Map Intelligence) baseline. Use the Replay Review Checklist's "Defense"
section in `PROGRESS_TRACKER.md` — were turrets useful, did they face
enemies, was titanium wasted on bad placements. `IDLE_AUDIT=1` (FSM phase
transitions) and `SENTINEL_DEBUG=1` (per-round targeting detail) are
available for root-causing a specific bad placement from a real replay.
