# Does a non-linear learner beat the ridge? No. (2026-10-01)

**Short answer: gradient-boosted trees lose to the shipped ridge on start/sit
ordering in every configuration tried and every held-out season.** The
owner approved scikit-learn for this test; it stays a research-only
dependency and nothing from it ships.

## Method

Command: `PYTHONPATH=src python scripts/research/gbm_backtest.py --tables DIR [--conservative]`.

Season-fold cross-validation (2023, 2024, 2025 each held out), the same
rows, universe and metrics as every other backtest here (weeks 4–18, players
Sleeper projected: 14,115 player-weeks, 178,947 pairs). The learner is
`HistGradientBoostingRegressor` on the shipped feature set (`advanced_v1`,
`+depth+last`); the ridge on the same set is the baseline that contains all
existing features (rule #5). Bar: better on start/sit AND MAE in every fold.

Two passes, both declared before they ran:

1. **Expressive**: 400 rounds, learning rate 0.04, 15 leaves, 40 rows per
   leaf; squared and absolute loss; per position and pooled (position as a
   category); plus the rest of the role block; plus a ridge/tree average;
   all also with Sleeper's number as an input (contender form); all also
   calibrated by a per-position median line.
2. **Conservative** (the first pass overfit): three configurations with
   early stopping on an internal 15% split — 4 leaves / 100 rows per leaf /
   L2 5; depth 3 / 60 rows / L2 2; 8 leaves / 150 rows / L2 10 — squared and
   absolute loss, per position, pooled, averaged with the ridge, and the
   contender form.

## Results (cross-validated mean)

| system | start/sit | MAE |
|---|---|---|
| ridge (ships) | **63.72%** | 3.983 |
| boosting, expressive, squared loss | 61.05% | 4.192 |
| boosting, expressive, absolute loss | 62.65% | 3.889 |
| boosting, conservative, best of three (squared) | 63.20% | 4.020 |
| boosting, conservative, best of three (absolute) | 63.33% | **3.832** |
| boosting, pooled across positions | 63.37% | 4.012 |
| ridge + boosting average | 63.62% | 3.987 |
| Sleeper | 64.78% | 3.764 |
| stack ridge, calibrated (ships as contender) | 64.79% | 3.736 |
| stack boosting, absolute loss, conservative | 64.57% | 3.712 |

The best tree model is behind the ridge on ordering in all three folds
(2023 63.37 vs 63.77, 2024 63.29 vs 63.24 is the one fold a pooled model
edges it, 2025 63.59 vs 64.14). The only thing trees improve is point error
under absolute loss — which the median-regression calibration already gives
the ridge (3.846 calibrated; 3.707 for the calibrated stack).

## Reading it honestly

- **About 2,000 rows per position is not enough for trees to find
  interactions a line misses**, at least not on these features. The role
  block (share, jump, reserve) added inside the trees made it worse, the
  same answer the linear test gave.
- **Absolute loss helps error and hurts ordering.** The two metrics want
  different things; the calibration step is the cheap way to have both.
- **The ceiling on history is where it was**: a linear model of public
  inputs, and a calibrated blend with Sleeper that ties it on ordering and
  beats it on error. Beyond that the live shoot-out is the only test left.
- Not tried: a learner with far more rows (several more seasons), player
  props, or monotone constraints. Each is a new input or a new project.

## What ships

Nothing. `scikit-learn` is listed in requirements.txt as research-only; the
cloud build does not install it and serving never imports it.
