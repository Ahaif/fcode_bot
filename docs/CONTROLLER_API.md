# Controller API — Quick Reference

Source of truth: the game's own `AGENTS.md` and Controller API docs. This
file is a condensed version for Cursor to keep in context without pulling
in the full prose docs every time. If anything here conflicts with the
full docs, the full docs win.

## Core rules Cursor must never violate

- Entry point: top-level `class Player` with `run(self, ct: Controller) ->
  None`, in `main.py`. Bots are Python only.
- `run()` is called once per round for **every living unit** — branch on
  `ct.get_entity_type()` at the top.
- 10ms CPU budget per unit per round (~5% buffer). Timeout ≠ crash: a
  timeout just skips that turn. Any **other** uncaught exception
  **permanently destroys the unit** for the rest of the match. Every
  `run()` needs a top-level try/except.
- Always call the matching `can_*()` before a mutating call. The engine
  raises `GameError` on illegal calls, it does not silently no-op.
- Compass: `(0,0)` is NW corner, x east, y south, so `NORTH == (0,-1)`.
- Builder bots move **cardinal only** (N/E/S/W). Diagonals raise on
  `move()`, and `can_move()` is `False` for them. Use
  `pos.cardinal_direction_to(target)` to convert any target into a legal
  step.
- All builder bot actions (build/attack/heal/destroy) require an
  **orthogonally adjacent** tile — not diagonal, not its own tile.
- Acting and moving are mutually exclusive per round for a builder bot.
  If `can_move()` is unexpectedly `False`, check `ct.can_act()` before
  assuming the move itself is illegal.
- Comm store: 16 ints, per team, shared by all units, **writes visible
  only from next round**.
- Always use `get_<entity>_cost()` getters, never hardcoded base costs —
  costs scale with how much of that category you've already built.
- Max 50 living units per team (`GameConstants.MAX_TEAM_UNITS`), including
  the core — check `get_unit_count()` before spawning.

## Method index by category

**Movement (builder bot only):** `can_move`, `move`

**Building:** `can_build_<type>` / `build_<type>` for conveyor, splitter,
harvester, barrier, gunner, sentinel, launcher; generic `can_build` /
`build(entity_type, pos, extra=None)`; `can_destroy` / `destroy`

**Combat:** `can_fire` / `fire`, `can_fire_from` (hypothetical),
`get_attackable_tiles` / `get_attackable_tiles_from`, `get_gunner_target`
(gunner only), `can_heal` / `heal`, `self_destruct`, `can_rotate` /
`rotate` (gunner only), `can_launch` / `launch` (launcher only)

**Vision/sensing:** `get_nearby_tiles`, `get_nearby_entities`,
`get_nearby_buildings`, `get_nearby_units`, `is_in_vision`, `get_tile_env`,
`get_tile_building_id`, `get_tile_builder_bot_id`, `is_tile_empty`,
`is_tile_passable`, `get_stored_resource` / `get_stored_resource_id`

**Unit info:** `get_position`, `get_entity_type`, `get_hp`, `get_max_hp`,
`get_direction`, `get_id`, `get_team`, `get_vision_radius_sq`,
`get_action_cooldown`, `get_move_cooldown`, `can_act`, `get_unit_count`

**Comm store:** `read_store(index)`, `write_store(index, value)` — index
0–15

**Economy:** `get_global_resources`, `get_global_ammo`,
`convert_ammo(amount)` (core only, once/turn, no action-cooldown cost),
`can_convert_ammo`, `get_scale_percent`, `get_<entity>_cost()` for
builder_bot/harvester/gunner/sentinel/launcher/conveyor/splitter/barrier

**Map/match:** `get_map_width`, `get_map_height`, `get_current_round`,
`get_cpu_time_elapsed`

**Debug:** `draw_indicator_line`, `draw_indicator_dot`, `resign(message)`

**Core-only:** `can_spawn(pos)` / `spawn_builder(pos)`

## Entity quick table

| Entity | HP | Base cost | Scale/build | Key trait |
|---|---|---|---|---|
| Core | 500 | — | — | 2x2, action r²=8, spawns builders |
| Builder bot | 40 | 30 Ti | +20% | only mobile unit, cardinal move only |
| Conveyor | 20 | 3 Ti | +1% | faces one direction, accepts 3 sides |
| Splitter | 20 | 6 Ti | +1% | accepts only from back, round-robins output |
| Harvester | 30 | 20 Ti | +5% | must be on ore tile |
| Barrier | 30 | 3 Ti | +1% | HP wall only |
| Gunner | 40 | 10 Ti | +10% | facing turret, r²=13, 2 ammo/shot, line-of-sight |
| Sentinel | 30 | 30 Ti | +20% | facing turret, r²=32, 10 ammo/shot, ignores obstacles |
| Launcher | 30 | 20 Ti | +10% | facing-independent, throws adjacent builder bots |
