# Model roadmap: 78 ideas, free sources only (plan, 2026-11-02)

**Status: EXECUTED through phase 2 on 2026-11-02; the rest is time-gated.**
The owner said "execute all phases now", read as approval of the four
decisions below as written. Every idea with free data was pre-registered
and tested; where each stands is in `docs/research/blocks/RESULTS.md`
(generated, one row per idea and per result). In short:

- **Feature blocks** (the block runner, `scripts/research/block_runner.py`,
  which reproduces the shipped seven-fold numbers): 58 scored, none beats
  the shipped set in every fold. A bug hunt over the near misses fixed
  three shared helpers; the 14 blocks that read them were re-run once and
  still fail. Round 2 (`blocks/ROUND2.md`, five declared post-hoc
  combinations): RB-R2 cleared all seven folds, then was worse on both
  metrics on 2026 weeks 4-7. Nothing serves.
- **Distributions** (workstream D, `distribution_backtest.py`): RB-13,
  TE-10 and K-03 pass their historical bars, DEF-03 fails. Their 2026
  holdouts: `blocks/<ID>-2026.md`.
- **Week 1-3 priors** (`early_season_backtest.py`): WR-10 passes history but
  fails its frozen 2026 test; QB-12 and TE-13 fail. 2027 work.
- **Built:** the play-caller table (workstream B, `PLAY_CALLERS.md`), the
  prop collector, record-only (workstream A, `PROPS_COLLECTOR.md`), the
  TE/K/DEF streaming lists (`gridiron.streaming`), the data inventory.
- **Not testable or deferred:** TE-02 (no per-player route data); RB-12,
  WR-06, WR-12 (no free data). The six market ideas (QB-01 ... DEF-01) wait
  on collected props.

Time-gated, in order: the collector runs only once it is on `main` with the
owner's free Odds API key (secret and `.env`) and `GRIDIRON_PROPS_ENABLED`;
the book ranking needs 4+ collected weeks; any contender needs two live
weeks record-only before a serving decision; nothing changes after the
week-14 board; the offseason re-tests everything on the full 2026 season.

The plan as written on 2026-11-02 follows, unchanged. It schedules the 13
ideas per position brainstormed on 2026-11-01, plus the four cross-cutting
workstreams they lean on, using **free sources only**. Paid sources (FTN
Data API, PFF, SIS, paid odds plans) are deferred. Each deferred idea says
what would unlock it.

Every idea is a hypothesis. Nothing reaches the page until it clears the
repo's bar (rule #5): it must beat a baseline that holds **all** existing
features, out of sample, on both start/sit ordering and point error. Most
will fail. `docs/research/FEATURE_EXPANSION.md` already rejected seven
metric blocks, and the plan budgets for that.

## Ground rules

1. **Pre-register every test.** Before any result is seen, the block's
   features, positions, seasons and bar are written in its research doc
   (as `RETURNER_BACKTEST.md` did). A redesign after seeing results is a
   new, declared test, never a quiet retry.
2. **The bar.** A season-fold cross-validation over 2019–2025 (seven
   folds, earlier weeks only, `features_as_of`). A position block must beat
   the shipped set on that position's rows on start/sit pairwise **and**
   MAE in every fold, without worsening the pooled numbers. Kicker and
   defense keep their own harness (`k_def_backtest.py`).
3. **Forward check before serving.** With 78 hypotheses, some will pass the
   historical bar by luck. A block that passes runs **record-only** in the
   weekly comparison (`grade_week.py` contenders) for at least two live
   weeks before it can serve. The 2026 season is the untouched holdout.
4. **Coverage.** A feature missing for some rows carries an explicit
   "unknown" indicator. It never silently takes the training mean, which
   is where `FEATURE_EXPANSION.md` saw features die.
5. **Rule #6.** Volume (snaps, routes, carries, targets) updates fast.
   Efficiency (yards per target, coverage splits) enters only as a
   slow-moving prior.
6. **Rule #3.** Joins use ids only (see Decisions, item 1, for odds data
   that arrives with names).
7. **Rule #10.** Bulk data stays in the gitignored cache. Small reference
   tables (the play-caller table, the odds name crosswalk) are committed.
8. **Serving freeze for the playoffs.** No change to the page's number
   after the week-14 board, the last regular-season week. Record-only
   contenders keep running through weeks 15–17. (Decision 3.)

## Free data, as checked on 2026-11-02

| Source | What it gives | Seasons | Live in 2026? |
|---|---|---|---|
| nflverse play-by-play | Every play: down, distance, field position, `xpass` and `pass_oe` (pass rate over expected), scrambles, red zone, 4th downs, returns | 1999– | yes |
| nflverse FTN charting (free subset) | Play action, screens, RPO, motion, no-huddle, QB sneaks, box count, blitzers and rushers, interception-worthy throws, drops, catchable balls, QB out of pocket | 2022– | **yes** (18,374 plays so far) |
| nflverse participation | Personnel, formation, **route run**, **man/zone and coverage shell**, pressure, time to throw, players on the field | 2016–2025 | **no**, so usable as prior-season tendencies only |
| Next Gen Stats, PFR advanced stats | Separation, cushion, time to throw, yards before and after contact, pressure | 2016– and 2018– | yes (already loaded) |
| Injuries, depth charts, weekly rosters (reserve lists), snap counts, schedules and lines, trades, officials, contracts, draft picks, combine | Everything in the name | varies | yes |
| Sleeper | Projections for every position, league transactions, the player map (once a day) | 2026 | yes (already used) |
| Open-Meteo | Weather forecast and historical archive | — | confirm terms in phase 0 |
| Odds, free tiers only | The Odds API free tier: 500 credits/month, as recorded in DECISIONS 2026-09-04. ParlayAPI free tier: 1,000 credits/month with a 48-hour lookback, per a review page. | forward only | confirm in phase 0 that player props are on the free tiers, and what they cost per game |

The free tiers allow **no historical backfill**. The market workstream can
only learn from weeks collected after it starts, so starting it is the most
time-critical item in this plan.

## Calendar

| Phase | Dates (NFL week) | Delivers | Exit criteria |
|---|---|---|---|
| 0. Foundations | Nov 2–8 (week 9) | Data inventory doc; block runner; odds free-tier trial; prop collector live before week 9's Sunday games; play-caller table started | Collector has stored one full Sunday; the block runner reproduces the shipped numbers exactly |
| 1. Events, distributions, coaching | Nov 9–22 (weeks 10–11) | Workstreams B, C and D built; first position blocks run on 2019–2025 | Each block has a pre-registered doc and a result |
| 2. Remaining blocks, market | Nov 23–Dec 6 (weeks 12–13) | All feasible position blocks run; book ranking after 4+ collected weeks; market projection running record-only | Results table complete; market contender graded weekly |
| 3. Ship and freeze | Dec 7–13 (week 14) | Only blocks that passed the historical bar and 2+ live weeks start serving; DECISIONS rows | Freeze from the week-14 board |
| 4. Offseason | Jan–Aug 2027 | Re-test everything on the full 2026 season (a whole season of props); `returner_v2`; 2026 participation once published; week 1–3 priors for 2027; revisit paid sources only where a free version showed signal | A 2027 serving set decided before the draft |

## Workstreams

### A. Prop-market collector, book ranking, market projection

- **Collector:** `scripts/ingest/pull_props.py` and `gridiron.props`, in the
  cloud workflow.
  - **Snapshots:** one per game window, about 90 minutes before kickoff
    (Thursday, Sunday early, Sunday late, Sunday night, Monday). A second
    Saturday snapshot only if credits allow.
  - **Markets:** pass attempts, completions, yards, TDs and interceptions;
    rush attempts and yards; receptions and receiving yards; anytime TD;
    kicker points and FGs made; defense, where offered.
  - **Credits:** sized in phase 0 from the provider's real per-call cost.
    If the free credits can't cover every book and market, the order is:
    core markets on every book first, then secondary markets.
- **Player identity:** odds providers send names, not ids. They resolve
  through a committed, human-reviewed table (decision 1). An unmatched name
  is listed and abstained on, never guessed.
- **Book ranking**, per market and per book (plus a consensus of all
  books):
  - the line's error against the actual result;
  - whether its over/under odds, margin removed, are right as often as
    they claim (Brier score and log loss);
  - how far each book's line sits from the consensus closing line.

  The first report comes after 4 collected weeks. Firm conclusions wait for
  the offseason, with a full season in hand.
- **Market projection:**
  - convert each player's props into an expected stat line (yardage lines
    as medians, adjusted to means with per-stat spreads fitted from
    nflverse history; anytime-TD odds give expected TDs via λ = −ln(1 − p));
  - score it with `gridiron.scoring` (rule #2);
  - enter it as a contender in the weekly comparison next to the page,
    Sleeper and the stack.

  It serves only after passing ground rule 3.

### B. Play-caller table and coaching tendencies

- **The table:** `data/reference/play_callers.csv`, one row per team-season
  2019–2026: offensive play-caller, defensive coordinator, head coach, with
  the public source for each row. Rows nobody can source stay blank and are
  marked unknown, never guessed.
- **Tendencies, computed from play-by-play per play-caller, not per team,**
  so they follow a coordinator who changes teams:
  - neutral-situation pass rate over expected (`pass_oe`);
  - seconds per play and no-huddle rate;
  - 4th-down go rate against a league expectation by distance and field
    position;
  - red-zone pass rate over expected;
  - running-back carry concentration;
  - two- and three-tight-end personnel rate (participation to 2025, FTN
    charting for 2026 where it can stand in).

### C. Injury and roster-move events

- **QB change:** a different starter from last game, from depth charts,
  injury reports and play-by-play. Flags teammates (receivers) and the
  opposing defense.
- **Return from injury:** first-game-back snap share by injury type and
  games missed, fitted on 2019–2025 injury reports and snap counts, with
  the backup's retained share.
- **Vacated work by role:** early-down, passing-down and red-zone work,
  replacing the single vacated-opportunity share.
- **Offensive line starters out**, from depth charts plus injury reports.
- **Transactions as signals:** signings, elevations and trades (nflverse
  trades, Sleeper's daily player map) feed a "role at risk" flag. This is
  record-only until it earns a place.

### D. Distributions instead of single numbers

- **Model:** a per-player outcome distribution (a touchdown count from its
  probability, plus a yardage spread), a defense points-allowed tier
  expectation and a kicker distance-tier expectation.
- **Evaluation:** CRPS and quantile loss on 2019–2025, and P(win) Brier
  score on the synthetic leagues (`pwin_calibration.py`) plus the recorded
  league matchups.

## Every idea, with its free route

Kinds:
- **F**: a feature block in the cross-validation.
- **A**: a fitted adjustment, tested like the returner study.
- **M**: market.
- **D**: distribution.
- **P**: a product feature (a list or a tool, no change to projections; it
  still has to be correct).

### QB

| ID | Idea | Free data | Kind | Phase | Note |
|---|---|---|---|---|---|
| QB-01 | Market QB projection from props | Odds free tier | M | 0–2 | Workstream A |
| QB-02 | Designed runs vs scrambles | Play-by-play `qb_scramble`, FTN `is_qb_out_of_pocket` | F | 1 | |
| QB-03 | Pass rate over expected by play-caller | Play-by-play `pass_oe` and the table | F | 1 | Team pass rate failed; this is per play-caller |
| QB-04 | Pace and no-huddle by play-caller | Play-by-play, FTN `is_no_huddle` | F | 1 | Team pace was a near miss (4 of 7 folds) |
| QB-05 | Team pressure generated vs allowed | Participation `was_pressure` (to 2025), play-by-play sacks and hits | F | 2 | Team level fixes the PFR coverage gap |
| QB-06 | Top receiver out, weighted by target share | Injuries, usage | F | 1 | Workstream C |
| QB-07 | Benching or hook risk | Depth charts, injuries | A | 2 | "Who starts" markets only if a free tier has them |
| QB-08 | Red-zone pass rate over expected by play-caller | Play-by-play | F | 2 | |
| QB-09 | Goal-line sneak usage | FTN `is_qb_sneak` | F | 2 | |
| QB-10 | Opponent man/zone vs the QB's splits | Participation coverage (to 2025) | F | 2 | Prior only; efficiency, so slow (rule #6) |
| QB-11 | Wind from a forecast, deep passers only | Open-Meteo | F | 2 | Weather failed in general; this is one interaction |
| QB-12 | Week 1–3 prior: last season, new coordinator, receiver turnover | Prior stats, the table | A | 4 | For 2027; overlaps `returner_v2` |
| QB-13 | Short-week and west-to-east travel splits | Schedules | F | 2 | |

### RB

| ID | Idea | Free data | Kind | Phase | Note |
|---|---|---|---|---|---|
| RB-01 | Market RB projection | Odds free tier | M | 0–2 | Anytime TD is the key market |
| RB-02 | Return-from-injury ramp, and the backup's retained share | Injuries, snap counts | A | 1 | Workstream C |
| RB-03 | Share of carries inside the 5 | Play-by-play | F | 1 | Expected TDs failed overall; this is the role |
| RB-04 | Committee or bell cow, by play-caller | Play-by-play, the table | F | 1 | |
| RB-05 | Early-down vs passing-down role | Play-by-play (down, distance, targets) | F | 1 | |
| RB-06 | Vacated carries redistributed by role | Injuries, play-by-play | F | 1 | Workstream C |
| RB-07 | Game-script volume from spread and total | Schedules | F | 2 | QUANT §1.4 expects ~0; one cheap test |
| RB-08 | Opponent yards before contact and box counts allowed | PFR advanced stats (team sums), FTN `n_defense_box` | F | 2 | |
| RB-09 | Own line's run blocking | PFR yards before contact (team) | F | 2 | ESPN run-block win rates: manual, deferred |
| RB-10 | Role-at-risk flag from transactions | Trades, Sleeper player map | A | 2 | Workstream C, record-only first |
| RB-11 | Workload hangover before a short week | Play-by-play, schedules | F | 2 | |
| RB-12 | Zone vs gap blocking fit | none | — | deferred | Paid charting only |
| RB-13 | TD-driven outcome spread | Play-by-play | D | 1 | Workstream D |

### WR

| ID | Idea | Free data | Kind | Phase | Note |
|---|---|---|---|---|---|
| WR-01 | Market WR projection | Odds free tier | M | 0–2 | |
| WR-02 | QB-change effect on receivers | Depth charts, injuries | A | 1 | Workstream C |
| WR-03 | Teammate absence redistributed by route share | Participation routes (prior), FTN (live) | F | 1 | |
| WR-04 | End-zone and red-zone target share | Play-by-play | F | 1 | |
| WR-05 | Opponent man/zone vs his splits | Participation (to 2025) | F | 2 | Prior only |
| WR-06 | Slot vs outside alignment | none free | — | deferred | Paid charting or PFF |
| WR-07 | Targets per route, full coverage | Participation routes (to 2025) | F | 2 | Free routes failed before; prior only |
| WR-08 | Volume from play-caller pass rate over expected and pace | Workstream B | F | 1 | Shares B with QB-03 and QB-04 |
| WR-09 | Screens and designed touches | FTN `is_screen_pass` | F | 1 | Live in 2026 |
| WR-10 | Rookie and early-season priors | Draft picks, combine, prior stats | A | 4 | For 2027 |
| WR-11 | Deep threats in wind | Open-Meteo, NGS aDOT | F | 2 | Low priority |
| WR-12 | Shadow cornerback matchups | none free | — | deferred | PFF |
| WR-13 | Soft-tissue return ramp | Injuries, snaps | A | 1 | Shares RB-02's fit |

### TE

| ID | Idea | Free data | Kind | Phase | Note |
|---|---|---|---|---|---|
| TE-01 | Market TE projection | Odds free tier | M | 0–2 | |
| TE-02 | Routes vs blocking (route participation) | Participation (to 2025) | F | 2 | Prior only; no free route data in-season |
| TE-03 | Red-zone and end-zone target share | Play-by-play | F | 1 | |
| TE-04 | Two- and three-tight-end usage by play-caller | Participation personnel (to 2025), the table | F | 2 | Prior only |
| TE-05 | Tight-end-friendly play-caller history | Workstream B | F | 2 | |
| TE-06 | QB-change effect | Workstream C | A | 1 | Shares WR-02 |
| TE-07 | Target bump when a WR is out | Workstream C | F | 1 | Shares WR-03 |
| TE-08 | Script: blocks when leading, routes when trailing | Play-by-play | F | 2 | |
| TE-09 | Weekly tight-end streaming list | Model and rest-of-season | P | 2 | Needed for week 14 (Arizona's bye) |
| TE-10 | TD-driven outcome spread | Workstream D | D | 1 | |
| TE-11 | Opponent coverage of tight ends | Play-by-play, NGS | F | 2 | Weak signal; cheap test |
| TE-12 | Return ramps | Workstream C | A | 1 | |
| TE-13 | Rookie tight-end development curve | Prior stats, draft picks | A | 4 | For 2027 |

### K

| ID | Idea | Free data | Kind | Phase | Note |
|---|---|---|---|---|---|
| K-01 | Market kicker projection | Odds free tier | M | 0–2 | Must beat our kicker model (56.8%) |
| K-02 | Coach 4th-down aggressiveness | Play-by-play, the table | F | 1 | |
| K-03 | Expected points per attempt by distance, league tiers | Play-by-play, league config | D | 1 | |
| K-04 | Red-zone TD rate, offense vs defense | Play-by-play | F | 1 | |
| K-05 | Wind, precipitation, roof, altitude | Open-Meteo, schedules (roof) | F | 2 | |
| K-06 | Coach's trust in long range, and accuracy by distance | Play-by-play | F | 2 | Accuracy is a slow prior |
| K-07 | Spread and total interplay | Schedules | F | 2 | |
| K-08 | Kicker changes and call-ups | Rosters, Sleeper map | A | 2 | |
| K-09 | Extra point vs two-point tendency | Play-by-play | F | 2 | |
| K-10 | End-of-half clock management | Play-by-play | F | 2 | |
| K-11 | Opponent "bend but don't break" | Play-by-play | F | 2 | |
| K-12 | Surface and late-season outdoor splits | Schedules (surface, roof) | F | 2 | |
| K-13 | Weekly kicker streaming list and 3-week lookahead | Model and rest-of-season | P | 2 | |

### DEF

| ID | Idea | Free data | Kind | Phase | Note |
|---|---|---|---|---|---|
| DEF-01 | Market defense projection | Opponent team total (have), sacks and INT props | M | 0–2 | |
| DEF-02 | Opponent backup or rookie QB | Workstream C | A | 1 | Likely the strongest defense signal |
| DEF-03 | Points-allowed tiers as a distribution | Play-by-play, league config | D | 1 | |
| DEF-04 | Opposing QB's sack rate and time to throw | NGS, play-by-play | F | 1 | |
| DEF-05 | Pass rush vs opposing line | Participation pressure (to 2025), play-by-play | F | 2 | |
| DEF-06 | Opposing line starters out | Depth charts, injuries | F | 1 | Workstream C |
| DEF-07 | Opponent interception-worthy rate vs fumble luck | FTN `is_interception_worthy` (live) | F | 1 | |
| DEF-08 | Wind and rain | Open-Meteo | F | 2 | |
| DEF-09 | Opponent pace | Workstream B | F | 2 | |
| DEF-10 | Home, short week, travel | Schedules | F | 2 | |
| DEF-11 | Own key defenders out | Injuries, depth charts | F | 2 | |
| DEF-12 | Return-unit quality | Play-by-play returns | F | 2 | Small |
| DEF-13 | Defense streaming optimizer, 2–3 weeks ahead | Model and rest-of-season | P | 2 | |

**Totals:**
- 78 ideas: 50 feature blocks, 12 adjustments, 6 market, 4 distribution,
  3 product lists and 3 deferred.
- 75 have a free route. 5 of those rest only on participation, which
  stops at 2025, so they test as prior-season tendencies: QB-10, WR-05,
  WR-07, TE-02 and TE-04. QB-05, WR-03 and DEF-05 rely on it in part.
- 3 are deferred for lack of free data: RB-12, WR-06 and WR-12. RB-09
  goes ahead without its ESPN half.
- 3 are 2027 work, the week 1–3 priors: QB-12, WR-10 and TE-13.

## Decisions needed from the owner

1. **Odds data and rule #3.** Odds providers send player names. The plan
   resolves them once, through a committed table of exact (name, team,
   position) matches against the existing crosswalk, reviewed by a person.
   At join time, ids only: no fuzzy matching, and an unmatched name
   abstains and is listed. This extends rule #3's "one cached crosswalk" to
   a new platform, and needs a yes before code.
2. **Keys and publishing.** A free API key goes in `.env` as
   `GRIDIRON_ODDS_API_KEY` (rule #9) and in a GitHub Actions secret for the
   cloud build. Because the repo, artifacts and caches are public, raw odds
   would be published. If the provider's terms forbid redistribution, only
   derived numbers (projections, grades) are kept in public storage. Phase
   0 reads the terms.
3. **The serving freeze** from the week-14 board through the playoffs
   (ground rule 8).
4. **No scraping of sportsbook websites.** Only sanctioned free API tiers.
   Changing that is the owner's call.

## Risks

- **Many hypotheses.** Mitigated by pre-registration, the every-fold bar,
  the live forward check and the untouched 2026 season.
- **Credits.** Free tiers may not cover every book and market each week.
  The collector then narrows by the order in workstream A and says so in
  its report.
- **Prior-only data.** Coverage, routes and personnel stop at 2025 until
  nflverse publishes 2026, so those blocks test as priors.
- **Effort.** Workstreams A–D are each several days of work. The calendar
  front-loads A (time-critical), C and D (likely biggest per hour), and
  leaves the long tail of small F blocks for phase 2. Anything unfinished
  rolls to the offseason, never past the freeze.
