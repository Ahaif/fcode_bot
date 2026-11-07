"""Sentinel role: fire at the nearest legal enemy target in raw attack range.

Sentinels have no get_gunner_target()-style convenience (that helper is
documented "only valid on gunners") and no can_rotate()/rotate() either
(also gunner-only per the engine's own docs) — facing is fixed at build
time forever. So this scans get_attackable_tiles() (the turret's raw
pattern, ignoring occupancy/legality) and picks the nearest tile that's
both enemy-occupied and actually can_fire()-legal.
"""

from __future__ import annotations

import os

from fcode import Controller, Position

_DEBUG = os.environ.get("SENTINEL_DEBUG", "") == "1"


def _debug_write(line: str) -> None:
    if not _DEBUG:
        return
    with open("sentinel_debug.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _is_enemy_tile(ct: Controller, tile: Position) -> bool:
    """True only if an enemy building or builder bot occupies tile."""
    my_team = ct.get_team()
    try:
        bid = ct.get_tile_building_id(tile)
        if bid is not None:
            return ct.get_team(bid) != my_team
    except Exception:
        return False
    try:
        uid = ct.get_tile_builder_bot_id(tile)
        if uid is not None:
            return ct.get_team(uid) != my_team
    except Exception:
        return False
    return False


def run_sentinel(ct: Controller, state: object) -> None:
    """One sentinel turn — fire at the nearest legal enemy in raw attack range."""
    del state  # no per-sentinel memory needed — facing is fixed forever
    try:
        tiles = ct.get_attackable_tiles()
    except Exception:
        return

    pos = ct.get_position()
    best: Position | None = None
    best_dist = 0
    if _DEBUG:
        my_team = ct.get_team()
        occ = []
        for t in tiles:
            bid = None
            uid = None
            try:
                bid = ct.get_tile_building_id(t)
            except Exception:
                pass
            try:
                uid = ct.get_tile_builder_bot_id(t)
            except Exception:
                pass
            if bid is not None:
                try:
                    occ.append(f"({t.x},{t.y})=building:team{ct.get_team(bid)}")
                except Exception:
                    occ.append(f"({t.x},{t.y})=building:?")
            elif uid is not None:
                try:
                    occ.append(f"({t.x},{t.y})=bot:team{ct.get_team(uid)}")
                except Exception:
                    occ.append(f"({t.x},{t.y})=bot:?")
        _debug_write(
            f"r={ct.get_current_round()} id={ct.get_id()} pos=({pos.x},{pos.y}) "
            f"dir={ct.get_direction()} n_tiles={len(tiles)} occupied={occ}"
        )

    for tile in tiles:
        if not _is_enemy_tile(ct, tile):
            continue
        try:
            fireable = ct.can_fire(tile)
        except Exception:
            fireable = False
        if _DEBUG:
            _debug_write(
                f"  candidate enemy tile ({tile.x},{tile.y}) can_fire={fireable}"
            )
        if not fireable:
            continue
        d = pos.distance_squared(tile)
        if best is None or d < best_dist:
            best = tile
            best_dist = d

    if best is not None:
        ct.fire(best)
        if _DEBUG:
            _debug_write(f"  FIRED at ({best.x},{best.y})")
