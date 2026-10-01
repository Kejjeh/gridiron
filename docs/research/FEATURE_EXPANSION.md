# More data: seven seasons and six new metric blocks (2026-10-01)

**Short answer: more seasons help, more metrics do not.** Trained on six
other seasons instead of the two neighbours, the shipped feature set is
better on both start/sit ordering and point error in every held-out season
2023–2025, so every model here is now fit on 2019–2025. Six blocks of new
metrics were tested on top of the shipped set; none beat it on both metrics
in every fold, and all of them together lose on error in two folds.

## Method

Command: `PYTHONPATH=src python scripts/research/feature_expansion_backtest.py --tables DIR [--only-seasons] [--save]`.

Seasons 2019–2025 (Sleeper's weekly projections, the backtest universe, go
back that far; nflverse's inputs too). Same rows, universe and metrics as
every other backtest: weeks 4–18, players Sleeper projected, start/sit
pairwise order and MAE. Every feature from `features_as_of`, earlier weeks
only.

**Question 1, more seasons.** For each test season 2023–2025, the shipped
set fit on the two neighbouring seasons (as the shipped evidence was) vs
fit on every other season.

**Question 2, more metrics.** Each season held out in turn (seven folds),
all others training. Blocks (`gridiron.models.advanced.EXPANSION_FEATURES`):

| block | adds |
|---|---|
| `+xtd` | expected touchdowns (last 3, season), share of the team's expected points |
| `+epa` | EPA per game, season (efficiency, slow — rule #6) |
| `+ngs2` | depth of target, intended-air-yards share, cushion (WR/TE); stacked boxes, rushing efficiency (RB); time to throw, aggressiveness (QB) |
| `+line` | the posted spread, wind, temperature (indoors: 0 wind, 70°) |
| `+team` | the offense's plays, pass rate, EPA per game (last 3) |
| `+pfr` | drops (WR/TE), yards before and after contact (RB), pressure and bad-throw rates (QB) |

Bar: better than the shipped set on start/sit AND MAE in every fold; the
eligible block with the fewest features ships.

## Results

**More seasons** (shipped set; start/sit, MAE):

| test | two neighbours | six other seasons | Sleeper |
|---|---|---|---|
| 2023 | 63.78%, 3.943 | **64.00%, 3.939** | 64.58%, 3.731 |
| 2024 | 63.24%, 4.055 | **63.25%, 4.039** | 64.35%, 3.791 |
| 2025 | 64.14%, 3.932 | **64.17%, 3.882** | 65.42%, 3.770 |

Better on both in every fold. The ridge is data-hungry enough that six
seasons beat two; the lift is small on ordering and real on error.

**More metrics** (seven-fold mean; start/sit, MAE):

| set | start/sit | MAE | folds better on both |
|---|---|---|---|
| shipped | 63.45% | 4.098 | — |
| `+xtd` | 63.47% | 4.098 | 3 of 7 |
| `+epa` | 63.43% | 4.096 | 1 of 7 |
| `+ngs2` | 63.48% | 4.097 | 3 of 7 |
| `+line` | 63.53% | 4.095 | 4 of 7 |
| `+team` | 63.50% | 4.096 | 4 of 7 |
| `+pfr` | 63.46% | 4.100 | 1 of 7 |
| `+all` | 63.61% | 4.092 | 4 of 7 |

No block is eligible. `+all` has the best mean ordering (+0.16) but is worse
on error in 2024 and 2025 and worse on ordering in 2019.

## Reading it honestly

- **The seven-fold means are lower than the three-fold ones** (63.45% vs
  63.72% for the same set) because 2019 and 2020 are harder seasons for
  every system, Sleeper included (64.61% vs 64.78%).
- **Coverage is the limit for several blocks.** Next Gen Stats and PFR rows
  exist only for players above a volume threshold: in 2025, depth of target
  for 37% of rows, stacked boxes 15%, pressure rate 13%. The ridge fills the
  rest with the training mean, which is where a feature goes to die.
- **The line and the offense's pace are the closest misses** (4 of 7 folds).
  Spread and weather are already partly in the implied total; pace is
  partly in the player's own opportunities.
- **Not tried**: routes run (nflverse participation is play-level with only
  the targeted receiver's route, and exists from 2023), player props (not a
  free source), more seasons still (Sleeper's projections thin out before
  2019).

## What ships

- `advanced_v1` skill positions refit on 2019–2025 (31,908 player-weeks;
  feature set unchanged, `+depth+last`), evidence under
  `meta.expansion_evidence`.
- Kickers, team defenses, the calibrated stack and the ROS methods refit on
  the same seven seasons (their own documents carry the new tables).
- The expansion inputs stay in the builder and the pull step (the record
  shows them; a missing input stays unknown). Nothing reads them for a
  number.
