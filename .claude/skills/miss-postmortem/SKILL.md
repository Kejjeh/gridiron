---
name: miss-postmortem
description: When the owner asks how a pickup, a breakout or a bad call was missed, trace the player through the archived board records to when he was available and why nothing named him, then fix the cause in code with a test pinned to the real case, check it against the real records, and ship it as a PR. Use for "how did I miss X", "make sure we never miss this again", or advice that proved wrong.
---

# miss-postmortem

The loop that produced `gridiron.openings` (2026 week 9: a receiver with
starter snaps, and a back behind a released lead back, both claimed by other
managers while the board ranked them WR52 and RB57).

## Steps

1. **Own it, with facts.** Look the player up by id, using the record's
   radar pool, `ros.players` (`held_by`) and nflverse rosters. Report:
   - his line this week;
   - whether he is still available.

   If he is gone, say so first.
2. **Timeline.** Every archived record lives under `data/outputs/cloud/*/`
   (`fetch_record.py` keeps each run). For each record, note:
   - its `generated` time;
   - whether his Sleeper id is in `radar.pool`;
   - any `radar_changes` item that names him ("claimed by a roster since
     the previous record").

   That gives the window when he could have been added.
3. **Cause.** Say why nothing named him in that window. Usually one of:
   - the projection rank was below the cut while his VOLUME was a
     starter's;
   - an opening came from an Out/IR designation, a release or a trade;
   - he was a returning player;
   - data was stale (record age, the player map's once-a-day budget);
   - a bug.

   Prove it with his usage lines and the teammates' tags, not with a
   guess.
4. **Fix in code, not in prose.** Put the fix under `src/gridiron/` and
   follow the patterns already there:
   - joins by id through the crosswalk;
   - absence is unknown, never zero;
   - alerts on transitions only (rule #8).

   Pin the real case in a test using invented players. Prefer adding a
   list or a flag to the existing lists over a new page.
5. **Check it against the real records.** Run the new logic on the archived
   record from inside the window, and confirm it names the player. Count
   how many others it names: a list nobody can read misses just the same.
   Tune the thresholds or the ranking, then re-check.
6. **Ship.**
   - Branch from `origin/main`.
   - Run `python scripts/ci/smoke.py`.
   - Run the full suite through `python scripts/ci/run_summary.py --
     python -m pytest`, and ruff on the new files.
   - Add a row to `docs/DECISIONS.md` and a paragraph to
     `docs/memory/board.md`.
   - Add a step to the skill that should read the new output.
   - Open a PR with Why / What / Checks, and merge on green CI when the
     owner asked for the fix.
   - Fetch the first cloud record built after the merge, and confirm the
     list is there and readable.
7. **Close the loop.** Tell the owner:
   - what was missed and why;
   - what now catches it;
   - the evidence that it would have caught this one;
   - its known limits (build cadence, data freshness).

## Never

- Never blame the data or the owner before tracing the records.
- Never ship a fix that has not been run against the real records it is
  meant to change.
- Never name-match to find the player (rule #3), and never widen the PR
  beyond the miss.
- Never submit anything in Sleeper, and never commit roster-bearing output
  (rule #10).
