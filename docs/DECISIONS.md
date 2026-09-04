# Decision ledger (project decisions — not the in-season decision ledger)

Settled questions, so nobody (human or model) re-litigates them. One line
each; link an ADR when the reasoning is long. The in-season START/SIT/WAIVER
ledger is data (`data/ledger/`), not this file.

| Date | Decision | Why / where |
|---|---|---|
| 2026-09-03 | Package name `gridiron`, repo lives in `Projects/Football` | valid importable identifier; bootstrap doc suggested it |
| 2026-09-03 | Ported run_summary.py verbatim; smoke/golden_run adapted (lists only) | ADR-0001 |
| 2026-09-03 | League settings are placeholders gated by `SETTINGS_VERIFIED=False` | real settings unavailable at bootstrap; rule #1 |
| 2026-09-03 | Stat keys follow nflverse weekly column names | no renaming at join boundary; scoring.py |
| 2026-09-03 | golden_run TARGETS starts empty; register pipelines as they land | no pipelines exist yet |
| 2026-09-04 | Ingest uses `nflreadpy`, NOT `nfl_data_py` (deprecated, repo archived 2025-09-25, last release 2024-09) | bootstrap doc §3 is stale on this; research draft data2026 |
| 2026-09-04 | Betting lines come free from nflverse `load_schedules()` (`spread_line`, `total_line`, moneylines, roof/wind); The Odds API only if we need intraweek line moves | ~108 credits/season needed vs 500/mo free tier |
| 2026-09-04 | nflverse `spread_line` sign is HOME-POSITIVE; convert at ingest via `vegas.implied_totals_from_nflverse` | verified corr(spread_line, home margin)=+0.49 on 2024 |
| 2026-09-04 | Sportsbook sign convention (negative = favored) is the package-internal one | `vegas.implied_team_total` docstring |
| 2026-09-04 | Build-step-3 baseline shape: volume from usage priors, efficiency = positional prior × line multiplier (`vegas.receiving_efficiency_multiplier`). NOT "share × implied total" | Vegas signal is 92% efficiency for receivers; volume-on-line has ~0 OOS skill (QUANT_FOUNDATIONS §1.4) |
| 2026-09-04 | Line→scoring/efficiency coefficients live in `gridiron.vegas`; volume functions are documented as league means only | all passed leave-one-season-out (rule #5); volume did not |
| 2026-09-04 | Positional efficiency stored as components (catch, Y/T, TD/T); points computed via `gridiron.scoring` weights | rule #2 — constants follow real scoring once rule #1 flips |
| 2026-09-04 | Variance-seeking threshold: chase ceiling only when trailing by ≳6 projected pts; Φ derivative alone is wrong-signed at deficit 5 | gamma-marginal MC, QUANT_FOUNDATIONS §2.4 |
| 2026-09-04 | Ingest client uses a plain script User-Agent (never spoof a browser); nflverse lines = DraftKings close via ESPN | site.api.espn.com 403s browser UAs only |
| 2026-09-04 | Pin `pandas>=2.0,<4` at ingest; test on 3.x | `>=2.0` resolves to 3.0.5 (copy-on-write, new string dtype) |
| 2026-09-04 | §5-7 of QUANT_FOUNDATIONS ship as UNVERIFIED; their constants live in `shrinkage.py` behind an explicit warning banner and may not reach user-facing output | verifier agents were killed by a usage limit; sibling verified sections had 14 corrections |
| 2026-09-04 | Shrinkage n0 for WR/TE target share = 90 team targets (~3 games); RB carry-share prior decays to zero by week 6 | out-of-sample RMSE grid vs rest-of-season share (QUANT_FOUNDATIONS §5.3) |
| 2026-09-04 | Waiver/start-sit replacement is always a FORWARD max over the pool, never a season-to-date rank | Henderson 2025: RB42 through wk 8, then 17.0 PPG (§6.6) |
| 2026-09-04 | Treat the flex as a WR slot when shaping replacement level (~10 of 12 flex slots are WR in full PPR) | order-statistic fill on 2023-25 (§6.2); revisit if the league is half-PPR |
| 2026-09-04 | FAAB: never bid 10-19% of budget; bid $1 above focal round numbers; expect optimal bids to RISE through the season | dead-zone clearing data + shadow-price dynamic program (§7) |
| 2026-09-04 | The §5-7 verifier SCRIPTS are committed and re-runnable even though their agents were killed; their pass/fail counts are the doc's evidence of record | 381/382, 177/12, 66/9 — better than no verification, worse than a reconciled one |
