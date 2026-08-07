# Layer: WorldState

**The only layer allowed to call `ct.*` directly.** Every other layer reads
from a `WorldState` object, never from `ct`. This keeps everything above it
testable without a real Controller, and makes "what did the bot actually
see this round" reconstructable from one object for debugging.

## Responsibility

Build one immutable-for-the-round snapshot containing exactly what the
higher layers need, no more. Not a wrapper around every Controller method —
a curated summary of the current round.

## Suggested contents

```python
@dataclass(frozen=True)
class WorldState:
    round_num: int
    self_id: int
    self_type: EntityType
    self_pos: Position
    self_team: Team
    action_cooldown: int
    move_cooldown: int          # only meaningful for BUILDER_BOT

    global_resources: int
    global_ammo: int
    unit_count: int

    nearby_tiles: list[Position]        # within this unit's vision
    nearby_units: dict[int, UnitInfo]   # id -> cheap struct (pos, type, team, hp)
    nearby_buildings: dict[int, UnitInfo]

    store: list[int]                    # all 16 comm-store slots, read once
```

`UnitInfo` should be a small frozen dataclass (pos, type, team, hp), not a
re-query of `ct.get_hp(id)` scattered through later layers — fetch once,
reuse.

## Caching rule (this is the part that saves your CPU budget)

`WorldState` is per-unit (vision differs per unit), but team-wide fields
(`global_resources`, `global_ammo`, `unit_count`, `store`) are identical for
every unit this round. Two options, pick one and document which in code:

1. **Per-round module-level cache keyed by round_num.** Simplest: a module
   global `_cache = {"round": -1, "team_state": None}`; if
   `ct.get_current_round() != _cache["round"]`, recompute and store.
   Works because all units of a team run within the same round before the
   engine advances — but confirm this ordering assumption against a replay
   before relying on it for anything correctness-critical (only use the
   cache for read-only convenience fields, never for anything that gates
   an irreversible action).
2. **Recompute team-wide fields fresh every call.** Safer, costs a handful
   of getter calls (~microseconds) — for team-wide fields alone this is
   usually cheap enough that the cache in (1) is a premature optimization.
   Only cache the *expensive* derived stuff (that lives in `Map`, not here).

Default to (2) for `WorldState` itself — it's just getters, it's cheap.
Reserve caching for `Map`, where real computation happens.

## What does NOT belong here

- Anything requiring iteration over the whole map (belongs in `Map`).
- Anything requiring history across rounds, e.g. "have we been attacked in
  the last 5 rounds" (belongs in `Map` or `Mission`, backed by comm-store
  bits if it needs to be shared team-wide).
- Any decision logic. `WorldState` is pure data, no `if`/strategy branches.

## Verification gate before moving to `Map`

Print a `WorldState` for a known unit at a known position/round and
manually diff it against what the replay viewer shows for that unit. Do
this for at least: a builder bot mid-map, the core, and a unit at the edge
of the map (bounds-check `nearby_tiles`).
