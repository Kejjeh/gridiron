# HANDOFF

State: **milestone 1 is merged to main (`c6429b8`, PR #1). Milestone 2 — the
five-minute live league sync — is PR #3 on `claude/compassionate-shannon-yq0hw5`
(head `d16eb2e`, Astra's installer fix; installed on the desktop and verified
over 180 refreshes). Milestone 3 — the weekly decision dashboard — is on
`claude/weekly-dashboard`, branched from `d16eb2e`, open for review as a
draft PR stacked on PR #3. Not merged.**

No identifiers in this file: the league id and the owner's Sleeper handle live
in `src/gridiron/league_config.py`, and every rendered roster artifact — the
weekly report, the dashboard, the decision archive — is gitignored. Nothing
here names a player the owner holds.

## The dashboard, in one paragraph

`scripts/weekly/dashboard.py` renders one offline HTML page from the cache:
input freshness first, then the chronological out-of-sample record of the
baseline, then the roster projected in league scoring with every component
visible, the matchup with an UNCALIBRATED closed-form P(win), the best legal
lineup under kickoff locks with every single-swap alternative (Δpts, z,
ΔP(win)), and available-player upgrades each paired with an explicit drop.
It writes a decision-time archive that later grading reads INSTEAD of
re-projecting, so a grade cannot see the future. Everything it cannot
support it abstains from, per row and per section, with the reason on the
page. Section "Weekly decision dashboard" below has the guarantees table and
the limitations.

## Read this first

PR #2 — the dependency-free offensive scoring repair plus the standing league
settings verification — was reviewed independently and merged to main as
`cd9d9ef`. **main has been merged into this branch**, so this branch now
contains that work rather than a second copy of it. The reconciliation kept
main's version of every shared file (it was a strict superset everywhere but
two `scoring.py` docstrings); `scoring.py`'s executable code is byte-identical
to main's. There is one scoring implementation, and it is the reviewed one.

The dependency question is **closed**: the owner approved `nflreadpy`,
`pandas` and `pyarrow` for this project's environment on 2026-09-17. No other
new direct dependency, and no paid API. `docs/review/MILESTONE1_DEPENDENCY_REVIEW.md`
is kept as the record of what each one buys.

## What you can actually run

```
PYTHONPATH=src python scripts/ingest/pull_week.py     # network, read-only
PYTHONPATH=src python scripts/weekly/report.py        # offline, prints
PYTHONPATH=src python scripts/weekly/report.py --write --anonymous
PYTHONPATH=src python scripts/verify_league_settings.py

PYTHONPATH=src python scripts/sync/sleeper_sync.py run       # network, ~1s
PYTHONPATH=src python scripts/sync/sleeper_sync.py run --if-due
PYTHONPATH=src python scripts/sync/sleeper_sync.py status    # offline

PYTHONPATH=src python scripts/weekly/dashboard.py --write            # offline; HTML+JSON+archive
PYTHONPATH=src python scripts/weekly/dashboard.py --write --anonymous --no-archive
PYTHONPATH=src python scripts/weekly/dashboard_scenarios.py --screenshot   # synthetic pages
```

The dashboard writes `data/outputs/dashboard/week{NN}_dashboard.html` (plus
`.json` and a `dashboard_latest` pair) and the decision archive under
`data/ledger/decisions/season{YYYY}/`. All of it is gitignored: it names
the owner's players. `--now` renders as of a given instant (locks and
freshness), which is how the scenarios and tests pin a Saturday.

**The two cadences are different jobs and must stay different jobs.**
`sleeper_sync.py` is five Sleeper GETs — league, users, rosters, current-week
matchups, NFL state — a few hundred KB, run every five minutes. `pull_week.py`
is nflverse frames plus the 16 MB Sleeper player dump, run daily at most; that
dump is on a once-a-day cadence because Sleeper's own documentation asks for
it, and the sync has a test that fails if it ever reaches for it.

### Installing the five-minute sync on the Windows desktop

From the checkout, in PowerShell (no admin, no stored password):

```
powershell -ExecutionPolicy Bypass -File .\scripts\windows\gridiron_task.ps1 `
  -Action Install `
  -RepoRoot "C:\Users\Joshua\Documents\Claude\Projects\Football-Live" `
  -Python   "C:\Users\Joshua\Documents\Claude\Projects\Football\.venv\Scripts\python.exe"
```

`-Action Status` reports both the task's view (last/next run) and the sync's
own view (last success, next due, failure streak, drift). `-Action Uninstall`
removes it. Install is idempotent — re-running replaces, never duplicates.

The task runs with the **Interactive** logon type — no stored credential, no
elevation — on a **time-based repeating trigger starting one minute after
install**, so installing it while already signed in works immediately instead
of waiting for the next logon. Install prints the first run time and the
`NextRunTime` Windows reports, so the two can be compared on the spot.

**Explicit requirement:** this is a scheduled task, not a service. It runs
only while that desktop is powered on and that user is signed in. That is the
price of not storing a password and not asking for admin, and it is the right
trade for a tool whose worst failure is five minutes of staleness that the
status command reports honestly.

**No AI runs on the schedule.** The task starts `python.exe`. Nothing wakes
Claude or Codex per refresh and no tokens are spent.

`pull_week.py` fetches nflverse weekly stats, snap counts, schedules and
injuries, the dynastyprocess id crosswalk, and a read-only Sleeper snapshot
into `data/research/cache/season2026/` (gitignored). `report.py` renders from
that cache and never opens a socket — there is a test that breaks the socket
and re-renders to prove it.

The report prints, per rostered player: lineup slot, opponent and market
implied total, the injury designation with its source, and measured usage
(snap %, targets, target share, carries, opportunities) plus league points
through the last admissible week. It ranks nothing and recommends nothing.

## The six invariants, and the tests that hold them

These are the ways a weekly tool lies. Each is pinned. The first four are
about time; the last two came out of Astra's review of `28ae09a`.

1. **No future leakage.** A report about week N reads weeks ≤ N-1 before the
   slate and ≤ N once it is COMPLETE. The boundary is
   `WeekContext.evidence_boundary`, derived from the report week and the
   phase — *not* from `max(weeks in the cache)`, which is what the code did
   before and which meant re-rendering week 2 in week 6 quietly read weeks
   3–5. `test_a_later_week_in_the_cache_cannot_leak_into_an_earlier_report`
   builds the same report twice, once from a cache stopping at week 1 and
   once from a cache also holding weeks 2–3 at ten times the volume, and
   asserts the numeric columns are identical.
   Weeks past the boundary are listed as WITHHELD in the report, and a
   re-render states that its injury designations and market lines come from
   the latest pull — they are after-the-fact, not what was known at kickoff.

2. **A failed refresh cannot fake freshness.** `Manifest.record_failure`
   records the failure and touches nothing that describes data: `path`,
   `rows`, `weeks` and `as_of` keep pointing at the last pull that actually
   returned rows. Previously a failure was recorded as a fresh entry stamped
   `now`, so a dead network aged as a current pull *and* dropped the good
   cached file over a transient 503. A source whose latest refresh failed can
   never read FRESH; it renders STALE with the failure reason and still shows
   its cached rows. Cache writes are atomic (temp file + `os.replace`), so a
   pull that dies mid-write cannot leave a truncated parquet behind.

3. **Season rollover is a refusal, not a note.** A cache season, or a cached
   Sleeper state season, that disagrees with the requested season exits 2
   rather than rendering last season's roster under this season's header.
   Sleeper reports week 0 between seasons; that exits 2 too, unless `--week`
   is passed to look back at a finished week.

4. **Coverage gaps are visible.** Freshness carries the full covered-week
   set, so a hole inside the range gets its own line (`covers wk1-5 (no
   wk3)` plus a GAP degradation). "Covers through week 5" off weeks
   {1,2,4,5} is true, and every season total built on it is short by a week.

5. **Every source the report reads declares its freshness.** The rule is
   consumption, not convenience: if a value from a source reaches the page,
   that source's as-of line is on the page too. `sleeper_players` was being
   read for the live injury designation, the NFL team, the position and the
   sleeper→gsis overlay, and was missing from `SOURCES` — so a month-old
   player dump with a *failed refresh recorded against it* rendered under
   "All inputs current" and exited 0 under `--fail-on-degraded`.
   It now has an injury-sensitive cadence (24h; 6h on gameday, waiver day
   and designation day), because `injury_status` is live rather than
   week-keyed: the value carries no date, so the age of the pull it rode in
   on is the only thing that distinguishes a month-old "Questionable" from a
   current one. A designation from a non-FRESH pull renders as
   `… — STALE designation from an out-of-date player pull …; NOT a current
   status`, and `AvailabilityNote.current` is False.
   `test_every_source_the_report_reads_declares_its_freshness` re-derives the
   consumed set from `report.py`'s own syntax (every `read_frame`/`read_json`
   /`file` call with a literal name) *and* compares it against a list
   maintained by hand — looping over `SOURCES` to check `SOURCES` is what let
   the omission hide.
   `designation_fresh` defaults to False and `build_report` fails closed when
   the player dump is absent from the freshness list: a caller that never
   established currency does not get the benefit of the doubt.

6. **Points are never published off a frame that cannot support them.**
   `fantasy_points` reads an absent key as zero. That is *right* for a null
   cell — nflverse leaves a running back's `passing_interceptions` null and
   the null genuinely means zero picks — and a wrong number for a missing
   *column*: drop `passing_interceptions` from the parquet and every
   quarterback silently scores a point per pick too high. On the committed
   fixture that is Drake Maye at 15.82 instead of 12.82, under "All inputs
   current", exit 0.
   `scoring.scoring_coverage()` checks a frame's column names against the
   league rules. It is alias-aware (a cache predating the nflreadpy 0.1.x
   rename scores through its legacy columns and is *recorded*, not flagged),
   weight-aware (a term weighted zero cannot move a total, so a missing
   column behind it is not a defect), catches partially present multi-column
   terms, and never reads a value. A gap BLANKS `pts`/`ppg` for the affected
   positions only — the offensive and kicking halves fail independently, and
   usage columns are untouched — names itself in the degraded block, and
   fails `--fail-on-degraded`.
   The check runs in **both** places: the puller persists the verdict to
   `Entry.missing_columns` (a stderr warning dies with the run that printed
   it; the manifest is what the next reader has), and the report re-checks
   the frame actually in hand on every render, which is the only thing that
   catches a cache edited or written by an older schema since. A recorded
   schema defect is deliberately *not* an error: the fetch succeeded, so
   `error` stays empty, the file stays readable and the as-of stands.
   `build_report(scoring=...)` is a required argument — a default would let a
   caller publish points without ever having looked at the schema behind them.

The three honesty invariants from milestone 1 still hold: blank is never zero,
a missing schedule renders `?` and never BYE, and "not on this week's injury
report" reads differently from "no injury report loaded".

## Verification (all green, 2026-09-17)

| check | result |
|---|---|
| `python scripts/ci/smoke.py` | PASS — 16 imports, 20 contract files |
| `python scripts/ci/run_summary.py -- python -m pytest` | **303 passed** (was 279; main: 239) |
| `node --test scripts/research/warroom/draftroom_logic.test.js` | 10/10 pass |
| `scripts/verify_league_settings.py` (live) | exit 0, 55 constants, no drift |
| `golden_run.py --target weekly_report` A/B | 1/1 byte-IDENTICAL |

Red-before/green-after for the two review blockers: the five new tests that
pin them were run against `28ae09a` in a detached worktree and **all five
fail there**; `test_a_null_stat_cell_is_not_a_missing_column` passes on both,
which is correct — it pins behavior that already worked and had to survive
the change.

The golden A/B here is a **reproducibility** check, not a refactor check: two
independent runs against the same cache produce byte-identical output. The
time-boundary and failed-refresh changes are deliberate behavior changes, so
A/B across them would be meaningless and was not claimed.

Live read-only validation: `pull_week.py` against nflverse and Sleeper, and
`report.py` rendering the real week-2 report. No league transaction of any
kind — `gridiron.sleeper` is GET-only by construction and
`tests/test_sleeper.py::test_module_is_read_only` pins it.

The review fixes were validated against the **real** cache with no new league
query: the live 1118-row, 150-column nflverse frame passes `scoring_coverage`
cleanly (no false positive), and the real render now reports
`sleeper_players STALE … pulled 7h ago, over the 6h gameday limit` — a
Wednesday is waiver day, so the tightened limit applies — where before that
source had no line at all. Three roster rows carry a labelled stale
designation that previously printed as current. Every regression test builds
its own synthetic cache from the committed fixtures.

## Provenance and coverage

| source | via | covers | note |
|---|---|---|---|
| weekly player stats | `nflreadpy.load_player_stats` | wk1 | wk2 lands after the slate |
| snap counts | `nflreadpy.load_snap_counts` | wk1 | PFR-keyed, joined through the crosswalk |
| schedules + market lines | `nflreadpy.load_schedules` | wk1–18 | `total_line` is the implied-total input |
| injuries | `nflreadpy.load_injuries` | wk1–2 | designation + practice status |
| id crosswalk | dynastyprocess `db_playerids.csv` | — | Sleeper's own `gsis_id` overlays gaps only |
| league / rosters / matchups | Sleeper public read API | wk2 | no auth, no writes |
| player dump (`sleeper_players`) | Sleeper public read API | live | live `injury_status`, team, position, gsis overlay; 24h cadence, 6h on gameday/waiver/designation days |

**Missing sources, named rather than worked around:** no DST scoring (nflverse
weekly data is player-level; a DST row carries blank points and an explicit
"n/a (team defense)" note, never 0.0). No blocked-kick mapping — no blocked
kick occurred in the reconciliation week, so whether the league scores it as a
miss is UNVERIFIED and deliberately unmapped. No projections, no rankings, no
start/sit or waiver output: the rule #5 gate is unmet.

Dependency versions in the project environment: pandas 3.0.5, pyarrow 25.0.1,
nflreadpy 0.1.5, polars 1.44.2 (transitive, via nflreadpy), numpy 2.4.6,
pytest 9.1.1, Python 3.11.15.

## Owner data boundary

The rendered weekly report names the owner's actual players, so
`data/outputs/week*_report.*` and `weekly_report_latest.*` are now gitignored
and the previously committed week-2 pair is untracked at this branch's tip.
`tests/test_hygiene_no_roster_in_repo.py` fails if a roster-bearing file is
staged again.

Two things deliberately NOT done: history is not rewritten (the untracked pair
still exists in this branch's earlier commits, and rewriting shared history is
a bigger hazard than the exposure), and the league-wide files already tracked
on main — the draft board, ADP and competition tables — are left alone. They
are keyed by player id and expose no roster; an id column is not a roster, and
treating it as one would ban the public files rule #10 exists to keep.

One item for the owner: some of those pre-existing tracked files under
`data/outputs/` do contain the owner's Sleeper display name in a column. That
predates this branch and is unchanged by it. Widening was avoided; whether to
narrow it is the owner's call.

## Next

1. **Astra review of the dashboard PR.** Render it on the desktop from the
   live cache (`dashboard.py --write`), open the HTML, and check the three
   synthetic scenarios (`dashboard_scenarios.py --screenshot`) against the
   PNGs in `docs/review/dashboard/`. Nothing is merged.
2. **First graded week.** After week 3 finals land in the cache, grade the
   week-3 archive: `gridiron.decisions.grade_archive(read_archive(path),
   actuals)` with actuals = week-3 `league_points` by gsis id from the
   scored frame. That is the first rule #7 settlement; no script wraps it
   yet, deliberately, until the first one has been done by hand.
3. **Week 2 rollover check.** After Sunday's slate, `pull_week.py` then
   `report.py` should show `weekly_stats covers wk1-2` and the lag return to
   0. That is the first live exercise of the phase boundary.
4. **DST scoring**, which needs team-level aggregation from nflverse
   play-by-play — the one place the report currently renders a hole.
5. **Then** the rule #5 gate for anything BEYOND the baseline: the dashboard
   ships the baseline (which is what the gate measures against) and zero
   features on top of it. `gridiron.models.validated_signals` is the
   registry; `gridiron.evaluate` produces the evidence.

## Live sync: the guarantees, and where each is held

| Guarantee | How | Test |
|---|---|---|
| Snapshot and manifest are always a coherent pair | Every publish writes an **immutable generation** `sleeper_league_<stamp>.json` and repoints the manifest at it atomically; no file is ever rewritten in place | `test_a_successful_sync_publishes_a_generation_the_manifest_points_at`, `test_an_unfinished_generation_is_never_read` |
| One writer at a time, **including the weekly puller** | An **OS-held lock** on a persistent, never-unlinked file: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, both non-blocking. No create/delete, no age, no takeover — a file-existence lock cannot be made race-free (two processes can both see it abandoned and both replace it), and the kernel drops an OS lock when the holder dies. `sync_once` holds it across state read → fetch → publish → state save, so a slow run cannot finish last and write stale counters over a newer one; the publish path it uses (`_publish_locked`) does not re-acquire, because the lock is not reentrant | `test_two_processes_cannot_both_hold_the_lock` and `test_a_killed_holder_releases_the_lock_with_no_timeout_and_no_reclaim` (real child processes), `test_state_is_read_inside_the_lock_so_a_slow_run_cannot_overwrite_a_newer_one`, `test_the_lock_is_not_reentrant_and_publish_does_not_nest_it` |
| A concurrent writer's entries are never clobbered | The manifest is **re-read inside the lock** and only `sleeper_league` is touched. `pull_week.py` never owns `sleeper_league` at save time — the committed on-disk entry always wins, including over the one that run published, because the 16 MB player fetch is long enough for several syncs to land | `test_a_sync_during_a_slow_player_fetch_is_not_rolled_back_by_the_puller` (real interleaving, executed) |
| The manifest is published atomically | `Manifest.save()` writes a pid-unique temp and `os.replace`s it. A truncated manifest is worse than a stale one: it is the only thing naming the published generation, so losing it orphans every snapshot file. Every `.part` name carries the pid so two writers never share a scratch file | `test_a_manifest_write_that_dies_leaves_the_previous_one_whole` (injected `OSError`), `test_state_writes_do_not_share_a_scratch_file` |
| Failure never loses data | Fetch → validate → *then* write. Nothing opens for writing until a whole payload passed every check | `test_a_failed_sync_preserves_the_last_good_snapshot` (9 failure modes) |
| Fail closed before publication, **both writers** | League id; season on the league object *and* NFL state, with a **missing** season refused rather than assumed; `scoring_settings` and `roster_positions` present; team count; owner present and owning a roster; duplicate roster ids; non-object list members; regular-week matchup roster ids unique and equal to the roster set (a count check accepts twelve copies of one roster). `pull_week.pull_sleeper` runs the same gate | `test_fail_closed_on_a_defective_payload` (8 cases), `test_the_weekly_puller_validates_before_publishing` |
| Sequential GETs are not claimed to be atomic | The read window is timed and recorded in the snapshot; NFL state is read at both ends and a mid-read rollover discards the snapshot and retries once | `test_a_week_rollover_mid_read_discards_the_snapshot`, `test_the_read_window_is_recorded_not_assumed_away` |
| Settings drift is detected, sticky, never auto-verified | Watched keys fingerprinted and diffed each sync; drift is recorded, shouted, and **stays flagged** through later clean syncs until a human clears it. `SETTINGS_VERIFIED` is never written (rule #1) | `test_settings_drift_is_reported_and_never_marks_anything_verified`, `test_settings_drift_stays_sticky_across_later_clean_syncs` |
| Restart-safe | State is a separate file; a corrupt one resets counters but never blocks a sync | `test_state_survives_a_restart_and_a_corrupt_state_file_does_not_block` |
| No league mutation | `gridiron.sleeper` is GET-only by construction; the sync adds no endpoint | `test_sleeper.py::test_module_is_read_only` |

## Weekly decision dashboard: the guarantees, and where each is held

| Guarantee | How | Test |
|---|---|---|
| Offline, from the cache alone | The CLI reads only the manifest's files; no module under it imports a network client | `test_the_dashboard_renders_from_the_cache_with_no_network` (socket broken) |
| Every source read declares its freshness on the page | `SOURCES` is re-derived from the script's own `read_frame`/`read_json`/`file` calls and compared to the freshness table | `test_every_source_the_dashboard_reads_declares_its_freshness` |
| Projections are transparent | `Projection.components` (volume × rate × multiplier = points) and `inputs` (games, observed share, prior, weight) are rendered under every number; the mean is their sum | `test_the_mean_is_the_sum_of_its_printed_components` |
| Projections are chronological | `build_evidence(through_week=...)` cuts BEFORE aggregation; the evaluation predicts each week from the ones before it, and perturbing later weeks moves nothing earlier | `test_evidence_is_cut_before_aggregation_...`, `test_each_week_is_predicted_only_from_the_weeks_before_it` |
| Rule #6 is the model's shape | Usage share shrunk toward the measured role's prior with the documented n0; efficiency is the positional prior × line multiplier; the player's own efficiency is never read | `test_usage_is_shrunk_toward_the_role_prior_...`, `test_the_line_moves_efficiency_not_volume` |
| Abstain, never guess | mean=None with a reason for: no admissible box score, DST, no pooled QB/K prior, unresolved id, incomplete scoring columns; withheld (0, model mean kept visible) for bye / Out / IR | `test_it_abstains_*`, `test_withheld_keeps_the_model_mean_visible`, `test_missing_inputs_abstain_everywhere_and_fill_nothing_in` |
| Rule #11 | Questionable / Doubtful are flagged on the row and NEVER adjusted; a designation from a stale player pull is labelled STALE, and an Out from a stale pull says so | `test_complete_projects_matches_and_recommends_with_labels`, `test_stale_inputs_are_shown_labelled_...` |
| Lineups are legal | Position/FLEX eligibility, nobody twice, nobody off IR, nobody moved after kickoff; an unprojected starter is frozen (not swapped out), an unprojected bench player is never proposed | `test_lineup.py`, `test_the_best_lineup_is_legal_under_the_locks` |
| Lock state unknown ⇒ abstain | No schedule ⇒ `kickoff_index` is None ⇒ start/sit and upgrades both refuse, with the reason | `test_unknown_lock_state_abstains_from_everything`, `test_unknown_locks_abstain_the_whole_board` |
| No same-value churn | Retained starters keep their slots when equally legal | `test_same_value_churn_is_not_reported_as_a_change` |
| Every upgrade names its drop | Add/drop pairs are scored by the change in the best LEGAL lineup; LINEUP vs DEPTH kinds kept apart; a locked starter is never the drop | `test_a_lineup_upgrade_names_the_drop_and_the_slot_it_enters`, `test_the_drop_is_never_a_locked_starter_...` |
| P(win) is never presented as calibrated | Closed form from `gridiron.winprob`, labelled UNCALIBRATED on the page and in the archive; abstains when any non-DST starter on either side is unprojected; `EvaluationReport.pwin_calibrated` is False by construction | `test_complete_...`, `test_missing_...` |
| The archive is the page | `Dashboard.record()` is written atomically at render time; grading reads it and the week's actuals only, never re-projects; a missing actual is `ungradeable`, not zero | `test_the_archive_is_the_page_and_grading_it_leaks_nothing`, `test_decisions.py` |
| Rule #5 gate is mechanical | `gridiron.models.validated_signals`: an entry in FEATS without VALIDATED evidence fails the import (smoke.py imports it first) | `test_the_rule_5_gate_fails_the_import_for_an_unvalidated_feature` |
| Owner data stays local | `data/outputs/dashboard/` and `data/ledger/decisions/` gitignored; hygiene test scans tracked outputs for dashboard/archive markers; stdout names nobody; opponent is "roster #N" | `test_hygiene_no_roster_in_repo.py`, `test_the_stdout_summary_names_no_player`, `test_the_opponent_is_a_roster_number_...` |

### Limitations, stated

- **The baseline is unvalidated and says so.** On the synthetic scenario its
  MAE does NOT beat season PPG (that fixture is built from one week copied
  with scaled counts, which is the case PPG is best at). On the real cache
  there is one week of box scores, so nothing can be evaluated yet. The
  page carries the verdict either way; the verdict changes only when the
  cache does.
- **SD is a literature CV, not a fit.** §2.1's CVs by position; the
  evaluation reports the ±1 SD coverage so a reader can see how wrong that is.
- **P(win) assumes independence** between all starters (no stack / same-game
  correlation, no DST on either side). It is a closed form, not the Monte
  Carlo QUANT §2.5 asks for at |margin| < 15. Labelled on every render.
- **QB and K priors are pooled from the frame**, not from a verified
  constant, and the n0 for those per-game rates (3 games) is stated by
  analogy, not fit. Backup QBs with one thin game are pulled toward the
  starter prior; the "why" panel shows the weight.
- **No FAAB pricing, no waiver-vs-free-agent state, no rest-of-season value.**
  Upgrade gains are this week's projected points only.
- **No DST projection**, so the DST slot is never optimized and both
  matchup totals exclude it.
- **Availability is a flag, not a probability.** Questionable and Doubtful
  are shown unadjusted (rule #11); a human verifies before kickoff.
- **The archive is graded by hand** until the first settlement has been
  done once; there is no `grade_week.py` yet.
- **Screenshots are of the synthetic scenarios only** (`docs/review/dashboard/`),
  never of the owner's page.

## Not done deliberately

- No lineup change, waiver claim, trade or message. Ever, by construction.
- No projection feature beyond the baseline while the rule #5 gate is
  unmet; the baseline itself is labelled UNVALIDATED on every page. A
  guessed recommendation presented as verified is the failure mode this
  repo exists against.
- No blocked-kick scoring and no DST scoring invented to fill the gap.
- No history rewrite, no merge, no deploy, no PR watcher.
