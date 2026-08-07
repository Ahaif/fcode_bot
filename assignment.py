"""Per-builder Assignment cache.

Ownership authority lives in ``claim_manager``. This module only holds the
dataclass and read helpers. Mutations of ``ore_pos`` / the claim table must
go through ClaimManager (Acquire / Release / Complete / Transfer).
"""

from __future__ import annotations

from dataclasses import dataclass

from fcode import Controller, Position

import claim_manager


@dataclass
class Assignment:
    ore_pos: Position | None = None
    harvester_pos: Position | None = None
    connected: bool = False
    completed: bool = False

    @property
    def active(self) -> bool:
        return self.ore_pos is not None and not self.completed

    @property
    def has_harvester(self) -> bool:
        return self.harvester_pos is not None


def empty_assignment() -> Assignment:
    return Assignment()


def get_assignment(state: object) -> Assignment:
    a = getattr(state, "assignment", None)
    if a is None or not isinstance(a, Assignment):
        a = empty_assignment()
        state.assignment = a  # type: ignore[attr-defined]
    return a


# ---- Thin facades (tests / legacy call sites) → claim_manager ----------------


def read_claim_board(ct: Controller) -> dict[tuple[int, int], int]:
    return claim_manager.read_table(ct)


def ore_owner(ore: Position, ct: Controller | None = None) -> int | None:
    return claim_manager.owner_of(ore, ct)


def builder_owns_any(builder_id: int, ct: Controller | None = None) -> bool:
    if ct is not None:
        return claim_manager.builder_claim(ct, builder_id) is not None
    return any(oid == builder_id for oid in claim_manager.read_table().values())


def claim_ore(state: object, ore: Position, owner_id: int = -1) -> Assignment:
    """Test helper — commits ownership via the manager protocol when possible."""
    if owner_id >= 0:
        state.claim_owner_id = owner_id  # type: ignore[attr-defined]
        state.claim_turns_alive = max(  # type: ignore[attr-defined]
            int(getattr(state, "claim_turns_alive", 0)), 1
        )
    # Direct test path: set cache + mirror without Controller.
    claim_manager._TABLE[claim_manager._ore_key(ore)] = (  # noqa: SLF001
        owner_id if owner_id >= 0 else -1
    )
    if owner_id >= 0:
        for k, oid in list(claim_manager._TABLE.items()):  # noqa: SLF001
            if oid == owner_id and k != claim_manager._ore_key(ore):  # noqa: SLF001
                del claim_manager._TABLE[k]  # noqa: SLF001
    a = Assignment(ore_pos=ore, harvester_pos=None, connected=False, completed=False)
    state.assignment = a  # type: ignore[attr-defined]
    return a


def clear_assignment(state: object, ct: Controller | None = None) -> None:
    if ct is not None:
        claim_manager.release(ct, state, reason="clear_assignment")
        return
    owner_id = getattr(state, "claim_owner_id", None)
    a = get_assignment(state)
    if a.ore_pos is not None:
        key = claim_manager._ore_key(a.ore_pos)  # noqa: SLF001
        if claim_manager._TABLE.get(key) == owner_id or owner_id is None:  # noqa: SLF001
            claim_manager._TABLE.pop(key, None)  # noqa: SLF001
    if isinstance(owner_id, int):
        dead = [k for k, oid in claim_manager._TABLE.items() if oid == owner_id]  # noqa: SLF001
        for k in dead:
            del claim_manager._TABLE[k]  # noqa: SLF001
    state.assignment = empty_assignment()  # type: ignore[attr-defined]
    claim_manager._clear_task_fields(state)  # noqa: SLF001


def record_harvester(state: object, ore: Position) -> None:
    claim_manager.note_harvester(state, ore)


def mark_connected(state: object, ct: Controller | None = None) -> None:
    if ct is not None:
        claim_manager.complete(ct, state)
        return
    clear_assignment(state, ct=None)


def reset_ownership_registry_for_tests() -> None:
    claim_manager.reset_for_tests()


# Removed from public ownership API (use claim_manager):
# publish_claim, clear_store_claim, sync_ownership, release_builder_claims
