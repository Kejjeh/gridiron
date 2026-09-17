# Architecture

Seeded 2026-09-03; grows with each build step. The design rationale lives in
docs/BOOTSTRAP_FROM_PLV.md — this file tracks what actually exists.

## Layers (planned; bold = built)

1. **Guardrails** — `scripts/ci/` (smoke, run_summary, golden_run),
   hygiene/contract tests, CLAUDE.md budget ratchet. *(built, step 1)*
2. **Ingest** — `scripts/ingest/pull_week.py` pulls nflverse weekly stats /
   snaps / schedules / injuries, the dynastyprocess id crosswalk and a
   read-only Sleeper snapshot into `data/research/cache/season{YEAR}/`, with
   a manifest recording each source's pull time, rows and covered weeks.
   `gridiron/ids.py` is the one crosswalk, `gridiron/sleeper.py` the one
   league connector (GET-only; `espn.py` stays for an ESPN league).
   `gridiron/freshness.py` turns manifest timestamps into cadence-aware
   FRESH/STALE/MISSING, and `gridiron/weekly.py` + `scripts/weekly/report.py`
   render the roster evidence table offline. *(built, step 2)*
3. Baseline projection — usage prior × positional efficiency × line
   multiplier (NOT share × implied total; DECISIONS 2026-09-04). Everything
   is measured against this. Until it passes the rule #5 gate, the weekly
   report ships no projection and says so. *(step 3)*
4. VOR / lineup — forward-looking, league-shaped replacement level; flex
   logic; start/sit table. *(step 4)*
5. Waiver board — FA pool × ROS VOR delta × FAAB pricing; ROLE_GAIN
   transition alerts. *(step 5)*
6. Decision layer — ledger + matchup Monte Carlo; everything denominated in
   ΔP(win). *(step 6)*

## Boundaries

- `src/gridiron/` is the production package; `scripts/` are drivers that
  import from it. Nothing in `src/` imports from `scripts/`.
- One auth home (`espn.py`), one paths module (`paths.py`), one scoring
  implementation (`scoring.py`), one settings object (`config.py`).
- Data flow: `data/research/cache/` (bulk, ignored) → pipelines →
  `data/outputs/` + `data/ledger/` (small, committed).
- Network lives in `scripts/ingest/` only. `scripts/weekly/report.py` is
  offline by construction, so a report can always be re-rendered and always
  states the as-of time of what it rendered from.
- Time discipline: `WeekContext` separates the week the report is ABOUT from
  the last week box scores exist for, and `season_to_date(through_week=)` is
  the single chronological truncation every as-of read goes through.
