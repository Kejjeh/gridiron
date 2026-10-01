# Catching a starter's injury faster: the role-change block (2026-09-30)

**Short answer: reading the player's LAST game beside the trailing averages
helps, a little and everywhere; the "clever" signals do not.** In season-fold
cross-validation over 2023–2025 the last-game block beat the shipped model on
both start/sit success and point error in every fold. Team-share, jump,
missing-teammate and reserve-list features were tested and did not earn a
place: the depth chart already carries that news into the model.

## Why

The shipped weekly model (`advanced_v1`) reads three-game averages. The
week after a starter's season-ending injury, his backup's averages still
hold two backup games, and the vacated-opportunity feature only sees
teammates Out or Doubtful on THIS week's report — a player placed on injured
reserve is not on it. So the backup was priced as a backup for two more
weeks (the owner saw it with a Miami running back in week 4, 2026).

## Method

Command: `PYTHONPATH=src python scripts/research/role_change_backtest.py --save`.

- **Folds.** Each of 2023, 2024, 2025 is held out in turn; the other two
  train. Weekly ridge models are refit per fold.
- **Rows.** Weeks 4–18, players Sleeper projected (the shipped evidence's
  universe): 14,115 player-weeks, 178,947 same-position start/sit pairs.
- **Baseline.** The set `advanced_v1` ships — every existing feature (rule #5).
- **Builder.** Every feature comes from `features_as_of`, the builder the
  page runs, one call per week with earlier weeks only.
- **Role-change subset.** Player-weeks whose last game was a jump of 5+
  opportunities over the trailing average, or with 3+ opportunities per
  game of teammates missing: 2,256 rows, 15,068 pairs.
- **Bar.** A set is eligible only if it beats the shipped set on pairwise
  AND MAE in EVERY fold; the eligible set with the fewest features ships.

Candidates (`gridiron.models.advanced.ROLE_FEATURES`):

| set | adds |
|---|---|
| `+last` | last game's expected points, opportunities, snap share |
| `+share` | share of the team's position-group opportunities in the team's last game; jump of opportunities and snap share vs the trailing average |
| `+absent` | per-game opportunities of teammates with no stat line in the team's last game and not Questionable (rule #11), handed out by last-game share |
| `+reserve` | the same for teammates on a reserve list per the official weekly roster (`RES`, `EXE`; never the game-day inactive list) |
| `+reserve_lag` | `+reserve` read from the week BEFORE's roster |
| `+ewm` | exponentially weighted averages, half-life one game |

## Results (cross-validated mean, 2023–2025)

| system | MAE | start/sit | close calls | role-subset MAE | role-subset start/sit |
|---|---|---|---|---|---|
| baseline | 4.060 | 62.91% | 54.83% | 5.584 | 61.40% |
| Sleeper | 3.764 | 64.78% | 57.01% | 5.468 | 63.09% |
| shipped (`advanced_v1`) | 3.997 | 63.62% | 55.65% | 5.586 | 62.12% |
| **`+last`** | **3.983** | **63.72%** | 55.74% | 5.589 | **62.38%** |
| `+share` | 3.978 | 63.69% | 55.71% | 5.579 | 62.01% |
| `+absent` | 4.001 | 63.58% | 55.59% | 5.609 | 61.98% |
| `+reserve` | 3.999 | 63.61% | 55.63% | 5.594 | 62.10% |
| `+reserve_lag` | 3.999 | 63.59% | 55.61% | 5.596 | 61.99% |
| `+ewm` | 3.986 | 63.72% | 55.77% | 5.593 | 62.37% |
| `+last+reserve` | 3.986 | 63.70% | 55.71% | 5.597 | 62.17% |
| `+last+share+reserve` | 3.981 | 63.73% | 55.77% | 5.588 | 62.09% |
| all of it | 3.982 | 63.68% | 55.69% | 5.594 | 62.07% |

`+last` per fold, shipped → `+last`:

| test season | start/sit | MAE | role-subset start/sit |
|---|---|---|---|
| 2023 | 63.63% → 63.77% | 3.978 → 3.963 | 61.05% → 60.82% |
| 2024 | 63.22% → 63.24% | 4.072 → 4.055 | 62.76% → 62.99% |
| 2025 | 64.01% → 64.14% | 3.941 → 3.932 | 62.56% → 63.33% |

By position (start/sit, shipped → `+last`, Sleeper): QB .615 → .616 (.618),
RB .654 → .656 (.670), WR .637 → .637 (.648), TE .593 → .596 (.613).

Eligible under the bar: `+last`, `+ewm`, `+last+reserve`, `+ewm+reserve`.
Fewest features: **`+last`**. It ships.

## Reading it honestly

- **The gain is small: a tenth of a point of start/sit and 0.014 of MAE.** It
  is consistent (every fold, both metrics), which is the bar, not large.
- **What the model does with it.** Standardised weight on last-game snap
  share: QB +1.7, RB +1.4, TE +0.8, WR +0.7 — the largest single fast-moving
  coefficient at RB. A backup who played the team's last game at a starter's
  snap share is priced on it the next week.
- **Why the confirmed reserve-list feature adds nothing.** The depth chart
  before kickoff (`depth_rank`, shipped) already promotes the backup the
  week the starter lands on reserve; a linear model gets no extra
  information from the same fact in volume units. The feature is still
  built (the record shows it) and the weekly roster is now an input, because
  the rest-of-season engine uses it as a FACT (below).
- **Why missing-teammate volume hurts.** "No stat line last game and not on
  the report" also describes a healthy player who was rested or has just
  returned; the signal is diluted.
- **Sleeper still leads** by about a point of start/sit on history; the live
  shoot-out (`grade_week.py`) remains the scoreboard.

## What ships

- `advanced_v1` refit on 2023–2025 with the last-game block (`feature_set`
  `+depth+last` in `src/gridiron/models/advanced_weights.json`, evidence under
  `meta.role_change_evidence`); registered as `role_change_v1` in
  `gridiron.models.validated_signals`.
- The weekly roster joins the model inputs the pull step fetches
  (`reserve.parquet`: week, gsis id on a reserve list). When it is missing
  the feature is unknown and filled like every other missing input; the
  weekly number does not depend on it.
- **Rest of season (`gridiron.ros`).** A player on a reserve list per the
  official weekly roster is credited 0 for the first four games of the
  stint — the NFL minimum before activation, a rule, not a guess
  (`IR_MIN_GAMES`) — and the stint is flagged. A return after those games is
  never guessed (rule #11). The player map's IR tag stays a flag only.
