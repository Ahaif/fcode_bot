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
# Comm store (writes visible next round only; values are u32)
# ---------------------------------------------------------------------------

# Slot 0: packed team meta (own/enemy core + known + gunner count).
# See team_store.py for pack layout and helpers — do not decode raw.
SLOT_TEAM_META = 0
# Slots 1-2: enemy building sightings (MapKnowledge, Stage B).
SLOT_SIGHTING_0 = 1
SLOT_SIGHTING_1 = 2
# Slot 3: choke hint (MapKnowledge, Stage B).
SLOT_CHOKE_HINT = 3
# Slot 4: team-wide "a launcher already exists" flag (Phase 5 offense).
# Slot 5: dedicated attacker builder id + claim round heartbeat (Phase 5).
SLOT_HAS_LAUNCHER = 4
SLOT_ATTACK_BUILDER = 5
# Back-compat aliases (older references).
SLOT_RESERVED_PHASE4 = SLOT_HAS_LAUNCHER
SLOT_RESERVED_PHASE5 = SLOT_ATTACK_BUILDER
# Slots 6..15: ore claim board (unchanged).
# Pack: builder_id * 100000 + x * 256 + y + 1
SLOT_CLAIM_BASE = 6
MAX_ORE_CLAIMS = 10  # slots 6..15 inclusive

# ---------------------------------------------------------------------------
# Economy / army size
# ---------------------------------------------------------------------------

# Tuned by A/B benchmark, re-run twice this session as the bot changed
# significantly in between (2026-08-22 first pass: 6/7/8 vs idle; 2026-08-23
# re-check after several rounds of defense/attack fixes: 4/5/6/7/8 vs full
# bots/starter, 46 maps each). 6 won both times — 0/46 losses, 0/46
# zero-mining, best (tied) Core-destroyed rate. 4 had 2/46 losses + 4/46
# zero-mining (too lean); 8 tied on Core-destroyed rate but had 2/46
# zero-mining from overcrowding small/tight maps; 7 was safe but slower.
MAX_BUILDERS = 6
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
# A harvester already built (post-harvester _fail_task) may be re-adopted
# via try_adopt_orphan_harvester this many consecutive times before the
# builder excludes it for a call, so a genuinely unreachable harvester
# doesn't trap a builder in an every-turn fail/re-adopt loop forever.
ORPHAN_HARVESTER_RETRY_CAP = 3
# After this many sector-waypoint advances with no ore, switch to defend/attack.
WAYPOINTS_BEFORE_COMBAT = 12

# Stay near the Core when placing defensive gunners (distance²).
DEFENSE_RING_SQ = 18

# ---------------------------------------------------------------------------
# Condition-state thresholds (condition_state.py)
#
# CORE_THREAT_RANGE_SQ_* are a proxy: the engine exposes no attack-range
# constant separate from vision (AGENTS.md), so these reuse the closest
# available vision-radius numbers. condition_state falls back to the engine's
# own can_fire_from() for gunners/sentinels sitting just past the proxy.
# ---------------------------------------------------------------------------

CORE_THREAT_RANGE_SQ_GUNNER = 13
CORE_THREAT_RANGE_SQ_SENTINEL = 32
CORE_THREAT_RANGE_SQ_BUILDER = 20
CORE_THREAT_RANGE_SQ_BARRIER = 8
# Enemy combat unit / turret / barrier this close to a friendly harvester /
# conveyor / splitter counts as an economy threat.
ECONOMY_THREAT_RADIUS_SQ = 50

# Starvation: N consecutive rounds under a living-unit-count-scaled floor.
STARVATION_RESERVE = 50
STARVATION_PER_UNIT_BUFFER = 15
STARVATION_ROUNDS = 5

# ---------------------------------------------------------------------------
# Reactive defense dispatch (defense.py + builder_role.py)
# ---------------------------------------------------------------------------

# Build the defensive turret once this close to the threat (distance²);
# otherwise advance toward it first.
DEFEND_THREAT_APPROACH_RANGE_SQ = 2
# Cap on simultaneous armed-threat coverage (builders_needed_for_defense =
# min(want - have, this)). "have" only counts *built* turrets, not
# builders already en route — and should_enter_defend_build's ranking
# includes those already-dispatched builders too, so at 2 this became a
# hard ceiling on total simultaneous defenders forever, never scaling past
# it even with 4 real armed threats (2026-08-22 report: a pure-builder
# rush destroyed the Core in 146 rounds with only ~2 defenders ever
# dispatched). Raised to 4 to match a full 4-direction rush.
DEFEND_BUILD_MAX_FROM_TRIGGER = 4

# Gunner swings toward a known Core threat after this many idle rounds.
GUNNER_IDLE_ROTATE_ROUNDS = 3

# A free builder withholds starting a new mining task while
# core_in_immediate_danger (Ti priority for the sentinel that saves the
# Core — see should_interrupt_for_core_defense). Uncapped, this deadlocks
# the whole economy for the rest of the match against an opponent that
# maintains persistent pressure we can never fully cover (real match,
# 2026-08-22, jotunheim: ~470 Ti collected by round 254, the attacker
# unable to afford even one sentinel, stalled that way for most of a
# 1000-round loss). After this many consecutive rounds a single builder
# has personally observed the pause, it resumes economy anyway —
# permanent starvation guarantees a loss via stalemate, which is strictly
# worse than accepting the risk and trying to out-economy the threat.
ECONOMY_PAUSE_MAX_ROUNDS = 30

# A builder in PHASE_DEFEND_BUILD only ever exits when get_core_threats
# returns empty — with no bound, an opponent that maintains persistent
# pressure (rebuilding/repositioning armed units faster than we can cover
# them, or a threat we simply can never find a legal placement against)
# can strand a builder there for the rest of the match. With up to
# DEFEND_BUILD_MAX_FROM_TRIGGER=4 builders potentially stuck this way at
# once, this is the primary mechanism behind severe/total economy
# starvation seen in real matches — not just the free-builder pause above,
# since a builder already committed to PHASE_DEFEND_BUILD never reaches
# that gate at all. After this many consecutive rounds in the phase, give
# up on this specific threat-response attempt and return to economy.
DEFEND_BUILD_TIMEOUT_ROUNDS = 40
# After a DEFEND_BUILD_TIMEOUT_ROUNDS give-up, a builder is ineligible to
# be pulled back into PHASE_DEFEND_BUILD for this many rounds. Without
# this, an unresolved threat immediately re-triggers should_enter_
# defend_build / should_interrupt_for_core_defense the very next round —
# a real match (2026-08-22, all 5 games went to the 1000-round tiebreak,
# lost 4/5) showed builders cycling defend-timeout-resume-defend so fast
# that a mining task never got far enough to matter ("frozen even though
# economy allows them to start building conveyors"). Long enough to let a
# freshly (re)started harvester+belt chain actually get somewhere.
DEFEND_BUILD_COOLDOWN_ROUNDS = 60

# ---------------------------------------------------------------------------
# Dedicated attacker dispatch (combat.py + builder_role.py)
# ---------------------------------------------------------------------------

# Keep at least this much Ti free (on top of a builder's cost) before
# committing a free builder to the attack.
ATTACK_RESOURCE_BUFFER = 60
# Slot is reclaimable after this many rounds with no heartbeat refresh.
ATTACK_SLOT_STALE_ROUNDS = 15
# Recall the attacker after this many rounds of no progress toward its target.
# Generous: routing around walls on large maps temporarily increases distance,
# and the attacker must not abandon its mission over transient nav stalls.
ATTACK_STALL_ROUNDS = 40
# Recall if HP drops to this fraction of max.
ATTACK_RECALL_HP_FRACTION = 0.35
# Within this distance² of the enemy Core, plant the standing sentinel.
ATTACK_SENTINEL_RANGE_SQ = 40
# Keep planting verified-placement sentinels around the enemy Core (one per
# approach side/corner) up to this many before switching to mop-up — a
# single sentinel only ever covers one facing; four gives sustained
# ammo-fed pressure from every direction instead of leaving the attacker's
# only follow-up as melee.
ATTACK_MAX_SENTINELS = 4
# Post-sentinel mop-up ("cut conveyors leading Ti to the Core") is scoped
# to literally one tile around the Core's own 2x2 footprint, not the wide
# ATTACK_SENTINEL_RANGE_SQ bubble — that used to let the attacker chase a
# distant harvester, get drawn into a prolonged fight with enemy builders
# defending it, and never come back to finish the sentinel ring or press
# the actual Core (real match, 2026-08-23, lost all 5 games by Core
# destroyed: "attacker kept stuck fighting with enemy builders for
# harvester enemy"). User-specified design: "cut all conveyor in
# surroundings the core only one tile around... this is only after
# sentinel built."
ATTACK_CORE_PERIMETER_RANGE_SQ = 2
# Commit to a BFS-computed movement waypoint for this many rounds instead
# of recomputing every round. BFS on partial (fog-limited) knowledge can
# legitimately choose a *different* shortest path as new terrain is
# revealed each round even though the underlying situation barely
# changed — observed directly in a stavkirke debug trace (waypoint
# flip-flopping between neighbouring tiles round after round) and matches
# a real match report (2026-08-22: attacker "keeps spinning in the same 4
# tiles" near a wall instead of committing to a route around it).
ATTACK_BFS_STICKY_ROUNDS = 6
# Build the one launcher only while this close to our own Core.
ATTACK_LAUNCHER_BUILD_RANGE_SQ = 18
# Give up waiting to be thrown after this many rounds of standing still.
ATTACK_LAUNCHER_MAX_WAIT_ROUNDS = 6

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
PHASE_DEFEND_BUILD = "defend_build"
PHASE_ATTACK = "attack"

# Back-compat aliases used by older tests / docs.
PHASE_APPROACH_ORE = PHASE_MOVE_TO_STAND
PHASE_BELT_TO_CORE = PHASE_BUILD_CONVEYOR
