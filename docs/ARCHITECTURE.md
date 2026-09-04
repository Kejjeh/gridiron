# Architecture

Seeded 2026-09-03; grows with each build step. The design rationale lives in
docs/BOOTSTRAP_FROM_PLV.md — this file tracks what actually exists.

## Layers (planned; bold = built)

1. **Guardrails** — `scripts/ci/` (smoke, run_summary, golden_run),
   hygiene/contract tests, CLAUDE.md budget ratchet. *(built, step 1)*
2. Ingest — nflverse weekly/snaps + id crosswalk into `data/research/cache/`;
   league connector behind `gridiron/espn.py`. *(step 2)*
3. Baseline projection — opportunity share × implied team total ×
   positional efficiency prior. Everything is measured against this. *(step 3)*
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
