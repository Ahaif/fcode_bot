"""Core role: publish position, spawn builders, convert ammo for gunners.

The Core runs once per round. Store writes are visible to other units only
from the *next* round — builders may still see the Core in vision on spawn.
"""

from __future__ import annotations

from fcode import Controller, GameConstants

from constants import (
    AMMO_CONVERT_CHUNK,
    MAX_BUILDERS,
    SLOT_CORE_X,
    SLOT_CORE_Y,
    SLOT_GUNNER_COUNT,
    SPAWN_DIRS,
    TITANIUM_RESERVE,
)
from geometry import in_bounds
import idle_investigate


def try_spawn_builder(ct: Controller) -> bool:
    """Spawn one builder on a legal tile. Returns True if spawned."""
    if ct.get_action_cooldown() != 0:
        return False
    if ct.get_global_resources() < ct.get_builder_bot_cost():
        return False
    if ct.get_unit_count() >= GameConstants.MAX_TEAM_UNITS:
        return False

    tiles = sorted(
        ct.get_nearby_tiles(dist_sq=GameConstants.CORE_SPAWNING_RADIUS_SQ),
        key=lambda t: (t.x, t.y),
    )
    for tile in tiles:
        if ct.can_spawn(tile):
            ct.spawn_builder(tile)
            return True

    pos = ct.get_position()
    for d in SPAWN_DIRS:
        tile = pos.add(d)
        if not in_bounds(ct, tile):
            continue
        if ct.can_spawn(tile):
            ct.spawn_builder(tile)
            return True
    return False


def maybe_convert_ammo(ct: Controller) -> None:
    """Turn spare titanium into turret ammo once we have (or want) gunners."""
    can_fn = getattr(ct, "can_convert_ammo", None)
    do_fn = getattr(ct, "convert_ammo", None)
    if not callable(can_fn) or not callable(do_fn):
        return

    guns = ct.read_store(SLOT_GUNNER_COUNT)
    if guns <= 0 and ct.get_current_round() < 80:
        # Early game: keep Ti for builders / belts / harvesters.
        return

    chunk = AMMO_CONVERT_CHUNK
    if ct.get_global_resources() < chunk + TITANIUM_RESERVE:
        return
    # Cap stockpile roughly — gunner shot costs 2 ammo.
    ammo_fn = getattr(ct, "get_global_ammo", None)
    if callable(ammo_fn) and ammo_fn() >= max(20, guns * 10):
        return
    if can_fn(chunk):
        do_fn(chunk)


def run_core(ct: Controller, state: object) -> None:
    """One Core turn: broadcast position, feed ammo, spawn up to MAX_BUILDERS."""
    pos = ct.get_position()
    ct.write_store(SLOT_CORE_X, pos.x)
    ct.write_store(SLOT_CORE_Y, pos.y)

    idle_investigate.log_scheduler_summary(ct, state)

    maybe_convert_ammo(ct)

    spawned = getattr(state, "num_spawned", 0)
    if spawned >= MAX_BUILDERS:
        return
    if try_spawn_builder(ct):
        state.num_spawned = spawned + 1  # type: ignore[attr-defined]
