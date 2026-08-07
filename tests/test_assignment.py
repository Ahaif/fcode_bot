"""Unit tests for Assignment ownership memory."""

from __future__ import annotations

from types import SimpleNamespace

from fcode import Position

from assignment import (
    Assignment,
    claim_ore,
    clear_assignment,
    empty_assignment,
    get_assignment,
    mark_connected,
    ore_owner,
    record_harvester,
    reset_ownership_registry_for_tests,
)
from geometry import manhattan, nearest_core_tile, on_shortest_path


def setup_function() -> None:
    reset_ownership_registry_for_tests()


def test_empty_assignment_defaults() -> None:
    a = empty_assignment()
    assert a.ore_pos is None
    assert a.harvester_pos is None
    assert a.connected is False
    assert a.completed is False
    assert a.active is False
    assert a.has_harvester is False


def test_claim_ore_does_not_set_harvester() -> None:
    """Claim is approach-only — harvester_pos stays None until build."""
    state = SimpleNamespace()
    ore = Position(4, 5)
    a = claim_ore(state, ore)
    assert state.assignment is a
    assert a.ore_pos == ore
    assert a.harvester_pos is None
    assert a.has_harvester is False
    assert a.connected is False
    assert a.completed is False
    assert a.active is True


def test_record_harvester_sets_ownership() -> None:
    state = SimpleNamespace()
    ore = Position(10, 10)
    claim_ore(state, ore)
    record_harvester(state, ore)
    a = get_assignment(state)
    assert a.ore_pos == ore
    assert a.harvester_pos == ore
    assert a.has_harvester is True


def test_clear_assignment_resets() -> None:
    state = SimpleNamespace()
    claim_ore(state, Position(1, 1), owner_id=9)
    record_harvester(state, Position(1, 1))
    assert ore_owner(Position(1, 1)) == 9
    clear_assignment(state)
    a = get_assignment(state)
    assert a.ore_pos is None
    assert a.harvester_pos is None
    assert a.active is False
    assert ore_owner(Position(1, 1)) is None


def test_get_assignment_creates_when_missing() -> None:
    state = SimpleNamespace()
    a = get_assignment(state)
    assert isinstance(a, Assignment)
    assert state.assignment is a


def test_mark_connected_flags_then_clears() -> None:
    state = SimpleNamespace()
    claim_ore(state, Position(3, 3))
    record_harvester(state, Position(3, 3))
    mark_connected(state)
    a = get_assignment(state)
    assert a.ore_pos is None
    assert a.active is False
    assert a.connected is False
    assert a.completed is False


def test_on_shortest_path_home_gate_unchanged() -> None:
    """Regression: belt gate geometry must stay identical."""
    start = Position(5, 5)
    goal = Position(5, 8)
    assert on_shortest_path(Position(5, 6), start, goal)
    assert on_shortest_path(Position(5, 7), start, goal)
    assert not on_shortest_path(Position(6, 6), start, goal)
    assert manhattan(start, goal) == 3


def test_nearest_core_tile_picks_closest_footprint() -> None:
    core = Position(2, 2)  # footprint (2,2)-(3,3)
    assert nearest_core_tile(Position(0, 2), core) == Position(2, 2)
    assert nearest_core_tile(Position(5, 3), core) == Position(3, 3)


def test_player_starts_with_empty_assignment() -> None:
    from main import Player

    p = Player()
    assert isinstance(p.assignment, Assignment)
    assert p.assignment.ore_pos is None
    assert p.assignment.harvester_pos is None
    assert not hasattr(p, "stand_pos")
