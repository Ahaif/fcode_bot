# Layer: Map

## Responsibility

Derived, mostly-static spatial knowledge built from `WorldState` (and
accumulated over rounds, since vision is limited and the map isn't fully
known on round 0). This is where you pay the one-time cost of "figure out
the map" so `Mission` and `Planner` never have to scan tiles themselves.

## Suggested contents

```python
@dataclass
class MapKnowledge:
    width: int
    height: int
    known_env: dict[Position, Environment]   # accumulates as vision reveals more
    ore_tiles: set[Position]                 # known ORE_TITANIUM tiles
    own_core_pos: Position
    enemy_core_pos: Position | None          # None until scouted
    danger_map: dict[Position, int]          # optional: enemy turret threat scoring
```

Key distinction from `WorldState`: `Map` is **cumulative** (it remembers
what was seen in past rounds — units have limited vision, the map is not
fully visible round 0) while `WorldState` is a fresh per-round snapshot.
`Map` needs a real object that persists across rounds, not a dataclass
rebuilt from scratch every call.

## Persistence across rounds — a hard constraint of this engine

There is **no shared game object** across units, and no Python state
survives between separate unit's `run()` calls except through:

1. The **comm store** (16 ints, per team, one-round write lag) — the only
   truly reliable cross-unit, cross-round channel.
2. **Module-level Python state**, which persists across rounds *within a
   single engine process* — but confirm with a real match/replay whether
   your bot process is long-lived for the whole match or whether each
   round (or each unit call) could run in a fresh process/interpreter. If
   you're not certain, don't rely on module-level state for anything that
   affects a `can_*()` gate; treat the comm store as the durable channel
   and module-level caches as a same-round-only optimization.

Practical pattern: each unit still calls `Map.update(world_state)` every
round; internally it checks a cached round number and skips recomputation
if already done this round *and* if module state actually persisted
correctly (validate this once, explicitly, before depending on it).

## Encoding into the comm store

If module-level persistence turns out unreliable across the process
model actually used, the fallback is encoding the minimum viable `Map`
into the 16-slot store: e.g. reserve slot 0 for "enemy core known" bitflag
+ packed low-res coordinates, slots 1–2 for ore-patch count/nearest patch
index if you precompute a small fixed list of candidate ore clusters at
round 0 from spawn symmetry. This is deliberately minimal — 16 ints isn't
enough for a full map, so `Map` should degrade gracefully to "what can I
currently see" if store-based sharing isn't available or isn't worth the
budget.

## What does NOT belong here

- Anything about what to *do* about a tile (that's `Mission`).
- Per-unit vision snapshots (that's `WorldState`).
- Anything requiring a full-map scan every round — precompute once,
  update incrementally as new tiles enter vision.

## Verification gate before moving to `Mission`

Confirm `ore_tiles` and `own_core_pos`/`enemy_core_pos` (once scouted)
match the actual map for at least 3 different maps, including one where
the enemy core isn't visible for the first N rounds (confirm it stays
`None` correctly rather than guessing).
