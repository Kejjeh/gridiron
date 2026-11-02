# DEF-12: return-unit quality, the team's kick and punt returns (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score)
and feature-only summaries (counts, distributions, the prior strengths on
2012-2018). No actual points were read.

## Idea
Roadmap DEF-12, "Return-unit quality" (the roadmap's note: small), from
play-by-play returns. The defense's fantasy points include special-teams
touchdowns (6 each); return yards score nothing. The shipped DEF set has no
special-teams column at all (`dst_ppg_*` carry past return touchdowns,
mixed with everything else). Return touchdowns are rare, 9 to 21 kick and
punt return touchdowns a season in 2019-2025, under one per team-season,
so the block also carries return yards over the league average, a steadier
measure of the same units.

## Hypothesis
A team with better kick and punt returners scores more return touchdowns,
so its defense scores more than the shipped set says. Yards per return are
efficiency, so they are shrunk hard toward the league (143 returns, about
two seasons; rule #6) and last season enters as its own column; return
touchdowns enter as counts per game, this season and last.

## Features (`COLUMNS`)
| column | position(s) | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `ret_yoe` | DEF | this team's kick and punt return yards minus its returns times the league's average for the kind (kick, punt) to date, over its returns plus 143; a return has a named returner, no touchback and no fair catch; its games this season before the row's week | nflverse play-by-play (`return_yards`, `kickoff_returner_player_id`, `punt_returner_player_id`) |
| `ret_yoe_prior` | DEF | the same over its whole previous season, against that season's league averages | same |
| `ret_td_pg` | DEF | its kick and punt return touchdowns (a kickoff or punt play with `return_touchdown`, `td_team` the receiving team) per game, this season before the row's week | play-by-play (`return_touchdown`, `td_team`) |
| `ret_td_pg_prior` | DEF | the same per game over its previous season | same |

Kick returns per season 2019-2025: 938, 992, 1,101, 1,011, 587, 919, 2,076
(the kickoff rules changed in 2023, 2024 and 2025), averaging 22.5, 22.5,
22.2, 22.8, 23.0, 27.6 and 25.9 yards; punt returns 876, 775, 878, 951, 975,
873, 827, averaging 7.6 to 10.2. Yards are measured against the same
season's league average for the kind, so the rule changes move no team.
Return touchdowns: kickoffs 7, 7, 9, 6, 4, 7, 6; punts 7, 8, 2, 3, 8, 6,
15. The prior strength is the method of moments on the 2012-2018
team-seasons (kick and punt returns pooled, each against its kind's league
average), seasons no fold scores, from the play-by-play alone.

Missing when: never on these rows (from week 4 every team has returned a
kick; every team has a previous season). No `__known` indicator.

Shared code: `scripts/research/blocks/_p6_kicker_defense_pbp.py`
(`returns`, `per_team_game`, `league_as_of`). On the 3,051 defense rows the
within-season SD is 0.34-0.48 yards (`ret_yoe`) and 0.40-0.54
(`ret_yoe_prior`); `ret_td_pg` averages 0.009-0.040 a season and
`ret_td_pg_prior` 0.017-0.029, zero on most rows.

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds on the defense table
  (`block_runner.py`, pairwise among all defenses in a week, as
  `k_def_backtest.py` does).
- Bar: better than the shipped defense set on pairwise AND MAE in every
  fold.
- Coverage seen before registering (no scores):
  - `ret_yoe`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `ret_yoe_prior`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `ret_td_pg`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `ret_td_pg_prior`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result
