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

Run 2026-11-02T07:59:20+00:00 at `cc61a0c` (one scored run;
`runs/DEF-03.csv`). The module, helpers, harness and this
pre-registration are those of `6d588e2`; the one commit between added
K-03's results. Mean CRPS on the defense rows (lower is better) for the
page's normal, DEF-03 and the two references, and the P(win) Brier of
each fold's synthetic leagues (defenses carry DEF-03, every other starter
the page's normal):

| fold | rows | page | DEF-03 | fitted normal | whole-point normal | Brier page | Brier DEF-03 |
|---|---|---|---|---|---|---|---|
| 2019 | 397 | 3.7250 | 3.6999 | 3.7298 | 3.7212 | 0.23927 | 0.23927 |
| 2020 | 416 | 3.3442 | 3.2980 | 3.3386 | 3.3402 | 0.23879 | 0.23879 |
| 2021 | 448 | 3.3891 | 3.3589 | 3.3891 | 3.3852 | 0.23891 | 0.23891 |
| 2022 | 446 | 3.1094 | 3.0813 | 3.1029 | 3.1053 | 0.23932 | 0.23933 |
| 2023 | 448 | 3.4030 | 3.3704 | 3.4010 | 3.3990 | 0.23953 | 0.23953 |
| 2024 | 448 | 3.1437 | 3.0961 | 3.1387 | 3.1397 | 0.23503 | 0.23503 |
| 2025 | 448 | 3.2567 | 3.2085 | 3.2546 | 3.2527 | 0.23599 | 0.23600 |

**Fails** on the bar's second half. CRPS is lower than the page's normal
in all 7 folds, by 0.025 to 0.048 points (mean -0.037; pooled 3.295
against 3.332), and below the fitted normal and the page's normal on
whole points in every fold; pooled pinball loss at 10/50/90% is 0.834 /
2.308 / 1.247 against the page's 0.859 / 2.346 / 1.253. But the pooled
P(win) Brier is worse: 0.238121 against 0.238118 over 18,811 matchups,
+0.0000031 unrounded (week-clustered 95% interval -0.0000015 to
+0.0000077; 9% of the bootstrap draws not worse). The bar says not worse,
so it fails. The change is tiny either way: one defense a side barely
moves a matchup.

The fitted σ_PA was 16.5 to 17.5 points in every fold, inside the grid
but nearly twice the 9.2 points a team's score scatters around its
implied total (weeks 4-18, 2019-2025, from the schedules). So the
"opponent's points" part is not describing the opponent's points: the
spread is absorbing what the independent counts and tiers leave out.
That is a reading after the result, not evidence; a model that ties the
counts to the points allowed would be a new block with its own
pre-registration. Not a contender. The run's JSON stores the fitted
normal's scores but not its (a, b): a key clash in the harness's fold
result, fixed after the runs (reporting only; the bar never read it).
