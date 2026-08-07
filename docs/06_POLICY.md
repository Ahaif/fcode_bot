# Layer: Policy

## Responsibility

The one true global-mode switch. Everything below Policy just executes
whatever mode it's told; Policy is where "are we winning, should we turn
aggressive, are we under threat" gets decided. Build this **last**, and
keep it a simple rule-based state machine at first — not a scoring model,
not a search.

## Data shape

```python
class PolicyMode(Enum):
    BOOTSTRAP = auto()   # round 0 — get first harvester up ASAP
    ECONOMY = auto()     # steady-state expansion
    DEFEND = auto()      # under attack or detected threat nearby
    ATTACK = auto()      # economy strong enough to push
```

## Decision function — start as a flat rule table, not a graph

```python
def decide(ws: WorldState, mp: MapKnowledge) -> PolicyMode:
    if ws.round_num < BOOTSTRAP_ROUNDS:
        return PolicyMode.BOOTSTRAP
    if under_threat(ws, mp):        # e.g. enemy unit within N tiles of own core
        return PolicyMode.DEFEND
    if ws.global_resources > ATTACK_THRESHOLD and mp.enemy_core_pos is not None:
        return PolicyMode.ATTACK
    return PolicyMode.ECONOMY
```

Every threshold here (`BOOTSTRAP_ROUNDS`, `ATTACK_THRESHOLD`, the "under
threat" radius) belongs in `constants.py`, tunable and benchmarkable — not
hardcoded inline. This is also the natural hook point for the offline
self-play/param-sweep tooling from the two-tier setup: Policy thresholds
are exactly the kind of thing you sweep, not the lower layers.

## Consistency across units (same concern as Planner's design choice)

Same requirement as `04_PLANNER.md`: every unit must land on the same
`PolicyMode` this round, since Mission/Planner assume one shared mode.
Whatever choice you made there (recompute locally vs. read from comm
store) — reuse the same mechanism here rather than inventing a second one.

## What does NOT belong here

- Anything that touches `ct.*` directly (Roles' job).
- Anything that picks a specific tile or unit (Mission/Planner's job).
- Search/lookahead of any kind, at least initially — if you want an MCTS
  or evaluation-function layer eventually, it lives *inside* Policy's
  decision function as an upgrade, not as a new layer.

## Verification gate — this is the finish line for v1

Full benchmark suite, all four modes wired, before touching AI-assisted
tuning or self-play sweeps: no maps mining 0, no unhandled `GameError`s,
win rate vs. the starter bot meaningfully above 50% on the core suite.
That's the point where the offline Tier-2 tooling (self-play, param
sweeps) becomes worth running against this, rather than against a bot
whose foundation is still shaky.
