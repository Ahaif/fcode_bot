# Progress Tracker — RTS Bot Development

> Rule:
> A phase is NOT complete because the code compiles.
> A phase is complete only after the benchmark improves or stays equal.
> Every behavioral change must be benchmarked before promotion.

---

# Current Status

Current version:
v0.2.9 (persistent conveyor plan FSM)

Current focus:
☑ Phase 1 Economy (harvester-first + persistent build_plan + sector explore)
☑ Permanent per-builder sector rays
☑ Phase 2.5 Navigator (`navigator.step_to` owns all movement)

Current benchmark (2026-08-05, bot vs idle, seed 1, full map pool):

Win rate vs idle:
21/21

Mining:
Avg **8547** collected (range 2460–16670)

Zero-mining maps:
**0 / 21**

Average titanium (global pool end):
n/a (use collected column)

vs starter (duel seed 1): economy alive, combat still weak.

---

# Phase 1 — Stable Economy (Highest Priority)

Goal:
Build a reliable economy on every map.

Success Criteria:
- No maps finish with 0 titanium mined.
- Every harvester reaches the Core.
- Builders never waste titanium.

Checklist

## Exploration

- [x] Builders spread naturally (sector rays from Core by unit id)
- [x] Builders don't follow each other (sector ownership + ore claim by nearer id)
- [x] No exploration loops (permanent sector waypoint; stuck→next ray waypoint)
- [x] Unknown map explored systematically (outward radii 6/12/18… along sector)
- [x] Each builder owns one sector (North-first, then clockwise)
- [x] Builder remembers explored regions (`visited` never cleared)
- [x] Explore target does not re-roll every round (waypoint until reach/stuck)
- [x] Builder reaches distant ore (sector waypoints to map scale)

## Mining

- [x] One harvester per ore (`ore_claimed_by_other` + skip occupied ore)
- [x] No duplicated harvesters (same)
- [x] Prioritize nearest free ore
- [x] Ignore blocked ore (occupied / contested / stuck abandon)

## Logistics

- [x] NEVER build conveyors before a harvester exists (`harvester_pos` only after successful build)
- [x] Only the owning builder may connect that harvester (assignment ownership)
- [x] Conveyor starts at harvester (pave stand immediately after step-off)
- [x] Conveyor reaches Core (persistent `build_plan` stand→Core; move only onto conveyors / Core)
- [x] Builder returns directly to Core (`RETURN_HOME` on plan conveyors)
- [x] No broken conveyor chains (no stepping onto empty tiles)
- [x] No useless conveyors (plan tiles only; facing along plan)

## Builder Management

- [x] Builders never idle (explore / economy / defend / attack)
- [x] Builders recover when stuck
- [ ] Stop spawning when economy saturated
- [x] Builder always has a next target

Verification

- [x] Benchmark (0/21 zero-mining vs idle)
- [ ] Replay review (confirm belts are home-path only in viewer)
- [x] No zero-mining maps

---

# Phase 2 — Economy Optimization

Goal:
Increase titanium income.

Checklist

- [ ] Choose best ore instead of nearest
- [ ] Ore cluster detection
- [ ] Shared logistics
- [ ] Smarter return paths
- [ ] Avoid long-distance mining
- [ ] Measure builder idle %

Verification

- [ ] Mining increases
- [ ] First titanium earlier
- [ ] Income per round higher

---

# Phase 2.5 — Navigator

Goal:
Shared movement layer. Roles choose WHERE; Navigator chooses HOW.

Checklist

- [x] `navigator.py` created (`step_to` + Bug2 wall-follow)
- [x] All movement routed through Navigator (explore / approach / belt / defend / attack)
- [x] Adjacent targets are strict (belt-safe — no side-step off conveyors)
- [x] No A↔B oscillation (recent-position memory + avoid)
- [x] Wall-follow only on terrain blocks (not unit traffic); aborts if no progress
- [x] Explorer still reaches unexplored areas (sector waypoints unchanged)

Verification

- [ ] Replay maps containing walls — harvester owner finds a route home if one exists
- [x] Benchmark equal or better vs prior version (8263 ≥ 8221, 0/21 zero-mining)

---

# Phase 3 — Defense

Goal:
Protect the economy.

Checklist

- [ ] Detect enemy pressure
- [ ] Turrets face enemy approach
- [ ] Never build turrets toward map edge
- [ ] Protect harvesters
- [ ] Protect conveyors
- [ ] Repair damaged structures

Verification

- [ ] Economy survives attacks
- [ ] Win rate increases

---

# Phase 4 — Offense

Goal:
Damage enemy economy.

Checklist

- [ ] Find enemy harvesters
- [ ] Destroy harvesters
- [ ] Destroy conveyors
- [ ] Attack Core when economy is secured
- [ ] Retreat when losing

Verification

- [ ] Enemy mining decreases
- [ ] Win rate increases

---

# Phase 5 — Map Intelligence

Goal:
Use map knowledge for better decisions.

Checklist

- [ ] Remember ore
- [ ] Remember walls
- [ ] Remember enemy buildings
- [ ] Estimate enemy Core
- [ ] Choke points
- [ ] Expansion scoring

Verification

- [ ] Better mining on difficult maps

---

# Phase 6 — Strategic Decisions

Goal:
Make better long-term decisions.

Checklist

- [ ] Expand vs Defend
- [ ] Attack timing
- [ ] Builder role balancing
- [ ] Resource reservation
- [ ] Combat production timing

Verification

- [ ] Win rate > previous version

---

# Phase 7 — Optimization

Goal:
Remove waste.

Checklist

- [ ] Builder idle %
- [ ] Walking distance
- [ ] Duplicate work
- [ ] Store usage
- [ ] CPU usage

Verification

- [ ] Faster economy
- [ ] More actions

---

# Phase 8 — AI Parameter Tuning

Goal:
Optimize parameters automatically.

Rules

- AI edits parameters only.
- AI never edits runtime code.
- Every generation must beat baseline.

Verification

- [ ] Benchmark passed
- [ ] Promote generation

---

# Replay Review Checklist

For every replay ask:

## Economy

- [ ] Did every builder find ore?
- [ ] Did anyone idle?
- [ ] Did someone build useless conveyors?
- [ ] Did conveyors reach Core?
- [ ] Were harvesters duplicated?

## Exploration

- [ ] Did builders spread?
- [ ] Did they revisit explored areas?
- [ ] Did they miss nearby ore?

## Defense

- [ ] Were turrets useful?
- [ ] Did they face enemies?
- [ ] Was titanium wasted?

## Combat

- [ ] Did we attack useful targets?
- [ ] Did we chase builders?
- [ ] Did we pressure enemy economy?

---

# Benchmark History

| Version | Phase | Win % | Avg Mining | Zero Mining Maps | Notes |
|----------|------|-------|------------|------------------|-------|
| v0.1 | Step1 flat | — | ~2480 (1 patch) | many | single builder prototype |
| v0.2 | Phase1 economy | idle 21/21 | ~7800 vs idle | **0/21** | harvester-first; shortest-path belts; sector explore |
| v0.2.1 | ownership refactor | idle 21/21 | 8058 vs idle (same per-map as v0.2) | **0/21** | `Assignment` replaces ore_pos/stand_pos; 12 unit tests; no behaviour change |
| v0.2.2 | harvester-before-belts | idle 21/21 | **8066** vs idle | **0/21** | claim ore → build harvester → record `harvester_pos` → belt home; owner-only connect; 17 unit tests |
| v0.2.3 | sticky exploration | idle 21/21 | **8066** vs idle (equal) | **0/21** | sticky targets; visited kept; stuck→sector waypoint; anti walk-back; 25 unit tests |
| v0.2.4 | sector-ray explore | idle 21/21 | **8221** vs idle | **0/21** | permanent sector waypoints (not frontier); resume after economy; intra-sector sweeps then rotate |
| v0.2.5 | Navigator layer | idle 21/21 | **8263** vs idle | **0/21** | `navigator.step_to`; roles choose targets; Bug2 wall-follow; belt-safe adjacent steps |
| v0.2.6 | Nav state machine | idle 21/21 | **8489** vs idle | **0/21** | Bug2 hit_distance leave; terrain vs traffic; fog-safe shift; Core local waypoint; side reverse before abort |
| v0.2.7 | Fault-tolerant nav | idle 21/21 | **8869** vs idle | **0/21** | Persistent traffic→Bug2; hit_pos/follow_steps; recent×10; failure escape; primary-wall Bug2 |
| v0.2.8 | Belt corridor alternate | idle 21/21 | **8844** vs idle | **0/21** | homeward candidates skip WALL primary; vision-safe belt_complete; belt stuck abandon |
| v0.2.9 | Persistent plan FSM | idle 21/21 | **8547** vs idle | **0/21** | one `build_plan` + index; replan suffix only; explicit states; greedy belt neighbours removed |
