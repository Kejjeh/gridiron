# HANDOFF

State: **milestone 1 complete and reconciled with main.** Branch
`claude/compassionate-shannon-yq0hw5`, open as PR #1, awaiting Astra review.
Not merged, not deployed.

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
```

`pull_week.py` fetches nflverse weekly stats, snap counts, schedules and
injuries, the dynastyprocess id crosswalk, and a read-only Sleeper snapshot
into `data/research/cache/season2026/` (gitignored). `report.py` renders from
that cache and never opens a socket — there is a test that breaks the socket
and re-renders to prove it.

The report prints, per rostered player: lineup slot, opponent and market
implied total, the injury designation with its source, and measured usage
(snap %, targets, target share, carries, opportunities) plus league points
through the last admissible week. It ranks nothing and recommends nothing.

## The four time invariants, and the tests that hold them

These are the ways a weekly tool lies about time. Each is pinned.

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

The three honesty invariants from milestone 1 still hold: blank is never zero,
a missing schedule renders `?` and never BYE, and "not on this week's injury
report" reads differently from "no injury report loaded".

## Verification (all green, 2026-09-17)

| check | result |
|---|---|
| `python scripts/ci/smoke.py` | PASS — 16 imports, 20 contract files |
| `python scripts/ci/run_summary.py -- python -m pytest` | **279 passed** (main: 239) |
| `node --test scripts/research/warroom/draftroom_logic.test.js` | 10/10 pass |
| `scripts/verify_league_settings.py` (live) | exit 0, 55 constants, no drift |
| `golden_run.py --target weekly_report` A/B | 1/1 byte-IDENTICAL |

The golden A/B here is a **reproducibility** check, not a refactor check: two
independent runs against the same cache produce byte-identical output. The
time-boundary and failed-refresh changes are deliberate behavior changes, so
A/B across them would be meaningless and was not claimed.

Live read-only validation this turn: `pull_week.py` against nflverse and
Sleeper, and `report.py` rendering the real week-2 report. No league
transaction of any kind — `gridiron.sleeper` is GET-only by construction and
`tests/test_sleeper.py::test_module_is_read_only` pins it.

## Provenance and coverage

| source | via | covers | note |
|---|---|---|---|
| weekly player stats | `nflreadpy.load_player_stats` | wk1 | wk2 lands after the slate |
| snap counts | `nflreadpy.load_snap_counts` | wk1 | PFR-keyed, joined through the crosswalk |
| schedules + market lines | `nflreadpy.load_schedules` | wk1–18 | `total_line` is the implied-total input |
| injuries | `nflreadpy.load_injuries` | wk1–2 | designation + practice status |
| id crosswalk | dynastyprocess `db_playerids.csv` | — | Sleeper's own `gsis_id` overlays gaps only |
| league / rosters / matchups | Sleeper public read API | wk2 | no auth, no writes |

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

1. **Astra review of this branch.** Nothing is merged or deployed.
2. **Week 2 rollover check.** After Sunday's slate, `pull_week.py` then
   `report.py` should show `weekly_stats covers wk1-2` and the lag return to
   0. That is the first live exercise of the phase boundary.
3. **DST scoring**, which needs team-level aggregation from nflverse
   play-by-play — the one place the report currently renders a hole.
4. **Then** the rule #5 gate: a projection cannot ship until it beats a
   baseline containing every existing feature, out-of-sample. Roster audit,
   waiver board and start/sit all sit behind that gate.

## Not done deliberately

- No lineup change, waiver claim, trade or message. Ever, by construction.
- No projections, rankings or start/sit advice while the rule #5 gate is
  unmet. A guessed recommendation presented as verified is the failure mode
  this repo exists against.
- No blocked-kick scoring and no DST scoring invented to fill the gap.
- No history rewrite, no merge, no deploy, no PR watcher.
