# P(win) calibration (2026-10-15)

**Decision.** The page's win probability stays the closed form
Φ((μa − μb) / √(σa² + σb²)) with no correction, and is now labelled
**TESTED** instead of UNCALIBRATED. One spread factor k
(P = Φ(d / (k·s))) was fit and tested out of sample two ways. With the means
the page actually sums, it did not beat the raw formula on held-out seasons
or on this league's own matchups. The raw spread held up in both
(`src/gridiron/models/pwin_calibration.json`, read by
`gridiron.winprob.load_calibration`). Every league matchup is now recorded
on each board and graded each week, so the label keeps being checked live.

Scripts: `scripts/research/pwin_calibration.py` (step 1, synthetic
leagues), `scripts/research/pwin_league_history.py` (step 2, this league
2023-2025), `scripts/research/pwin_calibration_save.py` (writes the file by
the rule below), `scripts/weekly/grade_week.py` `league_pwin` (step 3).
Outputs: `data/research/cache/tables/pwin_calibration.json` and
`pwin_league_history.json` (gitignored).

## Step 1: synthetic 12-team leagues, fit 2019-2022, test 2023-2025

Each week from 4 to 17, 32 seeded replicate leagues are dealt from the top-N
per slot (QB RB RB WR WR TE FLEX FLEX K DEF) and paired at random. Skill
sd is the page's sd. K uses the baseline. DEF uses the advanced DEF ridge
with resid_sd 5.96. That ridge is the shipped fit on 2019-2025, so it is in
sample for the test seasons. In step 2, refitting it with each season held
out moved k_mle by 0.01. k is fit by minimum log loss on the fit seasons and
scored on the test seasons. Uncertainty comes from a week-clustered
bootstrap (500 replicates).

**Primary: the page's own skill mean.** That is advanced_v1, refit
leave-one-season-out on the shipped columns (`loso_advanced`), so every row
is out of sample. The page sums this number when Sleeper's projection is
absent, and the stack takes it as input.

| | k (fit 2019-22) | test log loss raw / with k | test Brier raw / with k | reps better on both | bar |
|---|---|---|---|---|---|
| **served mean (primary)** | 1.15 [1.00, 1.31] | **0.66601** / 0.66646 | **0.23673** / 0.23695 | 13.8% | not cleared |
| served + 3% DNP shock | 1.21 [1.07, 1.42] | **0.66861** / 0.66895 | **0.23798** / 0.23815 | 31.2% | not cleared |
| served, skill-only league | 1.09 [0.98, 1.24] | 0.66636 / 0.66628 | 0.23687 / 0.23686 | 47.6% | (+0.00008: noise) |
| baseline skill mean | 1.08 [0.97, 1.20] | 0.67454 / 0.67381 | 0.24079 / 0.24049 | 89.6% | cleared |
| Sleeper mean (post-hoc) | 1.11 [0.99, 1.25] | 0.66358 / 0.66340 | 0.23573 / 0.23563 | 58.6% | (noise) |

8,060 test matchups over 42 test weeks. The k that best fits the test
seasons themselves, with the served mean, is **1.00**. The fit-season k is
pulled up by 2019 (in-season k 1.43; 2020-2022 are 1.04-1.13). On the test
seasons the held-out margin z-SD is 1.075 and the margin slope is 1.03:
the spread looks slightly narrow, but the projected margins are a little
better than the formula assumes, and the two offset. Held-out reliability
on the favourite's side (raw, served mean): 50-55% stated 0.525 observed
0.507; 55-60% 0.574 / 0.582; 60-65% 0.624 / 0.627; 65-70% 0.672 / 0.660;
70-80% 0.734 / 0.753; 80-90% (n = 49) 0.828 / 0.755.

**Why the first run is not the answer.** The first run used the baseline
skill mean, and with it a factor helps (k ≈ 1.08-1.15). The page never
sums baseline means for skill players. A worse mean makes the margin
noisier than its stated sd, so the factor was correcting a mean the page
does not use. Review caught this, and the served mean replaced it as the
primary. The baseline result is kept as a sensitivity.

Not tested directly: the stack's own mean. Its weights were fit on all of
2019-2025, so no season is held out for it. In review, the shipped stack
weights (in sample) gave k 1.03.

## Step 2: this league's own matchups, 2023-2025

The league chain comes from Sleeper's public API via previous_league_id:
2023 had 8 teams, 2024 had 10, 2025 had 12. Scoring and roster slots
matched the current season. Weeks 4-14 give 165 matchups. 157 were graded.
8 were excluded because the page would abstain: a starter had no earlier
game. Every starter is projected as of its week the page's way, with ids
joined through the one crosswalk (2,967 of 2,967 player slots resolved).
Sleeper's own points are the label; a recomputation agrees on 99.4% of
winners.

| skill mean | k_mle [CI] | log loss raw / k_mle | k fit on the other seasons, pooled held-out log loss raw / k | margin z-SD | favourite given / won |
|---|---|---|---|---|---|
| **served (advanced_v1 out of sample)** | 1.02 [0.80, 1.75] | 0.64556 / 0.64554 | **0.64556** / 0.65426 | 1.036 | 62.3% / 66.2% |
| baseline | 1.05 [0.80, 1.82] | 0.64311 / 0.64302 | **0.6431** / 0.64348 | 1.042 | 63.1% / 61.8% |

The served mean covers 2,584 of 2,629 projected skill starter slots. The
other 45 did not play and keep the baseline. The join check against the
season tables matched the baseline (median absolute difference 0.00). By
season with the served mean, the favourite was given 63% and won 76% in 2023
(n = 42), given 63% and won 74% in 2024 (n = 53), and given 62% and won 53%
in 2025 (n = 62). 157 matchups carry ±4 points of sampling noise on the
pooled win rate. They cannot fit a factor, so they are used as a check.

## The rule that wrote the file

`pwin_calibration_save.py` takes k from the synthetic fit seasons. It
reads both studies with the served mean.

- **bar_cleared** (apply k, label CALIBRATED) requires both:
  - k beats k = 1 on the held-out synthetic seasons on log loss AND Brier;
  - k is no worse on this league.
  - Not met: the factor lost both synthetic scores, and on this league k
    1.15 is worse (0.64604 vs 0.64556).
- **raw_tested** (k = 1, label TESTED) applies when the factor does not
  clear and the raw spread holds on its own:
  - the held-out synthetic margin z-SD is in [0.9, 1.1] (1.075);
  - on this league, a k fit on the other seasons does not beat k = 1 on
    the held-out season, pooled (0.64556 vs 0.65426 log loss; 0.22775 vs
    0.23166 Brier);
  - the league margin z-SD is in [0.9, 1.1] (1.036).
  - Met.
- **Disclosure:** the raw_tested branch was added after these results
  were seen. It changes no number on the page (k was already 1). It only
  replaces "never checked" with what the check found.

`tests/test_pwin_calibration.py` re-asserts the shipped file against this
rule.

## Step 3: every league matchup, graded weekly

Each board now records every matchup in the league (`league_matchups`).
Each entry carries:

- both roster ids;
- each side's mean and sd;
- the stated and raw P(win), and k;
- each starter's id-keyed scoring key.

The owner's matchup is the page's own view. `grade_week.py` scores each
recorded matchup once its week is final. A starter with no stat line
scores 0. A tie or an unscorable matchup is skipped and counted. The
ledger row records these fields:

- `pwin_n`;
- Brier and log loss, stated and raw;
- `pwin_fav_stated`, `pwin_fav_won`.

The report card shows the season to date. Boards built before 2026-10-15
carry no league matchups. Week 6 is the first graded week.

## What would change this

- **Clear the bar:** if the live league grade drifts from the held-out
  numbers over several weeks, re-run steps 1-2 with the new season
  appended. Example: favourites stated well above their win rate, or Brier
  steadily worse than the raw formula's expected value.
- **Ship a factor:** this needs `bar_cleared`. A factor is never set by
  hand.
- **Correlated starters:** a same-game stack or an opposing QB/DEF would
  need full Monte Carlo (`gridiron.winprob` docstring), not a k.
