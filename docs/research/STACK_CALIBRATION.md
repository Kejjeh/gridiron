# Ours + Sleeper, calibrated: the first system that is not behind Sleeper (2026-10-01)

**Short answer: a two-stage blend of our advanced mean, Sleeper's projection
and the baseline, recalibrated per position, is never worse than Sleeper on
start/sit ordering in any held-out season and beats it on point error in
every one.** It ships as the `stack` contender (`advanced_weights.json`),
not as the page's number: the live shoot-out decides what drives the page
(docs/DECISIONS.md).

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

## Results (cross-validated mean, 2023–2025)

| system | start/sit | close calls | MAE |
|---|---|---|---|
| Sleeper | 64.78% | 57.01% | 3.764 |
| stack (shipped before) | 64.79% | 57.04% | 3.855 |
| stack, calibrated | 64.79% | 57.04% | 3.736 |
| resid 0.5, calibrated | 64.78% | 57.01% | 3.728 |
| two_stage, raw | 64.84% | 57.11% | 3.832 |
| **two_stage, calibrated** | **64.84%** | **57.11%** | **3.707** |
| two_plus, calibrated | 64.74% | 56.94% | 3.721 |

Two-stage calibrated vs Sleeper, per fold (start/sit, MAE):

| test season | Sleeper | two-stage calibrated |
|---|---|---|
| 2023 | 64.58%, 3.731 | 64.70%, 3.686 |
| 2024 | 64.35%, 3.791 | 64.39%, 3.777 |
| 2025 | 65.42%, 3.770 | 65.42%, 3.659 |

Calibration lines by position are near a = −0.5, b = 0.9 (QB a ≈ −1.2,
b ≈ 1.06): the raw blend runs a little high for skill players, as a mean
does against a median.

## Reading it honestly

- **Ordering is a tie with Sleeper, not a win.** +0.06 points of start/sit
  on the mean; on 2025 the blend is behind by 2 of 60,813 pairs (0.654169
  vs 0.654202). The error gain is real: −0.057 MAE, every fold.
- **Sleeper's history may carry post-game edits** (PROJECTION_BACKTEST_2025),
  which would flatter Sleeper here, not us. Only the live shoot-out
  (`grade_week.py`) can confirm.
- **The news features did not help the blend** (`two_plus` is behind
  `two_stage`): once Sleeper's number is in, last-game usage and depth rank
  add nothing linear.
- **This is the ceiling of a linear blend of public inputs.** The next step
  up is a non-linear learner, which needs a dependency and the owner's call.

## What ships

- `advanced_weights.json` `stack` for QB/RB/WR/TE: a ridge on
  (`adv`, `sleeper`, `baseline`) with the median line folded into its
  coefficients (`meta.stack_form`, `meta.stack_evidence`). K and DEF stacks
  are unchanged.
- `AdvancedContext.stacked()` feeds its own advanced mean as `adv`.
- The record's `contenders` block and the weekly shoot-out carry it as
  before; nothing on the page changes until the shoot-out says so.
