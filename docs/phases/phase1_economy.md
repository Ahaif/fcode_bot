# Phase 1 — Stable Economy

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-1--stable-economy-highest-priority)
(this file does not duplicate the checkboxes — update them there, not here).

## Goal
Build a reliable economy on every benchmark map: no map finishes with 0
titanium mined, every harvester reaches the Core, builders never waste
titanium.

## Architecture reference
- `docs/03_MISSION.md` — this whole phase is, in target-architecture terms,
  a single hardcoded `BUILD_ECONOMY` mission. The docs explicitly say:
  *"Start with exactly one hardcoded mission until that alone produces a
  working, verified economy."* That's where we are.
- `docs/02_MAP.md` — ore tile knowledge (target: `MapKnowledge.ore_tiles`).

## Where this lives in code today
- `economy.py` — `scan_ore`, `pick_ore_stand` (prefer home-path neighbour),
  `try_build_harvester`, `try_pave_stand`, `try_belt_ahead_home` (shortest
  Manhattan path only), `belt_complete`, `ore_claimed_by_other`.
- `roles/builder_role.py` — phase machine:
  `EXPLORE → APPROACH_ORE → BUILD_HARVESTER → BELT_TO_CORE`,
  then back to explore or late-game defend/attack.
- `explore.py` — sector rays from Core (`sector_waypoints`), frontier prefer
  own sector + expand outward, `blocked_dirs` anti-thrash.
- `geometry.py` — `manhattan`, `nearest_core_tile`, `on_shortest_path`.
- `roles/core_role.py` — `try_spawn_builder` up to `MAX_BUILDERS`.
- `constants.py` — `MAX_BUILDERS`, `STUCK_LIMIT`, `WAYPOINTS_BEFORE_COMBAT`.

## Current strategy
1. Explore in an assigned angular sector from the known Core (map W/H).
2. Cap nearest free ore with a **harvester first** — no conveyors yet.
3. Belt home: pave the ore-neighbour stand, then Core-facing conveyors
   **only** on the shortest Manhattan path stand→Core; move only onto
   conveyors or the Core (no empty-tile gaps).

## Verification
Run `scripts/run_vs_starter.sh` (or the full map suite) and check the
Replay Review Checklist's "Economy" and "Exploration" sections in
`PROGRESS_TRACKER.md` — specifically zero maps mining 0 titanium — before
touching Phase 2.
