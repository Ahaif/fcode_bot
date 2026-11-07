# Phase 5 — Offense

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-5--offense)
(this file does not duplicate the checkboxes — update them there, not here).

## Goal
Damage enemy economy once ours is secure: find and destroy enemy
harvesters/conveyors, push the enemy Core, retreat when losing.

## Architecture reference
- `docs/03_MISSION.md` — `ATTACK` mission kind.
- `docs/06_POLICY.md` — `ATTACK` mode was originally specified as triggered
  by `global_resources > ATTACK_THRESHOLD and enemy_core known`, decided
  by `Policy`. What's actually built is narrower and reactive-to-nothing:
  a single capped attacker dispatches proactively starting round 1 (see
  below), independent of any economy-strength gate — "just to have a
  first attack" per the original ask that shaped this feature, not the
  timing-aware trigger `06_POLICY.md` describes. That gap is still real
  scope for Phase 6.

## How it works today — one capped, persistent dedicated attacker

Exactly one attacker exists at a time, team-wide, tracked via a comm-store
slot (5) with a heartbeat/staleness mechanism (see "Known bugs found and
fixed" below) rather than a vision-based liveness check.

1. **Dispatch** (`combat.should_dispatch_attacker`) — runs independently
   every round for any free builder, **not** gated behind our own Core
   being under threat (that was the original design; gating attack
   dispatch behind our own `CORE_THREAT` meant that while economically
   ahead — the common case — no attack ever happened at all, confirmed
   against a real match with 3500+ Ti banked and zero attacks the whole
   game). Ranks free builders nearest-to-*our*-Core first
   (`_eligible_attacker_rank`), gated on `ATTACK_RESOURCE_BUFFER`
   affordability and the slot being free
   (`not combat.attack_slot_stale`). `STARVATION` suppresses new dispatch;
   `DEFEND_BUILD` for this specific builder takes priority over sending it
   on offense.
2. **Launcher-assisted travel** (`attack_step`, `roles/launcher_role.py`)
   — the enemy was observed using a Launcher to fling their own builders
   toward our Core far faster than walking; applied the same technique.
   The attacker builds one launcher near our own Core on its way out
   (`try_build_launcher_near_core`, capped to exactly one ever team-wide
   via `team_store.read_has_launcher`/`write_has_launcher`, comm-store
   slot 4), then waits (without moving — the real per-round action
   ordering between a builder's own move and a launcher's `launch()`
   picking it up isn't documented anywhere, so not moving is the safe
   choice) up to `ATTACK_LAUNCHER_MAX_WAIT_ROUNDS` for the launcher's own
   turn to throw it. A position-delta check detects a successful throw and
   resumes normal offense logic the same round. If dispatched from
   outside `ATTACK_LAUNCHER_BUILD_RANGE_SQ` of our own Core, it walks back
   to build the launcher *first* rather than giving up on it and marching
   toward the enemy (a real match showed the launcher not getting built
   until round 699 of a ~1000-round match without this — see "Known bugs"
   below). `LAUNCHER_DEBUG=1` logs every reason a throw doesn't happen —
   this mechanic is genuinely new, previously-untouched engine territory
   this session; nothing about `can_launch`/`launch`/`get_attackable_
   tiles`'s real range/legality could be verified without live evidence.
3. **Targeting near the enemy Core** — once close enough
   (`ATTACK_SENTINEL_RANGE_SQ`), plants one standing sentinel facing the
   enemy Core (`try_build_sentinel_at_enemy_core`) instead of only a
   passing melee hit — a lasting forward position. Uses the exact same
   `best_verified_placement`/`barrier_target`-fallback pattern as
   defense.py's reactive placement (`_nearby_enemy_barrier`), for the
   identical reason: an unverified guess through a wall reads as "wrong
   direction, does nothing" in a real replay.
4. **Economy harassment** (`try_attack_adjacent`,
   `nearest_enemy_logistics_near`) — melees an adjacent enemy building
   (harvesters prioritized). When the enemy Core itself is walled off
   (can't verify a sentinel shot, can never get adjacent to melee it),
   redirects at a nearby enemy conveyor/splitter instead of stalling
   against the Core's tile forever — "if there is no way to core, start
   removing conveyors in tiles adjacent to enemy core so it loses
   economy," per direct ask. A **separate**, narrower function
   (`try_attack_adjacent_economy`, harvester/conveyor/splitter only, never
   Core/gunner/sentinel/barrier) is used by ordinary free builders passing
   an enemy harvester while exploring — the first version reused the
   dedicated-attacker's `try_attack_adjacent` for this and regressed avg
   mining >50% on every benchmark map (a builder adjacent to a 500 HP Core
   gets stuck meleeing it forever, since acting/moving are mutually
   exclusive per round) — caught by the standard benchmark-before-ship
   discipline before it shipped.
5. **Enemy Core location** — `note_enemy_core` publishes a sighting the
   moment any unit sees it; `enemy_core_target` falls back to a rotational-
   symmetry guess (`(W-1-x, H-1-y)` from our own Core) when not yet known,
   since most league maps are symmetric.
6. **Retreat** (`should_recall_attacker`) — recalls on low HP
   (`ATTACK_HP_RETREAT_FRACTION`) or `ATTACK_STALL_ROUNDS` consecutive
   rounds with no progress toward `attack_target`.

## Known bugs found and fixed (see `PROGRESS_TRACKER.md` for full detail)
- **Attack-slot ping-pong** — the original liveness check
  (`attack_slot_holder_alive`) treated *any* exception reading the holder
  — including simply being out of the checking unit's vision, normal on a
  large map, not evidence of death — as "holder is dead." On a
  sufficiently large/sprawling map, two builders far apart could each
  intermittently conclude the other must be dead and steal the slot from
  a still-perfectly-alive holder, with the same-round-race self-
  verification fix (below) then correctly kicking the *original* holder
  out — who a few rounds later would, from its own vision, see the slot
  as vacant again and re-claim, repeating forever with neither ever
  reaching the enemy Core. Fixed with `combat.attack_slot_stale`: a
  round-based heartbeat (the attacker re-writes its claim, refreshing the
  round, every round it runs) with no vision dependency at all —
  `ATTACK_SLOT_STALE_ROUNDS` (15) with no refresh is the only signal that
  ever frees the slot, and that can only mean the holder actually died.
- **Same-round race** — `should_dispatch_attacker` ranks candidates using
  each unit's own vision-limited view, so more than one free builder
  could independently conclude "I'm the winner" in the same round, before
  either write was visible to the other — up to seven builders were seen
  simultaneously self-selecting as attacker within 15 rounds on one map,
  draining the whole team's economy to zero mining. Fixed with
  `combat.attack_slot_claim_confirmed`: one round after claiming, verify
  the comm store actually confirms this builder as holder; release
  immediately if not, instead of waiting out a stall-based recall.

## Where this lives in code today
- `combat.py` — `note_enemy_core`, `enemy_core_target`,
  `try_attack_adjacent`/`try_attack_adjacent_economy`,
  `nearest_enemy_harvester`/`nearest_enemy_logistics_near`,
  `try_build_sentinel_at_enemy_core`/`_nearby_enemy_barrier`,
  `try_build_launcher_near_core`, `attack_step`,
  `should_dispatch_attacker`/`_eligible_attacker_rank`,
  `claim_attack_slot`/`attack_slot_claim_confirmed`/`attack_slot_stale`/
  `release_attack_slot`, `should_recall_attacker`.
- `roles/launcher_role.py` — `run_launcher`: throws only the comm-store-
  tracked dedicated attacker, only when adjacent, at the attackable tile
  closest to the enemy Core.
- `roles/builder_role.py` — `PHASE_ATTACK` branch (race-confirmation
  check, then `should_recall_attacker`/`attack_step`).
- `team_store.py` — slot 4 (`has_launcher`), slot 5 (attacker id + claim
  round, packed).
- `constants.py` — `ATTACK_RESOURCE_BUFFER`, `ATTACK_HP_RETREAT_FRACTION`,
  `ATTACK_STALL_ROUNDS`, `ATTACK_SLOT_STALE_ROUNDS`,
  `ATTACK_SENTINEL_RANGE_SQ`, `ATTACK_LAUNCHER_BUILD_RANGE_SQ`,
  `ATTACK_LAUNCHER_MAX_WAIT_ROUNDS`.

## Verification
Confirm enemy mining decreases and win rate increases vs. the Phase 4
(Defense) baseline. Because "does the attacker actually finish the job"
doesn't show up in a plain win/loss number (a match can still be won on
titanium-collected tiebreak without the attacker ever landing a kill), the
vs-idle benchmark tracks win *reason* specifically — how many of the 46
maps end in "Core destroyed" vs a tiebreak. Current state: 40/46 (87%);
the remaining 6 are large/complex maps where the attacker is confirmed
still alive and still making genuine progress (traced directly, not
assumed) but doesn't finish within the 1000-round match limit — a Bug2-
navigator pathing-speed limit on hard terrain, not a dispatch bug, and a
real pathfinding investment (out of scope for a quick fix) rather than
something fixable by tuning a threshold.
