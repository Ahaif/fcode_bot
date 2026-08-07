# Layer: Roles

## Responsibility

The only layer (besides `WorldState`) that calls `ct.*` mutating methods.
One module per `EntityType`, each turning a `Task` into actual
`can_*()`-gated Controller calls. This is where engine mechanics (cardinal
movement, orthogonal adjacency, cooldowns) live.

## File per role

```
roles/
  core_role.py       # spawn builders, convert_ammo
  builder_role.py     # move / build / harvest-route / attack / heal
  gunner_role.py      # fire, rotate
  sentinel_role.py    # fire
  launcher_role.py    # launch
```

## Contract every role module follows

```python
def execute(ct: Controller, task: Task) -> None:
    """Given this unit's Controller and its assigned Task, take at most
    one action this round. Must never raise — catch GameError internally
    and fall back to a no-op or a safe default (e.g. move toward target).
    """
```

## Example: builder_role.py

```python
def execute(ct: Controller, task: Task) -> None:
    try:
        if task.kind == TaskKind.BUILD and ct.get_action_cooldown() == 0:
            if task.entity_type == EntityType.HARVESTER:
                if ct.can_build_harvester(task.target):
                    ct.build_harvester(task.target)
                    return
            # ... other entity types
        # Fall through to movement if we didn't act this round.
        if task.target is not None and ct.get_move_cooldown() == 0:
            step = ct.get_position().cardinal_direction_to(task.target)
            if step != Direction.CENTRE and ct.can_move(step):
                ct.move(step)
    except GameError:
        pass  # swallow, try again next round with fresh state
```

Note the engine-specific gotchas this layer must always respect (from
`AGENTS.md`/Controller API — don't relitigate these per-role, just follow
them):

- Build/attack/heal/destroy targets must be **orthogonally adjacent**, not
  diagonal, not the bot's own tile.
- Builder bots **move only in cardinal directions** — always route a
  diagonal target through `pos.cardinal_direction_to(target)`.
- Acting and moving are mutually exclusive per round for a builder bot —
  check `ct.can_act()` if `can_move()` unexpectedly returns `False`, rather
  than assuming the move itself is illegal.
- Always `can_*()` before the mutating call — never assume.

## What does NOT belong here

- Deciding *which* tile to build the harvester on (`Planner`/`Mission`'s
  job — Role just executes the target it's handed). If a Role finds itself
  scanning `get_nearby_tiles()` to pick its own target, that logic has
  leaked down from `Planner` and should move back up.

## Verification gate before moving to `Policy`

Run the full benchmark suite with only `BUILD_ECONOMY` wired through all
five layers below Policy. Confirm 0 maps mining 0, 0 `GameError`s in logs,
before writing a single line of `Policy` mode-switching logic.
