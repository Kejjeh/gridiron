# HANDOFF

Current state and next step only. The running log through the Game Day,
Radar, Action Desk and publication releases moved to
`docs/memory/handoff_history.md` on 2026-10-01 (provenance, not current).

## Hardening pass (2026-10-01)

An audit of the repo (holes, not features) found and closed, on this branch:

- **Cloud build.** `gridiron.carryover` now carries the model inputs and the
  shadow projections as sidecars (they were refetched every 15 minutes and
  lost on a bad upstream day); the carry cache is saved with `if: always()`
  (the cache action's own post step saved only on success, breaking the
  run chain on a failed render); the pull step's writes sit inside their
  error handling and its exit code is non-zero when a source failed; both
  workflows install from `requirements.txt` (the sync installed nothing, so
  a league-id override was silently ignored — now said on stderr); a
  `tests.yml` workflow runs smoke + the suite on every push and PR.
- **Rule #11.** Missing stat columns, an absent injury report, missing team-
  stat columns and missing roster data are UNKNOWN (NaN, said in the status),
  never 0; model inputs older than 10 days fall back to the baseline and say
  so; a lineup total that counts an unprojected starter as 0 is marked partial.
- **Silence.** The ROS block, the model loaders, the Sleeper config fallback
  and the grader now say on stderr what they used to swallow.
- **Drift.** This file trimmed; `docs/CLOUD_SYNC.md` cadence; dead `espn.py`
  removed; one `find_owner_id` in `gridiron.sleeper`; smoke imports every
  production module; tests for `fetch_record.py` and the sync CLI; the
  research scripts share one helper module.

Still open after the pass: the model-input freshness limit is a constant,
not the ingest manifest's cadence-aware gate (rule #8), and the browser
parity tests run only where Chromium and Node exist (now: CI).


## Role-change signal + reserve-list rule (2026-09-30)

Branch `claude/compassionate-shannon-yq0hw5` on top of main `8a6a3ee`.
The owner's ask: catch a starter's injury faster (a Miami backup stayed
priced as a backup for two weeks after the starter's ACL). Two things ship,
both under rule #5's bar or as a rule of the game:

- **`role_change_v1`** inside `advanced_v1`: the player's LAST game (expected
  points, opportunities, snap share) beside the trailing averages. Season-
  fold CV 2023-2025 against the shipped set: start/sit 63.72% vs 63.62%, MAE
  3.983 vs 3.997, better on both in every fold; the eligible set with the
  fewest features. Weights refit on 2023-2025 (`feature_set` `+depth+last`).
  Tested and not shipped: team-share/jump, missing teammates, reserve-list
  teammates (the depth chart already carries it), exponential averages.
  `docs/research/ROLE_CHANGE_BACKTEST.md`, `scripts/research/role_change_backtest.py`.
- **Reserve lists in ROS** (`gridiron.ros.reserve_stints`, `IR_MIN_GAMES`):
  the pull step now fetches the official weekly roster (`reserve.parquet`
  beside the other model inputs); a player on `RES`/`EXE` is 0 for the first
  four games of the stint (NFL minimum) and flagged in `ros_rankings.py`,
  the record's `ros` block (`reserve_since`) and the review; never priced
  beyond that (rule #11). The ROS methods were re-chosen on the new weekly
  rates (`ros_backtest.py --save`, see ROS_BACKTEST.md for the table).

Also checked: ridge strength (`scripts/research/ridge_strength_sweep.py`,
same folds) is flat from 1 to 40 — 63.71% to 63.74% start/sit, MAE 3.983
throughout — so 5.0 stays. Live week-5 read on fresh nflverse data: the
Miami starter is flagged `IR since wk4` with his next four games zeroed; his
backup is priced on his last game (84% snaps, 20 touches) at about 8 points
a game, RB29 — the linear weights move him, not far. The live depth chart
still lists the third back first, so "the depth chart carries the news" from
history does not hold this week.

Stack calibration (2026-10-01, `docs/research/STACK_CALIBRATION.md`): the
`stack` contender (ours + Sleeper) is now a calibrated two-stage blend —
never worse than Sleeper on ordering in any fold (2025: behind by 2 of
60,813 pairs, a tie), better on MAE in every fold (3.707 vs 3.764). It is
the first system here that is not behind Sleeper out of sample; it does not
clear the strict bar and stays a contender until the shoot-out says so.
`AdvancedContext.stacked()` now feeds its own advanced mean as `adv`.

More data (2026-10-01, `docs/research/FEATURE_EXPANSION.md`): every model
is now fit on 2019-2025. Six training seasons beat two on both metrics in
every held-out season for the weekly ridge; K 57.3% / DEF 62.0% start/sit
on 2025 (were 56.8 / 61.5); ROS methods re-chosen on seven folds (QB/RB/TE/K
learned combination, WR advanced rate, DEF schedule-nudged). Six new metric
blocks (xTD and team share, EPA, more NGS, spread/wind/temp, team pace, PFR)
are built and fetched but none cleared the every-fold bar — nothing reads
them for a number. The `stack` contender is now the MIX (mean of
Sleeper + half our residual ridge, and the two-stage ridge), calibrated and
composed into one ridge: ahead of Sleeper on ordering in 6 of 7 seasons
(64.68% vs 64.61%; 2020 behind by 0.18) and on MAE in 7 of 7 (3.862 vs
3.971) — the strongest result here, recorded `bar_cleared: false`
(`scripts/research/blend_search.py`). Three-fold "ties" did not survive. The owner then asked to beat Sleeper in
EVERY season: nested blend weights, knob selection on inner folds, residual-
model structure, and the role/reserve features were all tried on seven folds
(STACK_CALIBRATION.md, "Trying to win every season"); 2020 stays behind by
0.12-0.19 under every untuned variant. Six of seven is the verified state. Routes run (participation x pbp,
all seven seasons) was then built and tested as the last new input: no gain
(FEATURE_EXPANSION.md). The remaining levers are not in public history:
player props, or the live shoot-out.

Non-linear learner (2026-10-01, owner-approved scikit-learn, research only):
gradient-boosted trees lose to the ridge on ordering in every fold and every
configuration (`docs/research/GBM_BACKTEST.md`); nothing ships, serving
never imports it.

Not done / next: the live shoot-out is the scoreboard for the new weights
and the calibrated stack (first graded week: 5). Honest ceiling on history:
a linear model of public inputs fit on seven seasons; the calibrated blend
with Sleeper beats it on error and trails it by a tenth of a point on
ordering. Remaining levers: a new input (player props, routes run — both
projects), and the live shoot-out.


## Weekly routine — skills, usage trends, grading (2026-09-30, draft)

Branch `claude/compassionate-shannon-yq0hw5` (fast-forwarded to main
`012a4aa`, no history rewritten), draft PR, not merged or deployed. What the
owner and Claude did by hand on 09-30 — pull the latest record, compare an
outside ranking image to the roster and free agents, look at the players the
owner named, then set projections aside and read actual usage — is now four
skills (`.claude/skills/`, rule #12's names) over three scripts, and the
record itself carries the usage. Detail: `docs/memory/weekly_routine.md`.

- `gridiron.trends` + record key `usage`: last 4 weeks of points, snap share,
  opportunities, targets, target share, carries per rostered/available
  player, and a volume-only trend label (rule #6). Record-only; no
  projection, gate or page changes.
- `scripts/weekly/fetch_record.py` (latest cloud artifact, read-only),
  `scripts/weekly/weekly_review.py` (the weekly read, incl. `--ranks` CSVs
  transcribed from screenshots and `--watch`), `scripts/weekly/grade_week.py`
  (`grade_archive` every week; aggregate ledger `data/ledger/grades/`).
- `gridiron.external_ranks`: the one name-to-id boundary (outside lists
  carry names only); exact normalised name + position against roster and
  available pool, ambiguity resolves to nobody. Rule #3 carve-out recorded
  in DECISIONS.
- First real grade (week 3): projection direction 8/24 on graded
  comparisons, 0 endorsed (all withheld/unverified), roster MAE 7.44. One
  week; not a reason to retune (rule #5).

Pending review: Astra. Next candidates, not built: show the trend chip on
the Board's roster rows; grade weekly in the cloud workflow.

Projection research (same branch): `docs/research/PROJECTION_BACKTEST_2025.md`,
`scripts/research/projection_backtest.py`. On 2025 (5,425 player-weeks, out
of sample), Sleeper's weekly projections ordered start/sit pairs correctly
65.2% of the time vs this repo's baseline 62.4% (MAE 3.77 vs 4.03), ahead in
15 of 17 weeks; the historical endpoint may carry post-game revisions, so the
next step is a forward shadow test (record Sleeper's pre-kickoff projection,
grade weekly) before the page uses it (rule #5).

Live Sleeper comparison (same branch): `gridiron.shadow` captures Sleeper's
weekly projections in the pull step (best effort, outside the manifest,
gates nothing), the record carries them as `shadow` with a pre-kickoff flag
per player, `grade_week.py` scores baseline / Sleeper / blend on the same
players every week (ledger columns `*_pairwise`, `*_mae`), and the weekly
review shows Sleeper's number beside ours. Advanced-stats research:
`docs/research/ADVANCED_STATS_BACKTEST_2025.md` — ours + advanced stats 64.0%
start/sit vs baseline 63.1%, Sleeper 65.4%, stack 65.5% (2025, out of sample).
Done since: `advanced_v1` promoted through the rule #5 gate and drives the
page from week 4 (inputs fetched by the pull step outside the manifest;
baseline + reason when missing); the stack and the old baseline are live
contenders in the record; the shoot-out grades page / baseline_v1 / Sleeper
/ blend / stack weekly. Practice-report features tested and dropped;
depth-chart rank kept. Kickers and team defenses joined `advanced_v1`
(`docs/research/K_DEF_BACKTEST_2025.md`; registered as `advanced_v1_k` /
`advanced_v1_def`): K 56.8% start/sit vs baseline 53.6% and Sleeper 54.2%;
DEF — which had no projection — 61.5% vs points-per-game 54.0% and Sleeper
61.0% (2025, out of sample). DEF is scored by `scoring.defense_points`
(95% of 2025 team-weeks within 1 pt of Sleeper). FA defenses now enter the
radar pool; `grade_week.py` grades K and DEF in their own shoot-outs.
Rest-of-season rankings (`gridiron.ros`, registered `ros_v1`): every
position ranked by points from the next week through week 17 (playoffs
15-17 on their own), method per position chosen by season-fold CV over
2023-2025 (`docs/research/ROS_BACKTEST.md`; points-per-game was weakest
everywhere). In the record as `ros`, in `weekly_review.py`, and as full
tables from `scripts/weekly/ros_rankings.py`.

