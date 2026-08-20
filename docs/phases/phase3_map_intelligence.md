# Phase 3 — Map Intelligence

**Checklist lives in:** [`PROGRESS_TRACKER.md`](../../PROGRESS_TRACKER.md#phase-3--map-intelligence)
(this file does not duplicate the checkboxes — update them there, not here).

Moved ahead of Defense/Offense: turret placement (Phase 4) and attack
targeting (Phase 5) both want real map memory — enemy Core position, known
choke points, discovered ore — rather than the comm-store-only pieces that
exist today. Build the `Map` layer once, reuse it in both.

## Goal
Use accumulated map knowledge (ore, walls, enemy buildings, enemy Core
estimate, choke points, expansion scoring) for better decisions.

## Architecture reference
This phase **is** the `Map` layer from `docs/02_MAP.md` — the one target
layer that doesn't exist as a real module yet (no `map_knowledge.py`).
Read `docs/02_MAP.md` in full before starting: it covers the hard
persistence constraint (no shared game object across units — only the comm
store or module-level state, and module-level state's reliability across
rounds must be verified against a real match before depending on it) and the
comm-store encoding fallback if module-level persistence doesn't hold up.

## Where partial map memory lives today
- `combat.py` — `note_enemy_core` / `enemy_core_target`: exactly the
  `MapKnowledge.enemy_core_pos` field from `docs/02_MAP.md`, but implemented
  directly against the comm store instead of a `Map` object. This is the
  pattern to generalize when building the real layer (ore tiles, walls,
  choke points).
- `roles/builder_role.py` — `state.visited: set[tuple[int, int]]` is
  per-unit "have I been here," not team-wide map knowledge — it resets on
  `_abandon_ore`/`_enter_late_game` and won't survive as the seed for a real
  `Map` object without changes.

## What building this phase actually means
Per `docs/02_MAP.md`'s verification gate: before anything else, confirm
whether module-level Python state actually persists correctly across
rounds for this engine's process model (validate once, explicitly, in a
replay) — everything else in this phase depends on that answer.

## Verification
Better mining on difficult maps (partial-visibility maps, maps where enemy
Core isn't visible early) vs. the Phase 2 (Economy Optimization) baseline.
