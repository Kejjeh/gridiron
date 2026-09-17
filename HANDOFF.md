# HANDOFF

Updated: 2026-09-17 (dependency-free slice: offensive scoring repair +
standing league-settings verification)

## This branch — read this first

`claude/scoring-settings-slice`, cut from `main` (83d3255). It is the half of
the in-season milestone that needs **no new dependencies**, peeled off so it
can be reviewed and landed while the dependency question on the full
milestone is still open.

**The full milestone PR (`claude/compassionate-shannon-yq0hw5`) is untouched.**
It is blocked pending an owner/Astra decision on nflreadpy / pandas /
pyarrow; the analysis is in `docs/review/MILESTONE1_DEPENDENCY_REVIEW.md`
**on that branch**. Nothing here installs, merges or presumes that decision.

**In this slice**
- `src/gridiron/scoring.py` — the repair. nflverse's stats rewrite renamed
  `interceptions`, `fumbles_lost` and `two_point_conversions`; the old code
  read the dead names, and a missing column scores ZERO. Every interception,
  lost fumble and two-point conversion silently vanished. Each term now names
  the current columns it sums plus the legacy key it replaced, read only when
  no current component is present, so a frame carrying both never
  double-counts. Adds `kicker_points` and `scoring_inputs`. Still exactly one
  scoring implementation (rule #2). Imports: stdlib + `league_config`.
- `src/gridiron/league_config.py` — `SLEEPER_LEAGUE_ID`,
  `MY_SLEEPER_USERNAME`, the waiver/keeper constants read off the live
  league, and the re-verification provenance.
- `src/gridiron/sleeper.py` — GET-only Sleeper adapter (stdlib `urllib`), the
  verifier's transport. No credential; no method that can change the league;
  `tests/test_sleeper.py::test_module_is_read_only` fails the build if one
  appears.
- `scripts/verify_league_settings.py` — the standing drift check. Read-only,
  never writes `league_config.py` (AST-tested), exits 0 match / 1 drift / 2
  unreachable.
- Docs: rule #1 in `CLAUDE.md` + `docs/memory/rules.md`, and the eight
  scoring/settings rows in `docs/DECISIONS.md`.

**Deliberately NOT in this slice**: `requirements.txt` (byte-identical to
main), `ids/freshness/ingest/usage/weekly`, `scripts/ingest/pull_week.py`,
`scripts/weekly/report.py`, the `golden_run.py` target, `ARCHITECTURE.md`,
`data/outputs/`, and the five ingest-only fixtures. No projections, no
rankings, no lineup advice — the rule #5 gate is unmet and this slice does
not touch it.

**`tests/conftest.py` is not in the diff.** It is exactly main's. The
milestone's pandas-backed fixtures are not reachable from here: the scoring
tests read their fixtures with stdlib `csv`, the Sleeper tests with `json`,
each defined in the test file that uses them.

### Evidence

Red before / green after, same fixture, only `scoring.py` changed:

| | main 83d3255 | this branch |
|---|---|---|
| rows matching nflverse's own scored columns | 10/17 | **17/17** |
| worst row | Baker Mayfield +6.00 (3 lost fumbles), Drake Maye +6.00 (3 INTs) | 0.00 |
| both spellings present (`sack_fumbles_lost` + `fumbles_lost`) | −10.0 (double-counted) | **−2.0** |
| legacy-only frame (`interceptions`, `fumbles_lost`) | correct | correct (unchanged) |
| kickers vs Sleeper actuals | `kicker_points` did not exist | **12/12 exact** |

`tests/test_scoring.py` (main's hand-computed pins, which use the legacy
`interceptions` key) passes unchanged — the repair is backward compatible.

Validation run on this branch:
- `python scripts/ci/smoke.py` → PASS (11 imports, 14 files)
- `python scripts/ci/run_summary.py -- python -m pytest` → **136 passed**
  (main baseline: 89)
- `PYTHONPATH=src python scripts/verify_league_settings.py` → exit 0,
  55 constants, no drift, live and in_season
- Dependency proof: the 58 tests across `test_scoring_nflverse`,
  `test_scoring`, `test_sleeper`, `test_verify_league_settings` and
  `test_league_config` all pass with `pandas`, `pyarrow`, `nflreadpy` and
  `polars` made **unimportable** by a `sys.meta_path` blocker.
- `golden_run.py` A/B: **not applicable.** `TARGETS` is empty on main, and
  the scoring change is a deliberate behavior change, not a refactor.

### One behavior change beyond PR1's version

`compare()` in `verify_league_settings.py` now reports a `settings` key the
live payload omits as **drift** (`live=None`) instead of skipping it, which
is what the scoring block already did; and `unchecked_weights()` counts
kicking/defense weights Sleeper does not return, so a check that quietly
covers less than last week is visible. Found by running the checker against
the saved fixture. No effect on the live league — all 55 constants are
present, still zero drift.

`src/gridiron/scoring.py` here differs from PR1's copy in two docstring hunks
only (the DST paragraph and `scoring_inputs`, which referenced
`gridiron.weekly` and `tests/test_weekly.py` — neither exists in this slice).
The code is byte-identical, so PR1 rebases with those two hunks as its only
conflict.

## State

**League settings are VERIFIED** (`league_config.SETTINGS_VERIFIED = True`).
Platform Sleeper, league 1389720742551093249 (id in `.env`, pulled by
`scripts/research/pull_sleeper.py`). 12 teams, half-PPR, INT −1,
QB/2RB/2WR/TE/2FLEX/K/DEF + 5 BN + 1 IR, 15-round snake, Josh at slot 1.
Rule #1 no longer blocks; K/DEF weights live in `league_config` as dicts.

**Code** — smoke green; 136 tests (89 on main + 47 here):
- Bootstrap skeleton + pure-math modules unchanged (`winprob`, `shrinkage`,
  `season`, `vegas`). Tests that pinned full-PPR now pass an explicit
  `ScoringRules(reception=1.0)`; `DEFAULT_SCORING` is the league's rules.
- `scripts/research/pull_sleeper.py`, `pull_nflverse_2026.py`,
  `pull_fantasypros.py` — draft-day pulls into `data/research/cache/draft2026/`
  (gitignored). nflreadpy 0.1.5 works; `load_injuries(2026)` refuses
  (season cap 2025) and 2026 stats 404 until week 1 lands.
- `scripts/research/draft_board_2026.py` — projections under league scoring,
  replacement by lineup fill, VOR, ADP-availability model, 300-draft Monte
  Carlo. Output committed: `data/outputs/draft2026_board.csv`.

- `src/gridiron/draft.py` — pure draft math (snake order, ADP survival,
  lineup-fill replacement, optimal lineup), written test-first in
  `tests/test_draft.py`; the board script imports it.
- `scripts/research/warroom/` — the war-room page source: template,
  `draftroom_logic.js` (mirrors `draft.py`; `node --test` in that dir, 7
  tests), and `warroom_build.py` which emits
  `data/outputs/draft2026_warroom.html`, the file published as the artifact.

**Draft plan** — `docs/research/DRAFT_2026_PLAN.md`. Gibbs at 1; Bowers at
the 2/3 turn (97% there at 24); static best-VOR beat every scripted opening
by ~100 lineup points. The live tool is the "1.01 War Room" artifact
(tracks picks, recomputes survival odds to the next pick, localStorage).

**Research** — `docs/research/QUANT_FOUNDATIONS.md` unchanged: §1, §2, §4
verified; §5–7 partly (381/382, 177/12, 66/9). The half-PPR replacement
question from §6 was answered empirically today: the 24 flex slots filled
16 WR / 8 RB on the 2026 projection curve, so replacement = RB33 / WR41 /
TE13, not the full-PPR RB25 / WR35.

**Room + injuries (draft day, later)** — `docs/research/COMPETITION_2026.md`
(manager profiles from 2023–25 Sleeper history; per-slot QB/TE/RB/WR timing
shifts feed the sim, which exports history-aware survival odds `ph{pick}`
into the board and the page) and `docs/research/INJURY_EFFECTS.md`
(Questionable-and-played = 0.84; RB ankle/knee returns 0.77/0.79 for six
games). `gridiron.ledger` (test-first) records the real draft with the
rejected side per pick and grades the survival predictions.

## Next

1. **Astra review of this slice.** It is standalone: it runs on main's
   declared dependencies, and landing it fixes a scoring bug that is silently
   wrong on `main` today. Not merged, not deployed.
2. **The dependency decision on the full milestone** (nflreadpy / pandas /
   pyarrow) — owner/Astra call, unresolved. Nothing installed. If it is
   approved, PR1 rebases onto this slice; if it is refused, the
   stdlib-only alternatives are costed in that branch's review doc.
3. **After the draft**: `PYTHONPATH=src python scripts/research/record_draft_2026.py`
   writes `data/ledger/draft_2026.csv` and prints my picks with the rejected
   side plus the Brier score of the board's `p{k}`/`ph{k}` predictions.
   Commit the ledger. Then compare ADP-only vs history-aware odds on the
   real picks to decide which model the in-season tools should trust.
4. Build step 2 ingest for the season: nflreadpy weekly + snaps + schedules
   lines, Sleeper league rosters/matchups each Tuesday. Cached 2023–25 data
   already exists.
5. Build step 3 baseline with the corrected shape (usage prior × efficiency ×
   line multiplier), now with real scoring. Register in `golden_run.py`.
6. Reconcile the §5–7 verification failures (unchanged from last handoff).
7. First skills (rule #12): roster-audit and waiver-board are the immediate
   in-season needs; waivers clear Wed 3 AM ET.

## Not done deliberately

- No skills yet.
- Blocked-kick scoring is unmapped on purpose: there was no blocked kick in
  the week-1 reconciliation, so whether Sleeper scores a blocked attempt as a
  miss is UNVERIFIED. Guessing would silently change kicker points.
- No DST scoring. nflverse weekly data is player-level, so DST points have to
  be aggregated from team stats. Until that exists a DST stat line scores
  nothing here, and a caller must render it as ABSENT, never as zero points.
- The dynamic VONA policy in the board script underperformed static VOR
  because its need weights were hand-set; left as-is rather than tuned on
  draft day.
- FantasyPros projection pages only render 10 rows server-side; the board
  used Sleeper projections + ECR-implied points instead.
