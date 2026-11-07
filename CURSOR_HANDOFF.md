# Handoff — Architecture Refactor for Continued Development

Paste this whole file into Cursor as the opening prompt for the next
session. It's written to be self-contained: what this project is, where
things stand, what to build next, and the mistakes already made once that
don't need repeating.

---

## What this is

A bot for the Florent Code League — a real-time-ish strategy game played
on a 16-slot comm-store engine (`fcode`), one Python `Player` instance per
unit, ~10ms/round/unit budget under real TLE enforcement. Repo root is
`fcode_bot/`. Read `PROGRESS_TRACKER.md` first (the running development
log — "Known issues found and fixed this session" is the authoritative
history of every bug found and why each fix looks the way it does), then
`docs/OVERVIEW.md` (file-by-file map), then `docs/ARCHITECTURE.md` and
`docs/phases/phase4_defense.md` / `phase5_offense.md`.

**How to validate any change** (do this before considering anything done —
skipping it is how the worst bug this project shipped happened):
```bash
# unit tests
python -m pytest tests/ -q

# vs-idle economy benchmark, all 46 local maps, must stay 0 losses / 0
# zero-mining (a helper script for this may or may not exist in your
# checkout — if not, loop scripts/run_vs_starter.sh-style calls over
# maps/*.map26 against bots/idle and check win/mined in the output)
fcode run . bots/idle maps/<map>.map26 --seed 1 --tle 0

# real TLE stress test — several real bugs this session were INVISIBLE
# under --tle 0 and only showed up here
fcode run . bots/starter maps/showdown.map26 --seed 1023944511 --tle 10

# package + submit only after both benchmarks are clean
./scripts/package_submission.sh --submit
```

## The one lesson worth repeating before you touch anything

Every fix in `PROGRESS_TRACKER.md`'s "Known issues" log was found by
**running a real benchmark or reading a real ladder replay**, not by
reasoning about the code. The single worst incident this session (attack
slot ping-ponging between two builders, draining the whole team's economy
to zero) was caused by a plausible-sounding vision-based liveness check
that nobody actually ran under load before shipping. Whatever you build
from this brief: unit-test it, then run the vs-idle benchmark, then run a
`--tle 10` stress pass, in that order, every time — the standing rule in
`PROGRESS_TRACKER.md` is not decorative.

---

## The refactor goal

> Everything should stay modular and easy to modify and improve.

Current code already has a rough version of what's being asked for
(`condition_state.py` senses, `defense.py`/`combat.py` respond), but it
grew incrementally under real match-report pressure and is due for a
proper pass: explicit state objects, a real type+severity model for
threats instead of implicit code ordering, and a formalized builder-role
split instead of ad-hoc dispatch checks sprinkled through
`roles/builder_role.py`.

### 1. A world-state object, read by Planner and Navigator alike

Right now every module (`condition_state.py`, `defense.py`, `combat.py`,
`roles/builder_role.py`) independently calls `ct.get_nearby_*()` (cached
per-round via `map_knowledge.get_cached_buildings`/`get_cached_units` —
keep that cache, it's load-bearing: an earlier version without it caused
real TLE timeouts) and re-derives what it needs. Build a single queryable
snapshot instead, refreshed once per unit per round, that carries:

- **Economy state**: our harvesters (position, connected: bool, HP),
  our conveyor/splitter tiles, current titanium/ammo, income trend if you
  add one (see the "known remaining gap" note below — nothing currently
  tracks whether income has stalled).
- **Map intelligence**: our Core position, enemy Core position (known or
  symmetry-guessed — `combat.enemy_core_target` already does this, reuse
  it), explored/frontier state.
- **Threats**: a list of typed, located, severity-scored threat records —
  see the Defense section below for the exact shape.

Whatever you build, `defense.py`, `combat.py`, and `roles/builder_role.py`
should all read *from* it rather than each recomputing their own view.
This is the single highest-leverage change for "modular and easy to
improve" — right now, changing what counts as a threat means editing
`condition_state.py`, `defense.py`, and sometimes `roles/builder_role.py`
all in different ways.

### 2. Builder role split: 5 base, scaling up

> Start would be with 5 with incrementing if needed, one for defend one
> for attack and 3 for economy.

`INITIAL_BUILDER_COUNT = 5` already exists (`constants.py`) as an
unconditional spawn floor, with further spawns gated on the economy not
being in `STARVATION` (`roles/core_role.py`). What doesn't exist yet is a
fixed initial *role* split within that floor.

**Important context — this was tried once already and reverted.** A
persistent, always-on "dedicated base defender" (`PHASE_DEFEND_BASE`,
mirroring the existing dedicated attacker) was built, unit-tested,
benchmarked clean on all 46 maps, shipped, and then explicitly reverted
the same day after watching it play, because defense should be reactive
("sense threat, build counter near it") rather than a standing perimeter
built regardless of whether anything is actually threatening. The revert
is documented in `docs/phases/phase4_defense.md`'s "Removed" section, and
the removed code is recoverable from git history.

**This doesn't mean don't build it — it means build it properly this
time**, with the state-driven design this whole brief describes, so the
1-defend/1-attack/3-economy split is genuinely reactive (the "defend"
slot's builder does normal economy work, or nothing wasteful, until the
Threat-Sense module actually reports something — it should not build a
static perimeter just because it exists) and doesn't regress the exact
things that made the last attempt get pulled: it must not fight the
existing reactive `DEFEND_BUILD` dispatch for the same threats, and it
must not spend titanium the economy can't afford (`STARVATION` must still
gate it, same as everywhere else).

### 3. Defense as two explicit, swappable modules

> 1 module: Sense threat, output type and severity... 2 module: Counter
> attack threat, based on severity and type.

This is a clean split of what `condition_state.py` (sensing) and
`defense.py` (response) already do — formalize it properly:

**Module 1 — Threat Sensing** (`condition_state.py` is the closest
existing thing; consider whether to extend it in place or split further)

Output a list of typed, severity-scored, located threat records, not just
a bucket name. Two threat categories exist today and should stay the
top-level split per the brief:

- **Economic threats** — blocking economy: an enemy barrier boxing in a
  harvester or cutting a belt route (`condition_state.get_threatened_
  structures` already includes barriers as of this session), or an enemy
  unit/turret near a friendly harvester/conveyor.
- **Core threats** — anything endangering the Core directly: enemy
  gunner/sentinel in range (`condition_state.get_core_threats`, with the
  authoritative `can_fire_from`-based fallback beyond the guessed radius —
  keep that, it's real, the engine publishes no actual attack-range
  constant separate from vision), an enemy barrier boxing in the Core
  itself, or an enemy builder rush (`CORE_THREAT_RANGE_SQ_BUILDER`,
  currently 20 — was 2 for most of this session, that undersized value is
  exactly what caused a Core to be destroyed in 91 rounds with zero
  defense ever triggered; don't shrink it back down without a very good
  reason and a benchmark to back it up).

Severity, per the brief: **Core destruction risk is highest priority.**
Any threat that could destroy the Core (an armed unit in range, a barrier
boxing in the Core itself) outranks a harvester/economy threat. This
already exists as an implicit ordering (`condition_state.get_condition_
state`'s `STARVATION > CORE_THREAT > ECONOMY_THREAT > SAFE` priority, and
`defense.THREAT_PRIORITY`'s armed-vs-barrier ranking within `CORE_THREAT`)
— making it an explicit, inspectable severity score on each threat record
instead of code-order priority is the actual ask here, and it's what
makes this "easy to improve" (adding a new threat type later just means
giving it a severity number, not re-threading a priority chain through
three files).

Location and communication matter — per the brief, threats need their
position published clearly so Counter-Attack can dispatch precisely. The
existing comm-store sighting slots (1–2, packed pos+type+last-seen, see
`docs/02_MAP.md`) are the team-shared channel; a locally-visible threat
(this round, this unit's own vision, via `map_knowledge.get_cached_
buildings`/`get_cached_units`) doesn't need to round-trip through the comm
store to be actionable *this round* by *this unit*, but does need to if
another unit should react to it next round.

**Module 2 — Counter-Attack / Threat Response** (`defense.py` is the
closest existing thing)

Reads the Threat Sensing module's output, picks the highest-severity
threat, and responds. What already exists and should carry forward:

- Verified placement (`best_verified_placement`, using the engine's own
  `can_fire_from` before committing to a tile+facing — never trust
  `facing_toward()`'s geometry alone, that caused a long string of real
  "wrong direction" reports before this fix landed).
- A barrier-target fallback (`_blocking_barrier`) when the direct shot on
  the real threat can't be verified — aim at what's blocking instead of
  falling back to an unverified guess.
- **Melee priority depends on threat kind, not a single fixed order**:
  weapon-first for an armed gunner/sentinel (melee is far too slow/costly
  against 30–40 HP), but melee-first for an enemy *builder* threat
  specifically (builders are weak, and melee is adjacency-only — immune to
  the friendly-conveyor-blocks-the-line problem below — building a whole
  new turret while a builder rush keeps hitting the Core is strictly
  worse). Keep this distinction; it's the fix for a real "Core destroyed
  purely by an enemy builder rush" incident.
- Redispatch when existing coverage goes blind (`defense._any_turret_can_
  hit`): a built turret doesn't mean the threat is actually covered — a
  friendly unit *or a friendly conveyor* can wander into an already-built
  sentinel's fixed line after construction and silently disarm it, since
  sentinels can't rotate at all (see the hard engine constraint below).
  `builders_needed_for_defense` already escalates a fresh dispatch when
  this happens; extend/preserve this rather than re-deriving it.
- Barrier response for economy threats too, not just Core-adjacent ones
  (`defense.try_destroy_adjacent_blocking_barrier`) — a real match showed
  the enemy cutting an already-completed belt route with barriers built
  well away from the Core, which nothing detected until this session.

**A hard engine constraint you cannot design around**: per the brief,
"make sure their firing direction line is free of team unit, otherwise
rotate the sentinel." **Sentinels cannot rotate — there is no `rotate()`
equivalent for `EntityType.SENTINEL` in this engine at all** (confirmed
against the engine's own `_types.py` stub this session — only `EntityType.
GUNNER` has `can_rotate`/`rotate`, costing 10 Ti + 1 round of action
cooldown). A sentinel's facing is fixed forever at build time. Do not
build a "rotate the sentinel out of a blocked line" feature — it will not
compile against a real engine call, and if you stub around it you'll ship
something that silently no-ops. The two real options, both already
partially built:
1. **Prevent the block in the first place** isn't fully solvable — belt
   planning (`economy.py`) has no concept of "don't route through an
   existing turret's firing line," and giving it one is a real, valuable,
   *not yet built* piece of this refactor if you want to reduce how often
   this happens. It would need each new conveyor-tile placement to check
   against every nearby friendly turret's stored facing + `can_fire_from`
   before committing to that tile.
2. **Detect and route around it after the fact** (what exists today):
   `_any_turret_can_hit` notices the block, `builders_needed_for_defense`
   escalates, a fresh dispatch places a *new* turret from a different
   angle, and (for builder-kind threats specifically) melee sidesteps the
   problem entirely since it's adjacency-only. If you want gunners
   specifically to help here, note they *can* rotate — a design that
   deliberately places a gunner instead of a sentinel in a spot likely to
   get blocked later (e.g., near where economy is still expanding) is a
   legitimate way to use the rotation capability that does exist.

### 4. Economy bots need harvester connectivity state

> 3 bots of economy should have a state about harvesters and their
> connectivity.

Partially exists: `assignment.Assignment.connected` is set once a
builder's own belt reaches Core, but it's per-builder local state that's
discarded once the task completes and the builder moves on to explore —
nothing re-verifies connectivity afterward. This session added
opportunistic *repair* (a free builder heals an adjacent damaged friendly
structure while exploring — `defense.try_repair_adjacent`, wired into
`roles/builder_role.py`'s `PHASE_EXPLORE` fallback) and barrier-clearing,
but **not** rebuilding a fully-destroyed tile (gone entirely, not just
damaged) — that needs the original route plan, which is discarded on task
completion, and nothing currently notices "there's a gap in what used to
be a complete belt" and replans around it. This is a real, scoped,
not-yet-built piece: a persistent, team-visible harvester registry (built,
connected: bool/last-verified-round, route tiles) that any free builder
can consult and act on, instead of connectivity being a private memory
that evaporates once its owner moves on.

### 5. Non-negotiable behavior checklist (mostly already true — verify, don't regress)

> Never freeze builders / Detect immediate Core threats / Place 1–2
> gunners immediately when threatened / Maintain a minimal titanium income
> / Explore/expand when safe / Attack from first round with a dedicated
> attacker that builds a sentinel and cuts ore on enemy core / Finish the
> enemy Core instead of sitting in a permanent EXPAND/ATTACK loop.

Status of each against the current code, so you know what's real vs. what
still needs work:

- **Never freeze** — `STUCK_LIMIT`-based escapes exist for
  `PHASE_DEFEND_BUILD` and the old static `PHASE_DEFEND`; verify any new
  phase you add gets the same treatment. A phase with no stuck-escape was
  a real recurring bug class this session.
- **Detect immediate Core threats, place 1–2 gunners immediately** — real,
  via `condition_state.get_core_threats` → `defense.should_enter_defend_
  build` → `defend_build_step`. Gated on proximity to the *threat*
  (`DEFEND_THREAT_APPROACH_RANGE_SQ`), not just Core's ring.
- **Maintain minimal titanium income** — `STARVATION` in `condition_
  state.py` is the only real resource-arbitration signal that exists;
  there's no positive "minimum income" target being maintained, only a
  floor that suppresses *spending* once crossed. If "maintain a minimal
  income" means something more active (e.g., guarantee at least N
  harvesters stay connected), that's new scope, not an existing behavior.
- **Explore/expand when safe** — real, `explore.py` + `step_explore`.
- **Attack from round 1, dedicated attacker, sentinel + cut ore supply** —
  real: `combat.should_dispatch_attacker` runs independently of our own
  `CORE_THREAT` state (an earlier version gated it behind that and the
  attacker essentially never dispatched at all while economically ahead —
  don't reintroduce that gate), launcher-assisted travel when available,
  strict sequencing (sentinel first, always targeting the known/guessed
  enemy Core directly with no mid-travel diversion, *then* — and only
  then — systematically working the conveyor perimeter via `nearest_
  enemy_logistics_near`). Melees an adjacent economy building (harvester/
  conveyor/splitter) as an obstacle-clearing measure regardless of
  mission phase — removing that caused a real "attacker abandons the
  mission" bug when a harvester blocked the direct path.
- **Finish the enemy Core instead of looping** — partially real:
  `should_recall_attacker` retreats on low HP or stalled progress, and
  there's no explicit "give up entirely, permanent EXPAND" loop coded in
  — but there's also no active "press the advantage once the enemy is
  clearly losing" logic. Confirmed via this session's benchmarking that
  the attacker *does* reliably finish undefended Cores (39/46 maps vs
  idle end via Core-destroyed, not tiebreak) — the ~7 that don't are
  large/complex maps where the attacker is still alive and still making
  progress but the Bug2 navigator hasn't gotten it there within the
  1000-round limit, not a logic loop. If "permanent EXPAND/ATTACK loop"
  describes something else you've observed in a real match, get the exact
  replay/match id before redesigning around it — this session's single
  biggest time-waster was chasing a symptom from a stale pre-fix replay.

---

## Suggested module layout (a starting point, not a mandate)

```
world_state.py       # NEW — the per-round snapshot described in §1
threat_sensor.py      # NEW, or condition_state.py extended in place — §3 module 1
threat_response.py    # NEW, or defense.py's dispatch logic extracted — §3 module 2
defense.py            # placement/verification primitives (best_verified_placement,
                       # try_build_sentinel/gunner) stay here regardless — they're
                       # reusable by both threat_response.py and combat.py
combat.py             # offense — mostly stays as-is per §5's "already real" list
roles/builder_role.py # phase dispatch — should shrink as world_state.py /
                       # threat_sensor.py / threat_response.py absorb logic
                       # currently living in the reactive-dispatch block here
```

Whatever you actually name things, keep the placement-verification
primitives (`best_verified_placement`, `can_fire_from`-based checks)
separate from the dispatch/priority logic that decides *when* to call
them — that split is what made the barrier-fallback and melee-priority
fixes this session each a small, isolated, well-tested change instead of
a rewrite.
