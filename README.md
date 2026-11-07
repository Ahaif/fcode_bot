# Florent Code League Bot

A Python strategy bot that coordinates a robot fleet to mine titanium, connect harvesters to its core, defend its economy, and attack the enemy core.

## Stack

- **Runtime:** Python and the `fcode` Controller API, with a 10 ms CPU budget per unit per turn.
- **Testing:** pytest with fake controllers; local matches against idle and starter bots.
- **Tooling:** Bash scripts for matches and submission packaging, TOML configuration, and replay diagnostics.

## Architecture

`main.py` exposes `Player.run(ct)` and dispatches each unit to its handler in `roles/`. Each unit retains its own state between turns; team coordination uses a shared, buffered 16-slot integer store.

- **Behavior:** a builder state machine delegates mining, exploration, defense, and offense to `economy.py`, `explore.py`, `defense.py`, and `combat.py`.
- **Coordination:** `assignment.py` and `claim_manager.py` assign ore ownership deterministically; `team_store.py` encodes shared information.
- **Intelligence:** `map_knowledge.py` remembers terrain and sightings; `condition_state.py` evaluates threats and starvation.
- **Movement:** `navigator.py` handles local movement and obstacle recovery; `pathfind.py` provides BFS waypoints for attackers.

The docs also describe future layers: dedicated World State and Mission objects are not implemented yet.

## Engineering practices

- Separate role execution, navigation, shared state, and tunable constants.
- Check action legality and contain exceptions at the entry point to protect units from permanent destruction.
- Use deterministic ownership, persistent state, and stuck recovery to reduce duplicate work and deadlocks.
- Validate behavior changes with regression tests, map benchmarks, replay review, and matches under the real CPU limit before promotion.

## Local checks

Requires an external environment with `fcode` and pytest installed; keep virtual environments outside this bot directory because the engine validates its Python files recursively.

```bash
python -m pytest tests/ -q
./scripts/run_vs_starter.sh maps/duel.map26 1
fcode run . bots/starter maps/showdown.map26 --seed 1023944511 --tle 10
./scripts/package_submission.sh
```

See [Architecture](docs/ARCHITECTURE.md), [Code overview](docs/OVERVIEW.md), [Controller API](docs/CONTROLLER_API.md), and [Progress tracker](PROGRESS_TRACKER.md) for details and development history.
