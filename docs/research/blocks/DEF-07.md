# DEF-07: the opponent's interception-worthy throws against its interceptions, and its fumble luck (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score),
feature-only summaries (counts, distributions, the prior strengths on
2012-2018) and a construction check of the expected quarterback against who
started (below). No actual points were read.

## Idea
Roadmap DEF-07, "Opponent interception-worthy rate vs fumble luck", from
FTN charting's `is_interception_worthy` (the free nflverse subset, 2022 on,
live in 2026) and play-by-play. The shipped DEF set carries the opponent's
giveaways per game (`opp_giveaways_pg`: interceptions plus fumbles lost,
season to date), the defense's own takeaways and sacks per game, the
opponent's sacks allowed, its implied total and the spread. Interceptions
and lost fumbles are the luck-laden part of a giveaway: in 2024, 333 of 596
interception-worthy throws were intercepted, and 54 interceptions came on
throws FTN did not call interception-worthy; an offensive fumble was lost
46% of the time in 2019-2025 (1,713 of 3,692). Nothing in the set separates
how often an opponent puts the ball in danger from how often the danger
paid off.

## Hypothesis
The opponent quarterback's interception-worthy throws predict the
interceptions a defense will get better than the interceptions he has
thrown, and the opponent's fumbles predict lost fumbles (and forced
fumbles, which score too) better than its lost fumbles. A defense facing a
quarterback who has been lucky, or an offense that fumbles often but has
recovered them, scores more than the shipped set says. Throw quality is
efficiency, so it is shrunk hard toward the league (1,091 throws, about two
seasons; rule #6); fumbles per game are counts.

## Features (`COLUMNS`)
| column | position(s) | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `oqb_intworthy_oe` | DEF | the opponent's quarterback (the player with the most throws for the opponent in its latest game before the row's week): his FTN interception-worthy throws, minus his charted throws times the league's rate to date, over his throws plus 1,091; his charted throws this season before the row's week, for any team | FTN charting joined to play-by-play on (`game_id`, `play_id`); a throw is play_type pass, `pass_attempt` 1, no sack |
| `oqb_worthy_minus_int` | DEF | his interception-worthy throws minus his interceptions on the same charted throws, minus his throws times the league's (worthy − interception) rate to date, over his throws plus 1,091 | same, with play-by-play `interception` |
| `opp_fumbles_pg` | DEF | the opponent offense's fumbles (pass and run plays on which the offense fumbled, `fumbled_1_team` = `posteam`, lost or not) per game, the opponent's games this season before the row's week | nflverse play-by-play (`fumble`) |
| `opp_fumbles_lost_oe_pg` | DEF | the opponent offense's fumbles lost (`fumble_lost`) minus its fumbles times the league's lost share in the previous season, per game, same games | same |

The prior strength for throws is the method of moments (QUANT_FOUNDATIONS
§5.1) for the interception rate on the 2012-2018 passer-seasons with 150+
throws, seasons no fold scores (FTN does not exist before 2022); both
quarterback columns use it. FTN's flag drifts: interception-worthy throws
per charted throw fall from 941 of 18,027 (2022) to 743 of 18,325, 596 of
17,839 and 559 of 17,454 (2025) while interceptions hold (418, 430, 387,
380), so both quarterback columns are measured against the league's rate of
the same season to date, and the drift moves no one.

Construction check (who started is a post-game fact, used for this check
only, never as a feature): the expected quarterback started for the
opponent in 88.3% of the 3,051 defense rows (2019 92.4%, 2020 88.2%, 2021
87.5%, 2022 87.2%, 2023 89.1%, 2024 85.7%, 2025 88.6%). A starter benched
or hurt between games is missed by construction (DEF-02's depth-chart route
catches about half of those changes).

Missing when: both quarterback columns on every row of 2019-2021 (no FTN
charting). Missing enters as 0 plus `<col>__known`. The fumble columns are
never missing on these rows.

Shared code: `scripts/research/blocks/_p6_kicker_defense_pbp.py`
(`team_games`, `per_team_game`, `league_as_of`, `over_league`, `codes`).
`fumbled_1_team` is not among the runner's team columns, so the helper maps
it to today's codes itself (OAK in 2018-2019). Offensive fumbles per season
2019-2025: 544, 507, 551, 544, 545, 542, 459 (lost 265, 242, 236, 252,
267, 235, 216). On the defense rows the within-season SD is 0.0020-0.0030
(`oqb_intworthy_oe`), 0.0015-0.0023 (`oqb_worthy_minus_int`), 0.30-0.42
(`opp_fumbles_pg`) and 0.17-0.20 (`opp_fumbles_lost_oe_pg`).

## Test
- Positions: DEF.
- Seasons: `FOLDS = (2022, 2023, 2024, 2025)`, the four seasons FTN charts.
  2019-2021 still train, with the quarterback columns unknown.
- Bar: better than the shipped defense set on pairwise (all defenses in a
  week, as `k_def_backtest.py` does) AND MAE in every scored fold.
- Coverage seen before registering (no scores):
  - `oqb_intworthy_oe`: 2019 0%, 2020 0%, 2021 0%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `oqb_worthy_minus_int`: 2019 0%, 2020 0%, 2021 0%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `opp_fumbles_pg`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `opp_fumbles_lost_oe_pg`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result
