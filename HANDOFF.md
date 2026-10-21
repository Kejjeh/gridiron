# HANDOFF

Current state and next step only. The running log through the Game Day,
Radar, Action Desk and publication releases moved to
`docs/memory/handoff_history.md` on 2026-10-01 (provenance, not current).

## Free agents the radar cannot rank are named (2026-10-21)

Week 7's miss: a 2025 RB8 with no 2026 game (exempt list) was dropped by
roster #5 at 03:00 UTC Fri 16 Oct, sat in the pool for 33 builds as part of
the radar's "missing evidence" count (no box scores, so `project()`
abstains and the radar only ranks projected players), and was claimed at
the Wednesday waiver run. Fixed in three parts:

- **Notable free agents** (`gridiron.notable`, radar section `#notable`,
  record `watch.notable`): last season's starters by league ppg and this
  league's recent drops that matter this week (an empty slot counts as 0;
  never a player designated Out), each with status (Sleeper tag; NFL
  reserve list with its roster week, only from inputs young enough for the
  model), ours or why not, Sleeper's number and rank, and the waiver clock
  (`notable.waiver_clock`, from this league's own transactions and the
  real kickoffs, this week's and last): on waivers 2 days after the drop,
  then a FREE AGENT until his kickoff, then locked until the Wednesday run
  (~03:20 ET), then free again. The first-screen alert (top of the Action
  Desk) is a transition, 48 h each, read off this build's data: each change
  of that clock. The one carried comparison (`notable.PreviousWatch`) is a
  pool entry with no league drop behind it (an NFL activation), against a
  previous record under 24 h old with a pool, and only when this build can
  see the drops. Never the owner's own drop. The snapshot now carries the
  last three transaction legs (`SleeperReadOnly.recent_transactions`, never
  raises; the leg advances at Wednesday's run, a day after `state.week`).
  Last season's box scores ride in the model inputs (`prior_weekly.parquet`,
  ~0.9 MB, refreshed with them every 6 h; a failed refresh keeps the good
  copy). Until the first cloud pull after this merge, the page says the
  starter check cannot run.
- **Can't project — Sleeper's number** (`#market`, `watch.market`): every
  abstained pool player Sleeper projects and who is not known to sit,
  highest first, with our reason; the record keeps every row.
  The weekly review shows both lists.
- **Returner projection: researched, NOT shipped**
  (docs/research/RETURNER_BACKTEST.md). Pre-registered `returner_v1`
  cleared its pooled bar, but it projects last season's 10+ ppg players at
  about half their output (+2.5 bias n=41, +5.0 n=14). Sleeper beat it on
  the same rows. `returner_v2` is declared and is tested on 2026 returners
  at season end.
- Replay on the 41 real builds of 15-21 Oct (real kickoffs): the week-7
  player is on the first screen in 32 of the 33 builds he was in the pool
  (Fri 06:18 to Wed 00:04 UTC; the 04:32 build, three hours before the
  run, is past 48 h from his lock) — on waivers to Sun 03:00 UTC, an
  instant-add FREE AGENT until his Sunday-night kickoff (Mon 00:20 UTC) by
  the inferred clock, then locked until the run — with at most four alerts
  at a time.
- Two adversarial reviews (40 + 29 agents) confirmed and this PR fixed:
  the waiver model (claimed "on waivers, bid before Wed" for an instant
  free agent), two-leg transaction reads, alert transitions lost or
  invented across records (the "first listed" comparison was removed
  rather than patched: it had five ways to compare against a degraded
  record), the empty-slot, locked-bench and Out cases, and a shadowed
  `reserve` variable that kept the NFL reserve label off the page.
- **Next:** after the first cloud build, check the record's
  `watch.notes` has no "not loaded" line, and that `watch.notable` is
  short enough to read.

## Board: dated practice rows, absence is not a bye, P(win) expires (2026-10-16)

- **Availability text**: every practice value names its week
  ("wk5 practice: Limited") or says "practice (Sleeper, undated)". An
  injury body part from an older row carries its week under a live
  Sleeper tag, and Sleeper's own body part is used when it has one.
  Report coverage is per team (`weekly.reported_teams`,
  `team_report_covered`): a team that has not filed (Monday-night teams
  early in the week) reads "no current wk6 report for his team", never
  "not on the report".
- **Absence is never a bye** in the projection, matching
  `lineup.lock_state`. One rule, `weekly.game_for`: a declared bye
  (`lineup.BYE_GAME_TYPES`) is withheld at 0, and a team with no game row
  ABSTAINS. A stale schedule that carries the week still decides who has
  a game. An Out/IR designation still withholds at 0. A team defense is
  left out of P(win) and named, as any unprojected defense is.
  **Then (owner decision, same day)**: a bye the schedule PROVES counts
  too (`lineup.proven_byes`: the team's whole regular season is in the
  frame without that week). Real byes are withheld at 0 again, locks read
  OPEN 'proven', and rest-of-season counts only proven byes (any other gap
  is counted as played and named in its status). Only an unproven gap (a
  cut-off or damaged pull) is UNKNOWN. CLAUDE.md's rule says so.
- **P(win) expiry**: the margin and P(win) (the matchup block and the
  first-screen line) carry the matchup gate's own `data-valid-until`, so
  they expire on the reader's clock. `VALIDITY_JS` honours per-element
  times and raises the banner for them. They read LAST KNOWN, naming the
  stale input, when the gate is closed at build.
- Also: the first-screen free-agent count is a live counter (it falls as
  the moves lock after kickoff).

## P(win) tested: no correction, labelled TESTED (2026-10-15)

- **Step 1** (`scripts/research/pwin_calibration.py`): one spread factor
  k fit on synthetic 12-team leagues 2019-2022 and tested on 2023-2025,
  with the page's own skill mean (advanced_v1 refit leave-one-season-out).
  k = 1.15 LOST to the raw formula on the held-out seasons on both log
  loss and Brier (0.66646 vs 0.66601). The best k for the test seasons is
  1.00. The first run's "k ≈ 1.1 helps" came from the baseline mean, which
  the page never sums.
- **Step 2** (`pwin_league_history.py`): this league's 157 graded matchups,
  2023-2025, with the served mean: k_mle 1.02, and a k fit on the other
  seasons is worse held out. Favourites were given 62% and won 66% (±4).
- **Shipped**: `src/gridiron/models/pwin_calibration.json` (k 1.0,
  `raw_tested`) via `pwin_calibration_save.py`. `winprob.Calibration`
  reads it, and the page, record and Game Day label P(win) TESTED with the
  evidence. A factor applies only if `bar_cleared` (label CALIBRATED).
  Nothing tested on file means UNCALIBRATED. `tests/conftest.py` pins
  tests to the raw label.
- **Step 3**: every board records all league matchups (`league_matchups`).
  `grade_week.py` scores them (ledger `pwin_*` columns), and the report
  card shows the season to date. Week 6 is the first graded week.
- Next: watch the live P(win) grade. Re-run steps 1-2 if it drifts over
  several weeks (`docs/research/PWIN_CALIBRATION.md`, "What would change
  this").

## The page serves the stack (2026-10-15)

- **Live grades, weeks 4-5**: the page (advanced_v1) below its own baseline
  on the owner's decision set (58.2% vs 61.3% on 777 pairs; Sleeper 59.5%,
  stack 59.5%). Diagnosed by three independent analyses with adversarial
  review (`docs/research/STACK_SERVING.md`): TE/QB in the 5-10 band; the
  offline harness reproduces it exactly, no pipeline defect; seven seasons
  of history contradict it and refute gating or shrinking the layer.
- **Shipped**: `stack_v1` is the page's number for skill players whose
  pre-kickoff Sleeper projection is in and under 36 h old (`STACK_NAME`,
  `STACK_SHADOW_MAX_AGE_HOURS`, `dashboard.shadow_number`,
  `AdvancedContext.stacked_mean`), on the MEAN scale so it sums and compares
  with every other number; advanced_v1 otherwise and for K/DEF. Gate:
  `scripts/research/stack_vs_page.py`, ahead of the page it replaces on both
  metrics in every held-out season 2019-2025.
- **Shipped**: a skill player a complete, fresh Sleeper feed omits is not
  projected (`dashboard.feed_absence`, `FEED_MIN_PER_POSITION`): 23 of 24
  such players did not play in weeks 4-5. `read_shadow` now keeps each
  player's position so the completeness guard can count.
  The record carries `system` per player and `contenders.adv_v1`; the status
  line counts how many got the stack. The weights' `evidence` block now
  describes the shipped weights (season-fold CV 2019-2025); the 2025 holdout
  block is kept under `evidence_holdout_2025_plus_depth`.
- **Grader**: six systems (page, baseline_v1, adv_v1, Sleeper, blend, stack);
  the ledger gains `adv_v1_pairwise` / `adv_v1_mae` (empty for older weeks).
- Next: watch week 6's grade. "page vs Sleeper" is now the live test of the
  2020 fold; "adv_v1 vs baseline_v1" keeps testing the advanced layer where
  the owner decides. One week is noise (bootstrap sd ~3 pts at 400 pairs).

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


## Trend chips, cloud grading, board restyle (2026-10-01)

The two "next candidates" from the weekly-routine section are built.

- **Trend chips** on the Board's roster rows: volume trend label (rule #6),
  an opportunities-per-week sparkline, ppg and last-game snaps. The roster
  is now grouped cards (starters, bench, reserve) instead of a 7-column
  table; free-agent radar rows carry the chip as well.
- **Cloud grading**: new `gridiron.grading` + `grade_week.py --finished`,
  run by `dashboard-artifact.yml` before the render (non-fatal, counts-only
  log). Pins live in `.carry/pins/`, the ledger in `.carry/grades/` and the
  run artifact. First automatic grade: week 4, the morning after Monday
  night. Week 3's hand grade is kept and never regraded.
  Week 5 showed the gap: a schedule pulled Sunday night was "current" by
  age (168 h cadence) past Monday's game, so the cloud said "not final"
  for days while the box scores were in. The pull now re-fetches the
  schedule whenever a finished game has no score
  (`grading.scores_overdue`, `pull_week._scores_overdue`); week 5 was
  graded by hand on 10-14 and the ledger committed.
- **Report card** section on the Board (KPIs, start/sit shoot-out bars with
  the 50% coin-flip mark, one row per week). Page-only, never archived.
- **Restyle** in `theme.CSS` (shared by both pages): glow background,
  elevated cards, accent section headers, gradient brand mark and primary
  buttons. Bars are SVG because the CSP blocks inline `style` attributes;
  `tests/test_board_trends.py` pins that.

Not verified: the real cloud run of the grading step (first final week with
a pin is week 4). Check the run log's `[grade]` lines on Tuesday 10-06.

## Merged: unpushed draft-2026 research (merged 2026-10-01)

Work from the 09-08 draft-day sessions sat uncommitted on the desktop
checkout while main moved 52 commits; it is now one commit (`04a8ce4`)
merged into main. It is draft research, not in-season production.

- New modules (with tests): `gridiron.draft_context`, `draft_theories`,
  `personnel`; `draft.py` and `ledger.py` extended.
- Scripts: `scripts/research/build_draft_context_2026.py`,
  `build_personnel_2026.py`, `test_draft_theories.py`,
  `test_vegas_weekly.py`; results in `docs/research/DRAFT_THEORIES_RESULTS.md`
  and `VEGAS_WEEKLY_RESULTS.md`, outputs in `data/outputs/`.
- War-room page and draft board regenerated (manifest stores input
  fingerprints, seeds, board hash). The earlier ~100-point "static VOR beats
  scripted openings" claim was invalid (competing policies left slots empty);
  rebuilt it is 1890.4 vs 1885.1, exploratory only.
- `requirements.txt`: both sides' dependencies unioned (adds `requests`,
  `lxml` for research pulls; pandas floor 2.2, pyarrow floor 18).
- Older `COMPETITION_2026.md` / `INJURY_EFFECTS.md` remain exploratory.

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

