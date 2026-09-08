# HANDOFF

Updated: 2026-09-08 (draft-day session; league settings verified, draft
board + Monte Carlo built, war-room artifact published)

## State

**League settings are VERIFIED** (`league_config.SETTINGS_VERIFIED = True`).
Platform Sleeper, league 1389720742551093249 (id in `.env`, pulled by
`scripts/research/pull_sleeper.py`). 12 teams, half-PPR, INT −1,
QB/2RB/2WR/TE/2FLEX/K/DEF + 5 BN + 1 IR, 15-round snake, Josh at slot 1.
Rule #1 no longer blocks; K/DEF weights live in `league_config` as dicts.

**Code** — smoke green; 74 tests:
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

**Draft plan** — `docs/research/DRAFT_2026_PLAN.md`. Gibbs at 1; Bowers at
the 2/3 turn (97% there at 24); static best-VOR beat every scripted opening
by ~100 lineup points. The live tool is the "1.01 War Room" artifact
(tracks picks, recomputes survival odds to the next pick, localStorage).

**Research** — `docs/research/QUANT_FOUNDATIONS.md` unchanged: §1, §2, §4
verified; §5–7 partly (381/382, 177/12, 66/9). The half-PPR replacement
question from §6 was answered empirically today: the 24 flex slots filled
16 WR / 8 RB on the 2026 projection curve, so replacement = RB33 / WR41 /
TE13, not the full-PPR RB25 / WR35.

## Next

1. **After the draft**: log the actual picks (Sleeper `draft/{id}/picks`) to
   `data/ledger/draft_2026.csv` with the board's projected value at each
   pick, and grade the room's ADP model (was sd = 0.57 + 0.11·ADP right?).
2. Build step 2 ingest for the season: nflreadpy weekly + snaps + schedules
   lines, Sleeper league rosters/matchups each Tuesday. Cached 2023–25 data
   already exists.
3. Build step 3 baseline with the corrected shape (usage prior × efficiency ×
   line multiplier), now with real scoring. Register in `golden_run.py`.
4. Reconcile the §5–7 verification failures (unchanged from last handoff).
5. First skills (rule #12): roster-audit and waiver-board are the immediate
   in-season needs; waivers clear Wed 3 AM ET.

## Not done deliberately

- No skills yet.
- The dynamic VONA policy in the board script underperformed static VOR
  because its need weights were hand-set; left as-is rather than tuned on
  draft day.
- FantasyPros projection pages only render 10 rows server-side; the board
  used Sleeper projections + ECR-implied points instead.
