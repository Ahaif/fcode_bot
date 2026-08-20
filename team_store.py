"""Team meta store — packed slot 0 (own/enemy core + gunners + flags).

Layout (u32, LSB = bit 0):
  bits  0-4   own_core_x
  bits  5-9   own_core_y
  bits 10-14  enemy_core_x
  bits 15-19  enemy_core_y
  bit  20     enemy_core_known
  bits 21-23  gunner_count
  bit  24     FLAG_DEFENSE_ACTIVE   (Phase 4; unused until then)
  bit  25     FLAG_ATTACK_COMMITTED (Phase 5; unused until then)
  bit  26     FLAG_TEAM_SPARE       (Phase 4/5 spare boolean)
  bits 27-31  reserved (0)

Claims stay in slots 6-15 unchanged — do not shrink the claim board.

All former SLOT_CORE_* / SLOT_ENEMY_* / SLOT_GUNNER_* call sites must go
through these helpers — never read/write the packed fields raw.
"""

from __future__ import annotations

from fcode import Controller, Position

from constants import SLOT_TEAM_META

_MASK5 = 31
_MASK3 = 7

# Team-wide boolean flags in bits 24-26 (cheap; no extra slots).
FLAG_DEFENSE_ACTIVE = 0
FLAG_ATTACK_COMMITTED = 1
FLAG_TEAM_SPARE = 2
_FLAG_SHIFT = 24
_FLAG_MASK = 7 << _FLAG_SHIFT  # bits 24-26


def _read_raw(ct: Controller) -> int:
    try:
        return int(ct.read_store(SLOT_TEAM_META)) & 0xFFFFFFFF
    except Exception:
        return 0


def _write_raw(ct: Controller, value: int) -> None:
    ct.write_store(SLOT_TEAM_META, int(value) & 0xFFFFFFFF)


def pack_team_meta(
    own_x: int,
    own_y: int,
    enemy_x: int,
    enemy_y: int,
    known: bool,
    guns: int,
    flags: int = 0,
) -> int:
    return (
        (own_x & _MASK5)
        | ((own_y & _MASK5) << 5)
        | ((enemy_x & _MASK5) << 10)
        | ((enemy_y & _MASK5) << 15)
        | ((1 if known else 0) << 20)
        | ((guns & _MASK3) << 21)
        | ((flags & _MASK3) << _FLAG_SHIFT)
    )


def unpack_team_meta(
    value: int,
) -> tuple[int, int, int, int, bool, int, int]:
    """Return (own_x, own_y, enemy_x, enemy_y, known, guns, flags)."""
    v = int(value) & 0xFFFFFFFF
    own_x = v & _MASK5
    own_y = (v >> 5) & _MASK5
    enemy_x = (v >> 10) & _MASK5
    enemy_y = (v >> 15) & _MASK5
    known = ((v >> 20) & 1) == 1
    guns = (v >> 21) & _MASK3
    flags = (v >> _FLAG_SHIFT) & _MASK3
    return own_x, own_y, enemy_x, enemy_y, known, guns, flags


def read_own_core(ct: Controller) -> Position:
    ox, oy, _, _, _, _, _ = unpack_team_meta(_read_raw(ct))
    return Position(ox, oy)


def write_own_core(ct: Controller, pos: Position) -> None:
    _, _, ex, ey, known, guns, flags = unpack_team_meta(_read_raw(ct))
    _write_raw(ct, pack_team_meta(pos.x, pos.y, ex, ey, known, guns, flags))


def read_enemy_core(ct: Controller) -> Position | None:
    _, _, ex, ey, known, _, _ = unpack_team_meta(_read_raw(ct))
    if not known:
        return None
    return Position(ex, ey)


def write_enemy_core(ct: Controller, pos: Position) -> None:
    ox, oy, _, _, _, guns, flags = unpack_team_meta(_read_raw(ct))
    _write_raw(ct, pack_team_meta(ox, oy, pos.x, pos.y, True, guns, flags))


def read_gunner_count(ct: Controller) -> int:
    return unpack_team_meta(_read_raw(ct))[5]


def write_gunner_count(ct: Controller, n: int) -> None:
    ox, oy, ex, ey, known, _, flags = unpack_team_meta(_read_raw(ct))
    _write_raw(
        ct,
        pack_team_meta(ox, oy, ex, ey, known, max(0, min(7, int(n))), flags),
    )


def bump_gunner_count(ct: Controller) -> int:
    n = read_gunner_count(ct) + 1
    write_gunner_count(ct, n)
    return n


def read_team_flag(ct: Controller, flag: int) -> bool:
    """Read one of FLAG_DEFENSE_ACTIVE / FLAG_ATTACK_COMMITTED / FLAG_TEAM_SPARE."""
    if not (0 <= flag <= 2):
        return False
    flags = unpack_team_meta(_read_raw(ct))[6]
    return ((flags >> flag) & 1) == 1


def write_team_flag(ct: Controller, flag: int, value: bool) -> None:
    """Set/clear a team boolean without disturbing cores/gunners."""
    if not (0 <= flag <= 2):
        return
    ox, oy, ex, ey, known, guns, flags = unpack_team_meta(_read_raw(ct))
    if value:
        flags = flags | (1 << flag)
    else:
        flags = flags & ~(1 << flag)
    _write_raw(ct, pack_team_meta(ox, oy, ex, ey, known, guns, flags & _MASK3))


def read_defense_active(ct: Controller) -> bool:
    return read_team_flag(ct, FLAG_DEFENSE_ACTIVE)


def write_defense_active(ct: Controller, value: bool) -> None:
    write_team_flag(ct, FLAG_DEFENSE_ACTIVE, value)


def read_attack_committed(ct: Controller) -> bool:
    return read_team_flag(ct, FLAG_ATTACK_COMMITTED)


def write_attack_committed(ct: Controller, value: bool) -> None:
    write_team_flag(ct, FLAG_ATTACK_COMMITTED, value)
