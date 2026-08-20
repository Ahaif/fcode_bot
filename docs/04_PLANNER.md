# Layer: Planner

> **Decision (see `docs/ARCHITECTURE.md`, Layer 4): superseded.** Option B
> below was chosen and is already implemented as `claim_manager.py` +
> `assignment.py` — a decentralized, comm-store-backed ore-ownership
> protocol, not a separate `planner.py`. It's proven on the full benchmark
> suite. This doc is kept for the reasoning trail (why B over A) and as
> the reference if a real need for centralized multi-mission arbitration
> ever appears — do not build a `planner.py` module without that need
> being concrete and evidenced first.

## Responsibility

Turn the active `Mission` list into a concrete `Task` per unit. This is the
layer most tempted to get clever early (utility scoring, auctions,
influence-weighted assignment) — don't, until the dumb version is proven.

## Data shape

```python
@dataclass(frozen=True)
class Task:
    kind: TaskKind          # MOVE_TO, BUILD, HARVEST_ROUTE, ATTACK, HOLD, IDLE
    target: Position | None
    entity_type: EntityType | None  # what to build, for BUILD tasks
    mission_kind: MissionKind        # which mission this task serves (for logging)
```

## The "who computes this" question — decide this explicitly, don't drift into it

Two legitimate designs; pick one and write it down here once decided:

**A. Core computes, publishes via comm store.** The Core (or a designated
"planner" unit) runs the full assignment once per round and writes a
compact encoding into the comm store. Every other unit reads it. Pro:
single source of truth, cheap for everyone else. Con: comm store writes
lag one round, and 16 ints is a tight budget for "task per unit" if you
have many units — needs a compact encoding (e.g. per-region assignment
counts rather than per-unit-id assignment).

**B. Every unit recomputes the same deterministic assignment locally.**
Every unit's `run()` calls `Planner.assign(missions, world_state, map)`
and, because the inputs are identical for every unit of the team this
round, gets the same output — then just looks up its own `unit_id` in the
result. Pro: no comm-store lag, no encoding problem. Con: the assignment
computation runs once per unit (redundant CPU), and it only works if every
unit truly sees the same `WorldState`/`Map` — verify this holds (e.g. two
units don't have meaningfully different `nearby_*` fields if they're
supposed to be reasoning about global assignment, not local action).

Recommended starting point: **B**, because the tight comm store budget in
A becomes a real bottleneck fast, and the CPU cost of a round-robin/
nearest-idle assignment over ≤50 units is trivial well within 10ms.
Revisit A only if the assignment computation gets expensive.

## Assignment algorithm — start dumb

```python
def assign(missions, ws, mp, all_builder_bot_ids) -> dict[int, Task]:
    tasks = {}
    # naive: sort missions by priority, round-robin idle builders onto them
    idle_bots = [bid for bid in all_builder_bot_ids]  # no notion of "busy" yet
    for mission in sorted(missions, key=lambda m: -m.priority):
        needed = mission.params.get("count", 1)
        for _ in range(needed):
            if not idle_bots:
                break
            bot_id = idle_bots.pop(0)
            tasks[bot_id] = mission_to_task(mission)
    return tasks
```

Do not add utility scoring, distance-weighted assignment, or an auction
mechanism until round-robin is verified working end to end on the
benchmark suite.

## What does NOT belong here

- Deciding *whether* to build economy vs defend (`Mission`'s job).
- Actually calling `ct.build_harvester(...)` (`Roles`' job).

## Verification gate before moving to `Roles`

Confirm every builder bot ends up with exactly one `Task` per round (none
silently `None`), and that the naive assignment doesn't oscillate (same
bot reassigned to a different target every round with no progress).
