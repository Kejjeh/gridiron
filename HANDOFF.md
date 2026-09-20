# HANDOFF

State: **milestones 1, 2 and the cloud sync are all merged to main (`8fbf468`).
Milestone 3 — the weekly decision board — is on `claude/weekly-dashboard`,
now reconciled with main and open as a draft PR based on main. Not merged.**

The desktop five-minute sync is **installed but its scheduled task is
DISABLED**, by the owner, and must stay disabled. Refresh runs in the cloud
instead: `.github/workflows/sleeper-sync.yml` (hourly, gated on the
`GRIDIRON_CLOUD_SYNC_ENABLED` repository variable and on the repo being
private) and, new on this branch, `.github/workflows/dashboard-artifact.yml`,
which renders the board and uploads it as a private artifact. Nothing about
the board depends on a PC being awake.

No identifiers in this file: the league id and the owner's Sleeper handle live
in `src/gridiron/league_config.py`, and every rendered roster artifact — the
weekly report, the dashboard, the decision archive — is gitignored. Nothing
here names a player the owner holds.

## The decision board, in one paragraph

`scripts/weekly/dashboard.py` renders one offline, phone-readable HTML page
from the cache. It leads with **what to do**: each action carries the real
kickoff behind its deadline, a legal backup for when the first choice cannot
be made, and its own ACTIONABLE or WITHHELD verdict. Then the inputs and the
gate they drive, what changed since the previous snapshot, the start/sit
comparisons, a short acquisition shortlist where every row names its drop as
the cost, the roster projections, and only then the matchup — where the
closed-form P(win) sits behind a disclosure, clearly UNCALIBRATED, ranking
nothing. It writes a decision-time archive that later grading reads INSTEAD of
re-projecting, so a grade cannot see the future. Everything it cannot support
it abstains from, per row and per section, with the reason on the page.

**A freshness label is not a gate.** `gridiron.gating` turns the freshness of
the league snapshot, the player dump, the injury table and the schedule into
per-action permission. When one of them is stale or unreadable the action is
WITHHELD: the comparison behind it stays on the page, labelled as the last
known picture, and the imperative disappears. Box-score sources deliberately
do not gate anything — they are stale by construction for most of every week,
and gating on them would withhold everything every Wednesday until the reader
learned to ignore the gate.

**Kickoff locks are three-valued.** LOCKED, OPEN and UNKNOWN. A game with no
readable kickoff time yields UNKNOWN for both its teams, never a guessed
13:00; a team absent from a week is a BYE only when that week's schedule
parsed completely and the team plays elsewhere in the season, otherwise it is
UNKNOWN too. Only OPEN makes a player movable, so a half-readable schedule
freezes the players it cannot time by name and still optimises the rest.

**A player who scores 0 this week is not worth 0.** Bye, Out, IR and suspended
players are PROTECTED from the automatic drop list and named with the reason,
because pricing them needs a rest-of-season model this repo does not have.
**And not on a roster is not addable:** the snapshot proves only that an id is
unrostered at its as-of, so add eligibility is reported UNVERIFIED with its
evidence, and no row ever says "add now".

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
PYTHONPATH=src python scripts/weekly/dashboard_scenarios.py --only partial_schedule
```

The dashboard writes `data/outputs/dashboard/week{NN}_dashboard.html` (plus
`.json` and a `dashboard_latest` pair) and the decision archive under
`data/ledger/decisions/season{YYYY}/`. All of it is gitignored: it names
the owner's players. `--now` renders as of a given instant (locks and
freshness), which is how the scenarios and tests pin a Saturday.

## Getting the board onto a phone: what is built, and the one open choice

Built and reviewable now, needing no PC:
`.github/workflows/dashboard-artifact.yml` runs on GitHub's Linux runners,
refreshes the Sleeper snapshot, pulls the week's nflverse frames, renders the
board, and uploads `dashboard` as a **private, repo-scoped artifact** (14-day
retention). It carries the same two gates as the Sleeper sync: it does nothing
unless the repository variable `GRIDIRON_CLOUD_SYNC_ENABLED` is exactly `true`,
and it refuses outright if the repository ever stops being private. The page
names the owner's players, so a public run would be a roster export. The run
summary prints freshness and the withheld-action counts only — no player, no
lineup, no matchup.

Cadence is rule #8 shaped, in UTC: Wednesday 08:41 (after waivers clear),
Friday 22:41 (after the final practice report) and Sunday 14:41 (before the
early kickoffs), plus manual dispatch with an optional `--week`. Those are
best-effort GitHub crons: runs get delayed and dropped under load, which is
stated in the workflow and is why the page gates on input age instead.

**To read it on a phone today:** open the repository's Actions tab in a mobile
browser, pick the newest successful *Weekly dashboard artifact* run, download
`dashboard`, and open the HTML. It is one self-contained file — no network
calls, no fonts, no scripts — so it renders offline once downloaded.

**The one decision left to the owner**, because every answer costs something
different and none of them is reversible for free:

| Option | What it costs | What it gives |
|---|---|---|
| **A. Artifact download** (built, nothing more to decide) | Four taps on a phone, and the artifact expires after 14 days | No new service, no hosting, no auth, no dependency. Private by the repo's own access control |
| **B. Commit the rendered page to a private branch** | Puts a roster-bearing artifact into git history, which rule #10 exists to prevent and which cannot be undone without rewriting history | One stable URL, viewable in the GitHub mobile app |
| **C. Push it to a private file host** | A new third-party service holding roster data, plus credentials to manage | A real link, openable without the Actions UI |

**A is live and is the recommendation.** B and C both widen where roster data
lives, and neither was authorized. Nothing further should be built here until
the owner picks; if A is good enough, this question is closed.

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

1. **Astra review of the decision-board PR.** Render from the live cache
   (`dashboard.py --write`), open the HTML, and check the four synthetic
   scenarios (`dashboard_scenarios.py --screenshot`) against the PNGs in
   `docs/review/dashboard/`. Worth attacking specifically: feed it a schedule
   with a real flexed game and confirm the deadline matches Sleeper's own lock
   time, and confirm the WITHHELD banner appears on a genuinely old cache.
   Nothing is merged.
2. **Owner: pick a phone-access option** (see the table above). A is built
   and needs no further work; B and C need authorization before anything is
   written.
3. **Turn on the dashboard workflow** by setting `GRIDIRON_CLOUD_SYNC_ENABLED`
   once the PR lands, then dispatch it manually once and confirm the private
   artifact appears with no roster content in the run summary.
4. **First graded week.** After week 3 finals land in the cache, grade the
   week-3 archive: `gridiron.decisions.grade_archive(read_archive(path),
   actuals)` with actuals = week-3 `league_points` by gsis id from the
   scored frame. That is the first rule #7 settlement; no script wraps it
   yet, deliberately, until the first one has been done by hand.
5. **Week 2 rollover check.** After Sunday's slate, `pull_week.py` then
   `report.py` should show `weekly_stats covers wk1-2` and the lag return to
   0. That is the first live exercise of the phase boundary.
6. **DST scoring**, which needs team-level aggregation from nflverse
   play-by-play — the one place the report currently renders a hole.
7. **Then** the rule #5 gate for anything BEYOND the baseline: the dashboard
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
| Lock state unknown ⇒ never movable | Locks are three-valued. No schedule at all ⇒ `kickoff_index` is None ⇒ start/sit and upgrades both refuse. A game with no readable kickoff time ⇒ UNKNOWN for both teams, never a guessed 13:00. A team absent from an INCOMPLETE week ⇒ UNKNOWN, not a bye. Only OPEN is movable | `test_unknown_lock_state_abstains_from_everything`, `test_a_game_with_no_kickoff_time_is_unknown_not_one_oclock`, `test_a_week_that_could_not_be_read_is_never_a_league_wide_bye`, `test_a_partially_readable_week_freezes_only_the_players_it_cannot_time` |
| A proven bye is distinguished from ignorance | "No game this week" is OPEN only when the week's schedule parsed completely AND the team plays in another week of the same frame; otherwise UNKNOWN | `test_a_team_absent_from_a_complete_week_is_a_bye_and_one_it_never_heard_of_is_not` |
| Stale inputs withhold the ACTIONS, not the evidence | `gridiron.gating` gates lineup / waiver / matchup on the league snapshot, player dump, injuries and schedule. A withheld action keeps its comparison, loses its imperative, and names what to verify. The archive records which actions were endorsed | `test_stale_inputs_withhold_every_action_but_keep_the_comparison`, `test_a_fresh_cache_still_endorses_its_actions`, `test_box_score_staleness_does_not_withhold_anything` |
| A file's timestamp is not a parse | A schedule that is FRESH on disk but not fully readable still withholds lineup and waiver actions | `test_an_unreadable_schedule_blocks_actions_even_though_the_file_is_fresh`, `test_a_half_readable_schedule_freezes_by_name_and_invents_nothing` |
| Temporary absence is not zero roster value | Bye / Out / IR / suspended players are excluded from the drop ranking, listed separately with the reason, and the board abstains if nothing droppable is left | `test_a_player_who_is_out_this_week_is_not_the_cheapest_thing_to_drop`, `test_a_bare_ir_projection_without_a_withholding_marker_is_still_protected`, `test_a_board_with_nothing_left_to_drop_abstains_and_says_how_many` |
| Unrostered is not addable | Add eligibility is UNVERIFIED with its evidence and the league's waiver rule; the page never says "add now" | `test_an_unrostered_player_is_never_promised_as_addable`, `test_protected_players_are_named_on_the_page_not_silently_dropped` |
| Uncalibrated P(win) ranks nothing | Actions are ranked by deadline then projected points; P(win) sits behind a disclosure in the matchup section and appears in no action headline | `test_the_page_leads_with_actions_and_demotes_uncalibrated_win_probability` |
| Changes are transitions, not recomputation | `diff_archives` reads two frozen archives as data; a projection must move ≥ 1.5 pts to be listed | `test_a_second_render_reports_what_changed_since_the_first` |
| It fits a phone | Narrow layout below 560 px; the scenario runner measures the rendered page in a 390 px iframe and reports the width it actually achieved | `dashboard_scenarios.py --screenshot` (executed; see below) |
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
- **No FAAB pricing and no rest-of-season value.** Upgrade gains are this
  week's projected points only.
- **Add eligibility cannot be established from this cache, and is not.** The
  league snapshot proves one thing: the id is on no roster at its as-of. The
  waiver clock, pending claims and the drop that created the vacancy live in
  Sleeper's transactions feed, which this repo does not pull. Every candidate
  is marked UNVERIFIED and the owner checks in the app. Adding that feed is
  the obvious next increment and was deliberately not done here.
- **Protected players are protected, not priced.** A bye / Out / IR player is
  kept off the drop list because his one-week 0 is not his value — but the
  page cannot tell you what he IS worth, because that needs a rest-of-season
  model and rule #5 does not allow one to ship unvalidated. A human still has
  to make that call.
- **Deadlines are kickoffs, not roster-lock settings.** The board reads the
  schedule's kickoff times. If the league ever uses a whole-week lock or a
  first-game lock, this would be wrong and nothing here would catch it.
- **No DST projection**, so the DST slot is never optimized and both
  matchup totals exclude it.
- **Availability is a flag, not a probability.** Questionable and Doubtful
  are shown unadjusted (rule #11); a human verifies before kickoff.
- **The archive is graded by hand** until the first settlement has been
  done once; there is no `grade_week.py` yet.
- **Screenshots are of the synthetic scenarios only** (`docs/review/dashboard/`),
  never of the owner's page.
- **The phone check is a synthetic browser check, not a device test.** The
  scenario runner measures the rendered page in a 390 px iframe under headless
  Chromium and reports the layout width it actually achieved (375 px client,
  zero horizontal overflow on all four scenarios). No real phone, no iOS or
  Android browser, and no touch interaction has been tested. The committed
  PNGs are captured at 500 px, not 390, because this headless build clamps its
  own window to a 500 px minimum — asking for 390 silently crops the image and
  makes correct text look clipped.
- **A scheduled refresh is not timely injury news.** The cloud workflows keep
  the SNAPSHOT current on a best-effort cron that GitHub may delay or drop.
  A designation can change minutes before kickoff regardless of when the last
  job ran. That is exactly why the page gates its actions on input age rather
  than on the existence of a schedule.

## Not done deliberately

- No lineup change, waiver claim, trade or message. Ever, by construction.
- No projection feature beyond the baseline while the rule #5 gate is
  unmet; the baseline itself is labelled UNVALIDATED on every page. A
  guessed recommendation presented as verified is the failure mode this
  repo exists against.
- No blocked-kick scoring and no DST scoring invented to fill the gap.
- No history rewrite, no merge, no deploy, no PR watcher.
- No rest-of-season model, invented to price a protected player. The page
  says it cannot price him, which is true, rather than producing a number
  rule #5 would not let ship.
- No Sleeper transactions pull, so add eligibility stays UNVERIFIED rather
  than guessed. That pull is a real next increment, not a limitation to
  paper over.
- No FAAB engine, trade engine, season simulation or model tuning in this
  milestone. The projection is byte-for-byte the reviewed baseline.
- Desktop scheduling stays DISABLED. Nothing here re-enables it, and the
  cloud path is deliberately independent of it.
