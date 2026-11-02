# DEF-08: forecast wind above 10 mph and precipitation at kickoff (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score).

## Idea
Roadmap DEF-08, "Wind and rain", from Open-Meteo. The shipped DEF set has
the opponent's implied total (`opp_implied`; the market lowers totals for
wind and rain), the spread, home, and the defense's and the opponent's
season rates (points per game, sacks, takeaways, points allowed, sacks
allowed, giveaways). It has nothing on the weather.

**What it can measure:** whether wind and rain at kickoff move a defense's
points (points allowed tiers, sacks, takeaways) beyond the opponent's
implied total.

**What it cannot measure:** the forecast a lineup decision sees days before
kickoff (Open-Meteo's archived forecast is assembled from the first hours of
each model run, close to the observation), gusts, snow on the field, or the
wind inside a stadium bowl.

## Hypothesis
Wind and rain cut passing and cause fumbles and errant throws, so the
opponent scores less and gives the ball away more; the defense should score
more in bad weather than the opponent's total alone says, because the total
prices the points but not the turnovers. Pre-game facts of this week's
game.

## Features (`COLUMNS`)
| column | position | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `wind10` | DEF | the venue's wind at kickoff: the mean of the hourly 10 m wind speed at the kickoff hour and the two hours after it (mph), minus 10, floored at 0; 0 indoors (roof dome or closed) | Open-Meteo historical forecast (`gfs_seamless`), ERA5 reanalysis where the forecast has no values; nflverse pre-game schedule (kickoff, roof, stadium); `data/reference/stadiums.csv` |
| `precip3h` | DEF | precipitation at the venue over the game's first three hours: the sum of the three hourly totals after the kickoff hour (mm), capped at 10; 0 indoors | same |

Definitions (`_p5_schedule_weather.py`): the kickoff hour is the schedule's
`gameday` and `gametime` (Eastern) in UTC, floored to the hour. The venue
is the schedule's `stadium_id`, except a neutral-site game listed at the
home team's own stadium (the six 2025 international games from week 4 on),
whose venue and weather are unknown. The 10 mph floor and the 10 mm cap are
fixed before any score, the same for every weather block of the package.

Weather data by Open-Meteo.com (CC BY 4.0). Weeks 4–18 of 2019–2025 (1,535
games): 1,045 from the archived forecast, 5 from the observed reanalysis
(the five 2019 international games, before the forecast archive covers
Europe and Mexico), 479 indoors (0 and 0), and 6 unknown (the 2025
international games listed at a home stadium); no outdoor game is left
without a value. Over the 1,050 outdoor games the forecast wind is above 10
mph in 22.6% (above 15 mph in 5.1%) and the precipitation 1 mm or more in
6.4%. Checked against the schedule's observed wind (955 outdoor games where
it has one): correlation 0.73, means 7.4 and 8.6 mph.

Missing when: the venue is unknown (both). Missing enters as 0 plus
`<col>__known`.

Distributions seen before registering (no scores): `wind10` is nonzero on
15.4% of DEF rows (mean 0.52, maximum 20.2); `precip3h` on 6.2% (95th
percentile 0.4 mm).

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds on the `k_def_backtest.py` defense
  tables (`block_runner.py`; the 2019 table has no Raiders rows).
- Bar: better than the shipped DEF set on pairwise (all defenses in a week)
  AND MAE in every fold.
- Coverage seen before registering (no scores):
  - `wind10`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 97%
  - `precip3h`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 97%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board. Serving it would
need a live forecast for the week's games, which the board does not pull
today.

## Result

Run 2026-11-02T04:16:38+00:00 (one scored run; `runs/DEF-08.csv`). Pairwise (all defenses in a week) and MAE on DEF rows:

| fold | shipped | DEF-08 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 60.47%, 5.242 |
| 2020 | 60.30%, 4.638 | 60.16%, 4.664 |
| 2021 | 60.28%, 4.801 | 60.44%, 4.798 |
| 2022 | 59.26%, 4.335 | 59.26%, 4.326 |
| 2023 | 60.72%, 4.835 | 61.03%, 4.824 |
| 2024 | 64.66%, 4.461 | 64.54%, 4.455 |
| 2025 | 62.01%, 4.588 | 62.22%, 4.588 |

**Fails.** Better on both metrics in 4 of 7 folds (2019, 2021, 2023, 2025; 2025's error is lower in the fourth decimal, 4.5877 vs 4.5878); 2022 ties on ordering (the same pairs) with lower error; 2024 has lower error but worse ordering; 2020 is worse on both (+0.026 error). Mean change +0.10 points of pairwise, -0.0011 MAE. Wind and rain move the defense ordering by -8 to +19 pairs a fold (of 5,222 to 6,179): what weather does to a defense's points is mostly already in the opponent's implied total. Not shipped, not a contender.

### Correction 2026-11-02: one bug-fix re-run

The near-miss bug hunt (after round 1, before any round-2 score) found a bug in the shared helper `_p5_schedule_weather.weather`. Where the archived forecast has no values (the five 2019 international games) the observed reanalysis stood in. The pre-registration disclosed it, but an observation made after kickoff is a leak under the ground rules. Fixed in `fb26c4b`: those games' weather is unknown (0 plus the column's `__known` flag). This block's module and the pre-registration above are unchanged.

Values the fix changes (block columns, all 2019-2025 rows; no scores):

- `precip3h`: 8 of 3,051 rows (2019 8)
- `wind10`: 8 of 3,051 rows (2019 8)

Coverage changes: `wind10` 2019 100.0% -> 98.0%; `precip3h` 2019 100.0% -> 98.0%.

Re-run 2026-11-02T09:14:31+00:00 at `fb26c4b` (the second row of `runs/DEF-08.csv`; helpers blob `8bb29e1d71b7`). The leak check passed again. Start/sit pairwise and MAE on DEF rows:

| fold | shipped | DEF-08, first run | DEF-08, re-run |
|---|---|---|---|
| 2019 | 60.23%, 5.248 | 60.47%, 5.242 | 60.46%, 5.245 |
| 2020 | 60.30%, 4.638 | 60.16%, 4.664 | 60.21%, 4.662 |
| 2021 | 60.28%, 4.801 | 60.44%, 4.798 | 60.38%, 4.798 |
| 2022 | 59.26%, 4.335 | 59.26%, 4.326 | 59.26%, 4.326 |
| 2023 | 60.72%, 4.835 | 61.03%, 4.824 | 61.09%, 4.822 |
| 2024 | 64.66%, 4.461 | 64.54%, 4.455 | 64.49%, 4.454 |
| 2025 | 62.01%, 4.588 | 62.22%, 4.588 | 62.30%, 4.590 |

**Re-run: fails.** Better on both metrics in 3 of 7 folds (2019, 2021, 2023); mean change +0.11 points of pairwise, -0.0015 MAE (first run: 4 of 7, +0.10, -0.0011). The re-run is this block's result; the first run's numbers above stay only as the record.
