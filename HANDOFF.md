# HANDOFF

Updated: 2026-09-17 (in-season milestone 1: weekly ingest + roster report.
Branch `claude/compassionate-shannon-yq0hw5`, NOT merged — awaiting Astra.)

## Settings: the contradiction is resolved, in favour of VERIFIED

The Sept-8 handoff said settings were verified; CLAUDE.md rule #1 still said
"UNVERIFIED placeholders". The flag was right and the prose was stale.

Evidence, re-pulled live today from the Sleeper league object (status
`in_season`, week 2): **55/55 checked constants match, zero drift** —
scoring weights, roster positions, team count, playoff/trade weeks, FAAB
budget, waiver day and clear days, every kicking and defense weight. The
check is now a committed script, `scripts/verify_league_settings.py`
(exit 0 match / 1 drift / 2 unreachable), and it is AST-tested to be
incapable of writing to `league_config.py`. The verified values are also
pinned in `tests/test_league_config.py`, so drift fails a test rather than
silently changing a number. CLAUDE.md rule #1 and `docs/memory/rules.md`
rule #1 now say verified, and keep the gate: engines still check
`SETTINGS_VERIFIED`, and flipping a flag to unblock a run is still banned.

Constants added from the live `settings` block (they were missing):
`SLEEPER_LEAGUE_ID`, `MY_SLEEPER_USERNAME`, `WAIVER_TYPE` (faab),
`WAIVER_BUDGET` 100, `WAIVER_MIN_BID` 0, `WAIVER_CLEAR_WEEKDAY` 2 (Wed),
`WAIVER_CLEAR_DAYS` 2, `TRADE_REVIEW_DAYS` 2, `MAX_KEEPERS` 1.

## The scoring bug this uncovered

`gridiron.scoring` read `interceptions`, `fumbles_lost` and
`two_point_conversions`. **None of those columns exist in the current
nflverse weekly frame.** Missing keys score as zero, so every interception,
lost fumble and two-point conversion had been silently worth nothing. The
draft board never noticed because it scored projections, not box scores.

Fixed in the one implementation (rule #2): each scoring term now names the
current columns it sums (`passing_interceptions`; `sack_/rushing_/
receiving_fumbles_lost`; `passing_/rushing_/receiving_2pt_conversions`) and
keeps the old name as a fallback read only when no current column is present,
so pre-rewrite frames still score and a frame carrying both spellings cannot
double-count. `kicker_points` is new. Two external reconciliations, both
pinned on committed fixtures:

- **357/357** offensive player-weeks of 2026 week 1 reproduce
  `(fantasy_points + fantasy_points_ppr) / 2` exactly (max abs error 0.0).
- **147/147** rostered offensive player-weeks and **12/12** kickers reproduce
  Sleeper's own `players_points` under this league's scoring.

`fumbles_lost_total` looks like the right column and is not — it counts
return fumbles and misses by 2 rows. Blocked kicks are deliberately unmapped
(no blocked kick in week 1, so blocked-as-miss is UNVERIFIED).

## What shipped (milestone 1)

Weekly workflow, in two commands:

    PYTHONPATH=src python scripts/ingest/pull_week.py      # network
    PYTHONPATH=src python scripts/weekly/report.py --write # offline

New in `src/gridiron/` (production boundary; scripts are thin drivers):

- `ids.py` — the ONE crosswalk (rule #3). Source of record is dynastyprocess
  `db_playerids.csv`: **168/168** rostered non-DST players resolve, against
  36/168 from Sleeper's own sparse `gsis_id` field (kept as a gap-filling
  overlay that never overrides). `nflreadpy.load_ff_playerids()` 403s through
  the egress proxy; we read raw.githubusercontent.com directly. Unresolved
  ids are a reported result, never a name match and never a silent drop.
- `sleeper.py` — read-only league adapter, GET-only by construction, with a
  test that fails the build if a write verb appears. Needs no credential.
- `freshness.py` — cadence-aware staleness in ET (rule #8: injuries tighten
  from 48h to 12h on gameday/designation/waiver days), plus `WeekContext`,
  which separates the week the report is ABOUT from the last week box scores
  exist for and flags the gap.
- `ingest.py` — the season cache manifest. Freshness is read from the PULL
  time, never a file mtime; a failed pull is recorded as a failure so the
  report degrades visibly instead of rendering last week's numbers.
- `usage.py` — opportunity and efficiency as explicitly separate column
  groups (rule #6), gsis-anchored, snaps joined through the gsis→pfr edge
  (99.2% coverage on the live frame). `season_to_date(through_week=)` is the
  single chronological truncation every as-of read goes through.
- `weekly.py` — the report. Prints measured usage, league points, injury
  designation, opponent and the market's implied team total. It ranks
  nothing.

Verification: smoke green (14 imports, 18 files); full suite **231 passed**
(was 74); war-room `node --test` 10/10 unchanged. `weekly_report` registered
as a `golden_run.py` target against the stable `weekly_report_latest.csv`.

## The three honesty invariants, and why they have tests

1. **Evidence of absence ≠ absence of evidence.** "Not on the week-2 injury
   report" and "we never loaded a week-2 injury report" render differently,
   and a week-1 designation shown in week 2 is labelled `wk1 report: Out —
   NO wk2 designation yet`. Rule #11 has teeth: there is no `is_startable`
   boolean, and a test bans one from appearing.
2. **Blank is blank.** "No snap row" renders blank, not 0 — it is a
   different fact from "played no snaps". A player with no box score gets a
   blank `g`/`pts`/`ppg`, not `0.0`. A team missing from a schedule we could
   not load renders `?`, not `BYE`.
3. **No guessed recommendation.** Nothing has passed the rule #5 gate, so the
   report opens with `NO PROJECTION MODEL SHIPPED` and carries no
   projection/rank/recommend column — a test asserts the absence.

## Next

1. **Astra review of this branch.** Nothing merged, nothing deployed.
2. Open question for the owner: should `data/outputs/week{NN}_report.*` keep
   being committed (rule #10 says yes for weekly CSVs) now that it contains
   the roster? `week02_report.*` is committed here as the first example.
3. Build step 3 baseline — usage prior × efficiency × line multiplier, with
   the corrected scoring. Must beat a baseline containing ALL existing
   features out-of-sample before any number reaches the report (rule #5).
   Chronological split only; `season_to_date(through_week=)` is the boundary.
4. Then roster audit → waiver board → start/sit, in that order, each gated on
   evidence. FAAB constants are now available for the waiver board; the §7
   bid guidance in QUANT_FOUNDATIONS is still UNVERIFIED.
5. Post-draft ledger (carried from the last handoff, still not done):
   `scripts/research/record_draft_2026.py` writes `data/ledger/draft_2026.csv`
   and grades the board's `p{k}`/`ph{k}` predictions. The ADP-only vs
   history-aware comparison decides which model the in-season tools trust.
6. Reconcile the QUANT_FOUNDATIONS §5–7 verification failures (381/382,
   177/12, 66/9) — unchanged from the last two handoffs.
7. DST scoring has no implementation (nflverse weekly is player-level). DST
   rows currently carry no points. Either aggregate team stats or read
   Sleeper's actuals.

## Not done deliberately

- No projections, rankings, start/sit advice or waiver board. Milestone 1 is
  evidence only.
- No skills yet (rule #12). The report is the thing a skill would wrap;
  wrapping it before the numbers are trustworthy would be premature.
- No new dependencies beyond the ones `requirements.txt` already planned for
  ingest (nflreadpy, pandas, pyarrow). `gridiron.weekly` renders its own
  markdown table rather than adding `tabulate`.
- Blocked-kick scoring left unmapped rather than guessed.
- `scripts/research/` left untouched; the name-join hygiene test scopes to
  the in-season path only.
