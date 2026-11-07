# Progress Tracker — RTS Bot Development

> Rule:
> A phase is NOT complete because the code compiles.
> A phase is complete only after the benchmark improves or stays equal.
> Every behavioral change must be benchmarked before promotion.

---

# Current Status

> Note (2026-08-22): this file was found reverted to the base commit
> (`8134197 defense`) — i.e. matching the state from *before* this entire
> two-day session's work, even though the code (`combat.py`, `defense.py`,
> `constants.py`, `roles/*.py`, etc.) still carries all the working-tree
> changes from that session. The section below replaces the stale v0.2.9
> snapshot with where things actually stand now. Nothing from this session
> has been committed to git yet.

Current version:
Cursor-refactored `combat.py`/`defense.py`/`team_store.py`/`main.py` (via
`CURSOR_HANDOFF.md`) + a post-refactor fix for a reintroduced caching-bypass
regression (see below). Two full days of iterative fixes preceded the
refactor: attack-slot claim/heartbeat protocol, threat-proximity defend
gate, barrier-aware economy threats, melee-first-for-builder-threats,
verified turret placement with barrier fallback, opportunistic repair.

Current focus:
☑ Phase 1 Economy — stable, decentralized ore claims
☑ Phase 2.5 Navigator (`navigator.step_to` owns all movement)
☑ Phase 3 Map Intelligence (`map_knowledge.py`, round-scoped Controller-API
  cache — `condition_state.get_cached_buildings`/`get_cached_units`)
☑ Phase 4 Defense — reactive: `condition_state.get_core_threats` /
  `get_threatened_structures` (incl. barriers) → `defense.defend_build_step`
  (verified placement, barrier fallback, melee-first for builder threats,
  opportunistic repair)
☑ Phase 5 Offense — one dedicated attacker (`combat.py`): claim/heartbeat
  slot protocol, BFS global routing (`pathfind.bfs_waypoint`) around wall
  bands, launcher-assist, sentinel-plant-then-melee-perimeter mission
☐ Phase 6 Policy / world-state object — not built; each role still reads
  `ct.*`/`condition_state` directly (works fine at this scale per
  `docs/ARCHITECTURE.md`'s World State row)

Validation after Cursor's refactor + my caching-bypass fix (2026-08-22):

vs-idle benchmark (46 maps, seed 1, `--tle 0`):
- **44/46 (96%) win via Core destroyed**, 2/46 win via tiebreak
  (`longhouse`, `royale` — both already at 1000-round cap with a huge
  titanium lead, not a failure to attack)
- **0/46 losses, 0/46 zero-mining**
- Avg mining 2648; most Core-kills land well under round 300 (fastest:
  `vault`=82, `bifrost`=85, `fjord`=88, `atoll`=99, `crossfire`=99)

TLE=10 stress test (8 trials, `showdown.map26` vs `bots/starter`, real
per-unit timing enforced):
- **8/8 Core-destroyed wins**, turns 142–191, no TLE/crash on our side

Regression found and fixed post-refactor: several functions in the
Cursor-rewritten `combat.py`/`defense.py`/`roles/builder_role.py` called
`ct.get_nearby_buildings()`/`ct.get_nearby_units()` directly instead of
through the round-scoped cache — the exact bug class that caused real TLE
timeouts earlier this session. Fixed all 11 call sites (5 in `defense.py`,
5 in `combat.py` + added the missing `import condition_state`, 1 in
`roles/builder_role.py`); `pytest tests/ -q` (168 tests) and both
benchmarks above confirm no behavioral regression from the fix.

Two more issues caught at submission time (2026-08-22), both fixed before
shipping:
- `roles/builder_role.py` had a module-level `os.path.join(...,  __file__)`
  default for its (unused-by-default) audit-log path — the platform's
  sandboxed loader has no `__file__` on bot modules, so this crashes the
  whole bot load 100% of the time regardless of whether the audit flag is
  set. Same failure class as the 2026-08-20 outage. Fixed by defaulting to
  a plain relative path (`"builder_audit.log"`), matching
  `idle_investigate.py`'s existing safe pattern. Caught by
  `scripts/package_submission.sh`'s own `__file__` guard, which stopped the
  first submit attempt (v56 was never actually shipped — the guard failed
  before `fcode submit` ran).
- `pathfind.py` (Cursor's new BFS routing module, imported by `combat.py`)
  was missing from `TOP_LEVEL_FILES` in `scripts/package_submission.sh` —
  same failure class as the 2026-08-21 `roles/sentinel_role.py` outage
  (new top-level module added to the codebase, never added to the explicit
  packaging list, so it silently ships without it and crashes on
  `import pathfind`). **This one did ship**: v56 (ID
  `7a06ef08-0ff4-4e4e-a636-f2986ef49376`) went out without `pathfind.py`
  and would have crashed on load. Fixed by adding `pathfind.py` to
  `TOP_LEVEL_FILES` and resubmitting as v57 (ID
  `e38b7eec-e28f-4933-8e47-3d958ac62450`), confirmed active via
  `fcode status`. v56 should be treated as broken/dead if it shows up
  anywhere (match history, rating deltas) — v57 is the real post-refactor
  submission.

Round 2 of post-refactor bug reports (2026-08-22, from real ladder matches
`c9658dd2` and `7f2b7c13` — both post-v57, currently active submission):
economy left built harvesters permanently unlinked to Core; the attacker
got dragged into destroying an entire enemy belt network instead of
reaching the Core; a busy economy was never able to counter an armed
rush because every builder was mid-mining-task; and a pathing deadlock
(friendly harvester + wall pocket) could strand the attacker for the rest
of a match. All four fixed and validated — **46/46 (100%) Core-destroyed
vs idle** (up from 44/46; even `longhouse`/`royale`, tiebreak-only all
session, now finish via Core kill), 0/46 losses, 0/46 zero-mining, 8/8
clean TLE=10 wins vs `bots/starter` (turns 153-639). 178 unit tests pass.

- **Orphan harvesters**: `claim_manager._fail_task`-adjacent releases
  (`_fail_task` in `roles/builder_role.py`) dropped ownership of a belt
  task *after* the harvester was already physically built —
  `visible_uncovered_ores` then filtered that tile out of claimable work
  forever (`ct.get_tile_building_id(tile) is not None`), so nothing ever
  came back to link it. Added `claim_manager.visible_orphan_harvesters`
  (friendly harvester, no belt-adjacent neighbour) +
  `try_adopt_orphan_harvester` (same claim-table protocol, skips the
  "occupied" rejection) as a fallback in `_try_acquire_mining_task`, with
  a same-tile retry cap (`ORPHAN_HARVESTER_RETRY_CAP=3`) so a genuinely
  unreachable harvester can't trap a builder in an every-turn loop.
- **Attacker distraction**: `combat.try_attack_adjacent_economy` fired on
  *any* adjacent enemy logistics building, including ones merely brushed
  past off the planned route — an extensive enemy belt network near their
  Core meant the attacker could spend the whole match destroying it tile
  by tile instead of reaching the Core. Added
  `combat.blocking_logistics_tile` — before the sentinel is planted
  (phase 2, "no mid-travel detours"), only the one tile actually on this
  turn's step is engaged; the liberal any-adjacent behavior is now scoped
  to the post-sentinel perimeter mop-up phase (4) where it's the intended
  design.
- **Defenseless against a fast rush**: `defense.should_enter_defend_build`
  only ever considers *free* builders — a fast enough rush (real match:
  Core destroyed in 60-105 rounds on 4/5 maps) could plant an armed
  threat before any builder happened to be free, going completely
  unanswered. Added `defense.core_in_immediate_danger` (armed threat +
  zero friendly coverage) and `should_interrupt_for_core_defense`, wired
  into `roles/builder_role.py` as the one case a mining task may be
  interrupted — tightly gated so a routine, already-answered threat never
  yanks miners. Synergizes with the orphan-harvester fix: an interrupted
  task's harvester is safely picked back up later instead of staying lost.
- **Attacker pathing deadlock**: found via the vs-idle benchmark itself —
  `stavkirke` regressed from Core-destroyed to a 1000-round tiebreak after
  the above fixes (a knock-on effect of more harvesters actually
  completing: one ended up blocking the one gap in a wall band). Root
  cause was `should_recall_attacker`'s stall counter comparing only
  against the *previous* round's distance, which resets to 0 on any local
  wiggle — an attacker oscillating through the same tiles for 40+ rounds
  without net progress never tripped recall. Changed to track the *best*
  distance ever reached (reset only when the mission target itself
  changes); the attacker now recalls and a redispatch finds a clear route.
  This one fix alone took the benchmark from 44/46 to 46/46.
- Also found while wiring the orphan-harvester fix: `claim_manager.
  _contest_winner` called `ct.get_nearby_units()` directly (same
  regression class as the post-refactor caching-bypass bug above,
  pre-existing in this specific function since it was never part of the
  refactor). Fixed via `map_knowledge.get_cached_units`, which also
  surfaced and fixed a latent sentinel-collision bug in
  `map_knowledge._refresh_entity_cache`/`reset_for_tests` (`-1` doubled as
  both the "reset" and "no round available" values, so a test double
  without `get_current_round()` silently got a permanently empty cache).

Round 3 of post-refactor bug reports (2026-08-22, from real ladder match
`ab3fb3ba` vs a strong opponent — longhouse/auroraveil/icefloe/jotunheim
lost, 73-377 rounds): sentinels placed near wherever the defending
builder happened to be standing rather than pinned on the threat; a
resource-scarce economy losing to a fast rush ("economy not enough");
and — reported separately — a harvester+belt task abandoned mid-link
after a brief titanium dry spell. All fixed and validated: still
**46/46 Core-destroyed vs idle**, 0 losses, 0 zero-mining, 8/8 clean
TLE=10 wins vs `bots/starter` (turns 147-404). 178 unit tests pass.

- **Mispositioned sentinels**: `defense.best_verified_placement`'s
  candidate tiles were the *defending builder's* own orthogonal
  neighbours, not the threat's — verified via `can_fire_from` so any
  placement returned could genuinely hit the target, but the candidate
  set itself could miss the tile actually pinned against the threat,
  leaving room for something to end up between them. Candidates are now
  the threat's four orthogonal neighbours first (nothing can be between
  turret and target), falling back to the builder's own neighbours so a
  sentinel already reachable this round doesn't wait for extra movement.
- **Fast rush outpacing a threat-scarce economy**: `defense.
  core_in_immediate_danger` (armed threat, zero coverage — same gate as
  v58's mining-task interrupt) now also pauses a free builder from
  *starting* a brand-new mining task, so titanium isn't split between
  economy expansion and the sentinel that actually saves the Core.
  Already-claimed tasks are untouched. User-confirmed trade-off: only
  applies during the same rare dire window as the v58 interrupt, so
  normal games (including the full idle benchmark) are unaffected.
- **Harvester+belt abandoned after a resource dry spell**: the pre-harvester
  stuck-cancel gate (`STUCK_LIMIT=3`) fired in `PHASE_BUILD_HARVESTER`
  too, where standing still is *correct* (already in position) — a brief
  titanium shortage read as "stuck" and released a perfectly good claim
  within 3 rounds. Now gated to `PHASE_MOVE_TO_STAND` only (a genuine
  navigation phase); `PHASE_BUILD_HARVESTER` uses the same lenient
  9-round threshold as the post-harvester belt-recovery path. Also:
  `try_build_plan_tile` returning `"insufficient_resources"`/`"cooldown"`
  (a benign wait, not a routing problem) now resets the stuck counter
  directly in `_state_build_conveyor`'s two call sites, so it can no
  longer burn the `MAX_BELT_REPLANS` budget purely from titanium being
  temporarily tight.

Round 4 of post-refactor bug reports (2026-08-22, match `eca9c028` vs
Memtrace, fimbulwinter lost to a pure-builder rush in 146 rounds — no
sentinels/gunners involved at all): not enough defenders ever dispatched
against a multi-builder rush; the attacker abandoning an active siege
(or an obstacle fight) once a false "stall" tripped. Fixed and validated:
still 46/46 Core-destroyed vs idle, 0 losses, 0 zero-mining, 8/8 clean
TLE=10 wins vs `bots/starter` (turns 152-371). 180 unit tests pass.

- **Defender count silently capped at 2 forever**: `DEFEND_BUILD_MAX_FROM_
  TRIGGER=2` was meant as a per-round throttle ("don't dispatch everyone
  at once"), but `builders_needed_for_defense`'s `have` only counts
  *built* turrets (not builders already en route), and `should_enter_
  defend_build`'s ranking includes already-dispatched builders too — so
  with `have` staying near 0 while defenders are still walking/building,
  `needed` stayed capped at 2 *forever*, regardless of how many armed
  threats actually existed. A 4-direction builder rush only ever got ~2
  defenders. Raised the cap to 4 to match a full rush.
- **Attacker recalled off an active siege/obstacle fight**: `should_recall_
  attacker`'s stall counter measured only distance-to-target — an attacker
  parked adjacent to the enemy Core grinding it down with melee, or
  fighting an enemy conveyor blocking its path, never gets closer in
  tile-distance while doing exactly what its mission requires, so it
  silently accumulated "stall" rounds even while succeeding and got
  recalled mid-kill (real match: reached the enemy Core by round 70, then
  abandoned it for a harvester once the counter tripped ~40 rounds later).
  `attack_step` now sets `state.attack_acted` whenever it successfully
  fires/builds; `should_recall_attacker` treats that as progress and
  resets the stall counter, regardless of distance. A genuinely stuck,
  actionless attacker (e.g. the stavkirke wall-pocket case from Round 2)
  still recalls on schedule — the exemption only covers rounds that
  actually did something.
- Investigated and *not* changed, with reasoning: (1) sentinel rotation —
  reconfirmed against the engine stub, sentinels genuinely cannot rotate;
  no code lever exists beyond what's already shipped (verified placement,
  melee-first-for-builders, opportunistic repair). (2) "keep checking
  economy during a core threat" — `get_condition_state`'s STARVATION >
  CORE_THREAT > ECONOMY_THREAT priority is per-builder, and each builder
  independently computes its own condition from its own vision each round,
  so a builder near a barrier-cut belt already reports ECONOMY_THREAT
  for itself regardless of what a different builder near the Core reports
  — the team-level behavior the report asked for already falls out of the
  existing per-unit design; no concrete bug found here to fix.

Builder-count A/B experiment (2026-08-22, requested after losing to a
4-builder opponent): benchmarked `MAX_BUILDERS` at 6, 7 (prior value), and
8 — same 46-map vs-idle suite + 8x TLE=10 vs `bots/starter` each. **6
won clearly**: 0/46 zero-mining, fastest/most consistent Core-kills (idle
avg mining 1028; TLE=10 turns 123-176, tighter and faster than 7's
152-371). **8 regressed**: still 46/46 Core-destroyed, but 2/46 maps
(`showdown`, `string` — small/tight layouts) hit *zero mining* despite
winning — too many builders crowded a small map's ore/route capacity and
stalled the economy entirely, just masked by the attacker eventually
grinding the Core down anyway. 7 was safe but strictly worse than 6 on
both axes. `MAX_BUILDERS` set to 6, with the comparison numbers recorded
in a comment at the constant itself for the next tuning pass.

Round 5 of post-refactor bug reports (2026-08-22, requested redesign +
match `bbcb63cd`, `paths` map lost to a wrong defender pick). Fixed and
validated: 46/46 Core-destroyed vs idle (0 tiebreaks), 0 losses, 0
zero-mining, 8/8 clean TLE=10 wins vs `bots/starter` — fastest/tightest
range yet (turns 120-204). 187 unit tests pass.

- **Attacker mission redesign (user-requested)**: was "plant one sentinel,
  then melee the conveyor perimeter." Now plants sentinels around the
  enemy Core on up to `ATTACK_MAX_SENTINELS=4` sides (best_verified_
  placement's candidates are the Core's own orthogonal neighbours, so an
  already-occupied tile is naturally skipped and a fresh side tried) for
  sustained ammo-fed pressure from every direction. Once that ring is
  complete, mop-up now prefers building a barrier (`try_build_barrier_
  blocking` — 3 Ti, no ammo, no combat exposure, engine's own
  `build_barrier` API, never used offensively before) adjacent to the
  nearest enemy economy target to deny it, only falling back to melee when
  no legal barrier placement exists. Barrier attempts are gated on the
  attacker actually being adjacent to the target — attempting it from far
  away would just wall off empty ground wherever the attacker happened to
  be walking, wasting Ti on a barrier blocking nothing relevant.
- **Wrong (walled-off) builder picked as sole defender**: `_rank_
  defenders_by_threat_distance` ranked candidates by raw `distance_
  squared` — a poor proxy on a maze-like map (`paths`, walled corridors
  throughout): a builder numerically close but behind a wall band can take
  far longer to arrive than one slightly farther with a direct corridor.
  Real match evidence: builder 8 was picked and "took many rounds to
  reach the core" while genuinely closer builders sat idle. Added
  `pathfind.bfs_path_length` (shares its BFS core with the existing
  `bfs_waypoint`) and wired it into the ranking via a new `_walk_distance`
  helper, falling back to Manhattan distance (same step-count units,
  unlike squared Euclidean) when BFS can't resolve — goal in fog, or
  walled off in known terrain.

Round 6 of post-refactor bug reports (2026-08-22, jotunheim economy
deadlock — investigated with a full local reproduction methodology — plus
a separately-reported sentinel-rebuild predictability bug, icefloe).
Fixed and validated: 46/46 Core-destroyed vs idle, 0 losses, 0
zero-mining, 8/8 TLE=10 wins vs `bots/starter` (7 Core-destroyed + 1
tiebreak, still a win). 187 unit tests pass. Full 46-map vs-`bots/starter`
benchmark (not just idle): 45/46 wins, avg mining 6314 — jotunheim
specifically went from a reproducible 0-mined tiebreak loss to a clean
3600-mined Core-destroyed win.

- **Root-cause methodology**: reproduced the reported deadlock locally
  (`jotunheim.map26` vs `bots/starter`, `--tle 0`) via a seed sweep —
  discovered the match outcome is **not fully determined by `--seed`**
  (confirmed with `PYTHONHASHSEED=0` too — still varies), so the deadlock
  is a real but non-deterministic tail event (~3-5% of runs) rather than a
  map-specific bug reproducible on demand. This means no single "fixed
  seed" repro is reliable — validate this failure mode via seed *sweeps*
  (30+ trials), not a single run.
- **The actual deadlock mechanism** (found via code reading, since the
  bug couldn't be pinned to a debuggable single run): a builder in
  `PHASE_DEFEND_BUILD` only ever exits when `get_core_threats` returns
  empty — unbounded. An opponent maintaining persistent pressure (or a
  threat with no legal placement) can strand a builder there for the rest
  of the match; with up to `DEFEND_BUILD_MAX_FROM_TRIGGER=4` builders
  stuck this way at once, this — not just the v59 free-builder Ti-pause
  gate, which never even applies to a builder already committed — is the
  primary mechanism behind severe economy starvation. Added
  `DEFEND_BUILD_TIMEOUT_ROUNDS=40`: give up on an unresolvable
  threat-response attempt and return to economy rather than deadlock
  forever. Also bounded the existing Ti-pause gate itself
  (`ECONOMY_PAUSE_MAX_ROUNDS=30`) the same way, since it had the same
  unbounded-if-the-threat-never-clears shape.
- **Attacker sentinel rebuilt in the same (easy-to-snipe) spot** — and
  **ring considered "complete" forever even after losses**: both traced to
  `state.attack_sentinels_built` being a monotonic counter that never
  accounted for the enemy destroying a sentinel, and placement having no
  memory of tiles already tried. Replaced the counter with
  `combat._count_friendly_sentinels_near` — a live recount every round, so
  a loss is noticed and retried automatically — and added `exclude` to
  `defense.best_verified_placement` / `combat.try_build_sentinel_at_enemy_
  core`, tracking every tile used this mission in `state.
  attack_sentinel_tiles` so a destroyed sentinel is rebuilt on a fresh
  side instead of predictably in the exact spot the enemy just sniped.

Round 7 of post-refactor bug reports (2026-08-22, match `a0f75ab6` — all 5
games hit the 1000-round tiebreak, lost 4/5). Two reports, both traced to
real bugs, one of them self-inflicted by Round 6's own timeout fix. Fixed
and validated: 46/46 Core-destroyed vs idle, 0 losses, 0 zero-mining, 8/8
TLE=10 wins vs `bots/starter`. Full vs-`bots/starter` sweep: 45/46 wins —
all 5 maps from this report (fimbulwinter/icefloe/stavkirke now
Core-destroyed; helheim/midgard tiebreak but with a decisive mining lead
instead of being outmined, unlike before). 189 unit tests pass.

- **Economy builders "frozen" despite resources allowing new builds** —
  self-inflicted by Round 6's `DEFEND_BUILD_TIMEOUT_ROUNDS`: a builder
  that gives up on an unresolved threat and resumes economy is
  immediately eligible to be pulled straight back into `PHASE_DEFEND_
  BUILD` by the *same* still-unresolved threat the very next round —
  defend/timeout/resume/defend cycling so fast a freshly (re)started
  harvester+belt chain never gets far enough to matter. Added
  `DEFEND_BUILD_COOLDOWN_ROUNDS=60`: after a timeout, ineligible to
  re-enter `PHASE_DEFEND_BUILD` (either dispatch path) for a while.
- **Attacker "spinning in the same 4 tiles" near a wall** — `pathfind.
  bfs_waypoint` was recomputed fresh every single round; on partial
  (fog-limited) terrain knowledge, BFS can legitimately choose a
  *different* shortest path as new tiles are revealed each round even
  though the underlying situation barely changed, so the waypoint
  flip-flopped between neighbouring tiles instead of committing to a route
  around the wall (this exact oscillation was directly visible in a
  stavkirke debug trace from Round 2, revisited here). Added
  `combat._sticky_bfs_waypoint` (commits to a waypoint for
  `ATTACK_BFS_STICKY_ROUNDS=6` rounds, recomputing early only if the
  target changes or the waypoint is reached).

Round 8: attacker mission priority redesign (2026-08-22, user-requested
after match `814f7829` — 5-0 loss, all games to tiebreak). Fixed and
validated: 46/46 Core-destroyed vs idle, 0 losses, 0 zero-mining, 8/8
TLE=10 wins vs `bots/starter` — fastest range yet (113-160 turns). Full
vs-`bots/starter` sweep: **0/46 losses** (first zero-loss run this
session), avg mining 6667 (best yet) — all 5 maps from this report now
Core-destroyed wins. 190 unit tests pass.

- **Removed the `ATTACK_MAX_SENTINELS` hard cap**: the mission now keeps
  attempting to plant sentinels around the Core every round, unbounded, as
  long as it's in range and can afford one ("build and point sentinels
  around the core as long as the economy allow it") — `best_verified_
  placement`'s candidates are the Core's own orthogonal neighbours, so
  this self-limits once every reachable side is legitimately occupied,
  no artificial counter needed.
- **Reordered mission priority**: sentinel-build is now tried first every
  round (not gated on a running tally); economy denial (barrier, then
  melee, on the conveyor/splitter/harvester perimeter) is the fallback
  only when a sentinel wasn't buildable this round (no legal spot or
  insufficient Ti); meleeing the Core directly is demoted to the last
  resort, tried only when neither of the above worked
  ("I want him to attack conveyors surrounding core... only go for
  [that] if there is no ammo to fire — if ammo exist, build and point
  sentinels").
- `ATTACK_MAX_SENTINELS` constant kept (used only by tests/docs now,
  no longer gates production logic) rather than deleted, since the
  4-candidate-tile geometry it documents is still the practical ceiling.

Round 9: gate new attacker dispatch on CORE_THREAT (2026-08-22, user
strategic question after match `e242d77b` — Core destroyed in 80 rounds,
"no counter attack from my side"). `should_dispatch_attacker`'s caller
only excluded STARVATION, not CORE_THREAT — a free builder could be sent
off to start a brand-new offensive mission even while the Core was under
active attack elsewhere. Now a fresh attacker is never dispatched during
CORE_THREAT; an already-dispatched attacker is unaffected. Validated:
46/46 vs idle, 0 losses, 8/8 TLE=10 wins, vs-`bots/starter` sweep 45/46 —
`glacierkeep` (this report) now Core-destroyed instead of lost;
`paths`/`skald` (also this report) now win via tiebreak with a healthy
mining lead instead of getting outmined. 190 unit tests pass.

Considered and declined (for now): having ordinary economy builders
opportunistically damage enemy conveyors near their harvesters/core while
exploring. Reasonable idea, but pulls economy builders into offensive
side-trips right when the ask is to prioritize economy stability, on top
of an economy already tuned lean (`MAX_BUILDERS=6`, confirmed by direct
A/B benchmark to outperform more builders) — revisit only if explicitly
requested again.

Round 10: restored opportunistic repair + added opportunistic attack
during explore (2026-08-22, user requested — "refactor all" after match
`fa435fa3`, 4-1 loss, "many idle builders"). Found that the fallback
dispatch for `PHASE_EXPLORE` was a bare `step_explore` call with no
opportunistic action at all — the "repair damaged infrastructure while
exploring" fix from earlier this session had been lost somewhere in
Cursor's refactor (`defense.try_repair_adjacent` existed but was only
wired into `economy_defend_step`, never the explore fallback). Restored
it, and added the equivalent for offense: an exploring builder now also
fires on an adjacent enemy harvester/conveyor/splitter it happens to pass
(`combat.try_attack_adjacent_economy`, adjacency-only so never a deliberate
detour). Validated: 46/46 vs idle, 0 losses, 8/8 TLE=10 wins, 0/46 losses
vs `bots/starter`. 190 unit tests pass.

Investigated but not resolved: the reported "0 Ti collected by round 242"
catastrophic economy failure (real match, `fa435fa3`, opponent "Team 48").
Could not reproduce against `bots/starter` (15-seed sweep on the same map,
all showed live economies) or find a code path that would fully halt
mining outside an active, unresolved CORE_THREAT/immediate-danger episode
(already bounded by Round 6/7's timeout+cooldown fixes). Likely specific
to this opponent's exact playstyle rather than a general bug — needs a
replay with a visibly-idle builder's own status panel (unit ID, phase,
position) to pin down further, the same way the enemy-sentinel-ID
screenshot pinned down Round 9's issue.

Round 11: builder-only rushes now count as "immediate danger" (2026-08-23,
match `4ce685a5` — "opponent target my core only with his builders and I
had no defense"). `defense.core_in_immediate_danger` — the gate for
whether a *busy* mining builder can be interrupted to defend — only
checked for gunner/sentinel threats, explicitly excluding THREAT_BUILDER.
A pure builder-only rush therefore never triggered the interrupt path (the
only one that can pull a busy builder off task); if every builder happened
to be mining when the rush arrived, none could respond and it walked in
unopposed. Broadened to any non-barrier threat kind. Also fixes the same
gap in the Ti-priority pause (same underlying check), so economy correctly
stops spending on new harvesters during an uncovered builder rush too, not
just an uncovered gunner/sentinel one. Validated: 46/46 vs idle, 0 losses,
8/8 TLE=10 wins, vs-`bots/starter` sweep 45/46 — all 5 maps from this
report now win. 192 unit tests pass.

Round 12: re-ran the builder-count A/B experiment (2026-08-23, requested
after the Round 11 fixes) since the bot's behavior has changed
substantially since the first pass. Swept `MAX_BUILDERS` ∈ {4, 5, 6, 7, 8}
against the full 46-map `bots/starter` benchmark (more realistic than the
vs-idle-only first pass): 4 had 2/46 losses + 4/46 zero-mining (too lean
for this much sharper defense/attack logic to run on); 5 was clean but a
weaker 27/46 Core-destroyed rate; 6 and 8 tied for best Core-destroyed
rate (31/46) with 0/46 losses each, but 8 showed 2/46 zero-mining
(`bridge`, `fjordgate` — builder overcrowding on tight maps, same failure
mode as the first pass) while 6 showed none; 7 was clean but slower
(25/46 Core-destroyed). **6 confirmed as the best value a second time**,
now with an updated comment recording both benchmark passes. Final
validation: 46/46 vs idle, 0 losses, 0 zero-mining, 8/8 TLE=10 wins. 192
unit tests pass.

Round 13: sentinels never used against a mobile (builder) threat, not just
adjacent-first (2026-08-23, match `8b2136b6` — "two sentinels built... none
of them fired... Core destroyed anyway"). `defend_build_step`'s
melee-first-for-builders rule only applied once the defender was already
literally orth-adjacent to the threat; while still closing in (within
`DEFEND_THREAT_APPROACH_RANGE_SQ` but not yet adjacent) it fell through to
sentinel-placement — verified via `can_fire_from` against wherever the
threat *was that instant*, which is already stale for a mobile target by
the time the sentinel is actually standing (a builder keeps walking; a
gunner/sentinel building does not). THREAT_BUILDER now never reaches the
sentinel-placement branch at all, at any distance — approach-then-melee
only. Validated: 46/46 vs idle, 0 losses, 8/8 TLE=10 wins, 0/46 losses vs
`bots/starter` — best result yet (33/46 Core-destroyed); `yggdrasil` (this
report's map) now wins. 193 unit tests pass.

Also investigated from the same report, not resolved: a builder shown
frozen (unit panel: Action CD 1, Move CD 0) at round 29 "in front of a
harvester" — most likely a normal single-round action-cooldown wait
(nothing in the code gates movement on the action cooldown, so this
should self-resolve the very next round) rather than a genuine freeze, but
unconfirmed without a replay parser; three builders "following each other"
during exploration and the attacker engaging a harvester before reaching
a not-yet-confirmed enemy Core were also reported but not independently
verified — flagged to the user as needing more specific evidence (a
timestamped screenshot sequence, the way the frozen unit's panel pinned
down this round's actual fix) rather than guessed at further.

Round 14: attacker never cleared an enemy barrier blocking its path
(2026-08-23, match `347ff4f4`, "paths" map — a dense maze layout;
"frozen... not even attacking conveyors"). `_ENEMY_LOGISTICS` (used by
`blocking_logistics_tile`, `try_attack_adjacent_economy`, and
`nearest_enemy_logistics_near`) only ever listed CONVEYOR/SPLITTER/
HARVESTER — never BARRIER. A barrier is "cheap HP wall, no other
function" (AGENTS.md): pure block, can't fire back, so clearing one is
never risky and can only help — but nothing did, since `pathfind.
bfs_waypoint` doesn't treat any building (barriers included) as an
obstacle, only terrain walls. An enemy barrier planted directly in the
attacker's route left it with no way to clear it and no way to route
around it either — genuinely stuck, matching the report exactly. Added
BARRIER to `_ENEMY_LOGISTICS`. Validated: 46/46 vs idle, 0 losses, 8/8
TLE=10 wins, 0/46 losses vs `bots/starter` — `paths` (this report's map)
now Core-destroyed. 194 unit tests pass.

Also reconsidered from the same report: "sentinels first, then conveyors"
appeared to not be happening — but the match snapshot was turn 39 with
~48 Ti banked, plausibly just not yet enough to afford a sentinel; falling
back to melee/conveyor-cutting at that point matches the existing "only
go for conveyors if there's no ammo/Ti" design rather than violating it.
No separate fix made here without stronger evidence of a real ordering bug.

Round 15: two severe, high-confidence bugs found from concrete per-unit
evidence (2026-08-23, matches `f86973ab` map valkyrie — two builders shown
frozen for 300+ rounds with zero action/move cooldown — and `cdf42bc6` map
icefloe — belt construction abandoned when blocked by another builder).

- **Dead builders were never replaced.** `run_core` gated spawning on
  `state.num_spawned`, a monotonic per-Core tally incremented on every
  spawn and never decremented — once it reached `MAX_BUILDERS` the Core
  stopped spawning forever, even after combat losses shrank the actual
  live roster ("when builder is died... the attacker or other builder
  never spawned"). Switched to `ct.get_unit_count()` — a global,
  engine-tracked stat unlike vision-scoped `get_nearby_units()`, and
  BUILDER_BOT is our only mobile unit type, so it's exactly the live
  builder count. Now replenishes losses instead of only ever ramping up
  once. This likely explains a chunk of the economy-starvation and
  no-response reports from earlier rounds too, retroactively.
- **A friendly/enemy unit standing on a belt-plan tile caused a silent,
  permanent no-op** in `_state_build_conveyor` (`status == "unit"`, both
  the "standing on current tile" and "next tile" cases) — no movement
  attempted, nothing to trigger the navigator's own traffic-avoidance. Two
  builders each waiting on a tile the other occupied could deadlock on
  each other forever, matching the two-builders-frozen evidence exactly.
  Both now call `step_to` toward the blocked tile instead of a bare
  no-op, letting the existing navigator traffic-handling sidestep or
  wait-with-timeout the way every other phase already does.

Validated: 46/46 vs idle, 0 losses, 8/8 TLE=10 wins — fastest range yet
(109-179 turns) — 0/46 losses vs `bots/starter`. `valkyrie`/`icefloe`
(this report's maps) both win with strong, healthy mining leads instead of
a starved economy. 196 unit tests pass (new `tests/test_core_role.py`).

Architecture note:
Planner deferred indefinitely (decentralized claim assignment already
covers it); Navigator promoted to a permanent architecture layer. See
"Architecture Layer Status" below. Next-session starting point:
`CURSOR_HANDOFF.md` (world-state object, builder-role split, defense
split into Threat Sensing / Counter-Attack modules, harvester-connectivity
state — not yet started).

Round 16: mop-up (conveyor-cutting) was chasing distant economy and never
returning to the sentinel mission (2026-08-23, match `0dea2b2c`, lost
0-5, all 5 by Core destroyed — "once he go to harvester or conveyor he
never comeback to built sentinel... he kept stuck fighting with another
enemy builders for harvester enemy").

Two compounding gating bugs in `attack_step`:
- Mop-up (march-target widening + barrier/melee economy denial) was
  gated on `close_to_core` (within `ATTACK_SENTINEL_RANGE_SQ=40` of the
  Core) and "a sentinel wasn't buildable *this round*" — not "a sentinel
  was ever actually planted." A round where Ti was merely short covered
  the same code path as "sentinel mission done," so the attacker could
  swing at economy before ever placing one.
- `nearest_enemy_logistics_near`'s search radius was the same wide
  `ATTACK_SENTINEL_RANGE_SQ` bubble used for sentinel placement — a
  harvester up to ~6.3 tiles from the Core qualified as a march target,
  well past the Core's own footprint. Chasing it (and the enemy builders
  defending it) is exactly what stranded the attacker away from the Core
  for the rest of the match.

User-specified fix: mop-up only starts once at least one sentinel has
actually been built this mission, and is scoped to literally one tile
around the Core's own 2x2 footprint. Added `_dist_sq_to_core_footprint`
(distance to the nearest footprint tile, not just the reference corner)
and a `range_sq` param on `nearest_enemy_logistics_near`; new constant
`ATTACK_CORE_PERIMETER_RANGE_SQ=2`. `attack_step` now computes
`sentinel_ever_built = bool(state.attack_sentinel_tiles)` and
`near_core_perimeter` (attacker within the tight radius) and gates both
the march-target widening (on `sentinel_ever_built` alone, so the
attacker paths toward a perimeter target once one exists) and the
liberal barrier/melee mop-up (on both flags together, so it only fires
once actually there) on these — falling back to the existing strict
`blocking_logistics_tile` (only the one tile genuinely blocking the next
step) at every other time, pre-sentinel or still marching in.

Validated: 46/46 vs idle (0 losses, 0 zero-mining, all Core destroyed),
8/8 TLE=10 wins vs `bots/starter` on `showdown` (seed `1023944511`),
45/46 vs `bots/starter` (single loss: `duel`, a close 6160-vs-8400
tiebreak, pre-existing — not one of this report's maps). All 5 maps from
the reported match (`auroraveil`, `bifrost`, `glacierkeep`, `paths`,
`holmgang`) win cleanly. 198 unit tests pass (2 new tests added for the
gating: distant off-path economy ignored pre-sentinel, mop-up withheld
until actually at the Core's perimeter).

---

# Architecture Layer Status

Maps the current code onto the layers in `docs/ARCHITECTURE.md`, so the
question "which layer owns this / is this layer built" has one answer
instead of being implicit across the phase checklists below. Full
narrative version: `docs/OVERVIEW.md`.

| Layer | Target doc | Status | Where it actually lives |
|---|---|---|---|
| World State | `docs/WORLDSTATE.md` | Not a real object | Every role calls `ct.*` directly (`roles/*.py`, `economy.py`, `explore.py`, `defense.py`, `combat.py`). Fine at this scale; revisit only if reading logic starts duplicating across roles. |
| Map Knowledge | `docs/02_MAP.md` | **Built (data-only)** | `map_knowledge.py` + `team_store.py`. Per-unit `known_env`; team sightings slots 1–2; choke slot 3; meta slot 0. Claims still `claim_manager` slots 6–15. Slots 4–5 reserved for Phase 4/5. Not yet consumed by defense/offense behavior. |
| Mission | `docs/03_MISSION.md` | Not started | No mission list exists; the phase machine in `roles/builder_role.py` picks its own targets per phase instead of reading a team mission. |
| Assignment (was "Planner") | `docs/04_PLANNER.md` | **Done — decentralized, no separate layer built** | `claim_manager.py` + `assignment.py` implement Option B from `04_PLANNER.md`: every builder recomputes the same deterministic ore claim locally, ties broken by (hunger, distance, id). Proven on the full benchmark suite. **Decision: no `planner.py`.** A centralized Planner would duplicate this for more complexity and no measured benefit — revisit only if a real need appears that per-ore claim ownership can't express (e.g. arbitrating mine-vs-defend under unit scarcity). |
| Roles | `docs/05_ROLES.md` | Done | `roles/core_role.py`, `roles/builder_role.py`, `roles/gunner_role.py`. Missing `sentinel_role.py`/`launcher_role.py` (not needed yet — no sentinels/launchers built). |
| Navigator | `docs/ARCHITECTURE.md` Layer 3.5 (new — added Phase 2.5, not in the original 6-layer doc set) | **Done, integrated** | `navigator.py`. All movement for every role routes through `navigator.step_to` (explore / approach / belt / defend / attack). |
| Policy | `docs/06_POLICY.md` | Not started | `DEFEND`/`ATTACK` today is a static odd/even unit-id split made once per builder, not a reactive team-wide mode. This is Phase 6. |

---

# Phase 1 — Stable Economy (Highest Priority)

Goal:
Build a reliable economy on every map.

Success Criteria:
- No maps finish with 0 titanium mined.
- Every harvester reaches the Core.
- Builders never waste titanium.

Checklist

## Exploration

- [x] Builders spread naturally (sector rays from Core by unit id)
- [x] Builders don't follow each other (sector ownership + ore claim by nearer id)
- [x] No exploration loops (permanent sector waypoint; stuck→next ray waypoint)
- [x] Unknown map explored systematically (outward radii 6/12/18… along sector)
- [x] Each builder owns one sector (North-first, then clockwise)
- [x] Builder remembers explored regions (`visited` never cleared)
- [x] Explore target does not re-roll every round (waypoint until reach/stuck)
- [x] Builder reaches distant ore (sector waypoints to map scale)

## Mining

- [x] One harvester per ore (`ore_claimed_by_other` + skip occupied ore)
- [x] No duplicated harvesters (same)
- [x] Prioritize nearest free ore
- [x] Ignore blocked ore (occupied / contested / stuck abandon)

## Logistics

- [x] NEVER build conveyors before a harvester exists (`harvester_pos` only after successful build)
- [x] Only the owning builder may connect that harvester (assignment ownership)
- [x] Conveyor starts at harvester (pave stand immediately after step-off)
- [x] Conveyor reaches Core (persistent `build_plan` stand→Core; move only onto conveyors / Core)
- [x] Builder returns directly to Core (`RETURN_HOME` on plan conveyors)
- [x] No broken conveyor chains (no stepping onto empty tiles)
- [x] No useless conveyors (plan tiles only; facing along plan)

## Builder Management

- [x] Builders never idle (explore / economy / defend / attack)
- [x] Builders recover when stuck
- [ ] Stop spawning when economy saturated
- [x] Builder always has a next target

Verification

- [x] Benchmark (0/21 zero-mining vs idle)
- [ ] Replay review (confirm belts are home-path only in viewer)
- [x] No zero-mining maps

---

# Phase 2 — Economy Optimization

Goal:
Increase titanium income.

Checklist

- [ ] Choose best ore instead of nearest
- [ ] Ore cluster detection
- [ ] Shared logistics
- [ ] Smarter return paths
- [ ] Avoid long-distance mining
- [ ] Measure builder idle %

Verification

- [ ] Mining increases
- [ ] First titanium earlier
- [ ] Income per round higher

---

# Phase 2.5 — Navigator

Status: complete, and promoted from "phase" to permanent architecture
layer — see `docs/ARCHITECTURE.md` Layer 3.5 and "Architecture Layer
Status" above. Kept as a phase entry here only for benchmark history.

Goal:
Shared movement layer. Roles choose WHERE; Navigator chooses HOW.

Checklist

- [x] `navigator.py` created (`step_to` + Bug2 wall-follow)
- [x] All movement routed through Navigator (explore / approach / belt / defend / attack)
- [x] Adjacent targets are strict (belt-safe — no side-step off conveyors)
- [x] No A↔B oscillation (recent-position memory + avoid)
- [x] Wall-follow only on terrain blocks (not unit traffic); aborts if no progress
- [x] Explorer still reaches unexplored areas (sector waypoints unchanged)

Verification

- [x] Replay maps containing walls — harvester owner finds a route home if one exists
- [x] Benchmark equal or better vs prior version (8263 ≥ 8221, 0/21 zero-mining)

---

# Phase 3 — Map Intelligence (→ Map Knowledge layer)

Moved ahead of Defense/Offense: turret placement (Phase 4) and attack
targeting (Phase 5) both want real map memory — enemy Core position, known
choke points, discovered ore — rather than reasoning about the map from
scratch each time. Build the `Map` layer once here, reuse it in both.

This is where the unified `MapKnowledge` object from `docs/02_MAP.md` gets
built, replacing the comm-store-only pieces (`claim_manager.py` ore
claims, `combat.note_enemy_core`) with a real cumulative object. See
"Architecture Layer Status" above for current partial state.

Goal:
Use map knowledge for better decisions.

Checklist

- [ ] Remember ore
- [ ] Remember walls
- [ ] Remember enemy buildings
- [ ] Estimate enemy Core
- [ ] Choke points
- [ ] Expansion scoring

Verification

- [ ] Better mining on difficult maps

---

# Phase 4 — Defense

Goal:
Protect the economy, using Phase 3's map knowledge (known choke points,
discovered enemy structures) to place turrets and detect pressure.

Checklist

- [ ] Detect enemy pressure
- [ ] Turrets face enemy approach
- [ ] Never build turrets toward map edge
- [ ] Protect harvesters
- [ ] Protect conveyors
- [ ] Repair damaged structures

Verification

- [ ] Economy survives attacks
- [ ] Win rate increases

---

# Phase 5 — Offense

Goal:
Damage enemy economy, using Phase 3's enemy Core estimate/choke points to
target and route attacks instead of guessing.

Checklist

- [ ] Find enemy harvesters
- [ ] Destroy harvesters
- [ ] Destroy conveyors
- [ ] Attack Core when economy is secured
- [ ] Retreat when losing

Verification

- [ ] Enemy mining decreases
- [ ] Win rate increases

---

# Phase 6 — Strategic Decisions (→ Policy layer)

This is where `docs/06_POLICY.md`'s reactive `BOOTSTRAP`/`ECONOMY`/
`DEFEND`/`ATTACK` mode switch replaces today's static odd/even unit-id
split. Not an Assignment/Planner concern — Policy only decides the mode;
`claim_manager.py` keeps handling per-builder assignment underneath it.

Goal:
Make better long-term decisions.

Checklist

- [ ] Expand vs Defend
- [ ] Attack timing
- [ ] Builder role balancing
- [ ] Resource reservation
- [ ] Combat production timing

Verification

- [ ] Win rate > previous version

---

# Phase 7 — Optimization

Goal:
Remove waste.

Checklist

- [ ] Builder idle %
- [ ] Walking distance
- [ ] Duplicate work
- [ ] Store usage
- [ ] CPU usage

Verification

- [ ] Faster economy
- [ ] More actions

---

# Phase 8 — AI Parameter Tuning

Goal:
Optimize parameters automatically.

Rules

- AI edits parameters only.
- AI never edits runtime code.
- Every generation must beat baseline.

Verification

- [ ] Benchmark passed
- [ ] Promote generation

---

# Replay Review Checklist

For every replay ask:

## Economy

- [ ] Did every builder find ore?
- [ ] Did anyone idle?
- [ ] Did someone build useless conveyors?
- [ ] Did conveyors reach Core?
- [ ] Were harvesters duplicated?

## Exploration

- [ ] Did builders spread?
- [ ] Did they revisit explored areas?
- [ ] Did they miss nearby ore?

## Defense

- [ ] Were turrets useful?
- [ ] Did they face enemies?
- [ ] Was titanium wasted?

## Combat

- [ ] Did we attack useful targets?
- [ ] Did we chase builders?
- [ ] Did we pressure enemy economy?

---

# Benchmark History

| Version | Phase | Win % | Avg Mining | Zero Mining Maps | Notes |
|----------|------|-------|------------|------------------|-------|
| v0.1 | Step1 flat | — | ~2480 (1 patch) | many | single builder prototype |
| v0.2 | Phase1 economy | idle 21/21 | ~7800 vs idle | **0/21** | harvester-first; shortest-path belts; sector explore |
| v0.2.1 | ownership refactor | idle 21/21 | 8058 vs idle (same per-map as v0.2) | **0/21** | `Assignment` replaces ore_pos/stand_pos; 12 unit tests; no behaviour change |
| v0.2.2 | harvester-before-belts | idle 21/21 | **8066** vs idle | **0/21** | claim ore → build harvester → record `harvester_pos` → belt home; owner-only connect; 17 unit tests |
| v0.2.3 | sticky exploration | idle 21/21 | **8066** vs idle (equal) | **0/21** | sticky targets; visited kept; stuck→sector waypoint; anti walk-back; 25 unit tests |
| v0.2.4 | sector-ray explore | idle 21/21 | **8221** vs idle | **0/21** | permanent sector waypoints (not frontier); resume after economy; intra-sector sweeps then rotate |
| v0.2.5 | Navigator layer | idle 21/21 | **8263** vs idle | **0/21** | `navigator.step_to`; roles choose targets; Bug2 wall-follow; belt-safe adjacent steps |
| v0.2.6 | Nav state machine | idle 21/21 | **8489** vs idle | **0/21** | Bug2 hit_distance leave; terrain vs traffic; fog-safe shift; Core local waypoint; side reverse before abort |
| v0.2.7 | Fault-tolerant nav | idle 21/21 | **8869** vs idle | **0/21** | Persistent traffic→Bug2; hit_pos/follow_steps; recent×10; failure escape; primary-wall Bug2 |
| v0.2.8 | Belt corridor alternate | idle 21/21 | **8844** vs idle | **0/21** | homeward candidates skip WALL primary; vision-safe belt_complete; belt stuck abandon |
| v0.2.9 | Persistent plan FSM | idle 21/21 | **8547** vs idle | **0/21** | one `build_plan` + index; replan suffix only; explicit states; greedy belt neighbours removed |
