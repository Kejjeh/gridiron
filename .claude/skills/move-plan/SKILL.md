---
name: move-plan
description: Build the owner's add/drop plan for the coming week — roster holes first (injuries, byes, falling roles), then candidates by usage, then a pre-flight of every add and drop with scripts/weekly/move_check.py before anything is recommended, ending in an ordered claim list. Use when the owner asks to suggest moves, who to drop, or whether a planned add/drop is right.
---

# move-plan

The weekly "suggest moves" job, written down after 2026 week 9. In that one
session, advice that skipped these steps:
- told the owner to drop a receiver the day he scored 18.5;
- missed the owner's own back going Out mid-game, while another back had a
  bye the next week;
- recommended an add whose team was on bye the next week;
- ignored quarterbacks (one team on its third QB in five weeks);
- got a rostered player's first name wrong;
- suggested a back who had already been released.

Every step below exists because of one of those.

## Steps

1. **Record.** `PYTHONPATH=src python scripts/weekly/fetch_record.py`. Say
   when the record was built; the cloud build is best effort and can run
   hours apart.
2. **Roster first.** Run the pre-flight with no moves:

       PYTHONPATH=src python scripts/weekly/move_check.py --record <record> --leaders 8

   Read every flagged row of **Your roster**:
   - a player tagged to miss next week (the tag comes from Sleeper's week
     stats, so an in-game injury shows);
   - a bye in either of the next two weeks;
   - a FALLING role.

   These holes decide WHICH positions to shop for. List them before naming
   any free agent.
3. **Candidates.** `PYTHONPATH=src python scripts/weekly/weekly_review.py
   --record <record>`, then read, in order:
   - the **Role openings and starter volume** table (usage before
     projection, rule #6);
   - the radar and notable lists;
   - `## Rest of season`;
   - the `--leaders` block from step 2.

   Rank receivers by targets and snap share, then their quarterback, then
   the projection. Rank backs by touches and snaps.
4. **Drops.** Draft the lowest-value non-protected players. Before keeping
   one, look at his latest game and his team's quarterback. The
   rest-of-season rank undercounts a returning player: it averages in the
   games he missed.
5. **Pre-flight the plan.**

       PYTHONPATH=src python scripts/weekly/move_check.py --record <record> \
           --add "<Name>,<POS>" [...] --drop "<Name>,<POS>" [...]

   Settle every flag before recommending:
   - **look before dropping** or **returning player** on a drop: keep him,
     or say why not, with his line;
   - **BYE in week N** on an add: it does not help week N; say so, and rank
     it lower;
   - **QB changed / weak QB** on a receiver: weigh it against his targets;
   - **NOT AVAILABLE**: remove it;
   - **UNRESOLVED**: fix the spelling or use the Sleeper id, never guess;
   - the **after these moves** section: every one of next week's slots
     must be fillable.
6. **Order the claims.** Fill next week's holes first, then rest-of-season
   value. Adds must equal drops.
7. **Report one table:** order, claim, drop, why (this week's line, usage,
   QB, bye), and the flags you overrode and why. Then the checks for each
   claim in Sleeper: still unrostered, status tag, waiver clear time.
   Nothing is submitted; the owner makes every claim.
8. **Friday.** Run steps 2 and 5 again after designations. The openings
   list depends on them.

## Facts that changed advice before

- A big game by a drop candidate outweighs his rest-of-season rank until
  his usage says otherwise.
- "Out" from Sleeper minutes after a game is a tag, not a diagnosis.
  Collect the backup and hold the injured player until the news is in.
- Two of the owner's players can share a bye (QB and backup QB both in
  week 11): check each add against the other's bye.
- A free agent's game kicking off locks him until the Wednesday waiver
  run; anything added after that is a claim.

## Never

- Never recommend an add or a drop without running move_check on it.
- Never name a player the record cannot resolve by id, and never correct a
  name from memory (rule #3).
- Never treat Questionable as out (rule #11), or a single touchdown as a
  role (rule #6).
- Never request Sleeper's full player map more than once a day. The
  week-stats feed carries current tags.
- Never submit, claim or drop anything in Sleeper.
