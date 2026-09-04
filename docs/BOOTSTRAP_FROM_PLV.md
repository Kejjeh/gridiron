# Fantasy Football repo bootstrap — what to carry over from plv_clone

Written 2026-09-01 at plv_clone's offseason shutdown. This is the seed doc
for a NET-NEW repo (suggested name: `gridiron` or `xfp-ff`). Copy it into
that repo's `docs/` on day one. It is opinionated in exactly the ways
plv_clone earned its opinions.

## 1. The lessons that transfer (install these on day ONE, they're cheap)

**Repo shape** — copy the pattern, not the code:

```
<repo>/
  src/<pkg>/            # the package = the production boundary
    espn.py             # ONE auth home (or sleeper.py — see §3)
    paths.py            # ONE root/paths module, env-var override for CI
    config.py           # pydantic-settings, one env prefix
    league_config.py    # SEASON_YEAR + league constants; rollover = 1 bump
    scoring.py          # canonical scoring formulas, imported everywhere
    models/             # projection models, one file per model
  scripts/<domain>/     # engines + skill drivers; scripts/<domain>/lib/ = real library
  scripts/ci/           # run_summary.py, smoke.py, golden_run.py — PORT THESE THREE VERBATIM
  tests/                # from day one; contract/schema pins, not just unit tests
  docs/adr/  docs/memory/  docs/DECISIONS.md  docs/ARCHITECTURE.md
  CLAUDE.md  HANDOFF.md  .claudeignore
```

**Process discipline** (each of these paid for itself in plv_clone):

- **CLAUDE.md budget test** from commit #1 (`test_claude_md_budget.py`
  pattern: two-sided line ratchet, numbered rules with full text in
  `docs/memory/`). plv_clone let it drift to 635 lines before installing
  this; don't repeat that.
- **Validated-signals gate before any model feature ships.** The rh3/rp3-v2
  lesson: a stripped-down baseline over-claimed feature lift 4×. Rule:
  every candidate signal beats a baseline containing ALL existing features,
  out-of-sample, before it enters a FEATS list — enforced by an import-time
  assert, not by discipline.
- **golden_run A/B** for behavior-preserving refactors (byte-identical
  outputs, input-hash freezing). **run_summary.py** so test output never
  floods agent context. **smoke.py** glob-discovered fast subset.
- **Decision ledger + counterfactual grading.** Grade the CHOICE, not the
  projection. Log every start/sit/waiver decision with the rejected side;
  settle weekly. (Carry the #54 lesson: define the *ungradeable* terminal
  state on day one — some rejected sides never get actuals.)
- **ID discipline.** Anchor every join on a stable player id (`gsis_id` /
  `player_id` from nflverse; ESPN/Sleeper ids mapped ONCE in a cached
  crosswalk). Never name-match, never `.str.contains`, never trust the
  platform's position tag (the `pitcher_role` lesson: derive role from
  usage, not from the roster tag — a "WR" playing 90% slot-TE routes is
  whatever his routes say he is).
- **Alerts fire on TRANSITIONS only** (the volume-alerts lesson: "X has
  been out since Week 3" must not re-page every week), and freshness is
  cadence-aware, not a flat TTL.
- **Docs**: ADRs for every rejected consolidation; DECISIONS.md so cheap
  models don't re-litigate; closed research families written down WITH the
  negative results.

**Decision-layer framing** — the single biggest transferable idea:
optimize **P(win) / playoff equity, not projected points**. The marginal
value of a weekly win depends on the standings race; the value of VARIANCE
flips sign with seeding safety (chase ceiling as underdog, floor as
favorite). This is *more* true in FF: single-game weeks, higher variance,
shorter season. Port the leverage-engine architecture (one MC engine,
everything ΔP(win)-denominated), not its baseball internals.

## 2. Where FF is a different beast (don't port assumptions)

- **n is tiny.** 17 games vs 162. Weekly cadence, not daily. Shrinkage and
  priors matter MORE; week-level results are mostly noise; season-long
  "breakout confirmed" claims need the plv_clone skepticism dialed UP.
  Expect most in-season signals to be as fake as the "different player
  now" family was in baseball — but role changes are the exception (see
  next point).
- **Opportunity IS the projection.** In baseball, rate × volume with volume
  fairly stable. In FF, volume (snap %, route %, target share, carries,
  red-zone/goal-line share) is ~70% of the projection and it MOVES —
  injuries, depth-chart changes, game script. The baseball lesson inverts:
  in-season USAGE deltas are real and fast-stabilizing even though
  in-season EFFICIENCY deltas are mostly noise. Model opportunity
  explicitly; treat efficiency (yards/route, YAC over expected) as the
  slow-moving skill layer.
- **The week has a shape.** Waivers Tue night/Wed (FAAB). Injury reports
  Wed–Fri (practice participation), designations Fri, inactives ~90 min
  before kickoff. Thu/Sun/Mon games lock rosters piecemeal. Your automation
  cadence is weekly-with-Sunday-morning-spike, not nightly: a Wed waiver
  pipeline, a Fri designation pipeline, a Sunday-morning inactives check.
- **Replacement level is positional.** VOR/VORP against the last startable
  player at each position drives everything (draft AND waivers); flex
  eligibility complicates it. plv_clone's fixed replacement-rank bug
  (issue #9, rprs2) is the exact bug class to avoid: replacement level must
  be forward-looking and league-shaped, not a season-total rank.
- **Vegas is the best public signal.** Implied team totals + spread are the
  strongest single input to weekly projections; there is no baseball
  equivalent this clean. Ingest lines early.
- **Draft prep is its own workstream** with no plv_clone analog: ADP value,
  tiers, roster construction, keeper math. Build it as a separate module
  with its own deadline (draft day), don't bolt it onto weekly tooling.

## 3. Data sources (2026 state; verify before building)

| Source | What | Access |
|---|---|---|
| `nfl_data_py` / nflverse | play-by-play, weekly stats, snap counts, depth charts, injuries, id crosswalk | free, pip install |
| ESPN fantasy API (`espn-api` pkg) | league rosters/FA/scoring — same package family as plv_clone; football module | cookies (SWID/espn_s2), same auth pattern as plv_clone `espn.py` |
| Sleeper API | if the league is on Sleeper: rosters, players, trending adds | free, NO auth — much nicer than ESPN |
| FantasyPros | ECR consensus, ADP | scrape/CSV export — the "PL cross-reference" analog |
| Odds API / scraped lines | spreads, totals, implied team totals | pick one early |
| Weather | outdoor games, wind for K/passing | nice-to-have |

Env vars only, `.env` gitignored, `.env.example` committed — identical to
plv_clone. Never write credentials into a file.

## 4. Build order (first ~6 sessions, each one small)

1. **Skeleton + guardrails**: repo tree above; port `run_summary.py`,
   `smoke.py`, `golden_run.py`, the CLAUDE.md budget test; write
   `league_config.py` + `scoring.py` from the actual league settings;
   CLAUDE.md ≤150 lines from day one.
2. **Ingest**: nfl_data_py weekly + snaps + the id crosswalk into
   `data/research/cache/`; league connector (rosters, FA pool) behind ONE
   auth module. Contract tests on every frame you'll join later.
3. **Naive baseline projection**: opportunity-share × team implied total ×
   positional efficiency prior. Ship it, then measure everything else
   against it (the Rule-9 spirit: no model ships unless it beats this).
4. **VOR layer + start/sit table**: replacement-aware weekly ranks, flex
   logic, lineup optimizer. This is the `/roster-audit` analog.
5. **Waiver board**: FA pool × ROS VOR delta × FAAB pricing. Transition
   alerts on role changes (snap/target-share jumps) — the ROLE_GAIN
   detector, which in FF is the highest-value alert in the sport.
6. **Decision ledger + P(win) engine**: log every move with rejected side;
   weekly MC of matchup outcome; only then start denominating moves in
   ΔP(win). Don't build the title-equity curve until there's a
   half-season of ledger data.

## 5. Anti-goals (scars, not opinions)

- Don't build 90 skills up front. plv_clone grew 94 organically WITH a
  registry test; start with 5 (roster-audit, waiver-board, start-sit,
  matchup, decision-log) and the registry test.
- Don't let any script hardcode the repo root (91 files needed migration
  in plv_clone). `paths.py` from commit #1.
- Don't lock the dependency file to a machine-wide `pip freeze`
  (plv_clone's `requirements.lock` shipped a trading stack). Freeze inside
  the project venv only.
- Don't commit bulk data; DO commit small daily projection CSVs — the
  git-history-as-point-in-time-archive trick
  (`recover_rp3_git_snapshots.py`) was a quiet win, and in FF a weekly
  projection archive is tiny.
- Don't add a convenience accessor that makes the wrong call easy (the
  `injured_players()` ADR-0004 lesson) — in FF the equivalent trap is
  "questionable == out" and "on roster == startable".
- Decide `.claudeignore` + context-hygiene rules before the first big
  data pull, not after the first blown context window.
