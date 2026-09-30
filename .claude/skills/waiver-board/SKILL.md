---
name: waiver-board
description: Compare free agents against the owner's roster using three kinds of evidence at once — the page's this-week lineup verdicts, each player's actual usage trend, and outside rankings the owner shares as screenshots (trade charts, ROS lists) — and produce an add/drop table with the exact Sleeper check for each. Use when the owner asks who to pick up, sends a ranking image, or names players they are looking at.
---

# waiver-board

## Steps

1. Record: reuse the one from `roster-audit`, or run
   `PYTHONPATH=src python scripts/weekly/fetch_record.py`.
2. Outside rankings (optional). For each screenshot the owner sends,
   transcribe it to `data/outputs/review/ranks/<source>_<YYYY-MM-DD>.csv`
   (gitignored) with the header `rank,name,position,team,value`:
   - one row per player you can actually read; skip what you cannot read
     rather than guessing, and say how many you skipped;
   - `rank` is the list's order (for a value chart, rank by value);
   - `value` only when the image shows one; if read off a small image,
     tell the owner the values are approximate (about +/-1-2).
3. Watch list (optional): the players the owner names, as
   `--watch "First Last,TE"` (or a Sleeper id). A name that resolves to
   nobody is reported, never guessed.
4. Run:

       PYTHONPATH=src python scripts/weekly/weekly_review.py --record <record> \
           --ranks <csv> [--ranks <csv> ...] --watch "<Name>,<POS>" [...]

5. Report one compact table: player, add/drop (or no-drop), this week's
   lineup change from the page, actual usage (ppg, trend), outside rank or
   value, the major caveat, and the exact Sleeper check. Then say plainly
   which move you would make and why, weighing:
   - the page's verdict (it counts THIS week's best legal lineup only; a
     bench-only add is research, and it does not price rest of season);
   - the usage trend (a RISING role beats a hot week of touchdowns);
   - outside value (useful for rest of season and trade leverage);
   - the record's own rest-of-season rank (`## Rest of season` in the
     review; `gridiron.ros`, cross-validated): prefer it to an outside
     list when they disagree, and say both. It is points, not ΔP(win).
   Full ROS tables for every position:
   `PYTHONPATH=src python scripts/weekly/ros_rankings.py --nflverse`
   (writes `data/outputs/review/ros_weekNN.md`, gitignored).
   The drop is the lowest-value non-protected player on all three; never a
   player the page lists as protected (Out, IR, no projection).

## Resolution rules (rule #3)

`gridiron.external_ranks` turns a ranking row into a Sleeper id only on an
exact normalised name AND position match against the owner's roster and the
available pool. Two matches = AMBIGUOUS (nobody); a team mismatch is shown as
TEAM DIFFERS for a person to check; no match = NOT HELD (another manager's
player, or a different spelling). Never fix a miss by loosening the match.

## Never

- Never call a pickup executable: availability is UNVERIFIED until the
  owner opens the player in Sleeper and sees FREE AGENT or the waiver clear
  time, and the claim must clear before the first kickoff involved.
- Never submit a claim, drop or trade; never call Sleeper's player map.
- Never commit transcribed rankings or the review (gitignored paths only).
