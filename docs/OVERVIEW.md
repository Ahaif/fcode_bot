# Code Overview — Where Everything Lives

This is the map of the repo: every file, every function, what it's responsible
for, and how it relates to the documented target architecture and to the
current phase of development. Read this when you need to find *where* to make
a change. Read `PROGRESS_TRACKER.md` to know *what* to change right now.

## Read order

1. **`PROGRESS_TRACKER.md`** (repo root) — current version, current focus
   phase, latest benchmark numbers, checklist status. Always start here.
2. **`docs/00_ARCHITECTURE.md`** — the target six-layer design and the
   build order. Tells you what a layer is and isn't allowed to do.
3. **This file** — maps every file/function that exists *today* onto those
   layers, and flags the gap between target and actual.
4. **`docs/phases/phaseN_*.md`** — the phase matching current focus in the
   tracker: strategy notes, relevant files, and a link back to that phase's
   checklist.

## The gap: target architecture vs. current code

`docs/00_ARCHITECTURE.md` and `docs/01`–`06` describe a **target** six-layer
pipeline (`world_state.py`, `map_knowledge.py`, `missions.py`, `planner.py`,
`policy.py`, plus a full `roles/` set including `sentinel_role.py` and
`launcher_role.py`). **None of `world_state.py`, `map_knowledge.py`,
`missions.py`, `planner.py`, or `policy.py` exist yet.**

What exists today is a simpler, flatter design: a per-builder **phase
machine** (state stored on the `Player` instance) spread across
`economy.py`, `explore.py`, `defense.py`, `combat.py`, and `roles/*.py`. It
gets a working economy/defense/offense loop running fast, at the cost of the
layer separation the docs describe. This is expected at this stage of
`PROGRESS_TRACKER.md` (Phase 1–4 territory) — the six-layer split is the
*target* for later phases (roughly Phase 5–6, "Map Intelligence" /
"Strategic Decisions"), not a bug in the current code.

Rough mapping of today's files onto the target layers:

| Today's file | Target layer(s) it stands in for | Gap |
|---|---|---|
| `main.py` | Entry point / dispatch (matches target — this file doesn't change shape) | none |
| `geometry.py` | Shared helpers used by every layer (movement/adjacency math) | none — pure functions, no layer owns these exclusively |
| `economy.py` | `Roles` (builder execution) + a sliver of `Mission`/`Map` (ore scanning/targeting is a scan-the-map decision, not just execution) | ore targeting logic should eventually move to `Map`/`Mission`, execution stays in `Roles` |
| `explore.py` | `Roles` (movement execution) + `Mission`/`Map` (frontier/waypoint selection is spatial-knowledge logic) | same kind of gap as `economy.py` |
| `defense.py` | `Roles` (gunner placement execution) + `Policy` (thresholds like `MAX_GUNNERS`, `DEFENSE_RING_SQ` are exactly the kind of thing `06_POLICY.md` says belongs in a tunable policy layer) | thresholds live in `constants.py` today, which is fine per `06_POLICY.md` — there's just no `Policy.decide()` state machine reading them yet |
| `combat.py` | `Roles` (attack execution) + `Map` (`note_enemy_core`/`enemy_core_target` *is* the enemy-core-memory the target `MapKnowledge.enemy_core_pos` field describes, just stored in the comm store directly instead of a `Map` object) | this is the closest thing to a `Map` layer that exists today |
| `constants.py` | `Policy` config surface (tunable knobs, per `06_POLICY.md`) | correctly separated already |
| `roles/core_role.py`, `roles/builder_role.py`, `roles/gunner_role.py` | `Roles` layer, matches `05_ROLES.md` almost exactly | missing `sentinel_role.py` / `launcher_role.py` (not built yet — no sentinels/launchers in the current strategy) |

**Practical rule of thumb:** if you're adding logic that decides *what tile
or target* something should use, and you're tempted to put it in
`economy.py`/`explore.py`/`combat.py`, that's fine for now (that's where
targeting logic lives today) — but if it starts requiring a full-map scan or
cross-round memory beyond the comm store, that's the signal Phase 5 ("Map
Intelligence") has arrived and it's time to actually build `map_knowledge.py`
per `docs/02_MAP.md`.

## File-by-file, function-by-function

### `main.py` — entry point
- `Player.__init__` — per-unit state: builder phase machine fields
  (`phase`, `core_pos`, `ore_pos`, `stand_pos`, `visited`, `explore_target`,
  `attack_target`, `waypoint_i`, `stuck`, `last_pos`), plus `num_spawned`
  for the Core.
- `Player.run(ct)` — dispatches on `ct.get_entity_type()` to
  `core_role.run_core` / `builder_role.run_builder` / `gunner_role.run_gunner`.
  Wrapped in try/except so no exception ever escapes and destroys the unit
  (per the non-negotiable engine constraint in `00_ARCHITECTURE.md` /
  `CONTROLLER_API.md`).

### `constants.py` — tuning knobs, store slots, phase names
- `CARDINALS`, `SPAWN_DIRS` — direction tuples used for iteration order.
- `SLOT_*` — comm store slot assignments (core pos, enemy core pos/known
  flag, gunner count). Only 16 slots total, one-round write lag.
- `MAX_BUILDERS`, `MAX_GUNNERS`, `AMMO_CONVERT_CHUNK`, `TITANIUM_RESERVE` —
  economy/army sizing knobs.
- `STUCK_LIMIT`, `WAYPOINTS_BEFORE_COMBAT`, `DEFENSE_RING_SQ` — movement and
  phase-transition thresholds. These are exactly the "sweepable" parameters
  Phase 8 (AI Parameter Tuning) targets.
- `PHASE_*` — the builder phase-machine state names (see `builder_role.py`).

### `geometry.py` — pure geometry + cheap Controller helpers
- `xy(pos)` — `(x, y)` tuple for dict keys / set membership.
- `in_bounds(ct, pos)` — map bounds check.
- `orth_adjacent(a, b)` — Manhattan distance == 1 check.
- `builder_can_act(ct)` — action-cooldown gate (prefers `can_act()` if the
  engine exposes it).
- `passable_env(ct, pos)` — in-bounds and not a wall.
- `facing_toward(origin, target)` / `facing_away_from(origin, away_from)` —
  cardinal direction helpers for facing a build toward/away from a point.
- `on_or_orth_adjacent_to_core(pos, core)` — true if `pos` touches the
  Core's 2×2 footprint or is orthogonally adjacent to it.
- `tile_has_conveyor(ct, tile)` — checks the building on a tile is a conveyor.
- `try_move_toward(ct, target, avoid=None)` — one cardinal step toward
  `target`, with fallback ranking if the direct step is blocked, and an
  `avoid` tile (usually last round's position) to prevent oscillation.
- `orthogonal_neighbors(ct, pos)` — in-bounds N/E/S/W tiles.

### `economy.py` — ore → belt → harvester choreography
- `ore_claimed_by_other(ct, ore)` — true if a nearer (or lower-id, tie-break)
  friendly builder already owns this ore, so we don't duplicate harvesters.
- `scan_ore(ct)` — closest visible uncovered ore not claimed by another
  builder. This is the de facto "ore targeting" decision (see gap table
  above — conceptually a `Mission`/`Map` responsibility).
- `pick_ore_stand(ct, ore)` — deterministic orthogonal neighbour tile of the
  ore to stand on while belting.
- `try_belt_ahead(ct, core)` — build one Core-facing conveyor tile ahead,
  on the path back to Core.
- `try_build_harvester(ct, ore)` — place the harvester once adjacent, if
  affordable.
- `try_pave_stand(ct, stand, ore, core)` — lay a conveyor on the ore-adjacent
  stand tile once the builder has stepped off it.
- `close_ore_needs_core_step(pos, stand, ore, core)` — edge case: the stand
  tile is Core-adjacent but empty, so the builder must step onto/through the
  Core-adjacent tile before it can pave (ordering constraint of the belting
  choreography).

### `explore.py` — frontier exploration + long-range waypoints
- `explore_waypoints(ct)` — deterministic scout targets: 4 corners, 4 edge
  midpoints, then map centre.
- `nearest_frontier(ct, visited)` — nearest unvisited passable tile in
  vision, preferring the vision perimeter (push outward instead of
  backfilling).
- `step_explore(ct, state, avoid=None)` — picks/refreshes the current
  explore target (frontier tile, or next waypoint if frontier is exhausted
  or the builder is stuck) and takes one step toward it.

### `defense.py` — outward-facing gunner ring around the Core
- `count_visible_friendly_gunners(ct)` — count of friendly gunners in vision.
- `try_build_gunner(ct, core)` — place one outward-facing gunner on an
  orthogonal neighbour tile, gated by `MAX_GUNNERS`, cost, and
  `TITANIUM_RESERVE`; prefers tiles farther from Core (outer ring).
- `defend_step(ct, state, avoid=None)` — one defensive round: build a
  gunner if within the defense ring and able, else walk toward Core.

### `combat.py` — offense: harass economy, push enemy Core
- `note_enemy_core(ct)` — if an enemy Core is in vision, publish its
  position + a "known" flag into the comm store for the whole team. This is
  the current stand-in for `MapKnowledge.enemy_core_pos`.
- `enemy_core_target(ct, own_core)` — known enemy Core from the store, else
  a rotational-symmetry guess (`(W-1-x, H-1-y)`) since most league maps are
  symmetric.
- `_enemy_building_on(ct, tile)` — entity type of an enemy building on a
  tile, or `None`.
- `try_attack_adjacent(ct)` — melee an orthogonally adjacent enemy building;
  prefers harvesters over other buildings.
- `nearest_enemy_harvester(ct)` — closest visible enemy harvester tile.
- `attack_step(ct, state, avoid=None)` — one offensive round: melee if
  adjacent, else march toward nearest enemy harvester, else enemy Core
  (known or guessed), else map centre as a last resort.

### `roles/` — per-`EntityType` turn handlers (matches `05_ROLES.md`)
- **`roles/__init__.py`** — package marker only.
- **`roles/core_role.py`**
  - `try_spawn_builder(ct)` — spawn one builder on the nearest legal tile
    within spawn radius, falling back to adjacent tiles.
  - `maybe_convert_ammo(ct)` — convert titanium → ammo once gunners exist
    (or round ≥ 80), capped so it doesn't starve the economy or over-stock
    ammo.
  - `run_core(ct, state)` — one Core turn: publish position, maybe convert
    ammo, spawn up to `MAX_BUILDERS`.
- **`roles/builder_role.py`** — the phase machine. See "Strategy — current"
  below for the full state diagram.
  - `_ensure_core_pos`, `_abandon_ore`, `_enter_late_game`,
    `_economy_exhausted` — phase-machine bookkeeping helpers.
  - `run_builder(ct, state)` — per-round dispatcher: updates memory (core
    pos, enemy core, visited set, stuck counter), handles the "ore got
    stolen/blocked/stuck" interrupt, then delegates to the active phase.
  - `_phase_approach_ore`, `_phase_belt_home`, `_phase_return_ore` — the
    three economy sub-phases (see diagram below); `defend`/`attack` phases
    delegate straight to `defense.defend_step` / `combat.attack_step`.
- **`roles/gunner_role.py`**
  - `run_gunner(ct, state)` — auto-fire at `ct.get_gunner_target()` if a
    target and ammo/cooldown allow. No per-gunner memory needed yet.

### `bots/` — test opponents, not part of our bot
- **`bots/starter/main.py`** — the official Florent Code League reference
  bot. Useful as a benchmark opponent and as commented example code, but not
  something we edit — treat as read-only reference.
- **`bots/idle/main.py`** — do-nothing opponent, used to keep replays
  readable when debugging our own bot in isolation (no enemy interference).

### `scripts/run_vs_starter.sh` — local benchmark runner
Runs this bot against `bots/starter` on a given map/seed via the `fcode`
CLI, writing a replay. This (or the equivalent full-suite runner, if one
exists) is what "benchmarked" means in `PROGRESS_TRACKER.md`'s promotion
rule — a phase isn't done until this comes back with equal-or-better numbers
across the map suite.

### `maps/*.map26` — benchmark map suite
The fixed set of maps every phase gets verified against (see
`PROGRESS_TRACKER.md`'s "no zero-mining maps" success criteria and the
Replay Review Checklist).

### `fcode.toml` — local run config
`bots_dir`, `maps_dir`, `replay` path, and default `seed` for the `fcode`
CLI.

### `replay.replay26` — most recent generated replay
Output artifact from the last local run; open it in the `fcode` replay
viewer for the Replay Review Checklist in `PROGRESS_TRACKER.md`.

### `docs/` — architecture reference (see `docs/00_ARCHITECTURE.md` for the
full six-layer spec; `CONTROLLER_API.md` for the condensed engine rules).

## Strategy — current (as implemented)

Builder phase machine (per-unit, stored on `Player`):

```
EXPLORE ──(ore found)──> APPROACH_ORE ──(adjacent or on stand)──> BELT_HOME
   ^                                                                  │
   │                                                       (reaches Core)
   │                                                                  ▼
   └──(ore stolen/blocked/stuck, or harvester built)── RETURN_ORE ────┘

EXPLORE ──(no ore after WAYPOINTS_BEFORE_COMBAT waypoint advances)──>
   even unit id  → DEFEND (defense.py: build outward gunner ring)
   odd unit id   → ATTACK (combat.py: harass harvesters, push enemy Core)
```

- **Core**: publish position every round; convert titanium → ammo once
  gunners exist (or round ≥ 80), reserving a titanium buffer; spawn builders
  up to `MAX_BUILDERS`.
- **Economy**: nearest unclaimed visible ore wins (`scan_ore`), tie-broken
  deterministically by distance then tile coordinates so builders don't
  double-claim. Belt is built one Core-facing tile at a time while walking
  home, then the stand tile is paved once vacated, then the harvester is
  placed on the ore.
- **Exploration**: prefer the nearest unvisited tile on the current vision
  perimeter (push the frontier outward); once frontier + local search dries
  up, fall back to a fixed list of corner/edge/centre waypoints, spread
  across builders by `unit_id % len(waypoints)` so they don't all beeline
  the same corner.
- **Defense**: builders within `DEFENSE_RING_SQ` of Core place outward-
  facing gunners (facing away from Core) up to `MAX_GUNNERS`; gunners
  auto-fire whatever `get_gunner_target()` returns.
- **Offense**: melee an adjacent enemy building (harvesters prioritized
  over other buildings, Core included as fallback target), else walk toward
  the nearest visible enemy harvester, else the enemy Core (comm-store
  known position, or a symmetry guess), else map centre.
- **Enemy Core memory**: whoever spots it first writes it to the comm
  store; every other unit reads it from there. No fully-fledged `Map` object
  yet — see gap table above.

There is no `Policy` mode switch yet: the `DEFEND`/`ATTACK` split is a static
one-time decision per builder (odd/even unit id) made once economy dries up,
not a team-wide reactive mode. That reactive switch is exactly what Phase 6
("Strategic Decisions") / `docs/06_POLICY.md` describes building next.

## Strategy — target (per `docs/00`–`06`, not yet built)

Six layers, data flows up (`WorldState → Map → Mission`), decisions flow
down (`Policy → Roles`), meeting in `Planner`. Build bottom-up, gate each
layer with a manual replay check before moving to the next
(`00_ARCHITECTURE.md`'s "Build order" section):

1. **WorldState** (`docs/01_WORLDSTATE.md`) — one per-round snapshot per
   unit; the only layer allowed to call `ct.*` directly.
2. **Map** (`docs/02_MAP.md`) — cumulative spatial knowledge (ore tiles,
   own/enemy Core, danger map) built from WorldState across rounds.
3. **Mission** (`docs/03_MISSION.md`) — team-level objective list
   (`BUILD_ECONOMY`, `DEFEND_CORE`, `SCOUT`, `ATTACK`), no per-unit
   assignment yet.
4. **Planner** (`docs/04_PLANNER.md`) — turns missions into one `Task` per
   unit; start with round-robin/nearest-idle, no scoring model.
5. **Roles** (`docs/05_ROLES.md`) — turns a `Task` into `ct.can_x()`/`ct.x()`
   calls. Closest layer to what exists today.
6. **Policy** (`docs/06_POLICY.md`) — the global mode switch
   (`BOOTSTRAP`/`ECONOMY`/`DEFEND`/`ATTACK`), built *last*, once 1–5
   reliably win the economy benchmark.

## Where to look when…

| Symptom | Start here |
|---|---|
| A map mines 0 titanium / harvester never placed | `economy.py`, `roles/builder_role.py` (`_phase_approach_ore`/`_phase_belt_home`/`_phase_return_ore`), `docs/phases/phase1_economy.md` |
| Builders loop, oscillate, or get stuck | `explore.py`, `geometry.try_move_toward`, `constants.STUCK_LIMIT` |
| Titanium income too low / bad ore choice | `economy.scan_ore`, `economy.pick_ore_stand`, `docs/phases/phase2_optimization.md` |
| Gunners missing, misplaced, or facing the wrong way | `defense.py`, `roles/gunner_role.py`, `constants.DEFENSE_RING_SQ`/`MAX_GUNNERS`, `docs/phases/phase3_defense.md` |
| Offense not finding/hitting enemy economy | `combat.py`, `roles/builder_role.py` (`PHASE_ATTACK`), `docs/phases/phase4_offense.md` |
| Need map memory beyond the comm store (ore clusters, choke points) | there is no `map_knowledge.py` yet — this is Phase 5, see `docs/02_MAP.md` and `docs/phases/phase5_map_intelligence.md` |
| Need a real win/lose reactive mode switch | there is no `policy.py` yet — this is Phase 6, see `docs/06_POLICY.md` and `docs/phases/phase6_strategic_decisions.md` |
| CPU/idle-time/waste cleanup | cross-cutting, `docs/phases/phase7_optimization.md` |
| Parameter sweeps / self-play tuning | `constants.py` (values only, never runtime code), `docs/phases/phase8_ai_parameter_tuning.md` |

## Non-negotiable rule (repeated from `PROGRESS_TRACKER.md`)

> A phase is NOT complete because the code compiles. A phase is complete
> only after the benchmark improves or stays equal. Every behavioral change
> must be benchmarked before promotion.
