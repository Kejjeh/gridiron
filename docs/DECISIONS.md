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
| 2026-09-08 | League settings VERIFIED from the Sleeper API: 12-team half-PPR, INT −1, QB/2RB/2WR/TE/2FLEX/K/DEF + 5 BN + 1 IR, 15-round snake, slot 1 | `pull_sleeper.py`; `SETTINGS_VERIFIED=True` flipped in the same commit as the values |
| 2026-09-08 | `DEFAULT_SCORING` IS the league's rules; tests needing full-PPR pass `ScoringRules(reception=1.0)` explicitly | rule #2 — one implementation, real weights |
| 2026-09-08 | Draft projections = ½ Sleeper stat-line (scored in-repo) + ½ ECR-implied points; replacement by lineup order-statistic fill (RB33/WR41/TE13/QB13) | `draft_board_2026.py`; FantasyPros projection pages are JS-paginated |
| 2026-09-08 | Draft availability uses a 60/40 Sleeper/FFC ADP blend with sd = 0.57 + 0.11·ADP | the room drafts on Sleeper; sd fitted on FFC per-player spread |
| 2026-09-08 | Draft policy: best static VOR each pick, K/DEF last two rounds; elite TE at the 2/3 turn | beat scripted RB/WR openings by ~100 lineup pts over 300 sims (`DRAFT_2026_PLAN.md`) |
| 2026-09-08 | War-room page logic lives in ONE place per language: `gridiron/draft.py` (Python) and `scripts/research/warroom/draftroom_logic.js` (page), each with tests pinning the same numbers | audit found the page and the script had drifted into two hand-copies of the same math |
| 2026-09-08 | Page confirmations never use `window.confirm`/`prompt` (the artifact sandbox swallows them); use in-page two-tap controls | Reset button silently did nothing |
| 2026-09-17 | League settings RE-VERIFIED live (55/55 constants, zero drift); CLAUDE.md rule #1's "unverified placeholders" prose was stale, the flag was not | `scripts/verify_league_settings.py`; the Sept-8 pull was real, the headline just never followed it |
| 2026-09-17 | Verification is standing, not one-off: a checker script + pinned values in `tests/test_league_config.py`; the checker is AST-tested to be write-free | leagues get edited mid-season; a checker that can edit what it checks can flip a flag to pass |
| 2026-09-17 | A constant absent from the live payload is reported as drift (`live=None`), never skipped; omitted kicking/defense weights are counted in the output | a check that silently covers less than it did last week fails the same way as one that passes wrongly |
| 2026-09-17 | Scoring reads the CURRENT nflverse columns (`passing_interceptions`, the three `*_fumbles_lost`, the three `*_2pt_conversions`) with the legacy names kept as read-only fallbacks | the rewritten nflverse schema made INTs, fumbles and 2-pointers score as ZERO — a wrong number, not a missing one |
| 2026-09-17 | `fumbles_lost_total` is NOT the fumble input: it counts return fumbles | 2 mismatches vs nflverse on the 2026 wk-1 frame; the 3-component sum reconciles exactly (357/357) |
| 2026-09-17 | Kicker points ship (`scoring.kicker_points`, KICKING_SCORING onto nflverse `fg_made_*`/`pat_*`); blocked kicks deliberately unmapped | 12/12 exact vs Sleeper actuals; no blocked kick in wk 1, so the blocked-as-miss question is UNVERIFIED |
| 2026-09-17 | Sleeper->gsis crosswalk source of record is dynastyprocess `db_playerids.csv`, fetched from raw.githubusercontent.com | 168/168 rostered players resolve vs 36/168 from Sleeper's own sparse `gsis_id`; `nflreadpy.load_ff_playerids()` 403s through the egress proxy |
| 2026-09-17 | Sleeper's own `gsis_id` is kept as an OVERLAY that only fills gaps, never overrides | a brand-new player dynastyprocess hasn't picked up still resolves without a name match |
| 2026-09-17 | The Sleeper adapter is read-only by construction and tested for it; lineups/waivers/trades/messages stay a human's job | `gridiron/sleeper.py`, `tests/test_sleeper.py::test_module_is_read_only` |
| 2026-09-17 | Freshness is cadence-aware in ET, not one TTL: injuries tighten from 48h to 12h on gameday/designation/waiver days | rule #8 — the week has a shape |
| 2026-09-17 | Report freshness is read from the ingest MANIFEST's pull time, never a file mtime | a file rewritten from cache is not fresher data |
| 2026-09-17 | "Not on this week's injury report" (evidence) and "no injury report loaded" (no evidence) render differently; a prior week's designation is labelled with its week | collapsing them is how a report tells you a hurt player is fine |
| 2026-09-17 | A team absent from a schedule we could not load renders `?`, not BYE | a missing schedule must not invent a bye week |
| 2026-09-17 | Milestone 1 ships NO projections, rankings or lineup advice; the report prints measured usage + market lines and says so at the top | rule #5 gate is unmet; a guessed recommendation presented as verified is the failure mode the repo exists against |
| 2026-09-17 | Weekly outputs are written to `data/outputs/week{NN}_report.*` plus a stable `weekly_report_latest.*` pair for golden_run A/B | golden_run compares literal paths; the week-stamped name changes weekly |
| 2026-09-17 | Scoring's test suite reads its fixtures with stdlib `csv`, not pandas, and uses no shared conftest fixture | the scoring implementation is stdlib-pure; its tests must run on the repo's declared dependencies alone or the purity claim is untested |

