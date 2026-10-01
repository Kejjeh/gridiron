# Rest-of-season rankings: which method orders players best? (2026-09-30)

**Short answer: build ROS rankings on the advanced weekly model, not on
points per game.** In season-fold cross-validation over 2023–2025, ranking
by points-per-game-so-far × games left (the usual ROS shortcut) was the
weakest or near-weakest method at every position. Advanced-model rates,
with the schedule nudge or with a learned combination, won everywhere.

## Method

Command: `PYTHONPATH=src python scripts/research/ros_backtest.py --save`.

**Folds.** Each season (2023, 2024 and 2025) is held out in turn, and the
other two train. The weekly models are refit on the train seasons only.

**Cuts.** At each cut week 4–14 of the held-out season, every method
projects each player's points from the cut through week 17:
- the last fantasy week is 17 (`gridiron.ros.HORIZON_END`);
- bye weeks come from the schedule;
- a missed game counts 0, because a ROS ranking has to live with injuries.

**Scoring.** Each projection is compared with the points the player actually
scored over those weeks.

**Universe.** Within each position and cut, the universe is the union of
every method's top N (QB 24, RB 48, WR 60, TE 24, K 20, DEF 20), among
players active at the cut.

**Metrics.**
- Rank correlation (Spearman).
- Pairwise order.
- The same measures for the playoff weeks (15–17) alone.

The per-position method was chosen by the **mean over the three folds**,
never by one test season.

**Candidates.** Each is a per-game rate multiplied by the games left:

| method | rate |
|---|---|
| `ppg` | points per game so far |
| `base` | this repo's baseline weekly projection |
| `adv` | the page's weekly number (`advanced_v1`, including K and DEF) |
| `adv_sched` | `adv`, with each future game nudged by its matchup (see below) |
| `ros_model` | a per-position ridge regression on all of the above plus games played |

`adv_sched` details:
- The nudge goes through the weekly model's own coefficients: implied total
  and points allowed to the position; dome and spread for K; the opponent's
  implied total for DEF.
- Future lines are not posted, so each week's implied total is estimated.
  The estimate is team points scored plus opponent points allowed, both
  shrunk 4 games toward the league average.

`ros_model` details:
- It is fit to actual points per remaining game.
- It is trained only on out-of-fold weekly rates.

## Results (cross-validated mean, 2023–2025)

Re-run 2026-09-30 after the weekly model gained the last-game block
(`docs/research/ROLE_CHANGE_BACKTEST.md`); the method per position was
re-chosen on the new rates.

| pos | ppg | base | adv | adv_sched | ros_model | **shipped** | gain vs ppg |
|---|---|---|---|---|---|---|---|
| QB | .494 | .509 | .504 | .520 | **.553** | ros_model | +.059 |
| RB | .661 | .646 | .672 | **.678** | .676 | adv_sched | +.017 |
| WR | .555 | .555 | .604 | **.604** | .603 | adv_sched | +.049 |
| TE | .397 | .406 | .467 | .473 | **.479** | ros_model | +.082 |
| K | .135 | .160 | .130 | **.193** | .188 | adv_sched | +.058 |
| DEF | .110 | .110 | .161 | .186 | **.187** | ros_model | +.077 |

Pairwise order, shipped method vs ppg:

| pos | shipped | ppg |
|---|---|---|
| QB | 69.9% | 68.1% |
| RB | 74.6% | 74.0% |
| WR | 71.8% | 69.3% |
| TE | 67.2% | 63.7% |
| K | 56.2% | 53.7% |
| DEF | 56.5% | 53.3% |

WR `adv` and `adv_sched` tie (.604); TE's learned combination leads the
schedule-nudged rate by .006. Both are hairline calls and could flip on
another season; the first run (before the last-game block) chose WR `adv`
and TE `adv_sched`.

Per-fold tables are saved in `src/gridiron/models/ros_weights.json` under
`evidence.folds`.

## Reading it honestly

- **RB and WR ROS order is the most predictable.** About 3 of 4 RB pairs are
  ordered right.
- **Kicker and defense ROS order is barely better than a coin.** Spearman is
  about 0.19. A ROS K or DEF ranking is a weak tiebreaker. The weekly
  streaming call (`advanced_v1` K/DEF) is where the edge is.
- **The schedule nudge helps where the weekly model leans on the matchup**
  (K, DEF, TE, RB) and is neutral for WR (a tie, resolved to it).
- **The learned combination wins at QB, TE and DEF.**
  - QB: it rewards a longer track record. Games played carries weight, a
    proxy for job security.
  - DEF: it leans on the schedule-nudged rate.
  - DEF's lead over `adv_sched` is a tie (.187 vs .186); TE's is .006.
- **Playoff-week (15–17) ordering is weaker than full-ROS ordering** at every
  position. It is shown as a separate column, not ranked on.
- **ROS is points, not ΔP(win) (rule #7).** It is for holds, drops and
  trades; the page still makes every lineup call.

## Where it lives

- **Engine:** `gridiron.ros`.
  - A week with no games in the schedule is not a bye. It is counted as
    played and stated.
  - A player ruled Out on the week's official report gets 0 for that week
    only.
  - A player on a reserve list per the official weekly roster is credited 0
    for the first four games of the stint (`IR_MIN_GAMES`, the NFL minimum
    before activation) and flagged; a return after them is never guessed
    (added 2026-09-30, `docs/research/ROLE_CHANGE_BACKTEST.md`).
  - The player map's IR tag and other statuses are flags and never change
    the number (rule #11).
- **Weights:** `src/gridiron/models/ros_weights.json`.
  - The chosen method per position.
  - The learned combination, fit on every season's out-of-fold rows.
  - The evidence.
- **Registered:** `ros_v1` in `gridiron.models.validated_signals`.
- **Cloud board:** the record's `ros` block, refreshed every build.
  - Other rosters are labelled `rostered`, never by manager, because the
    record is public.
- **Local:** `scripts/weekly/ros_rankings.py` writes
  `data/outputs/review/ros_weekNN.md` / `.csv` (gitignored).
  - It includes all positions, MINE / FA / manager, and the best free agent
    at each of the owner's positions.
