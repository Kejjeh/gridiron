# DEF-10: rest days for both teams, the opponent's zones crossed, Thursday games (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score).

## Idea
Roadmap DEF-10, "Home, short week, travel", from the schedules. Home is
already in the shipped DEF set (`home`), with the spread and the opponent's
implied total (`opp_implied`), which can price a tired or travelling
opponent only through its total. Nothing in the shipped set is rest or
travel.

**What it can measure:** whether a defense scores differently when it or
its opponent is short of rest or rested, when the opponent crossed time
zones to the venue, and on Thursdays, beyond the shipped set.

**What it cannot measure:** when a team actually travelled (a London trip
often leaves days early), or body-clock effects by kickoff time (QB-13 tests
eastward travel for early kickoffs). Thursday games are few (about 7% of
rows).

## Hypothesis
An opponent on a short week or after crossing time zones executes worse, so
the defense allows fewer points and makes more sacks and takeaways; a
defense with more rest (after a bye) plays better and one on a short week
worse. Calendar and travel facts, known before the week.

## Features (`COLUMNS`)
| column | position | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `rest_days` | DEF | days since the defense's previous game (the schedule's `home_rest` or `away_rest` for its side), clipped to [4, 14] | nflverse pre-game schedule |
| `opp_rest_days` | DEF | the same for the opponent | same |
| `opp_tz_crossed` | DEF | the time zones the opponent crossed to the venue: abs(the venue's UTC offset − the opponent's home UTC offset at kickoff), DST-aware, capped at 3 | schedule (kickoff, stadium), `data/reference/team_timezones.csv`, `data/reference/stadiums.csv` |
| `thursday` | DEF | 1 when the game is on a Thursday, else 0 | schedule |

Definitions (`_p5_schedule_weather.py`): a team's home zone is the IANA zone
of the stadiums of its home games, 2019–2026 (`team_timezones.csv`, a source
on every row). The venue is the schedule's `stadium_id`, except a
neutral-site game listed at the home team's own stadium (the six 2025
international games from week 4 on), whose venue is unknown.

Missing when: the venue is unknown (`opp_tz_crossed` only). Missing enters
as 0 plus `<col>__known`.

Distributions seen before registering (no scores): `rest_days` and
`opp_rest_days` median 7, 95th percentile 14; `opp_tz_crossed` nonzero on
30% of rows (mean 0.52); `thursday` 1 on 6.9%.

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds on the `k_def_backtest.py` defense
  tables (`block_runner.py`; the 2019 table has no Raiders rows).
- Bar: better than the shipped DEF set on pairwise (all defenses in a week)
  AND MAE in every fold.
- Coverage seen before registering (no scores):
  - `rest_days`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `opp_rest_days`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `opp_tz_crossed`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 97%
  - `thursday`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result

Run 2026-11-02T04:15:36+00:00 (one scored run; `runs/DEF-10.csv`). Pairwise (all defenses in a week) and MAE on DEF rows:

| fold | shipped | DEF-10 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 60.55%, 5.227 |
| 2020 | 60.30%, 4.638 | 61.11%, 4.616 |
| 2021 | 60.28%, 4.801 | 60.72%, 4.772 |
| 2022 | 59.26%, 4.335 | 59.07%, 4.316 |
| 2023 | 60.72%, 4.835 | 60.88%, 4.816 |
| 2024 | 64.66%, 4.461 | 64.56%, 4.457 |
| 2025 | 62.01%, 4.588 | 61.75%, 4.610 |

**Fails.** Better on both metrics in 4 of 7 folds (2019, 2020, 2021, 2023); 2022 and 2024 have lower error but worse ordering; 2025 is worse on both. Mean change +0.17 points of pairwise, -0.0130 MAE. Rest and the opponent's travel lower the defense error in six folds and lift the ordering by 10 to 46 pairs (of 5,222 to 6,179) in four, but the gain fades after 2021 and turns into a loss in 2025: a small, unsteady edge the every-fold bar does not admit. Not shipped, not a contender.
