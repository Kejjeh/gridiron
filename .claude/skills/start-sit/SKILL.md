---
name: start-sit
description: Decide this week's lineup from the published Gridiron record — the page's best legal lineup and Action Desk, cross-checked against each player's actual usage trend, with lock deadlines and the Sleeper checks. Use when the owner asks who to start, whether to bench someone, or about a specific slot.
---

# start-sit

## Steps

1. Record: reuse the week's record, or
   `PYTHONPATH=src python scripts/weekly/fetch_record.py`.
2. `PYTHONPATH=src python scripts/weekly/weekly_review.py --record <record>`
   and read three sections:
   - **Action Desk** — each lineup action with its status (ACTIONABLE,
     CONDITIONAL, WITHHELD) and deadline (the first kickoff among the players
     involved);
   - **Projection vs actual usage — start/sit** — every swap where the
     projection prefers one player while the volume trend favours the other;
   - **My roster — actual usage** — ppg and trend per player.
3. Report per slot in question: who the page would start and by how many
   projected points, what usage says, the deadline, and the check. When the
   two disagree, say so and explain both sides; the projection models this
   week's matchup, the trend says which way the role is moving. Do not
   silently pick one.
4. An inactive starter (Out, projected 0) is the highest-value fix of the
   week: lead with it and give the backup the page names.

## Freshness

Designations come from Sleeper's player map, requested at most once a day;
on game days the page allows 6 h, so by kickoff the lineup gate is usually
WITHHELD. Then every swap is "check in Sleeper first": read both players'
status tags, and act before the deadline. Final inactives come about 90
minutes before kickoff.

## Never

- Never present a WITHHELD swap as executable, and never change the lineup
  or submit anything in Sleeper.
- Never override the page's lock state: a player whose game has kicked off
  cannot move.
