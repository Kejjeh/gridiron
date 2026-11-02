# DEF-05: this defense's pass rush against the opposing line's protection (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only runs
before this were `--coverage-only` (coverage and the leak check, no score).
The other checks looked at the features' own distributions and at how much
of each team rate is signal rather than sampling noise; none read points.

## Idea
Roadmap DEF-05, "Pass rush vs opposing line", from participation pressure
(to 2025) and play-by-play. The shipped defense set carries `sacks_pg` (this
defense's sacks per game) and `opp_sacks_allowed_pg` (the opponent's sacks
allowed per game), both season to date. What is new here:
- **Pressure.** About 30% of dropbacks are pressured, against 14–15% with a
  sack or a hit, so it is a wider read of the rush. It comes as last
  season's tendency.
- **Rates per dropback.** They do not move with how often a team passes.
- **QB hits** next to sacks.

## Hypothesis
A pass rush that pressured more than the league last season, and is getting
home this season, against a line that allowed more, should produce more
sacks and turnovers, so more defense points, than per-game sack counts
imply. Pressure is a team tendency, so it enters slowly (rule #6): last
season's rate, and this season's rate to date shrunk toward the league by
how little of it is signal after a few games.

## Features (`COLUMNS`)
| column | position | definition, as of the weeks before the row's week | source |
|---|---|---|---|
| `press_gen_prior` | DEF | this defense's share of the regular-season dropbacks it faced in season s−1 that participation marks pressured, among those where pressure is recorded; minus the league's share that season | nflverse participation `was_pressure` × play-by-play dropbacks |
| `sackhit_gen_std` | DEF | this defense's dropbacks faced with a sack or a QB hit, this season's games before the week: (x − n·p) ÷ (n + 400), with n the dropbacks it faced, x those with a sack or hit, p the league's share over the same weeks. The rate shrunk toward the league's with 400 pseudo-dropbacks, minus the league's | play-by-play `qb_dropback`, `sack`, `qb_hit` |
| `opp_press_allowed_prior` | DEF | the opponent offense's pressured share of its own dropbacks in s−1, minus the league's share | participation × play-by-play |
| `opp_sackhit_allowed_std` | DEF | the opponent offense's sack-or-hit share of its own dropbacks, this season before the week, the same way with 150 pseudo-dropbacks | play-by-play |

A dropback is `qb_dropback == 1` (passes, sacks, scrambles); a sack that is
also a hit counts once. These are QB-05's four team quantities, read from
the defense's side.

**The shrinkage** is set from the rates themselves, never from points:
k = p(1 − p) ÷ τ², with τ² the between-team variance of full-season rates
left after the sampling variance (method of moments, 2018–2025). Defense,
sack or hit generated: τ = 0.018, k ≈ 390. Offense, sack or hit allowed:
τ = 0.028, k ≈ 160. Rounded to 400 and 150. A defense's rush is the noisier
of the two: three games of it (about 110 dropbacks faced) are about a fifth
signal. The prior-season
pressure rates are not shrunk: every team has a full season of dropbacks,
so shrinking would only rescale them.

Missing when: never for the 2019–2025 rows. A missing value would enter as 0
plus `<col>__known`.

Real share: participation records pressure on 89.0% (2018), 89.2% (2019),
89.6% (2020), 89.2% (2021), 88.7% (2022) and 100% (2023, 2024) of
regular-season dropbacks, the seasons the priors read. (DATA_INVENTORY.md's
84–85% counts play-by-play `pass == 1` plays, a wider denominator.) The
prior-season rates rest on those plays. Every rate is centered within its
season (the league's pressure rate is 28.5–31.2%), so the 2023 change of
source cannot shift the feature. Sack-or-hit is play-by-play, 100% of
dropbacks.

Shared builders: `scripts/research/blocks/_p4_participation_priors.py`
(package P4). Participation is joined to regular-season play-by-play on
(game, play), so participation's playoff plays never enter.

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds (`block_runner.py`, on the
  `k_def_backtest.py` defense tables).
- Bar: better than the shipped defense set (`DEF_ADV`) on pairwise (among
  all defenses in a week) AND MAE in every fold. The frozen 2019 defense
  table has 31 teams: it has no Raiders rows, for the shipped set and the
  block alike.
- Coverage seen before registering (no scores):
  - `press_gen_prior`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `sackhit_gen_std`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `opp_press_allowed_prior`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - `opp_sackhit_allowed_std`: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board. Live in 2026 the
priors read 2025 participation, which is published; 2027 needs nflverse's
2026 participation.

## Result

Run 2026-11-02T03:53:01+00:00 at `eeaf832` (one scored run; `runs/DEF-05.csv`).
Pairwise (among all defenses in a week) and MAE on DEF rows:

| fold | shipped | DEF-05 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 59.98%, 5.246 |
| 2020 | 60.30%, 4.638 | 60.81%, 4.643 |
| 2021 | 60.28%, 4.801 | 60.05%, 4.841 |
| 2022 | 59.26%, 4.335 | 59.69%, 4.319 |
| 2023 | 60.72%, 4.835 | 60.46%, 4.792 |
| 2024 | 64.66%, 4.461 | 65.45%, 4.438 |
| 2025 | 62.01%, 4.588 | 61.63%, 4.592 |

**Fails.** Better on both metrics in 2 of 7 folds (2022, 2024); mean change
+0.09 points of pairwise, -0.0048 MAE. (The defense folds have no pooled
numbers; the JSON's `pooled_not_worse_every_fold: true` is vacuous.) Pressure
and sack-or-hit rates per dropback, for this rush and for the line it faces,
move the defense numbers by -0.37 to +0.79 points of pairwise and -0.043 to
+0.040 of MAE, but in no consistent direction: ordering is better in three
folds and worse in four, error better in four and worse in three. Over the
per-game sack counts already in the set, they read as noise on 397 to 448
defense-weeks a fold. Not shipped, not a contender.
