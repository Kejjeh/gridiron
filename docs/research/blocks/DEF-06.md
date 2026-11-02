# DEF-06: the opponent's offensive-line regulars out this week (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score)
and counts of how often each column is nonzero (no actual points read).

## Idea
Roadmap DEF-06, "Opposing line starters out" (kind F, workstream C): the
opponent's starting offensive linemen who are Out or Doubtful this week, or
on a reserve list, counted. The roadmap takes the starters from the depth
chart; this block takes them from usage (rule #4), because the weekly depth
charts lag (DEF-02's check: the chart labeled week w names last game's
quarterback more often than this game's) and a team may move an injured
starter down its chart, which would hide exactly the absences counted here.
The shipped DEF set carries the opponent's sacks allowed and giveaways per
game this season and its implied total; neither knows who blocks this week.

## Hypothesis
A defense facing a line that is missing regulars this week gets more sacks
and pressure-driven turnovers, and allows fewer points, than the
opponent's season averages and the line say. A fact about this week's
personnel, read from the week's report and roster (volume-like, rule #6).
A Questionable regular may play hurt or not at all (rule #11): a separate
column, never folded into "out".

## Features (`COLUMNS`)
| column | position(s) | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `opp_ol_out` | DEF | the opponent's offensive-line regulars who are Out or Doubtful on this week's final injury report, or not on its 53 this week (a reserve list, the practice squad, released, traded; weekly roster status other than ACT or INA, INA read as ACT). A regular: snap-count position T, G, C, OL, OT or OG, and at least 50% of the opponent's offensive snaps over its last 3 games | snap counts (post-game, weeks before), injury reports, weekly rosters |
| `opp_ol_q` | DEF | those regulars listed Questionable on this week's final report (and not out) | same |

Missing when: the opponent has no snap history this season before the
week (never on these rows, which start in week 4). It would enter as 0
plus `__known`.

Ids only (rule #3): snap counts carry PFR ids, and the crosswalk maps under
2% of linemen's snap rows, so the helper maps them through nflverse's own
player table (`load_players`: gsis_id, pfr_id), which maps over 99%; an id
the crosswalk and the table map differently is dropped. Shared code:
`scripts/research/blocks/_p2b_events_ramps_defense.py` (`regulars`,
`availability`); the helper loads injury reports one season at a time
(`Context.injuries` over 2019-2025 returns 2025 alone, a runner loader bug,
reported, not changed here).

On the 3,051 DEF rows of 2019-2025 (31 teams in 2019, by design): the
opponent has 4.9 linemen regulars on average (2 to 7). `opp_ol_out` is
nonzero on 795 rows (one regular out on 690, two on 93, three on 12;
94-134 rows a season); `opp_ol_q` is nonzero on 723 rows (one on 602, two
on 104, three or four on 17).

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds on the defense table
  (`block_runner.py`, pairwise among all defenses in a week, as
  `k_def_backtest.py` does).
- Bar: better than the shipped defense set on pairwise AND MAE in every
  fold.
- Coverage seen before registering (no scores):
  - `opp_ol_out`, `opp_ol_q`: 100% every season
    (2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%)
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result

Run 2026-11-02T03:20:17+00:00 at `99c82dd` (one scored run; `runs/DEF-06.csv`).
Pairwise (all defenses in a week) and MAE on the defense table:

| fold | shipped | DEF-06 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 60.34%, 5.242 |
| 2020 | 60.30%, 4.638 | 60.51%, 4.637 |
| 2021 | 60.28%, 4.801 | 60.31%, 4.804 |
| 2022 | 59.26%, 4.335 | 59.21%, 4.331 |
| 2023 | 60.72%, 4.835 | 60.37%, 4.843 |
| 2024 | 64.66%, 4.461 | 64.64%, 4.459 |
| 2025 | 62.01%, 4.588 | 61.91%, 4.586 |

**Fails.** Better on both metrics in 2 of 7 folds (2019, 2020); 2021 is
better on pairwise only, 2022, 2024 and 2025 on MAE only, and 2023 is worse
on both. Mean change -0.02 points of pairwise, -0.0006 MAE. A missing
lineman on the other side moves a defense's points too little, against the
noise of a defense's week, for the count to order defenses. Not shipped,
not a contender.
