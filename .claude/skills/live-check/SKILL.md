---
name: live-check
description: Read a week while it is being played or just after — which games are final, the owner's players' platform points and lines, any in-game injury tags, and the free agents scoring big — and schedule a follow-up once the late games end. Use when the owner asks how the team is doing today, whether any free agents are going off, or to look again after the afternoon or night games.
---

# live-check

What the owner asks on game days, and how to answer it from data keyed by id
instead of from memory.

## Steps

1. **What is final.** List the week's games from nflverse's schedule
   (`nflreadpy.load_schedules`): the kickoff (`gameday`, `gametime`, ET) and
   the score columns, which are blank until the game ends. Say which games
   are still to play, for example Sunday night and Monday.
2. **Pull the week.**

       PYTHONPATH=src python scripts/weekly/fetch_record.py
       PYTHONPATH=src python scripts/weekly/move_check.py --record <record> --leaders 8

   - **Your roster:** each player's platform points, line, and current tag.
     An Out tag that appears during the day is an in-game injury: lead with
     it, and run `move-plan` for the backup.
   - **Available pool, best scores:** the free agents going off. Read each
     with its volume (opportunities) and his team's QB before calling him a
     pickup. One game with a touchdown is not a role (rule #6). Cross-check
     the review's Role openings and starter volume table.
3. **Gaps to say out loud.**
   - Sleeper's week rows carry no snap counts until later, and nflverse
     snaps land the next day. Say "snaps not posted yet", never 0%.
   - A player with no row has not played yet (or is on bye). He has not
     scored 0.
4. **Follow-up after the late games.** When the owner asks to look again
   later, schedule one message for about 3 h 15 min after the last kickoff
   in question (for example 8:00 PM ET after the 4:25 games), using the
   session's scheduling tool. Write the message so it stands alone:
   - the date and the games to wait for;
   - the commands above;
   - the players and moves to check again;
   - that nothing is submitted.

   Tell the owner the time and how to cancel it.
5. **Report.**
   - A table of the owner's players: points, line, tag.
   - The free agents worth a look, each with volume and QB.
   - What changed since the last read: new tags, players now owned (the
     record's radar changes), verdicts that moved.
   - What is still to play.

## Never

- Never tell the owner an unrostered player can be added after his game has
  kicked off. He is locked until the Wednesday waiver run, and the move is
  a claim.
- Never score a line yourself. The points are the platform's own (Game
  Day's rule).
- Never request Sleeper's full player map for tags. The week-stats rows
  carry them.
- Never submit anything in Sleeper, and never write roster-bearing output
  outside `data/outputs/review/` (gitignored).
