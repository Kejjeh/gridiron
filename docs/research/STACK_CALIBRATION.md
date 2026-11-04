# Ours + Sleeper, calibrated: the mix that is ahead of Sleeper in six of seven seasons (2026-10-01)

**Short answer: the average of two blends — Sleeper plus half of our
residual model, and a two-stage ridge on our advanced mean, Sleeper and the
baseline — recalibrated per position, orders start/sit pairs better than
Sleeper in six of seven held-out seasons and has a lower point error in all
seven.** It is behind in 2020 by 0.18 points of start/sit, so it does not
clear the every-fold bar and is recorded that way. It ships as the `stack`
contender (`advanced_weights.json`, one composed ridge); the live shoot-out
decides what drives the page (docs/DECISIONS.md), and the page's own number
is unchanged.

## Why calibration, not more features

The shipped stack (a ridge on every feature plus Sleeper's number) ordered
start/sit pairs a hair better than Sleeper but its point error was WORSE
(3.855 vs 3.764). Those are different losses: a least-squares ridge predicts
the mean of a right-skewed outcome, while the error metric rewards the
median. A per-position line a + b·x fit by median regression (least absolute
deviations) is monotone, so it cannot change which of two players ranks
higher — only how far the number sits from the truth.

## Method

Command: `PYTHONPATH=src python scripts/research/stack_calibration.py --tables DIR --save`.

- **Folds.** Each of 2023, 2024, 2025 held out; the other two train.
- **Rows.** Weeks 4–18, players Sleeper projected: 14,115 player-weeks,
  178,947 same-position pairs (the other backtests' universe).
- **Out of fold everywhere.** The advanced mean used as a two-stage input is
  predicted out of fold inside the training seasons; the calibration line
  is fit on out-of-fold training predictions, never on the test season.
- **Bar.** Point error better than Sleeper in EVERY fold, ordering never
  worse in any fold — a tie stands, and a tie is a difference under one pair
  in ten thousand — and better on the mean. The strict bar (both better in
  every fold) is reported too, and the winner does NOT clear it.

Variants: `stack` (shipped form), `resid` (Sleeper + a ridge fit to actual −
Sleeper), `resid_k` (the residual shrunk by k), `two_stage` (ridge on the
out-of-fold advanced mean, Sleeper, baseline), `two_plus` (+ last-game snaps
and touches, depth rank, vacated opportunity); each raw and calibrated.

## Results

**Seven seasons, 2019–2025**, mean over folds; "ahead" counts the folds the
system beats Sleeper (`scripts/research/blend_search.py`):

| system | start/sit | MAE | ordering ahead | MAE better |
|---|---|---|---|---|
| Sleeper | 64.61% | 3.971 | — | — |
| resid 0.5, calibrated | 64.65% | 3.893 | 5 of 7 | 6 of 7 |
| two_stage, calibrated | 64.51% | 3.852 | 3 of 7 | 6 of 7 |
| **mix 0.5 (mean of the two), calibrated** | **64.68%** | 3.862 | **6 of 7** | **7 of 7** |
| rank-level blend 0.7, calibrated | 64.67% | 3.873 | 6 of 7 | 7 of 7 |

The mix vs Sleeper, per fold (start/sit, MAE):

| test | Sleeper | mix 0.5 calibrated | ordering |
|---|---|---|---|
| 2019 | 63.95%, 4.341 | 64.01%, 4.179 | ahead |
| 2020 | 63.93%, 4.122 | 63.76%, 4.018 | behind (−0.18) |
| 2021 | 65.33%, 4.208 | 65.49%, 3.996 | ahead |
| 2022 | 64.71%, 3.833 | 64.76%, 3.720 | ahead |
| 2023 | 64.58%, 3.731 | 64.75%, 3.693 | ahead |
| 2024 | 64.35%, 3.791 | 64.55%, 3.787 | ahead |
| 2025 | 65.42%, 3.770 | 65.44%, 3.644 | ahead |

The shrink k barely matters (0.3 to 0.6 within 0.01 of each other); 0.5 is
kept. Earlier runs for the record: the two-stage form alone on three seasons
(2023–2025) looked like a tie on ordering and a win on error; on seven it
was behind in four.

## Reading it honestly

- **Ahead of Sleeper in six of seven seasons on ordering and in all seven on
  error, +0.07 on the mean.** One season (2020) is behind by 0.18 points.
  That is the strongest result this repo has against the strongest public
  comparator, and it is not an every-fold win.
- **Why the mix beats its parts.** The residual form keeps Sleeper's
  ordering and nudges it with ours; the two-stage form has the best error;
  averaging keeps most of both. A rank-level blend does about the same.
- **Sleeper's history may carry post-game edits** (PROJECTION_BACKTEST_2025),
  which would flatter Sleeper here, not us. Only the live shoot-out
  (`grade_week.py`) can confirm.
- **This is the ceiling of linear blends of public inputs on history.**
  Trees lost (GBM_BACKTEST.md); new metrics did not clear the bar
  (FEATURE_EXPANSION.md); seven seasons are all Sleeper's history allows.

## What ships

- `advanced_weights.json` `stack` for QB/RB/WR/TE: the mix composed into ONE
  ridge in raw units over the features, the page's advanced mean (`adv`),
  Sleeper's number and the baseline, with the per-position median line
  folded in (`meta.stack_form`, kind `mix_calibrated`; `meta.stack_evidence`
  carries `bar_cleared: false` and every fold). The save step asserts the
  composed ridge reproduces the research computation on every training row,
  NaNs included. K and DEF stacks come from their own backtest.
- `AdvancedContext.stacked()` feeds its own advanced mean as `adv`; no other
  serving change.
- The record's `contenders` block and the weekly shoot-out carry it as
  before; nothing on the page changes until the shoot-out says so.

## Trying to win every season (2026-10-01, later)

Goal set by the owner: beat Sleeper in every held-out season on both
metrics. The mix is behind in 2020 alone (−0.18 points of start/sit; error
is ahead there too). Everything below was declared before it ran and judged
on all seven folds; nothing was tuned to 2020.

| attempt | script | 2020 ordering vs Sleeper | folds ahead |
|---|---|---|---|
| fixed mix (ships) | `blend_search.py` | −0.18 | 6 of 7 |
| learned blend weights, nested (pooled / per position / averaged with the mix) | `super_learner.py` | −0.58 / −0.40 / −0.24 | 2 / 3 / 4 of 7 |
| mix knobs (shrink k, part weight w) selected on inner folds by ordering, pooled / per position | `mix_select.py` | −0.17 / −0.29 | 5 / 4 of 7 |
| residual model on the evaluation universe only | `resid_variants.py` | −0.19 | 6 of 7 |
| residual model + Sleeper's number and its gaps to ours | `resid_variants.py` | −0.35 | 4 of 7 |
| residual model with week × position fixed effects | `resid_variants.py` | −0.17 | 6 of 7 |
| residual model + reserve-list teammates (2020's absences ran through the reserve/COVID list, never the injury report) | `resid_variants.py --role` | −0.20 | 6 of 7 |
| residual model + team share and jumps | `resid_variants.py --role` | −0.12 | 6 of 7 |
| residual model + the whole role block | `resid_variants.py --role` | −0.14 | 6 of 7 |

Reading it: the learned meta-learner is unstable (its inputs are collinear —
the two-stage form already contains Sleeper) and loses; the blend knobs do
not matter; the residual model's structure does not matter; the reserve-list
hypothesis for 2020 did not hold. 2020's input coverage (depth charts,
expected points, snaps, lines, report flags) is in line with other seasons.
Sleeper is simply better that season by about a sixth of a point on
ordering, for reasons the public inputs do not carry. Six of seven is where
the evidence stops; a seventh would have to come from a new input, not from
re-arranging these.
