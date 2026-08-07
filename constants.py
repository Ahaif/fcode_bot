"""Tuning knobs, store slots, and builder phase names.

Keep magic numbers here so roles stay readable. Costs always come from
``ct.get_*_cost()`` at runtime — never hardcode titanium amounts for builds.
"""

from __future__ import annotations

from fcode import Direction

# ---------------------------------------------------------------------------
# Directions
# ---------------------------------------------------------------------------

CARDINALS = (
    Direction.NORTH,
    Direction.EAST,
    Direction.SOUTH,
    Direction.WEST,
)

# Deterministic spawn try-order (cardinals first, then diagonals).
SPAWN_DIRS = (
    Direction.NORTH,
    Direction.EAST,
    Direction.SOUTH,
    Direction.WEST,
    Direction.NORTHEAST,
    Direction.SOUTHEAST,
    Direction.SOUTHWEST,
    Direction.NORTHWEST,
)

# ---------------------------------------------------------------------------
# Comm store (writes visible next round only)
# ---------------------------------------------------------------------------

SLOT_CORE_X = 0
SLOT_CORE_Y = 1
SLOT_ENEMY_CORE_X = 2
SLOT_ENEMY_CORE_Y = 3
SLOT_ENEMY_CORE_KNOWN = 4  # 1 when a friendly unit has seen the enemy Core
SLOT_GUNNER_COUNT = 5      # approximate; bumped when we place a gunner
# Explicit mining ownership board (comm store is only 16 ints total).
# Slots 6..15: up to 10 packed claims. Value 0 = empty.
# Pack: builder_id * 100000 + x * 256 + y + 1
SLOT_CLAIM_BASE = 6
MAX_ORE_CLAIMS = 10  # slots 6..15 inclusive

# ---------------------------------------------------------------------------
# Economy / army size
# ---------------------------------------------------------------------------

MAX_BUILDERS = 7
MAX_GUNNERS = 4
AMMO_CONVERT_CHUNK = 20
# Keep this much Ti in the global pool before converting ammo / building guns.
TITANIUM_RESERVE = 40

# ---------------------------------------------------------------------------
# Movement / exploration
# ---------------------------------------------------------------------------

STUCK_LIMIT = 3
# Belt plan may replan this many times before FAILED_TASK.
MAX_BELT_REPLANS = 6
# After this many sector-waypoint advances with no ore, switch to defend/attack.
WAYPOINTS_BEFORE_COMBAT = 12

# Stay near the Core when placing defensive gunners (distance²).
DEFENSE_RING_SQ = 18

# ---------------------------------------------------------------------------
# Builder FSM (authoritative ``state.phase``)
#
#   EXPLORE → MOVE_TO_STAND → BUILD_HARVESTER → PLAN_CONVEYOR
#          → BUILD_CONVEYOR → RETURN_HOME → COMPLETE_TASK → EXPLORE
#                              ↘ FAILED_TASK → EXPLORE
#          ↘ DEFEND / ATTACK (late game)
#
# Conveyors follow a persistent build_plan; no per-turn greedy neighbours.
# ---------------------------------------------------------------------------

PHASE_EXPLORE = "explore"
PHASE_MOVE_TO_STAND = "move_to_stand"
PHASE_BUILD_HARVESTER = "build_harvester"
PHASE_PLAN_CONVEYOR = "plan_conveyor"
PHASE_BUILD_CONVEYOR = "build_conveyor"
PHASE_RETURN_HOME = "return_home"
PHASE_COMPLETE_TASK = "complete_task"
PHASE_FAILED_TASK = "failed_task"
PHASE_DEFEND = "defend"
PHASE_ATTACK = "attack"

# Back-compat aliases used by older tests / docs.
PHASE_APPROACH_ORE = PHASE_MOVE_TO_STAND
PHASE_BELT_TO_CORE = PHASE_BUILD_CONVEYOR
