# Layer: Policy

## Status: partially built as `condition_state.py`

`get_condition_state(ct) -> SAFE | CORE_THREAT | ECONOMY_THREAT |
STARVATION` (not the `PolicyMode` enum below) is real, live, and consumed
by `defense.py` (turret dispatch) and `combat.py` (attacker dispatch) —
see `PROGRESS_TRACKER.md`'s "How Defense/Offense interact with Assignment
and Economy" for the exact mechanics and `docs/ARCHITECTURE.md` Layer 6
for the architectural framing. It was built alongside Phase 4/5 rather
than last, because turret placement and attack dispatch both needed *some*
threat signal to react to — narrower than this doc's original design, but
proven on real ladder matches rather than sitting unused.

What it does cover:
- `CORE_THREAT` ≈ this doc's `DEFEND` (real attack/melee range against the
  Core, not just "enemy visible nearby" — `CORE_THREAT_RANGE_SQ_GUNNER`/
  `_SENTINEL`/`_BUILDER`/`_BARRIER` from the actual engine range
  constants).
- The capped one-attacker dispatch ≈ a narrow slice of `ATTACK` — triggers
  reactively on `CORE_THREAT` + affordability, not on `global_resources >
  ATTACK_THRESHOLD` as designed below.
- `STARVATION` is the only resource-arbitration concern between Economy
  and Defense/Offense spending that exists at all today (no reservation
  system beyond it).

What's still missing (the real remaining scope of this layer):
- No `BOOTSTRAP` or `ENDGAME` modes.
- No `ATTACK_THRESHOLD`-style economy-strength gate — see the decision
  function below; today's actual trigger is `CORE_THREAT` being true, not
  "the economy is strong enough to justify pushing." This is deliberate
  for now (small, capped, "just to have a first attack" per the
  instruction that shaped the current Offense implementation), not an
  oversight — but it's exactly the timing decision this doc describes
  Policy owning, unaddressed.
- No single `PolicyMode` object every layer reads — each condition is read
  independently by whichever module needs it (decentralized, same pattern
  as Assignment/`claim_manager.py`, not the "recompute locally vs. read
  from comm store" choice this doc's own "Consistency across units"
  section below assumes gets made once and reused).
- No `BOOTSTRAP_ROUNDS`/`under_threat` implementation as literally
  specified below — `condition_state.py`'s equivalents
  (`CORE_THREAT_RANGE_SQ_*`, `STARVATION_ROUNDS`) are real but shaped
  around what Defense/Offense actually needed, not derived from this
  doc's flat rule table.

The design below (the `PolicyMode` enum, the `decide()` rule table,
`ATTACK_THRESHOLD`) is still the target for closing that gap — treat it as
what Phase 6 finishes, not as describing something already obsoleted by
`condition_state.py`.

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
