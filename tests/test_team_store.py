"""Unit tests for packed team-meta store (slot 0)."""

from __future__ import annotations

from fcode import Position

import team_store


def test_pack_unpack_roundtrip() -> None:
    packed = team_store.pack_team_meta(3, 14, 20, 7, True, 2, flags=0b101)
    ox, oy, ex, ey, known, guns, flags = team_store.unpack_team_meta(packed)
    assert (ox, oy) == (3, 14)
    assert (ex, ey) == (20, 7)
    assert known is True
    assert guns == 2
    assert flags == 0b101


def test_helpers_rmw_preserve_other_fields() -> None:
    class Ct:
        def __init__(self) -> None:
            self.store = [0] * 16

        def read_store(self, i: int) -> int:
            return self.store[i]

        def write_store(self, i: int, v: int) -> None:
            self.store[i] = v

    ct = Ct()
    team_store.write_own_core(ct, Position(1, 2))  # type: ignore[arg-type]
    team_store.write_enemy_core(ct, Position(10, 11))  # type: ignore[arg-type]
    team_store.write_gunner_count(ct, 3)  # type: ignore[arg-type]
    team_store.write_defense_active(ct, True)  # type: ignore[arg-type]
    team_store.write_attack_committed(ct, True)  # type: ignore[arg-type]

    assert team_store.read_own_core(ct) == Position(1, 2)  # type: ignore[arg-type]
    assert team_store.read_enemy_core(ct) == Position(10, 11)  # type: ignore[arg-type]
    assert team_store.read_gunner_count(ct) == 3  # type: ignore[arg-type]
    assert team_store.read_defense_active(ct) is True  # type: ignore[arg-type]
    assert team_store.read_attack_committed(ct) is True  # type: ignore[arg-type]

    team_store.bump_gunner_count(ct)  # type: ignore[arg-type]
    assert team_store.read_gunner_count(ct) == 4  # type: ignore[arg-type]
    # Core / enemy / flags survive gunner bump.
    assert team_store.read_own_core(ct) == Position(1, 2)  # type: ignore[arg-type]
    assert team_store.read_enemy_core(ct) == Position(10, 11)  # type: ignore[arg-type]
    assert team_store.read_defense_active(ct) is True  # type: ignore[arg-type]
    assert team_store.read_attack_committed(ct) is True  # type: ignore[arg-type]

    team_store.write_own_core(ct, Position(5, 6))  # type: ignore[arg-type]
    assert team_store.read_defense_active(ct) is True  # type: ignore[arg-type]


def test_enemy_core_none_when_unknown() -> None:
    class Ct:
        def __init__(self) -> None:
            self.store = [0] * 16

        def read_store(self, i: int) -> int:
            return self.store[i]

        def write_store(self, i: int, v: int) -> None:
            self.store[i] = v

    ct = Ct()
    team_store.write_own_core(ct, Position(4, 5))  # type: ignore[arg-type]
    assert team_store.read_enemy_core(ct) is None  # type: ignore[arg-type]


def test_team_flags_default_false() -> None:
    class Ct:
        def __init__(self) -> None:
            self.store = [0] * 16

        def read_store(self, i: int) -> int:
            return self.store[i]

        def write_store(self, i: int, v: int) -> None:
            self.store[i] = v

    ct = Ct()
    assert team_store.read_defense_active(ct) is False  # type: ignore[arg-type]
    assert team_store.read_attack_committed(ct) is False  # type: ignore[arg-type]
    assert team_store.read_team_flag(ct, team_store.FLAG_TEAM_SPARE) is False  # type: ignore[arg-type]
