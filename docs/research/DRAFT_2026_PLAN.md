# 2026 draft plan — corrected after readiness review

Updated 2026-09-08. This replaces the earlier strategy comparison, which
included incomplete rosters and a confounded downstream policy. Historical
results remain in Git history and must not be used as current evidence.

## League and tool

Sleeper league 1389720742551093249. 12 teams, half-PPR, passing TD 4,
passing INT -1. QB / 2 RB / 2 WR / TE / 2 FLEX / K / DEF, five bench,
one IR. Slot 1, 15-round snake. Cached draft time is September 8 at
9:00:20 PM Eastern; autostart was off. Confirm in Sleeper before the draft.

Choose the HPPR_4ptPTD_1QB 2WR 2FLEX | 12T cheat sheet, not the auction tab.
Your picks are 1, 24, 25, 48, 49, 72, 73, 96, 97, 120, 121, 144, 145, 168, 169.

Open data/outputs/draft2026_warroom.html as a local file. It contains all
983 board records and does not need a running server. Keep Sleeper open:
this page records selections manually and does not submit draft picks.

## Corrected simulation

Each policy has 300 draws with seed 20260908; identical seeds provide a
common random comparison. All simulated teams must complete the ten
starter slots within 15 selections. Temporarily ineligible candidates
are reconsidered on later turns. Scripted openings use static VOR
after the opening, so the opening is the comparison being changed.

| policy | mean lineup points | 10th percentile |
|---|---:|---:|
| static_vor | 1890.4 | 1869.4 |
| dynamic_vona | 1885.1 | 1862.2 |
| rb_rb_rb | 1874.8 | 1848.4 |
| rb_wr_wr | 1870.4 | 1851.4 |
| rb_rb_wr | 1889.1 | 1869.8 |
| rb_te_wr | 1852.5 | 1835.8 |
| rb_wr_qb | 1852.2 | 1833.6 |
| wr_at_1 | 1850.8 | 1828.3 |

Static versus dynamic differs by about 5.3 points in this run, not the
previous roughly 100-point advantage. These are projected outcomes under
assumptions, not observed wins or an out-of-sample validated strategy.
Do not force a named opening from this result.

Room probabilities now come from 1,000 separate neutral-market drafts,
seed 20260909, with all actual simulated selections removed. They are
unconditional pre-draft scenarios; they do not learn the real room's picks.
ADP mode is the default and is also an unvalidated estimate. At consecutive
own picks, a player passed at the first pick remains available at the second.

The manifest records input hashes and file modification times, seeds,
policy summaries, and the board hash. The page builder rejects a board
that does not match its manifest. Rebuilding never refreshes source news.

## Pre-draft checklist

- Confirm slot, league scoring, start time and available players in Sleeper.
- Check current injury and suspension news. Saved news and projections are
  snapshots. Excluded-scenario labels are assumptions, not live status.
- Open the final HTML in the browser you will use all evening. Try a pick,
  export a backup, then Reset twice to clear the rehearsal.
- Check that the save status is healthy. If browser storage is unavailable,
  export backups frequently; never assume a refresh will retain picks.
- Confirm the counter stays aligned with Sleeper. Mine is only for your
  turns; Gone is for opponents. Skip reserves an unresolved numbered pick.
- Correct old picks through Pick log > Edit, then select the replacement.
  Clear player preserves the pick number; Undo removes only the latest pick.
- Use Compare to mark alternatives before your selection. The JSON backup
  records the considered alternatives, model mode, time, and board version.
- Record an unlisted player by name and position if needed; its projection
  is zero, so it does not invent value. Recording an excluded player is
  allowed to keep the tracker consistent with Sleeper, with a warning.
- Export at each turn. Before switching browsers or files, export and then
  restore the pasted backup in the new page. Check the counter and roster.
- Finish with QB, two RB, two WR, TE, two eligible FLEX, K, DEF and five bench.

## Remaining limits

Conditional GO as a manual companion; not a validated recommendation engine.
No new live data was fetched during the repair. Name-based fallback joins
remain for some sources, with ambiguous matches withheld rather than guessed.
A full stable-ID crosswalk and reproducible producers for every cached input
remain unfinished. The scoring adapter still lacks some less-common stats
(e.g. two-point conversions), and K/DEF projections retain source assumptions.
Injury discounts and manager-history estimates are exploratory; neither
causal validation nor out-of-sample predictive validation is complete.

After the draft, record_draft_2026.py can fetch the completed Sleeper draft.
Its alternative is explicitly a retrospective best-VOR benchmark, not a
claim about what you considered. The exported page backup is the record
of your actual marked alternatives. ADP and room Brier scores use common
nonmissing support across all fifteen own-pick checkpoints; one draft is
not enough to select a model confidently.

## Verification and rebuild (PowerShell, repository root)

    $env:PYTHONPATH="src"
    .venv/Scripts/python.exe scripts/ci/smoke.py
    .venv/Scripts/python.exe scripts/ci/run_summary.py -- .venv/Scripts/python.exe -m pytest
    node --test scripts/research/warroom/draftroom_logic.test.js
    .venv/Scripts/python.exe scripts/research/draft_board_2026.py
    .venv/Scripts/python.exe scripts/research/warroom_build.py

The board build requires the existing local research cache; it does not
download missing inputs. Environment dependencies are declared in
pyproject.toml and requirements.txt and installed in the repository .venv.

## Historical blocking and schedule context

Four columns now show run blocking, pass protection, early opponents (weeks
1-4) and fantasy playoff opponents (weeks 15-17). Click each cell for the
metric explanation, opponents and coverage. Run blocking and pass protection
use 2025 ESPN team win rates and the source's rankings; higher rates and
lower ranks are better. These are historical team results, not grades of
the current starting five.

Schedule cells show two separate averages of opponents' 2025 run-stop and
pass-rush win rates. Lower means weaker historical opposition. They are
not full fantasy SOS: pass coverage, player roles, game script, coaching,
2026 personnel changes and opponent adjustment are absent. Bye weeks do
not become easy games; any missing opponent rate makes that metric
unavailable instead of averaging the missing opponent away. K/DEF show N/A.

All context has LOW predictive confidence and a ZERO projection adjustment.
Do not apply another boost to the existing projection based on these columns.
Publication, retrieval and schedule-cache dates are visible. Source:
https://www.espn.com/nfl/story/_/id/46138675/2025-nfl-win-rates-top-teams-players-rankings-pass-run-block

The small numeric snapshot is data/outputs/draft2026_context_source.json.
It was transcribed from the primary team table because direct HTTP returned
403. It is an inspectable snapshot, not an automatic live feed. The producer
scripts/research/build_draft_context_2026.py runs offline using that snapshot
and the cached 2026 schedule. It rejects incomplete schedules, future-dated
source information, duplicate teams/weeks and invalid rates. The page
builder verifies source fingerprints. To rebuild:

    $env:PYTHONPATH="src"
    .venv/Scripts/python.exe scripts/research/build_draft_context_2026.py
    .venv/Scripts/python.exe scripts/research/warroom_build.py

Before activating any numerical adjustment, evaluate baseline, schedule,
blocking and combined variants with chronological folds and as-of source
snapshots. Do not use later closing lines or end-of-season metrics in
preseason backtests. No predictive validation was performed for this release.

## 2026 personnel, coaching, scheme and optional PFF

Open the 2026 changes panel above the board or the 2026 changes button
beside a player. Select any of the 32 teams. The panel shows roster
differences, skill-position chart entries, the current offensive-line
chart, coaching reports, and sourced scheme plans. Defense and other
roster changes are available in an expandable section.

Roster comparisons anchor on GSIS IDs. They compare saved 2025/2026
membership, not transactions or prior starter status. A missing player in
the earlier snapshot is not assumed to be a rookie. The latest chart per
team before the cutoff is used; top ranks are selected separately for
each chart slot, so WR2/WR3 are not lost. Conflicting IDs or teams are
flagged, and grades/history are withheld from those entries. Chart order
is not a snap projection; roster ACT status is not medical clearance.

Coaching coverage is a reviewed collection of NFL hiring reports, plus
specific play-caller and scheme sources; it is not a complete current
directory. Missing entries do not prove continuity. Coordinator changes
and play-caller changes are separate. Scheme reporting is not measured
scheme frequency. Source publication/retrieval and snapshot dates are
visible. No numerical adjustment or causal claim is added.

PFF grades are NOT loaded in this release. The optional import expects
data/research/cache/draft2026/pff_blocking_2025.csv, using the header in
data/outputs/pff_blocking_import_template.csv. Supply an authorized export
mapped to this schema; do not rename unrelated PFF metrics as grades.
Each player has one prior-season/position record, a GSIS ID or uniquely
mapped PFF ID, separate run/pass blocking grades and snap counts, an HTTPS
source, and a timezone-aware published_at. Blank component grades remain
unavailable. Duplicate, future, unknown-ID, ambiguous-ID, invalid grade
and zero-snap grade records are rejected. College grades are not translated.

To build offline after updating verified inputs:

    $env:PYTHONPATH="src"
    .venv/Scripts/python.exe scripts/research/build_personnel_2026.py
    .venv/Scripts/python.exe scripts/research/warroom_build.py

The new 2025 roster cache was fetched with nflreadpy.load_rosters([2025]).
The builder's --as-of option also rejects snapshots saved after the cutoff;
historical evaluation requires original archived snapshots. Today's roster
cannot be relabeled as a historical draft-day snapshot. Full roster-refresh
automation and statistical personnel/scheme weighting are not included.

Verification:

    .venv/Scripts/python.exe scripts/ci/smoke.py
    .venv/Scripts/python.exe scripts/ci/run_summary.py -- .venv/Scripts/python.exe -m pytest
    node --test scripts/research/warroom/draftroom_logic.test.js
    git diff --check
