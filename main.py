"""Florent Code League bot — entry point.

``fcode`` loads this file and constructs one ``Player`` per living unit.
``run`` is called once per round for that unit (~10ms budget).

Layout (keep imports shallow — Tier-1 code only):
  constants.py   — knobs, store slots, phase names
  team_store.py  — packed slot-0 team meta helpers
  map_knowledge.py — per-unit known_env + team sightings/choke
  geometry.py    — movement / adjacency / shortest-path helpers
  navigator.py   — shared Bug2 / wall-follow movement
  assignment.py  — per-builder economy ownership memory
  claim_manager.py — ore claim board (slots 6–15)
  economy.py     — ore → harvester → belt home (shortest path only)
  explore.py     — sector exploration from Core
  defense.py     — gunners around our Core
  combat.py      — attack enemy harvesters / Core
  roles/         — per-EntityType turn handlers
"""

from __future__ import annotations

from fcode import Controller, Direction, EntityType, GameError, Position

from assignment import Assignment, empty_assignment
from constants import PHASE_EXPLORE
from navigator import NAV_NORMAL
from roles import builder_role, core_role, gunner_role, launcher_role, sentinel_role


class Player:
    """Per-unit brain. Instance fields persist across rounds for this unit only."""

    def __init__(self) -> None:
        # --- Builder ---
        self.phase = PHASE_EXPLORE
        self.core_pos: Position | None = None
        self.assignment: Assignment = empty_assignment()
        self.approach_stand: Position | None = None
        self.stand_paved = False
        self.build_plan: list[Position] | None = None
        self.build_index = 0
        self.replan_count = 0
        self.last_replan_reason: str | None = None
        self.visited: set[tuple[int, int]] = set()
        self.explore_target: Position | None = None
        self.explore_trail: list[tuple[int, int]] = []
        self.attack_target: Position | None = None
        # --- Dedicated attacker (combat.py) ---
        self.attack_claim_round: int | None = None
        self.attack_sentinel_tiles: set[tuple[int, int]] = set()
        self.attack_last_dist: int | None = None
        self.attack_stall_rounds = 0
        self.attack_launcher_built = False
        self.attack_launcher_wait_pos: Position | None = None
        self.attack_launcher_wait_rounds = 0
        self.attack_launched_or_gave_up = False
        self.builder_index = 0
        self.num_sectors = 4
        self.sector_init = False
        self.waypoint_i = 0
        self.waypoints_advanced = 0
        self.harvests_done = 0
        self.stuck = 0
        self.last_pos: Position | None = None
        self.blocked_dirs: set[Direction] = set()
        # ClaimManager bookkeeping (ownership protocol).
        self.claim_turns_alive = 0
        self.free_turns = 0
        self.claim_owner_id = -1

        # --- Navigator (movement only — no strategy) ---
        self.nav_mode = NAV_NORMAL
        self.nav_hit_distance = 10**9
        self.nav_last_distance = 10**9  # legacy alias of hit_distance
        self.nav_wall_side = "left"
        self.nav_side_reversed = False
        self.nav_blocked = Direction.CENTRE
        self.nav_recent_positions: list[tuple[int, int]] = []
        self.nav_escape_counter = 0
        self.nav_target: Position | None = None
        self.nav_local_waypoint: Position | None = None
        self.nav_follow_dir = Direction.NORTH
        self.nav_debug_log: list[str] = []

    def run(self, ct: Controller) -> None:
        """Engine entry — must never let a non-timeout exception escape."""
        try:
            etype = ct.get_entity_type()
            if etype == EntityType.CORE:
                core_role.run_core(ct, self)
            elif etype == EntityType.BUILDER_BOT:
                builder_role.run_builder(ct, self)
            elif etype == EntityType.GUNNER:
                gunner_role.run_gunner(ct, self)
            elif etype == EntityType.SENTINEL:
                sentinel_role.run_sentinel(ct, self)
            elif etype == EntityType.LAUNCHER:
                launcher_role.run_launcher(ct, self)
        except GameError as exc:
            try:
                import idle_investigate

                if idle_investigate.enabled():
                    idle_investigate._write(
                        f"GAME_ERROR | id={ct.get_id()} | r={ct.get_current_round()} | "
                        f"err={type(exc).__name__}:{exc}"
                    )
            except Exception:
                pass
        except Exception as exc:
            # Ownership audit only — never change control flow.
            try:
                import idle_investigate

                if idle_investigate.enabled():
                    idle_investigate._write(
                        f"EXCEPTION | id={ct.get_id()} | r={ct.get_current_round()} | "
                        f"err={type(exc).__name__}:{exc}"
                    )
            except Exception:
                pass
