# DEF-03: points-allowed tiers as a distribution (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen. The only runs
before this were `--coverage-only` (coverage and the leak check, no score)
and a plumbing test of the harness in which every real outcome was replaced
by a random draw before anything was fitted, so no real score exists.

## Idea
Roadmap DEF-03, "Points-allowed tiers as a distribution" (kind D, workstream
D). The page gives a team defense one normal: the advanced DEF ridge's mean
(which already reads the opponent's implied total, sacks, takeaways and
points allowed per game) and one sd for every defense, the model's
`resid_sd['DEF']` = 5.96 (`AdvancedContext.defense_projection`). This league
scores a defense by a STEP function of the points it allows (10, 7, 4, 1, 0,
−1, −4 for 0, 1–6, 7–13, 14–20, 21–27, 28–34, 35+) plus counting stats
(sack 1, interception and fumble recovery 2, forced fumble 1, touchdown 6,
safety and blocked kick 2: `DEFENSE_SCORING`). A step function of a spread-out
score, plus a few lumpy counts, is not a normal, and its spread is not the
same for every defense.

## Hypothesis
The shape of a defense's week follows from the tiers: against a weak offense
(a low implied total) the mass sits in the 4- and 7-point tiers; against a
strong one it piles on 0, −1 and −4, with touchdowns as rare six-point lumps
on top. The tiers applied to a distribution of the opponent's points, plus
the counts, should describe a defense's week better than one normal with
one sd for all. The rates are volume and stay fast; touchdowns are rare and
heavily shrunk (rule #6).

## Distribution inputs (`COLUMNS`)
| column | position | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `dst_rate_1` | DEF | 1-point events per game: sacks, the average of the defense's sacks per game and the opponent's sacks allowed per game, each shrunk to last season's league per-game rate with 3 games; plus the defense's forced fumbles per game, shrunk the same way | nflverse `load_team_stats` (week) |
| `dst_rate_2` | DEF | 2-point events per game: takeaways, the average of the defense's interceptions + fumble recoveries per game and the opponent's giveaways per game (interceptions thrown + offensive fumbles lost), each shrunk with 3 games; plus safeties and blocked kicks at last season's league rate per team-game | same |
| `dst_rate_6` | DEF | 6-point events per game: defensive, fumble-return and return touchdowns, the defense's own, shrunk to last season's league rate with 8 games | same |

Each event's columns come from `gridiron.scoring._DEFENSE_COMPONENTS` and
its value from `DEFENSE_SCORING` (asserted: one value per group). The
opponent's implied total is the frozen DEF table's `opp_implied` (from the
line), a shipped pre-game feature.

Missing when: never (a team with no earlier game gets the league prior).

## Distribution (`pmf`, `fit`; shared code `blocks/_p7_distributions.py`)
For a row with the page's mean μ:
- the opponent's points PA = round(N(`opp_implied`, σ_PA²)): everything below
  0.5 is a shutout, 80 or more is folded into 80;
- tier(PA) from `DEFENSE_SCORING` through `gridiron.scoring.points_allowed_tier`;
- counts N1 ~ Poisson(`dst_rate_1`), N2 ~ Poisson(`dst_rate_2`),
  N6 ~ Poisson(`dst_rate_6`), independent of each other and of PA;
- points = tier(PA) + N1 + 2·N2 + 6·N6, exact on the integer lattice from the
  characteristic function (FFT, −20 to 107), then moved so the mean is
  exactly μ while every point stays a whole number (this league's defense
  points are whole): by whole points, and the remaining fraction of a point
  as a one-point Bernoulli step (`on_grid_mean`; for a whole-number outcome
  this is never worse in CRPS than sliding the support off the integers).
  The harness refuses a run where any row's mean differs from μ by more
  than 1e-6;
- σ_PA: one value per fold, fitted on the six training seasons by minimum
  mean CRPS of the total over 5.0 to 20.0 in steps of 0.5. "Implied total
  plus a fitted spread": the fit sets the spread by how defense points
  scatter, not by the opponent's points alone, so it also absorbs what the
  independence assumption leaves out;
- μ is the harness's: the DEF ridge (`DEF_ADV`) refit with the row's season
  held out; the training rows' μ come from models fit on the five seasons
  other than the test season and their own.

## Test
- Position: DEF.
- Harness: `scripts/research/distribution_backtest.py` (block_runner.py's
  tables, rows and folds; pwin_calibration.py's leagues; neither changed).
- Rows: block_runner.py's defense table (k_def_backtest.py's, weeks 4–18,
  Sleeper projected, actual known), 2019–2025, seven season folds. The 2019
  table has no Raiders rows (31 teams), by design.
- Comparator: the page's normal, N(μ, 5.96).
- Bar: a lower mean CRPS than the page's normal in every one of the seven
  folds, AND the pooled P(win) Brier over the seven folds' synthetic leagues
  not worse (≤), both decided on unrounded numbers. Leagues:
  pwin_calibration.py's 12-team leagues (its dealing,
  seeds and slot pools, checked against its own `week_matchups`), dealt from
  the fold's rows and means, weeks 4–17, 32 seeded leagues a week. Every
  starter keeps the page's normal except the defenses, whose distributions
  are this one; P(win) is computed exactly on the lattice.
- Reported, never the bar: pinball loss at 10/50/90%, the share of actuals
  inside the 10–90% interval, log loss, a week-clustered bootstrap of the
  Brier change; a fitted normal N(μ, a + bμ) fitted the same way on the same
  rows (is a gain the shape, or only a refitted spread?); and the page's
  normal put on whole points (how much of a gain is only knowing that the
  points are whole numbers?).
- Coverage seen before registering (no scores):
  - `dst_rate_1`, `dst_rate_2`, `dst_rate_6`: 100% every season
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3). The page would keep
its defense mean and order; the distribution would feed the ± and P(win). If
the fitted normal does as well, the cheaper change is the page's DEF sd, a
separate decision. Then a DECISIONS.md row. Nothing serves after the week-14
board.

## Result
