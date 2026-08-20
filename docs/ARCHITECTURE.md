# Architecture — Florent Bot v2

> Principle:
>
> Every layer exists because the layer below it is already correct.
> Never build a smarter decision layer to compensate for a broken lower layer.

---

# Overall Architecture

```
                    Decision Flow
                           │
                           ▼
+------------------------------------------------------+
|                    POLICY                            |
| Economy / Defend / Attack / Endgame                 |
+------------------------------------------------------+
                           │
                           ▼
+------------------------------------------------------+
|                   MISSIONS                           |
| What should the team accomplish?                    |
| (Expand, Return Ore, Defore, Attack...)             |
+------------------------------------------------------+
                           │
                           ▼
+------------------------------------------------------+
|            ASSIGNMENT  (not a "Planner" layer)       |
| Which builder owns which ore/task?                  |
| Decentralized: claim_manager.py + assignment.py —   |
| every builder recomputes the same deterministic     |
| claim locally. No planner.py. See decision below.   |
+------------------------------------------------------+
                           │
                           ▼
+------------------------------------------------------+
|                     ROLES                            |
| Core / Builder / Gunner / Sentinel / Launcher       |
| Decides WHAT to do and WHERE. Executes exactly ONE  |
| action per turn. Delegates movement to Navigator.   |
+------------------------------------------------------+
                           │
                           ▼
+------------------------------------------------------+
|                   NAVIGATOR                          |
| HOW to move, never WHERE/WHY.                       |
| navigator.step_to() — Bug2 wall-follow, belt-safe   |
| adjacency, no A*/BFS. Roles never compute movement. |
+------------------------------------------------------+
                           ▲
                           │
+------------------------------------------------------+
|                 MAP KNOWLEDGE                        |
| Ore discovered                                       |
| Enemy structures                                     |
| Friendly structures                                  |
| Path distances                                        |
| Connectivity                                          |
| Cached information                                    |
+------------------------------------------------------+
                           ▲
                           │
+------------------------------------------------------+
|                  WORLD STATE                         |
| Pure Controller snapshot                             |
| No decisions                                          |
+------------------------------------------------------+
                           ▲
                           │
                     Controller API
```

> Note on this diagram vs. the current code: Navigator and Assignment
> (`claim_manager.py`/`assignment.py`) are real and benchmarked today.
> Policy, Missions, Map Knowledge (as a unified object), and World State
> (as a real object) are not built yet — see "Architecture Layer Status"
> in `PROGRESS_TRACKER.md` for the current per-layer state, and
> `docs/OVERVIEW.md` for the file-by-file mapping.

---

# Layer Responsibilities

---

# Layer 1 — WorldState

Purpose

Read everything from the engine.

Nothing else.

Responsibilities

- nearby units
- nearby buildings
- nearby tiles
- current titanium
- cooldowns
- hp
- unit positions

Never

- choose targets
- compute strategy
- assign missions

Output

A pure immutable snapshot.

---

# Layer 2 — Map Knowledge

Purpose

Transform observations into persistent world knowledge.

Stores

Static

- map size
- core location
- inferred enemy core
- center

Dynamic

- discovered ore
- harvested ore
- blocked tiles
- friendly harvesters
- friendly conveyors
- enemy defenses
- explored frontier

Provides queries

nearest_ore()

nearest_unclaimed_ore()

is_connected_to_core()

distance()

find_frontier()

nearest_enemy_harvester()

Everything here should answer questions.

Never issue actions.

---

# Layer 3 — Roles

Roles define capabilities.

Example roles

Core

Builder

Gunner

Sentinel

Launcher

A Builder knows WHAT to do and WHERE (build harvester, build conveyor,
attack, or move toward a target). It never decides WHEN — that's Mission —
and it never computes HOW to physically get there — that's Navigator.

---

# Layer 3.5 — Navigator

Purpose

Shared movement layer. Roles decide WHERE (a target tile); Navigator
decides HOW to get there this round.

Why it's a separate layer, not part of Roles: every role that moves
(builder economy, explore, defend, attack) was independently reimplementing
"step toward a target, handle a wall, avoid oscillating" — the same bug
(wall-follow, A↔B flapping, belt side-stepping) kept getting fixed in one
role and re-appearing in another. `navigator.py` centralizes it once.

```python
def step_to(ct: Controller, state, target: Position, *, avoid=None) -> bool:
    """Take at most one step toward target this round. Returns True if
    the unit acted (moved). Owns a small per-unit state machine
    (NORMAL / WALL_FOLLOW, Bug2) — Roles never touch movement internals."""
```

No A*, no BFS — deliberately. Bug2 wall-follow (hug an obstacle until a
line back to the target is clear) is the entire pathing algorithm. This
was proven sufficient across the full benchmark map suite (Phase 2.5);
don't add real pathfinding without evidence Bug2 is actually the
bottleneck.

What does NOT belong here

- Choosing *which* tile to move toward (Roles'/Mission's job).
- Any strategy — Navigator has no notion of ore, enemies, or Core, only
  "get from A to B without getting stuck."

Status: built and integrated — see `navigator.py` and Phase 2.5 in
`PROGRESS_TRACKER.md`. Every role's movement goes through this layer.

---

# Layer 4 — Assignment (decision: no separate Planner layer)

Purpose

Decide which builder performs which unit of work (which ore, which task).

Decision made: **do not build a centralized `planner.py`.** `04_PLANNER.md`
laid out two designs — (A) a central computer publishing via the comm
store, or (B) every unit recomputing the same deterministic assignment
locally. Option B is already implemented as `claim_manager.py` +
`assignment.py`: builders claim ore through a comm-store-backed ownership
table, ties broken by a pure function of (hunger, distance, id) so every
observer agrees without needing a dedicated planning pass. It's proven on
the full benchmark suite.

Adding a `planner.py` on top would duplicate this for more moving parts
and no measured benefit — the exact "bad optimization" the Design
Principles section below warns against ("More planners... without proving
the lower layer works first"). Revisit only if a concrete need appears
that per-ore claim ownership can't express — e.g. arbitrating builders
between mining and defending under unit scarcity (that's closer to
Mission/Policy's job than a generic Planner's).

Example (as implemented today, via claim ownership, not a Planner object)

Mission

Expand to Ore A

Assignment (claim_manager)

Builder 4 claims Ore A

Builder 7 claims Ore B

Builder 2 owns the return belt for its own harvester

Output

Each builder's own `Assignment` (ore_pos / harvester_pos / connected)

Never call Controller directly.

---

# Layer 5 — Missions

Purpose

Describe WHAT the team needs.

Examples

Build Economy

Reconnect Belt

Explore Frontier

Repair Belt

Defend Core

Destroy Enemy Harvester

Rush Enemy Core

Mission owns priorities.

Planner owns assignment.

---

# Layer 6 — Policy

Highest level.

Very small.

Switches between

ECONOMY

↓

DEFEND

↓

ATTACK

↓

ENDGAME

Policy never cares about individual builders.

Only macro strategy.

---

# Development Order

Never skip phases.

Each phase must pass benchmarks before continuing.

```
Phase 1
↓

WorldState

↓

Phase 2

Map Knowledge

↓

Phase 3

Economy

↓

Phase 4

Assignment (claim-based, no planner.py — see Layer 4 decision above)

↓

Phase 5

Policy

↓

Phase 6

Defense

↓

Phase 7

Attack

↓

Phase 8

Optimization

↓

Phase 9

AI Parameter Search
```

---

# Economy Pipeline (Most Important)

The economy is the foundation.

Every future feature depends on this pipeline.

```
Explore

↓

Find Ore

↓

Reserve Ore

↓

Build Harvester

↓

Return Toward Core

↓

Build Conveyor Chain

↓

Harvester Connected

↓

Ore Reaches Core

↓

Titanium Stored

↓

Core Spawns Builder

↓

Repeat
```

The pipeline is complete only when titanium reaches the Core.

A built Harvester alone is **not success**.

---

# Combat Pipeline

Only implemented after economy is stable.

```
Detect Enemy

↓

Evaluate Threat

↓

Choose Mission

↓

Planner Assigns Units

↓

Roles Execute

↓

Destroy Target

↓

Return To Economy
```

---

# Performance Rules

Every optimization must satisfy

- Reduce Controller API calls
- Reduce path recomputation
- Avoid duplicate searches
- Cache expensive calculations
- Never increase decision complexity unnecessarily

Good optimizations

✓ Cache nearest ore

✓ Cache frontier

✓ Cache explored tiles

✓ Cache connected conveyors

✓ Cache enemy structures

✓ Reuse previous paths

Bad optimizations

✗ More heuristics

✗ More if-statements

✗ More modes

✗ More planners (see Layer 4 decision above — claim-based assignment
  already covers this)

without proving the lower layer works first.

---

# Benchmark Gates

Nothing is promoted until benchmarks pass.

Economy

✓ No idle builders

✓ No disconnected harvesters

✓ No useless conveyors

✓ No conveyor before harvester

✓ No duplicate ore assignment

✓ Titanium reaches Core

Defense

✓ Turrets face useful directions

✓ Core survives attacks

Attack

✓ Enemy harvesters destroyed

✓ Enemy economy disrupted

Overall

✓ Win rate improves

✓ Mining improves

✓ No GameErrors

✓ No regression

---

# Design Principles

1. Lower layers never know upper layers.

2. Every layer has exactly one responsibility.

3. Decisions move downward.

4. Information moves upward.

5. A bug is fixed in the layer where it originates.

Never compensate in higher layers.

---

# Success Criterion

The bot should become progressively smarter by replacing algorithms inside a layer—not by adding new layers or more heuristics.

When reading the code, it should always be possible to answer:

- Where does this information come from?
- Which layer owns this decision?
- Which benchmark validates this behavior?

If the answer is unclear, the architecture is wrong.