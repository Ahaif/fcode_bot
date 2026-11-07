"""Gunner role — fire at the first *enemy* target on the facing line, and
rotate toward a known Core threat when idle for too long.

``get_gunner_target()`` returns the nearest targetable tile with no team
filter, so a friendly conveyor/harvester in the line looks identical to an
enemy — always confirm the occupant's team before firing. Gunners (unlike
sentinels) can rotate, so a gunner facing the wrong way while an enemy sits
just off its line should swing around instead of idling forever.
"""

from __future__ import annotations

from fcode import Controller

import condition_state
from constants import GUNNER_IDLE_ROTATE_ROUNDS
from geometry import facing_toward
import map_knowledge
from team_store import read_own_core


def _is_enemy_tile(ct: Controller, tile) -> bool:
    my_team = ct.get_team()
    try:
        bid = ct.get_tile_building_id(tile)
        if bid is not None:
            return ct.get_team(bid) != my_team
        uid = ct.get_tile_builder_bot_id(tile)
        if uid is not None:
            return ct.get_team(uid) != my_team
    except Exception:
        return False
    return False


def _try_rotate_toward_threat(ct: Controller, state: object) -> None:
    core = read_own_core(ct)
    threats = condition_state.get_core_threats(ct, core)
    if not threats:
        return
    pos = ct.get_position()
    nearest = min(threats, key=lambda t: pos.distance_squared(t.pos))
    facing = facing_toward(pos, nearest.pos)
    try:
        if facing == ct.get_direction():
            return
        if ct.can_rotate(facing):
            ct.rotate(facing)
            state.idle_rounds = 0  # type: ignore[attr-defined]
    except Exception:
        return


def run_gunner(ct: Controller, state: object) -> None:
    """One gunner turn — auto-fire on an enemy, else count idle / rotate."""
    map_knowledge.update(ct)

    target = ct.get_gunner_target()
    if target is not None and _is_enemy_tile(ct, target):
        try:
            if ct.can_fire(target):
                ct.fire(target)
                state.idle_rounds = 0  # type: ignore[attr-defined]
                return
        except Exception:
            pass

    state.idle_rounds = getattr(state, "idle_rounds", 0) + 1  # type: ignore[attr-defined]
    if state.idle_rounds >= GUNNER_IDLE_ROTATE_ROUNDS:  # type: ignore[attr-defined]
        _try_rotate_toward_threat(ct, state)
