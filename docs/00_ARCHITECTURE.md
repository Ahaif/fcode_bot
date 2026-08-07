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
|                   PLANNER                            |
| Which builder performs which mission?               |
+------------------------------------------------------+
                           │
                           ▼
+------------------------------------------------------+
|                     ROLES                            |
| Core / Builder / Gunner / Sentinel / Launcher       |
| Executes exactly ONE action per turn                |
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

A Builder knows HOW to

- move
- build harvester
- build conveyor
- attack

It never decides WHEN.

---

# Layer 4 — Planner

Purpose

Convert missions into assignments.

Example

Mission

Expand to Ore A

Planner

Builder 4 → Ore A

Builder 7 → Ore B

Builder 2 → Return Belt

Output

Tasks

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

Planner

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

✗ More planners

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