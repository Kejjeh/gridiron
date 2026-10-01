# Ours + Sleeper, calibrated: better on error, not on ordering (2026-10-01)

**Short answer: a two-stage blend of our advanced mean, Sleeper's projection
and the baseline, recalibrated per position, beats Sleeper on point error
in six of seven held-out seasons but is behind it on start/sit ordering in
four of seven.** On the first three seasons tested it looked like a tie on
ordering; seven seasons say it is not. It ships as the `stack` contender
(`advanced_weights.json`) with the bar recorded as NOT cleared; the live
shoot-out decides what drives the page (docs/DECISIONS.md), and the page's
own number is unchanged.

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

**Seven seasons, 2019–2025** (the run that ships; mean over folds):

| system | start/sit | close calls | MAE |
|---|---|---|---|
| Sleeper | **64.61%** | 56.70% | 3.971 |
| resid 0.5, calibrated | **64.65%** | **56.79%** | 3.893 |
| stack (old form), calibrated | 64.53% | 56.56% | 3.859 |
| **two_stage, calibrated** | 64.51% | 56.53% | **3.852** |

Two-stage calibrated vs Sleeper, per fold (start/sit, MAE):

| test | Sleeper | two-stage calibrated | ordering |
|---|---|---|---|
| 2019 | 63.95%, 4.341 | 63.73%, 4.115 | behind |
| 2020 | 63.93%, 4.122 | 63.40%, 4.022 | behind |
| 2021 | 65.33%, 4.208 | 65.47%, 3.964 | ahead |
| 2022 | 64.71%, 3.833 | 64.58%, 3.730 | behind |
| 2023 | 64.58%, 3.731 | 64.82%, 3.692 | ahead |
| 2024 | 64.35%, 3.791 | 64.38%, 3.795 | ahead (error behind) |
| 2025 | 65.42%, 3.770 | 65.22%, 3.649 | behind |

**Three seasons, 2023–2025** (the first run, kept for the record): the same
form was never worse than Sleeper on ordering in any fold (2025 behind by 2
of 60,813 pairs) and better on error in every fold — 64.84% vs 64.78%,
3.707 vs 3.764. That is what a three-fold result is worth.

## Reading it honestly

- **Ordering: behind Sleeper by 0.10 points on the seven-season mean**, ahead
  in three seasons, behind in four. The three-season tie did not survive
  more data. The error gain is real and large: −0.12 MAE on the mean, ahead
  in six of seven seasons.
- **The shrunk residual form (`resid 0.5`) orders best** (64.65%, ahead of
  Sleeper by 0.04) but its error is worse than the two-stage form's; neither
  clears the bar. The two-stage form is kept because its serving path is
  wired and its error is best.
- **Sleeper's history may carry post-game edits** (PROJECTION_BACKTEST_2025),
  which would flatter Sleeper here, not us. Only the live shoot-out
  (`grade_week.py`) can confirm.
- **This is the ceiling of a linear blend of public inputs on history.**
  Trees were tried and lost (GBM_BACKTEST.md); more metrics were tried and
  did not clear the bar (FEATURE_EXPANSION.md).

## What ships

- `advanced_weights.json` `stack` for QB/RB/WR/TE: a ridge on
  (`adv`, `sleeper`, `baseline`) fit on 2019–2025 with the median line
  folded into its coefficients (`meta.stack_form`; `meta.stack_evidence`
  carries `bar_cleared: false` and every fold). K and DEF stacks come from
  their own backtest.
- `AdvancedContext.stacked()` feeds its own advanced mean as `adv`.
- The record's `contenders` block and the weekly shoot-out carry it as
  before; nothing on the page changes until the shoot-out says so.
