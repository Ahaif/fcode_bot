"""Launcher role: pick up our dedicated attacker builder and throw it toward
the enemy Core, cutting most of the walking distance for the very first
attacker of the match — the enemy was observed using this same technique.

New, previously-untouched engine mechanic this session: no prior code built
or drove a Launcher. Deliberately narrow — only ever throws the one
comm-store-tracked attack-dedicated builder (team_store.read_attack_builder_id),
never a mining/economy builder, so this can't accidentally yank a builder off
an in-progress claim. See combat.py's attack_step for the matching
build-launcher-then-wait-to-be-thrown logic on the attacker's side.
"""

from __future__ import annotations

import os

from fcode import Controller, EntityType

from geometry import orth_adjacent
from team_store import read_attack_builder_id, read_own_core

_DEBUG = os.environ.get("LAUNCHER_DEBUG", "") == "1"


def _debug_write(line: str) -> None:
    if not _DEBUG:
        return
    with open("launcher_debug.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run_launcher(ct: Controller, state: object) -> None:
    """One launcher turn — throw the dedicated attacker toward the enemy
    Core if it's adjacent and a throw is legal this round.

    LAUNCHER_DEBUG=1 logs every reason a throw doesn't happen — this is
    genuinely new, previously-untouched engine territory (no prior code
    ever built or drove a Launcher), and a real match report ("the
    attacker that used the launcher never reached the enemy core") had no
    way to be root-caused without this, same as SENTINEL_DEBUG existing
    for the same reason on the placement side.
    """
    del state  # no per-launcher memory needed
    r = ct.get_current_round() if _DEBUG else 0
    attacker_id = read_attack_builder_id(ct)
    if attacker_id <= 0:
        _debug_write(f"r={r} id={ct.get_id()} skip=no_attacker_claimed")
        return

    my_team = ct.get_team()
    try:
        if ct.get_team(attacker_id) != my_team:
            _debug_write(f"r={r} id={ct.get_id()} skip=attacker_wrong_team")
            return
        if ct.get_entity_type(attacker_id) != EntityType.BUILDER_BOT:
            _debug_write(f"r={r} id={ct.get_id()} skip=attacker_not_builder attacker_id={attacker_id}")
            return
        bot_pos = ct.get_position(attacker_id)
    except Exception as exc:
        _debug_write(f"r={r} id={ct.get_id()} skip=exception_reading_attacker err={exc}")
        return

    if not orth_adjacent(ct.get_position(), bot_pos):
        _debug_write(
            f"r={r} id={ct.get_id()} skip=attacker_not_adjacent "
            f"my_pos=({ct.get_position().x},{ct.get_position().y}) "
            f"bot_pos=({bot_pos.x},{bot_pos.y})"
        )
        return

    # Local import — combat.py already imports from defense.py; importing
    # combat here at module scope would risk a cycle if defense/combat ever
    # import roles.* directly. Deferred import keeps this one-directional.
    from combat import enemy_core_target

    enemy_core = enemy_core_target(ct, read_own_core(ct))
    if enemy_core is None:
        _debug_write(f"r={r} id={ct.get_id()} skip=no_enemy_core_known_or_guessable")
        return

    try:
        tiles = ct.get_attackable_tiles()
    except Exception as exc:
        _debug_write(f"r={r} id={ct.get_id()} skip=exception_get_attackable_tiles err={exc}")
        return
    if not tiles:
        _debug_write(f"r={r} id={ct.get_id()} skip=no_attackable_tiles")
        return

    target = min(tiles, key=lambda t: t.distance_squared(enemy_core))

    try:
        legal = ct.can_launch(bot_pos, target)
    except Exception as exc:
        _debug_write(f"r={r} id={ct.get_id()} skip=exception_can_launch err={exc}")
        return
    if not legal:
        _debug_write(
            f"r={r} id={ct.get_id()} skip=can_launch_false "
            f"bot_pos=({bot_pos.x},{bot_pos.y}) target=({target.x},{target.y})"
        )
        return
    try:
        ct.launch(bot_pos, target)
        _debug_write(
            f"r={r} id={ct.get_id()} LAUNCHED bot_pos=({bot_pos.x},{bot_pos.y}) "
            f"target=({target.x},{target.y})"
        )
    except Exception as exc:
        _debug_write(f"r={r} id={ct.get_id()} skip=exception_launch err={exc}")
