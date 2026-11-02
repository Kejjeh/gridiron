# DEF-11: this defense's own regulars out this week, front and secondary (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score)
and counts of how often each column is nonzero (no actual points read).

## Idea
Roadmap DEF-11, "Own key defenders out" (kind F): this defense's starters
who are Out, Doubtful or on a reserve list this week, counted, with edge
rushers and cornerbacks perhaps separately. Starters come from usage
(rule #4), as in DEF-06, not the depth chart. Edge rushers cannot be told
apart in the snap counts' labels (2025 writes DL, LB, S and DB for whole
groups), so the split is the front seven against the secondary. The
shipped DEF set carries the defense's own points per game (season, last 3),
sacks and takeaways per game and points allowed per game: averages over
games its regulars played. The opponent's implied total may price some
absences, likely the stars'.

## Hypothesis
A defense missing regulars this week scores fewer fantasy points than its
season averages say: fewer sacks when the front is short, more points
allowed and fewer interceptions when the secondary is. Questionable
regulars may play at less than full strength or not at all (rule #11:
questionable is not out), a third, separate column. Facts about this
week's personnel (volume-like, rule #6).

## Features (`COLUMNS`)
| column | position(s) | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `own_front_out` | DEF | the defense's front-seven regulars (snap-count position DE, DT, NT, DL, LB, ILB, OLB or MLB in the latest of the window's games) who are Out or Doubtful on this week's final injury report, or not on its 53 this week (a reserve list, the practice squad, released, traded; weekly roster status other than ACT or INA, INA read as ACT). A regular: at least 50% of the defense's snaps over its last 3 games | snap counts (post-game, weeks before), injury reports, weekly rosters |
| `own_back_out` | DEF | the same for its secondary regulars (CB, S, FS, SS or DB) | same |
| `own_def_q` | DEF | its regulars of either group listed Questionable on this week's final report (and not out) | same |

Missing when: the defense has no snap history this season before the week
(never on these rows, which start in week 4). It would enter as 0 plus
`__known`.

Ids only (rule #3): snap counts map PFR ids to gsis ids through the
crosswalk, then nflverse's player table for the ids it lacks; an id the two
map differently is dropped. Shared code:
`scripts/research/blocks/_p2b_events_ramps_defense.py` (`regulars`,
`availability`); the helper loads injury reports one season at a time
(`Context.injuries` over 2019-2025 returns 2025 alone, a runner loader bug,
reported, not changed here).

On the 3,051 DEF rows of 2019-2025 (31 teams in 2019, by design): a defense
has 10.6 regulars on average (6 to 15). `own_front_out` is nonzero on 636
rows (one out on 545, two on 83, three or four on 8); `own_back_out` on 875
rows (one on 729, two on 122, three on 24); `own_def_q` on 1,243 rows (one
on 882, two on 250, three or more on 111).

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds on the defense table
  (`block_runner.py`, pairwise among all defenses in a week, as
  `k_def_backtest.py` does).
- Bar: better than the shipped defense set on pairwise AND MAE in every
  fold.
- Coverage seen before registering (no scores):
  - `own_front_out`, `own_back_out`, `own_def_q`: 100% every season
    (2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%)
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result

Run 2026-11-02T03:20:42+00:00 at `f7a8b6a` (one scored run; `runs/DEF-11.csv`).
Pairwise (all defenses in a week) and MAE on the defense table:

| fold | shipped | DEF-11 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 60.19%, 5.248 |
| 2020 | 60.30%, 4.638 | 60.13%, 4.638 |
| 2021 | 60.28%, 4.801 | 60.50%, 4.786 |
| 2022 | 59.26%, 4.335 | 60.05%, 4.309 |
| 2023 | 60.72%, 4.835 | 60.50%, 4.841 |
| 2024 | 64.66%, 4.461 | 65.35%, 4.447 |
| 2025 | 62.01%, 4.588 | 61.70%, 4.584 |

**Fails.** Better on both metrics in 3 of 7 folds (2021, 2022, 2024); 2019,
2020 and 2025 are better on MAE only (2019 and 2020 by under 0.001), and
2023 is worse on both. Mean change +0.14 points of pairwise, -0.0074 MAE.
The defense's own absences lower its error in six of seven folds and lift
the order by 0.7-0.8 points in 2022 and 2024, but they order defenses worse
in four folds, so by the pre-registered bar DEF-11 is not a contender and
is not shipped.
