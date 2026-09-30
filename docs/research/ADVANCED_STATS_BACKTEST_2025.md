# Can NFL advanced stats beat Sleeper? (2026-09-30)

Short answer: **not yet on history — they improve this repo's model, and
stacked with Sleeper they tie it.** Advanced stats lift our start/sit
success rate from 63.1% to 64.0% out of sample; Sleeper sits at 65.4%; our
model + advanced stats + Sleeper reaches 65.5%. Sleeper's historical records
may carry post-game revisions (docs/research/PROJECTION_BACKTEST_2025.md), so
the live shadow test now running is the real scoreboard.

## Method

`scripts/research/advanced_model_backtest.py --train 2023 2024 --test 2025`.
Per-position ridge regressions trained on 2023-2024, scored on 2025 weeks
4-18 (4,798 player-weeks, 60,813 same-position start/sit pairs among players
Sleeper projected >= 5). Every feature for week *w* uses weeks before *w*
only. Public data only (nflverse via nflreadpy; Sleeper's projections
endpoint). Scoring is the league's half-PPR (`gridiron.scoring`).

Advanced inputs:

- **Expected fantasy points** (nflverse `ff_opportunity`): what a player's
  targets, carries and field position are worth on average — volume and
  red-zone role with touchdown luck removed. Last 3 games and season.
- **Points over expectation**, season to date (efficiency; the regression
  decides how much to trust it — rule #6).
- **Snap share, target share, air-yards share, carries** (last 3 games).
- **Next Gen Stats**: receiver separation and YAC over expectation; rushing
  yards over expectation per attempt; QB completion % over expectation.
- **Implied team total** from the posted line.
- **Opponent**: points the defense allowed to the position in earlier weeks,
  relative to the league.
- **Vacated opportunity**: teammates on the final injury report as Out or
  Doubtful, their recent targets + carries, times this player's share.

## Results (2025, out of sample)

| system | MAE | start/sit pairs | close calls (<= 3 pts) | Spearman |
|---|---|---|---|---|
| baseline (ours today) | 4.01 | 63.1% | 54.7% | 0.342 |
| **adv** — ours + advanced stats, no Sleeper | 3.98 | **64.0%** | 55.4% | 0.364 |
| sleeper | **3.77** | 65.4% | 57.2% | 0.395 |
| blend (ours + sleeper) / 2 | 3.80 | 65.0% | 56.5% | 0.391 |
| **stack** — adv + sleeper | 3.89 | **65.5%** | **57.4%** | **0.402** |

By position (start/sit): QB adv 60.3% vs Sleeper 60.3%, stack 61.0%;
RB 66.5 / 68.2 / 68.0; WR 63.8 / 65.5 / 65.4; TE 61.1 / 62.2 / 63.0.
Stack beat Sleeper in 9 of 15 weeks; adv in 4 of 15.

What the models lean on (standardised weights): expected fantasy points and
snap share dominate QB; our baseline, snap share and points-over-expected
dominate RB; target share, snap share and vacated opportunity carry WR/TE;
NGS efficiency adds a little at every position. With Sleeper available the
regression gives it the largest weight everywhere.

## Reading it honestly

- The advanced-stat model beats our current baseline out of sample
  (+0.9 points of start/sit, lower MAE) — a real, if small, improvement.
- It does not beat Sleeper on its own. Sleeper encodes information a
  box-score model does not see: news timing, depth-chart moves, coaching
  signals — and possibly some hindsight in the historical records.
- Stacking ties Sleeper on history; if Sleeper's history is inflated by
  post-game edits, the stack may lead live. Only the forward shadow test
  (`gridiron.shadow`, graded weekly by `grade_week.py`) can say.
- Its MAE is worse than Sleeper's (3.89 vs 3.77) although its ordering is as
  good: ordering is what start/sit needs, but a calibration step would be
  required before its point numbers are shown.

## Where an edge over Sleeper could still come from

1. **News timing we can see**: final practice participation (DNP / limited),
   depth-chart changes (`load_depth_charts`), and game-day inactives —
   features the box scores lack and Sleeper updates for.
2. **Better use of what we have**: a non-linear model (gradient boosting)
   would capture interactions (a vacated role matters more for a high-snap
   player); that needs a new dependency and the owner's approval.
3. **Markets**: player props are the one source with a plausible edge in the
   literature, but free access is scraping and the published evidence is thin.

## Recommendation

1. Keep the live shadow running (built on this branch); after 3-4 weeks the
   shoot-out says whether Sleeper's edge survives without hindsight.
2. Promote the **adv** features into the production projection through the
   rule #5 gate (they beat the full baseline out of sample on 2025), then
   add **stack** as a second shadow system beside Sleeper.
3. Whichever of baseline / Sleeper / stack wins the live shoot-out drives
   the page's comparisons; usage trends, freshness gates and lock logic stay.
