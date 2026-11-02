# DEF-02: the opponent's expected starting quarterback, backup or inexperienced (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score),
counts of how often each column is nonzero, and a construction check of the
expected starter against who started (below). No actual points were read.

## Idea
Roadmap DEF-02, "Opponent backup or rookie QB" (kind A, workstream C, "likely
the strongest defense signal"), cast as features on the defense rows of
`k_def_backtest.py`'s table: the opponent's expected starting quarterback
this week (depth chart QB1, pre-game), his career starts before this week,
and whether he started the opponent's previous game. A rookie with few
starts is covered by the start count (a rookie flag would add little to it,
and the block keeps to three columns). The shipped DEF set carries the
opponent's implied total and the spread, closing lines that price a known
quarterback change, and the opponent's sacks allowed and giveaways per game
this season (`opp_sacks_allowed_pg`, `opp_giveaways_pg`): team averages over
games another quarterback may have played.

## Hypothesis
A defense facing a quarterback with few career starts, or one who did not
start the opponent's last game, scores more (sacks, interceptions, fumbles,
fewer points) than the line and the opponent's season averages say: the
market prices the points more than the sacks and turnovers, and the season
averages belong partly to someone else. A structural fact about this week's
opponent, not an efficiency estimate (rule #6).

## Features (`COLUMNS`)
| column | position(s) | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `oqb_starts32` | DEF | the opponent's expected starter's regular-season starts before this week (from 1999), capped at 32 | schedule's starting-QB ids (`home_qb_id`, `away_qb_id`), games before the row's week only |
| `oqb_changed` | DEF | 1 when the expected starter did not start the opponent's previous game | same |
| `oqb_season_share` | DEF | the share of the opponent's games this season before this week that he started | same |

The expected starter: the first quarterback on the opponent's depth chart
published for the week who is not Out or Doubtful on the week's final
injury report and is on the opponent's 53 that week (weekly roster status
ACT or INA, INA read as ACT); if none is, last game's starter if he is; if
neither, an emergency starter (0 starts, a change, no share: 4 rows, in
2020, 2021 and 2023). Weekly depth charts through 2024 (`depth_team` in the
offense formation), the latest timestamped snapshot published for the week
from 2025 (`pos_rank`).

Missing when: never (the emergency rule covers the rows with no available
quarterback), so no `__known` indicator.

Construction check, against who started (a post-game fact used only for
this check, never as a feature): the expected starter was the actual
starter in 91.0% of the 3,051 opponent-weeks (2019 91.9%, 2020 93.5%, 2021
90.4%, 2022 89.5%, 2023 89.1%, 2024 88.2%, 2025 94.6%), and in 49.9% of the
359 games where the opponent's starter changed (36-74% a season). The block
marks 300 changes, 179 of them real. The weekly depth charts lag: the chart
labeled week w names the previous game's starter more often than week w's
(2019: 94.7% against 89.1%), so a benching or an injury announced late in
the week is often missed; the injury report and the roster catch most of
the rest.

Shared code: `scripts/research/blocks/_p2b_events_ramps_defense.py`
(`availability`, `depth_by_season`). The helper loads depth charts and
injury reports one season at a time: `Context.depth_charts` over 2019-2025
drops 2025 and `Context.injuries` over 2019-2025 returns 2025 alone (runner
loader bugs, reported, not changed here).

On the 3,051 DEF rows of 2019-2025 (31 teams in 2019, by design):
`oqb_changed` is 1 on 300 rows (2019 23, 2020 28, 2021 48, 2022 42, 2023
50, 2024 58, 2025 51); `oqb_starts32` is 0 on 45 rows, under 8 on 392 and at
the cap on 1,930; `oqb_season_share` is 0 on 108 rows.

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds on the defense table
  (`block_runner.py`, pairwise among all defenses in a week, as
  `k_def_backtest.py` does).
- Bar: better than the shipped defense set on pairwise AND MAE in every
  fold.
- Coverage seen before registering (no scores):
  - `oqb_starts32`, `oqb_changed`, `oqb_season_share`: 100% every season
    (2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%)
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result
