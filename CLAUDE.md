# gridiron — fantasy football decision engine

Keep this file tight. The ceiling is enforced by `tests/test_claude_md_budget.py`
(two-sided ratchet). Detail goes in `docs/memory/` with a one-line headline here.

## Orientation
- Seed doc: `docs/BOOTSTRAP_FROM_PLV.md` — the plv_clone lessons this repo is
  built on. Read it before proposing architecture.
- Current state and next step: `HANDOFF.md`. Decisions: `docs/DECISIONS.md`,
  ADRs in `docs/adr/`.
- `src/gridiron/` is the production boundary; scripts import from it, never
  the reverse. `paths.py` is the ONLY place the repo root is computed.
- Verified math + constants: `docs/research/QUANT_FOUNDATIONS.md`. Read §1.4
  (Vegas moves efficiency, not volume) before touching projections.

## Commands
- After ANY change: `python scripts/ci/smoke.py` (offline, <60s).
- Full suite: `python scripts/ci/run_summary.py -- python -m pytest`
  (never run bare pytest into agent context — the summary wrapper exists so
  output doesn't flood the window).
- Behavior-preserving refactors: `python scripts/ci/golden_run.py` A/B.
- Weekly (in-season): `PYTHONPATH=src python scripts/ingest/pull_week.py` then
  `PYTHONPATH=src python scripts/weekly/report.py --write`. Report reads the
  cache only — offline, and it states every input's as-of week and staleness.
- Decision board (offline): `PYTHONPATH=src python scripts/weekly/dashboard.py
  --write`; scenarios + phone-fit check: `scripts/weekly/dashboard_scenarios.py
  --screenshot`. Freshness GATES actions (`gridiron.gating`), locks are
  three-valued, absence is NEVER a bye unless the schedule declares one, and
  the board is built in the cloud as a private artifact with its records AND
  its last-good inputs carried between runs by `gridiron.carryover` (an
  Actions cache, not durable storage). Game Day (`scripts/weekly/gameday.py`,
  scenarios A-F in `gameday_scenarios.py --screenshot --browser`): platform
  actuals only, game status OBSERVED or UNKNOWN (never inferred from the
  clock), legal moves re-judged by player id, no live win odds, and a
  one-tap read-only refresh inside the artifact file itself.
- Settings drift check: `PYTHONPATH=src python scripts/verify_league_settings.py`.

## Rules (full text in docs/memory/rules.md — cite by number)
1. League settings are VERIFIED (2026-09-08, re-verified 2026-09-17: 55/55
   constants, `scripts/verify_league_settings.py`). Engines still gate on
   `SETTINGS_VERIFIED`; never flip a flag to unblock a run.
2. Scoring has ONE implementation: `gridiron.scoring.fantasy_points`. Never
   copy a weight into a script.
3. Every join anchors on a stable player id (nflverse `gsis_id`/`player_id`;
   platform ids via ONE cached crosswalk). Never name-match, never `.str.contains`.
4. Derive role from usage (snap/route/target share), never from the roster
   position tag.
5. No model feature ships without beating a baseline that contains ALL
   existing features, out-of-sample — enforced by import-time assert once
   models exist.
6. Opportunity (volume) is modeled explicitly and fast; efficiency is a
   slow-moving prior. In-season usage deltas are real; efficiency deltas are noise.
7. Decisions are denominated in ΔP(win), not projected points. Log every
   decision WITH the rejected side; grade the choice, not the projection.
8. Alerts fire on TRANSITIONS only; freshness checks are cadence-aware
   (the week has a shape: Wed waivers, Fri designations, Sun inactives).
9. Credentials live in `.env` (gitignored) only, prefix `GRIDIRON_`, read via
   `gridiron.config`. Never write a credential into a tracked file.
10. Don't commit bulk data (`data/research/cache/` ignored) or roster-bearing
    weekly reports (`data/outputs/week*_report.*` ignored — they name the
    owner's players); DO commit the small projection/ledger CSVs.
11. "Questionable" ≠ out; "on roster" ≠ startable. No convenience accessor
    that makes the wrong call easy.
12. Start with 5 skills max (roster-audit, waiver-board, start-sit, matchup,
    decision-log) plus a registry test. Resist premature skill growth.
