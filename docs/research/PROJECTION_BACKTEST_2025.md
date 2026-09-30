# Is there a weekly prediction system with a better success rate? (2026-09-30)

Short answer: **yes — Sleeper's own weekly projections beat this repo's
baseline on the 2025 season, by about 3 points of start/sit accuracy, in 15
of 17 weeks.** The margin is real but small, and part of the historical
number may be hindsight (see "Leakage"), so the switch should be earned by a
forward test, not by this backtest alone (rule #5).

## Method

`scripts/research/projection_backtest.py --season 2025` (reproducible; public
data only: nflverse via nflreadpy, Sleeper's projections endpoint, cached
under `data/research/cache/sleeper_proj_2025/`, gitignored).

- **baseline** — this repo's projection, predicted for week *w* from weeks
  before *w* only (`gridiron.evaluate.chronological_evaluation`).
- **sleeper** — Sleeper's published projected stat line for week *w*, scored
  with the league's own half-PPR rules (`gridiron.scoring`; mean absolute
  difference from Sleeper's own `pts_half_ppr` total 0.21).
- **ppg_to_date**, **last_week** — naive references; **blend** — mean of
  baseline and sleeper.
- Same 5,425 QB/RB/WR/TE player-weeks (weeks 2–18) for every system, joined
  by gsis id. "Pairwise" = over every pair of players at the same position in
  the same week (both projected ≥ 5 by Sleeper), the share the system ordered
  the way actual points did — the start/sit success rate. "Close" = pairs
  whose Sleeper projections are within 3 points.

## Results

| system | MAE | pairwise | close calls | Spearman |
|---|---|---|---|---|
| baseline (ours) | 4.03 | 62.4% | 54.1% | 0.327 |
| ppg_to_date | 4.09 | 61.8% | 53.0% | 0.302 |
| last_week | 4.99 | 57.1% | 51.3% | 0.183 |
| **sleeper** | **3.77** | **65.2%** | **57.1%** | **0.390** |
| blend | 3.80 | 64.6% | 56.0% | 0.382 |

69,409 pairs (34,661 close calls). By position (pairwise, baseline → sleeper):
QB 58.6 → 60.3, RB 66.3 → 68.3, WR 61.5 → 65.1, TE 60.0 → 61.8. The blend
has the best MAE at RB (5.36) and TE (4.35).

Per-week: Sleeper's pairwise edge over the baseline was positive in 15 of 17
weeks (range −0.3 to +8.2 points).

## Leakage

Every stored Sleeper projection record was last modified 1–22 days AFTER its
game, and for regular starters (≥ 60% snaps over their prior games) who then
played ≤ 25% of snaps, Sleeper's stored projection averaged 5.4 points
against the baseline's 9.5 (actual 2.7), while for normal games the two
agree (11.95 vs 11.38). Part of that may be legitimate pre-game news (a
questionable tag); part may be post-game revision. Dropping every row where
snaps fell ≥ 25 points below the player's recent norm (262 rows) leaves the
result unchanged: sleeper 65.6% / baseline 62.6% pairwise, MAE 3.74 / 3.95.
So the edge is not explained by that channel — but it cannot be proven
leak-free from the historical endpoint. Only projections captured BEFORE
kickoff can prove it.

## What the published evidence says (web research, 2026-09-30)

- The strongest multi-season evidence (Fantasy Football Analytics, 11
  seasons of weekly projections, 9 sources) is that **an average of sources
  beats any single source** (63% of weekly head-to-heads), but the top
  sources differ by only ~0.01–0.03 points of MAE, and the leader changes
  season to season.
- Achievable weekly accuracy is low everywhere: best published weekly MAE
  among fantasy-relevant players ≈ QB 6.2, RB 5.2, WR 4.9, TE 3.9, with R²
  ≈ 0.1–0.25. This repo's baseline on the same kind of pool: QB 6.30,
  RB 5.48, WR 5.05, TE 4.37 — near the published range, not far behind.
- Betting-market (player prop) projections: plausible, but the only direct
  comparison found is one vendor's 13-week test against one rival (MAE 4.76
  vs 4.84, start/sit tied). No independent evidence they beat consensus.
- No published, out-of-sample result shows a machine-learning model beating
  consensus weekly projections.
- FantasyPros grades rankings, not projections; its expert "accuracy %"
  clusters at 64–65% for the top 10 and is not a hit rate.

Sources: fantasyfootballanalytics.net (2026-09 weekly/DFS study; 2026-08
seasonal study; 2016 crowd-vs-paid and projections-vs-rankings),
fantasypros.com in-season accuracy methodology and 2024 results,
parlaysavant.com (2025-12), subvertadown.com (position predictability).

## Recommendation

1. **Forward shadow test (next):** record Sleeper's projection for every
   rostered and available player in each decision record, captured before
   kickoff, and grade baseline vs Sleeper vs blend every week with
   `scripts/weekly/grade_week.py`. Three to four weeks is enough to see
   whether the ~3-point pairwise edge survives without hindsight.
2. **If it holds:** feed the page's lineup and waiver comparisons from the
   blend (or Sleeper alone for WR/QB ordering), keeping this repo's usage
   trends, freshness gates and lock logic — those are what the outside
   projections do not provide. Rule #5 is satisfied by the forward result.
3. **Later, if wanted:** a multi-source consensus (FantasyPros consensus,
   ESPN, Sleeper) is the best-evidenced system in the literature, but each
   extra source is scraping or a paid feed; start with the free one that
   already won here.
