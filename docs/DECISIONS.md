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
| 2026-09-17 | A report about week N reads weeks <= N-1 pregame, <= N once the slate is COMPLETE; the boundary comes from the report week and phase, never from `max(weeks in cache)` | re-rendering week 2 in week 6 silently read weeks 3-5 — a backtest that sees the future scores itself |
| 2026-09-17 | Weeks past the boundary are WITHHELD and named in the report, and a re-render says its injury designations are after-the-fact | withholding silently is its own dishonesty; the reader has to know the cache is ahead of the report |
| 2026-09-17 | A failed refresh records the failure ONLY: `path`/`rows`/`weeks`/`as_of` keep describing the last pull that succeeded | recording the failure as a new entry stamped it with `now`, so a dead network read as a current pull — and dropped the good cached file over a transient 503 |
| 2026-09-17 | A source whose latest refresh failed can never read FRESH; it renders STALE with the failure reason and still shows its cached rows | stale-but-real, labelled, beats an empty report; the newest thing that happened to that source is a failure and the reader is told |
| 2026-09-17 | Cache writes go through a temp file + `os.replace` | a pull that dies mid-write must not leave a truncated parquet where the last good one was |
| 2026-09-17 | Freshness carries the FULL covered-week set, not just the last; a hole inside the range is its own degradation line | "covers through wk5" off weeks {1,2,4,5} is true and misleading — every season total is short a week and nothing said so |
| 2026-09-17 | Season rollover is a refusal, not a note: a cache season, or a cached Sleeper state season, that disagrees with the requested season exits 2; Sleeper week 0 (offseason) exits 2 unless `--week` is passed | joining last season's roster to this season's box scores produces a confident wrong report |
| 2026-09-17 | The rendered weekly roster report is gitignored (rule #10 carve-out); league-wide id-keyed files stay committed | the `lineup` column is a statement about one manager's players, and the file regenerates from the cache in one command |
| 2026-09-17 | Owner-roster exposure already in history is left alone | rewriting shared history is a bigger hazard than the exposure; the rule binds what gets committed next |
| 2026-09-17 | nflreadpy/pandas/pyarrow approved for the project env only (owner, 2026-09-17); PR2's dependency-free scoring slice merged first and stays dependency-free | the scoring repair was worth shipping without waiting on the dependency call, and it still runs on the stdlib |
| 2026-09-17 | Every source the report READS declares its freshness; `sleeper_players` joined SOURCES | the report took the live injury designation, team, position and gsis overlay from a dump it never aged — a month-old "Questionable" printed under "All inputs current" |
| 2026-09-17 | `sleeper_players` gets an injury-sensitive cadence (24h, 6h on gameday/waiver/designation days) | it carries the live `injury_status`, the one field that can flip an hour before kickoff, and it is not week-keyed so age is the only signal there is |
| 2026-09-17 | A Sleeper designation from a non-FRESH player pull is rendered as STALE with the reason, and `AvailabilityNote.current` is False | the value is live, not week-keyed, so a month-old "Questionable" and a current one are the same five characters on the page |
| 2026-09-17 | `AvailabilityNote.designation_fresh` defaults to False, and `build_report` fails closed when `sleeper_players` is absent from the freshness list | a caller that never established currency is not entitled to the benefit of the doubt on the fastest-staling field |
| 2026-09-17 | `scoring_coverage()` checks a frame's COLUMNS against the league rules; alias-aware, weight-aware, and never reads a value | `fantasy_points` scoring an absent key as zero is right for a null cell and a wrong number for a missing column; the contract stays and this is the gate in front of it |
| 2026-09-17 | A partially present multi-column term (e.g. 2 of 3 lost-fumble columns) is a gap, not a pass | it sums what it has and returns a plausible number, which is worse than a blank one |
| 2026-09-17 | A missing scoring column BLANKS `pts`/`ppg` for the affected positions only, adds a named degradation, and fails `--fail-on-degraded`; usage columns are untouched | a target is a target whatever the scoring columns say, and the two halves of `gridiron.scoring` fail independently |
| 2026-09-17 | `build_report(scoring=...)` is a REQUIRED argument with no default | a default would let a caller publish points without ever looking at the schema behind them |
| 2026-09-17 | Scoring-input validation is BOTH persisted (`Entry.missing_columns`, written by the puller) and re-checked at read time by the report | a stderr warning dies with the run that printed it; the manifest is what the next reader has, and only a read-time check catches a cache edited or written by an older schema since |
| 2026-09-17 | A recorded schema defect is not an error: `error` stays empty, the file stays readable, the as-of stands | the fetch succeeded and the intact columns are still worth reading; conflating the two would discard good data over a renamed column |
