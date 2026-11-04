# DEF-09: opposing play-caller's pace and no-huddle rate (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score).

## Idea
Roadmap DEF-09, "Opponent pace", workstream B: the tempo of the person
calling the opponent's plays. The shipped defense set (`DEF_ADV`) carries
the defense's points per game (season, last 3), sacks, takeaways and points
allowed per game, the opponent's implied points, sacks allowed and
giveaways per game, home and the spread; nothing about how many plays the
opponent runs, and no history from before this season.

## Hypothesis
A fast opponent runs more plays: more dropbacks to sack, more snaps to
fumble or throw an interception, and more chances to score. The opposing
play-caller's tempo is his habit and follows him, so his career plus this
season estimates it better than one team's recent games. Which way the
extra plays net out for a fantasy defense (more sacks and takeaways, more
points allowed) is the model's to learn; the block only says how fast.
Volume (rule #6).

## Features (`COLUMNS`)
| column | position | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `opp_pc_pace` | DEF | the week's play-caller (OPC) of the OPPONENT: seconds of game clock between consecutive snaps when the clock ran, minus the league's mean in the same season, summed over his snap pairs in every earlier season as play-caller (any team) and this season with that team before the row's week (against the league over this season's earlier weeks), ÷ (his pairs + 50). Negative is faster. | nflverse play-by-play 2019–2025, the play-caller table |
| `opp_pc_no_huddle` | DEF | the same construction for his neutral plays' `no_huddle` flag, ÷ (his plays + 100) | same |

Snap pairs and neutral plays exactly as in QB-04 (`_tendencies.py`,
`pace_obs` and `neutral_rate_obs`); this is QB-04's pair of tendencies read
for the other side of the ball.

Who is in charge: the table's OPC for (season, opponent, week); no row
means unknown (3,001 of 3,070 team-games of weeks 4–18 have one).

Missing when: the opponent's team-week has no named play-caller, or he has
neither an earlier season as play-caller nor an earlier game this season
with that team. Missing enters as 0 plus `<col>__known`.

## Test
- Positions: DEF (the `k_def_backtest.py` defense tables; 2019 has 31
  teams, as every block faces).
- Seasons: 2019–2025, seven season folds (`block_runner.py`). 2019 rows
  have this season's part only (the table starts in 2019).
- Bar: better than the shipped defense set on pairwise (all defenses in a
  week) AND MAE in every fold.
- Coverage seen before registering (no scores):
  - `opp_pc_pace`: 2019 98%, 2020 96%, 2021 95%, 2022 96%, 2023 96%, 2024 100%, 2025 98%
  - `opp_pc_no_huddle`: 2019 98%, 2020 96%, 2021 95%, 2022 96%, 2023 96%, 2024 100%, 2025 98%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result

Run 2026-11-02T04:20:12+00:00 at `d8847b6` (one scored run; `runs/DEF-09.csv`).
Pairwise (all defenses in a week) and MAE on the defense rows:

| fold | shipped | DEF-09 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 60.09%, 5.247 |
| 2020 | 60.30%, 4.638 | 60.07%, 4.642 |
| 2021 | 60.28%, 4.801 | 60.07%, 4.795 |
| 2022 | 59.26%, 4.335 | 59.08%, 4.332 |
| 2023 | 60.72%, 4.835 | 60.87%, 4.836 |
| 2024 | 64.66%, 4.461 | 64.51%, 4.463 |
| 2025 | 62.01%, 4.588 | 61.99%, 4.594 |

**Fails.** Better on both metrics in 0 of 7 folds; mean change -0.11 points
of pairwise, +0.0007 MAE. It orders defenses worse in six folds (by 1 to
13 pairs of 5,222 to 6,178) and better only in 2023, and its error is lower
only in 2019, 2021 and 2022. The opposing play-caller's tempo adds nothing
to the opponent's implied points, sacks allowed and giveaways. Not
shipped, not a contender.
