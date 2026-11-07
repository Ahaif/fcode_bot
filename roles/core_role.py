"""Core role: publish position, spawn builders, convert ammo for gunners.

The Core runs once per round. Store writes are visible to other units only
from the *next* round — builders may still see the Core in vision on spawn.
"""

from __future__ import annotations

from fcode import Controller, GameConstants

from constants import (
    AMMO_CONVERT_CHUNK,
    MAX_BUILDERS,
    SPAWN_DIRS,
    TITANIUM_RESERVE,
)
from geometry import in_bounds
import idle_investigate
import map_knowledge
from team_store import read_attack_committed, read_gunner_count, write_own_core

# Ammo bank target while a sentinel is grinding the enemy Core. A sentinel
# shot costs 10 ammo and the enemy Core has 500 HP (~28 shots), so a 20-ammo
# gunner cap would starve it after two shots and it could never finish.
ATTACK_AMMO_CAP = 260


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

    guns = read_gunner_count(ct)
    attacking = read_attack_committed(ct)
    if guns <= 0 and not attacking and ct.get_current_round() < 80:
        # Early game: keep Ti for builders / belts / harvesters.
        return

    chunk = AMMO_CONVERT_CHUNK
    if ct.get_global_resources() < chunk + TITANIUM_RESERVE:
        return
    # Cap stockpile: a gunner shot costs 2 ammo, a sentinel 10. Once an
    # attack sentinel is grinding the enemy Core, keep a deep ammo bank so it
    # never stalls mid-grind (a 20-ammo cap dries up after two sentinel shots).
    cap = max(20, guns * 10)
    if attacking:
        cap = max(cap, ATTACK_AMMO_CAP)
    ammo_fn = getattr(ct, "get_global_ammo", None)
    if callable(ammo_fn) and ammo_fn() >= cap:
        return
    if can_fn(chunk):
        do_fn(chunk)


def run_core(ct: Controller, state: object) -> None:
    """One Core turn: broadcast position, feed ammo, spawn up to MAX_BUILDERS
    *currently alive* — replenishing losses, not just a one-time ramp-up."""
    pos = ct.get_position()
    write_own_core(ct, pos)
    map_knowledge.update(ct)

    idle_investigate.log_scheduler_summary(ct, state)

    maybe_convert_ammo(ct)

    # get_unit_count() is a global, engine-tracked team stat (unlike
    # get_nearby_units(), it isn't limited to the Core's own vision, so
    # builders scattered far away exploring are still counted correctly).
    # A one-time "spawned so far" tally (the previous approach) never
    # decreases when a builder dies, so once it reached MAX_BUILDERS the
    # Core stopped spawning forever — permanently under-strength for the
    # rest of the match after any combat losses (real match, 2026-08-23:
    # "when builder is died... the attacker or other builder never
    # spawned"). BUILDER_BOT is our only mobile unit type, so unit_count
    # is exactly the live builder count.
    if ct.get_unit_count() >= MAX_BUILDERS:
        return
    try_spawn_builder(ct)
