# Layer: Mission

## Responsibility

Team-level objectives — the "what" without the "who does it." A Mission
answers "what does the team need right now," not "which unit does step 3."
That's the Planner's job.

## Data shape

```python
@dataclass(frozen=True)
class Mission:
    kind: MissionKind          # e.g. BUILD_ECONOMY, DEFEND_CORE, SCOUT, ATTACK
    priority: int              # higher = more urgent; Planner uses this to
                                # arbitrate when unit supply is scarce
    target: Position | None    # e.g. ore patch, choke point, enemy core
    params: dict               # kind-specific extras (e.g. desired harvester count)
```

`MissionKind` enum, start small and only add kinds you have evidence you
need:

```python
class MissionKind(Enum):
    BUILD_ECONOMY = auto()   # get a harvester -> conveyor -> core chain running
    EXPAND_ECONOMY = auto()  # add another ore patch to the pipeline
    DEFEND_CORE = auto()     # place/rotate turrets, fall back builders
    SCOUT = auto()           # find enemy core / map the ore
    ATTACK = auto()          # push toward enemy core
```

## Selection logic

`Mission.select(policy_mode, world_state, map) -> list[Mission]` is a small,
rule-based function — no search, no scoring model at first. Example shape:

```python
def select(policy_mode, ws, mp) -> list[Mission]:
    missions = []
    if policy_mode == PolicyMode.BOOTSTRAP:
        missions.append(Mission(MissionKind.BUILD_ECONOMY, priority=10,
                                 target=nearest_unclaimed_ore(mp), params={}))
    elif policy_mode == PolicyMode.DEFEND:
        missions.append(Mission(MissionKind.DEFEND_CORE, priority=20,
                                 target=mp.own_core_pos, params={}))
        missions.append(Mission(MissionKind.BUILD_ECONOMY, priority=5,
                                 target=nearest_unclaimed_ore(mp), params={}))
    # ...
    return missions
```

Start with exactly **one hardcoded mission** (`BUILD_ECONOMY` at the
nearest ore patch) until that alone produces a working, verified economy.
Resist adding `DEFEND_CORE`/`ATTACK`/`SCOUT` until the gate below is met —
this is the step that broke last time (see `00_ARCHITECTURE.md` build
order).

## What does NOT belong here

- Per-unit assignment (`Planner`'s job).
- Actual `ct.*` calls (`Roles`' job).
- Global mode switching logic like "are we winning or losing" (`Policy`'s
  job — Mission only reacts to whatever mode Policy hands it).

## Verification gate before moving to `Planner`

With `Mission.select` hardcoded to always return `[BUILD_ECONOMY]`, confirm
in a replay that the single mission's target is a real, reachable ore tile
on every one of your benchmark maps — including the ones that historically
mined 0.
