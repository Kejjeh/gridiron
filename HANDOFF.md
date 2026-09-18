# HANDOFF

State: **milestone 1 is merged to main (`c6429b8`, PR #1). Milestone 2 — the
five-minute live league sync — is on branch
`claude/compassionate-shannon-yq0hw5`, branched fresh from `c6429b8`, open for
review. Not merged, not installed.**

No identifiers in this file: the league id and the owner's Sleeper handle live
in `src/gridiron/league_config.py`, and the rendered roster report is
gitignored. Nothing here names a player the owner holds.

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
```

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

1. **Astra review + local install of the live sync.** The install command is
   above. Nothing is merged or installed.
2. **Week 2 rollover check.** After Sunday's slate, `pull_week.py` then
   `report.py` should show `weekly_stats covers wk1-2` and the lag return to
   0. That is the first live exercise of the phase boundary.
3. **DST scoring**, which needs team-level aggregation from nflverse
   play-by-play — the one place the report currently renders a hole.
4. **Then** the rule #5 gate: a projection cannot ship until it beats a
   baseline containing every existing feature, out-of-sample. Roster audit,
   waiver board and start/sit all sit behind that gate.

## Live sync: the guarantees, and where each is held

| Guarantee | How | Test |
|---|---|---|
| Snapshot and manifest are always a coherent pair | Every publish writes an **immutable generation** `sleeper_league_<stamp>.json` and repoints the manifest at it atomically; no file is ever rewritten in place | `test_a_successful_sync_publishes_a_generation_the_manifest_points_at`, `test_an_unfinished_generation_is_never_read` |
| One writer at a time, **including the weekly puller** | Both writers publish through `livesync.publish_snapshot`, which takes an `O_EXCL` lock (Windows-safe, no `fcntl`); an abandoned lock is taken over after 240s | `test_an_overlapping_run_declines_instead_of_double_writing`, `test_the_weekly_puller_publishes_through_the_same_generation_scheme` |
| A concurrent writer's entries are never clobbered | The manifest is **re-read inside the lock** and only `sleeper_league` is touched; `pull_week.py` merges on-disk entries it did not touch before saving | `test_a_concurrent_writer_s_unrelated_entries_are_not_overwritten` |
| Failure never loses data | Fetch → validate → *then* write. Nothing opens for writing until a whole payload passed every check | `test_a_failed_sync_preserves_the_last_good_snapshot` (9 failure modes) |
| Identity is checked before publication | League id, season (league object *and* NFL state), team count, owner present and owning a roster | `test_validation_names_every_problem_and_separates_partial_from_wrong` |
| Sequential GETs are not claimed to be atomic | The read window is timed and recorded in the snapshot; NFL state is read at both ends and a mid-read rollover discards the snapshot and retries once | `test_a_week_rollover_mid_read_discards_the_snapshot`, `test_the_read_window_is_recorded_not_assumed_away` |
| Settings drift is detected, never auto-verified | Watched scoring/roster/settings keys are fingerprinted and diffed each sync; drift is recorded and shouted, and `SETTINGS_VERIFIED` is untouched (rule #1) | `test_settings_drift_is_reported_and_never_marks_anything_verified` |
| Restart-safe | State is a separate file; a corrupt one resets counters but never blocks a sync | `test_state_survives_a_restart_and_a_corrupt_state_file_does_not_block` |
| No league mutation | `gridiron.sleeper` is GET-only by construction; the sync adds no endpoint | `test_sleeper.py::test_module_is_read_only` |

## Not done deliberately

- No lineup change, waiver claim, trade or message. Ever, by construction.
- No projections, rankings or start/sit advice while the rule #5 gate is
  unmet. A guessed recommendation presented as verified is the failure mode
  this repo exists against.
- No blocked-kick scoring and no DST scoring invented to fill the gap.
- No history rewrite, no merge, no deploy, no PR watcher.
