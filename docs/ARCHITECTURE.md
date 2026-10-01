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
3. **Baseline projection** — `gridiron/projection.py`: usage prior ×
   positional efficiency × line multiplier (NOT share × implied total;
   DECISIONS 2026-09-04). Transparent components, chronological evidence
   cut, abstention. `gridiron/models/validated_signals.py` is the rule #5
   registry (empty FEATS; import-time assert). `gridiron/evaluate.py`
   scores it chronologically against PPG-to-date and last-week. Labelled
   UNVALIDATED on every render. *(built, step 3 — baseline only)*
4. **Lineup** — `gridiron/lineup.py`: legal best lineup under kickoff
   locks, single-swap alternatives with z-scores. Weekly start/sit only;
   no season VOR yet. *(built, step 4 — weekly half)*
5. **Waiver board** — `gridiron/waivers.py`: available pool × this week's
   best-legal-lineup delta, each add paired with an explicit drop. No FAAB
   pricing, no ROS value, no ROLE_GAIN alerts yet. *(built, step 5 — this
   week only)*
6. **Decision layer** — `gridiron/decisions.py` (decision-time archive +
   leak-proof grading) and `gridiron/dashboard.py` (assembly + offline
   HTML; closed-form P(win) labelled UNCALIBRATED). Matchup Monte Carlo and
   playoff-equity denomination not built. *(built, step 6 — ledger half)*

Drivers: `scripts/weekly/dashboard.py` (offline render) and
`scripts/weekly/dashboard_scenarios.py` (complete/stale/missing synthetic
pages + screenshots).

## Boundaries

- `src/gridiron/` is the production package; `scripts/` are drivers that
  import from it. Nothing in `src/` imports from `scripts/`.
- One auth home (`espn.py`), one paths module (`paths.py`), one scoring
  implementation (`scoring.py`), one settings object (`config.py`).
- Data flow: `data/research/cache/` (bulk, ignored) → pipelines →
  `data/outputs/` + `data/ledger/` (small, committed).
- Network lives in `scripts/ingest/` and `scripts/sync/` only.
  `scripts/weekly/report.py` and `scripts/weekly/dashboard.py` are
  offline by construction, so a report can always be re-rendered and always
  states the as-of time of what it rendered from.
- Time discipline: `WeekContext` separates the week the report is ABOUT from
  the last week box scores exist for, and `season_to_date(through_week=)` is
  the single chronological truncation every as-of read goes through.
