---
name: roster-audit
description: Weekly audit of the owner's fantasy roster from the latest published Gridiron record — build time and freshness, what the page lets the owner do, and each player's ACTUAL usage (snap share, opportunities, points per game) with a volume-only trend. Use when the owner asks how the team looks, who is losing or gaining work, or starts the weekly routine.
---

# roster-audit

Answers "how is my team actually doing?" from one record, not from a fresh
data pull. Start every weekly session here; `waiver-board` and `start-sit`
reuse the same record.

## Steps

1. Get the newest cloud record (read-only; never dispatches anything):

       PYTHONPATH=src python scripts/weekly/fetch_record.py

   It prints `record:` and `archives:` paths under `data/outputs/cloud/`
   (gitignored). Offline alternative: `scripts/weekly/dashboard.py --write`
   builds `data/outputs/dashboard/dashboard_latest.json` from the local cache.
2. Run the review on that record:

       PYTHONPATH=src python scripts/weekly/weekly_review.py --record <record path>

3. Report to the owner, in this order:
   - the record's build time, which inputs are not current, and which gates
     are open (`lineup`, `waiver`, `matchup`);
   - starters whose trend is **FALLING** (quote the `why`: opportunities and
     snap share before -> after), players marked **TOO FEW GAMES**, and anyone
     Out / on IR;
   - players whose trend is **RISING** on the bench;
   - from `## Rest of season`: any position where a free agent's ROS beats
     my lowest player there (a hold/drop question, not a lineup call).
4. If the record says "no usage block", the record predates it: say so; do not
   pull stats by hand unless the owner asks.

## How to read a trend

`gridiron.trends` compares the last 2 games with a stat line against the games
before them in a 4-week window, on VOLUME only — snap share and opportunities
(targets + carries; pass attempts + carries for a QB). Points, yards and
touchdowns are shown but never move the label (rule #6: a two-game efficiency
swing is noise). A week with no line is absent, never zero.

## Never

- Never present a move as executable when the page's gate for it is
  WITHHELD; say what to check in Sleeper instead.
- Never call Sleeper's player map, dispatch or re-run a workflow, or submit
  anything to the league.
- Never commit the record or the review: they name the owner's players
  (`data/outputs/cloud/` and `data/outputs/review/` are gitignored).
