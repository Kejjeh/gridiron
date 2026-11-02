# DEF-04: the opposing starting QB's sack rate and time to throw (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score).
The other checks looked at the features' own distributions, at how much of a
QB's sack rate is signal rather than sampling noise, and at who the block
names as the starter against who led the game in dropbacks. None read
points.

## Idea
Roadmap DEF-04, "Opposing QB's sack rate and time to throw", from NGS and
play-by-play. The shipped defense set carries `sacks_pg` (this defense's
sacks per game) and `opp_sacks_allowed_pg` (the opponent offense's sacks
allowed per game), both season to date. Both are per game, which mixes in
pass volume, and both are team level, which mixes in every QB the opponent
has used. This block reads the quarterback expected to start this week (a
backup or a new starter brings his own sack rate), plus his time to throw
from NGS, which nothing in the defense set has. FEATURE_EXPANSION's `+ngs2`
tested time to throw as the QB's own feature on QB rows, never for the
defense facing him.

## Hypothesis
A defense facing a QB who is sacked on more of his dropbacks, and who holds
the ball longer, gets more sacks and turnovers, so more points, than per-game
team sack counts say. That should matter most when the opponent's starter
changes. Both are QB tendencies, read season to date and shrunk toward the
league by how little of a few games is signal (rule #6).

## Features (`COLUMNS`)
| column | position | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `opp_qb1_sack_rate_std` | DEF | the opponent's expected starter (below): his sacks per dropback as the play's passer (`passer_id`, any team), this season's games before the week, (x − n·p) ÷ (n + 200), with p the league's rate over the same weeks. The rate shrunk toward the league's with 200 pseudo-dropbacks, minus the league's | play-by-play `qb_dropback`, `sack`, `passer_id` |
| `opp_qb1_ttt_std` | DEF | his mean NGS `avg_time_to_throw`, weighted by attempts, over his weekly rows this season before the week, shrunk toward the league's attempt-weighted mean over the same weeks with 50 pseudo-attempts, minus the league's. Only rows with week ≥ 1 are read; the week-0 rows are season totals that include later weeks | Next Gen Stats weekly passing |

**The expected starter** comes from pre-game facts only. It is the
best-ranked QB on the opponent's depth chart published for the week (weekly
`depth_team` through 2024; in 2025 the latest snapshot the Context assigns to
the week) who meets both of these:
- he is not listed Out or Doubtful on the week's final injury report;
- he is not on a reserve list on the week's roster (RES, EXE). INA, the
  game-day inactive list, is never read.

A team-week with no chart, no such QB, or two QBs sharing the best rank is
left unknown. The roadmap row and the task named "this week's depth chart
QB1". Checked against the game's leading passer by dropbacks (play-by-play,
no points), the chart's QB1 alone matched in 84.4% of the 2019–2025 rows.
With the two rules it matches in 88.9%: 89.9%, 91.6%, 88.8%, 87.7%, 86.2%,
85.3% and 93.1% for 2019–2025. In the remaining 11% the chart was stale, or
the starter left mid-game or was benched, which no pre-game source shows.

**The shrinkage** is set from the inputs themselves, never from points:
- **Sack rate:** over QB-seasons with 150+ dropbacks (2018–2025), a
  between-QB spread of τ = 0.017 is left after sampling noise, so
  k = p(1 − p) ÷ τ² ≈ 200.
- **Time to throw:** over the NGS weekly rows of 2019–2025, a QB's game
  means vary by 0.054 s² around his season mean, at about 33 attempts a
  game, and τ = 0.19 s is left between QBs. So k = σ² ÷ τ² ≈ 51
  attempts, rounded to 50, about a game and a half. A three-game mean is
  then about two-thirds signal.

Missing when:
- the opponent has no qualifying starter for the week (0.1% of rows);
- he has no dropback this season before the week (the sack rate);
- he has no NGS weekly row before the week (time to throw). NGS lists a
  passer's game only from 15 attempts.

Missing enters as 0 plus `<col>__known`.

Real share: nothing comes from participation. NGS weekly rows exist for the
leading passer of 99.1% of 2024's team-games.

Shared builders: `scripts/research/blocks/_p4_participation_priors.py`
(package P4). The Context has no NGS loader, so the helper reads NGS through
the Context's own frame store under the post-game `weekly` classification;
the runner's leak-check cut applies to it like any other post-game frame.
The helper reads depth charts, injuries and rosters one season per Context
call: a multi-season call that includes 2025 drops seasons (2025 alone for
injuries, no 2025 for depth charts; runner bug, in the final report).

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds (`block_runner.py`, on the
  `k_def_backtest.py` defense tables).
- Bar: better than the shipped defense set (`DEF_ADV`) on pairwise (among
  all defenses in a week) AND MAE in every fold. The frozen 2019 defense
  table has 31 teams: it has no Raiders rows, for the shipped set and the
  block alike.
- Coverage seen before registering (no scores):
  - `opp_qb1_sack_rate_std`: 2019 99%, 2020 100%, 2021 99%, 2022 99%, 2023 99%, 2024 99%, 2025 100%
  - `opp_qb1_ttt_std`: 2019 99%, 2020 96%, 2021 97%, 2022 98%, 2023 97%, 2024 97%, 2025 98%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board. Every input is
live in 2026.

## Result

Run 2026-11-02T03:52:29+00:00 at `100d175` (one scored run; `runs/DEF-04.csv`).
Pairwise (among all defenses in a week) and MAE on DEF rows:

| fold | shipped | DEF-04 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 60.42%, 5.247 |
| 2020 | 60.30%, 4.638 | 60.11%, 4.646 |
| 2021 | 60.28%, 4.801 | 60.20%, 4.803 |
| 2022 | 59.26%, 4.335 | 59.59%, 4.337 |
| 2023 | 60.72%, 4.835 | 60.92%, 4.825 |
| 2024 | 64.66%, 4.461 | 64.64%, 4.473 |
| 2025 | 62.01%, 4.588 | 62.06%, 4.596 |

**Fails.** Better on both metrics in 2 of 7 folds (2019, 2023); mean change
+0.07 points of pairwise, +0.0028 MAE. (The defense folds have no pooled
numbers; the JSON's `pooled_not_worse_every_fold: true` is vacuous.) The
opposing starter's own sack rate and time to throw add nothing reliable to
the per-game sack counts and the opponent's implied total already in the
defense set: ordering is up in four folds and down in three, and error is
up in five. Not shipped, not a contender.
