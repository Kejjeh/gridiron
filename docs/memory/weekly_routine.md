# Weekly routine

The loop the owner and Claude run every week, as four skills in
`.claude/skills/` (rule #12's names; `tests/test_skill_registry.py` keeps them
honest). Every step is read-only: nothing dispatches a workflow, calls
Sleeper's player map, or submits anything to the league.

| when | skill | command |
|---|---|---|
| any time | roster-audit | `PYTHONPATH=src python scripts/weekly/fetch_record.py`, then `scripts/weekly/weekly_review.py --record <path>` |
| Tue-Sat | waiver-board | `weekly_review.py --record <path> --ranks <csv> --watch "Name,POS"` |
| before kickoffs | start-sit | `weekly_review.py --record <path>` (Action Desk + "Projection vs actual usage") |
| Tue after the week | decision-log | `scripts/weekly/grade_week.py --week W --archives <path> --before <Sunday kickoff> --actuals-nflverse [--acted KEY]` |

## What each piece adds

- **Usage in the record** (`gridiron.trends`, record key `usage`): for every
  rostered and available player with a stat line, the last 4 weeks of league
  points, snap share, opportunities, targets, target share and carries, plus
  a trend label. The label is volume-only (rule #6): the last 2 games vs the
  earlier ones in the window; RISING at >= +25% opportunities or >= +15 pp
  snap share, FALLING at <= -25% or <= -15 pp, MIXED when both fire, TOO FEW
  GAMES under 3 games. Nothing reads it for a projection.
- **Weekly review** (`scripts/weekly/weekly_review.py`): one markdown read of
  a record — header and gates, the Action Desk, start/sit swaps where the
  projection and the trend disagree (flagged, never overridden), roster
  usage, free agents (page verdicts, available RISING roles, watch list),
  outside rankings, and the Sleeper checks. Output goes to stdout or
  `data/outputs/review/` (gitignored).
- **Outside rankings** (`gridiron.external_ranks`): screenshots transcribed
  to `data/outputs/review/ranks/<source>_<date>.csv`
  (`rank,name,position,team,value`). The one place names become ids:
  exact normalised name + position against the roster and available pool
  only; AMBIGUOUS resolves to nobody; TEAM DIFFERS is shown for a person.
- **Fetch** (`scripts/weekly/fetch_record.py`): the newest successful cloud
  build's `dashboard` artifact, unpacked under `data/outputs/cloud/`
  (gitignored). Uses the owner's `gh` login when present.
- **Grading** (`scripts/weekly/grade_week.py`): `grade_archive` on one
  archive per week, actuals by gsis id, and one aggregate row per week in
  `data/ledger/grades/season<YYYY>.csv` (committed; counts only). Two rates,
  never merged: `agree/scorable` (advice the page endorsed) and
  `direction_agree/direction_n` (the projection's direction on every graded
  comparison, including withheld ones — the page withholds most of game day).
- **Live Sleeper comparison** (`gridiron.shadow`): the pull step records
  Sleeper's weekly projections (outside the manifest; gates nothing); the
  record's `shadow` block holds each player's number and whether it was
  captured before the player's kickoff; `grade_week.py` prints and stores a weekly
  shoot-out — baseline vs Sleeper vs their average, start/sit pairwise rate
  and MAE on the same pre-kickoff players — and the season line accumulates
  it. This is the forward test that decides which projection drives the page.

## First real grade (2026-09-30, week 3, board of 09-27 13:06Z)

24 comparisons graded, 6 ungradeable; 0 endorsed (every comparison was
withheld or rested on an unverified add); projection direction 8/24; roster
projection MAE 7.44 over 11 players. One week is noise (rule #5) — it is the
first row of the ledger, not a verdict on the model.
