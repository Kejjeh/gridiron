# ADR-0002: The IR slot can lock the whole lineup — guard it in code

Date: 2026-11-15 · Status: accepted

## Context

Sleeper lets a player sit in the IR slot only while he carries a tag the
league allows there. This league allows IR, Out, Doubtful, suspended, NA,
COVID and DNR (`reserve_allow_*` settings); Questionable and "no tag" are not
allowed. When a player in the slot loses an allowed tag, for example because
he is activated, **Sleeper blocks every lineup move, add and drop for the
team** until he is moved out. He cannot be moved once his own game has
kicked off.

2026 week 10: the owner's back was tagged Out on Tuesday (MRI), was placed in
the IR slot, and was activated before Sunday's 9:30 ET Munich game. He was
still in the slot at kickoff, and he played. For the rest of the day the
owner could not:
- swap a Questionable receiver for the bench player;
- bench a back whose role had collapsed;
- add a free agent before his game locked.

Nothing warned. The board, the move pre-flight and the advice all treated
"in the IR slot" as "IR-eligible". This is the rule #11 trap ("on roster ≠
startable") in another form.

## Decision

1. **The allowed tags are a verified league setting.**
   `league_config.IR_ELIGIBLE_DESIGNATIONS` is checked against Sleeper's
   `reserve_allow_*` flags by `scripts/verify_league_settings.py` (61
   constants, no drift on 2026-11-15).
2. **One check, `gridiron.irslot.ir_traps`.** It flags every IR-slot player
   whose current tag is not allowed. An unreadable tag counts as UNKNOWN and
   is flagged, never assumed allowed. The check gives a deadline: the first
   kickoff still to come among ALL the team's players, because after that
   the lock costs the whole day. It also says when the player's own game has
   already locked him.
3. **The check appears everywhere a decision is read:**
   - the board's first screen (`_ir_alert`, above every other alert, with
     the tags' as-of time);
   - the record (`ir_traps`, `ir_tags_as_of`);
   - the weekly review header ("LINEUP LOCKED — fix the IR slot first");
   - the move pre-flight (`movecheck.card`), from Sleeper's week-stats tag,
     which is current to the minute and unaffected by the once-a-day
     player map.
4. **It is a standing blocker, not a transition alert.** It shows for as long
   as it holds. Rule #8 governs news; this is a lock on the owner's own
   controls.
5. **The skills check it first:**
   - `start-sit` step 0;
   - `move-plan` step 2;
   - `live-check` before the first kickoff of the day.

   Each says: move him out before the first kickoff of any of your players.

## Consequences

- The board's tag is only as fresh as its player map (once a day). On game
  days, the pre-flight's live tag is the faster read, and the alert names its
  as-of time.
- A Thursday or Sunday-morning international game moves the deadline
  earlier. The deadline is computed, not assumed to be 1 PM.
- Taking a player out of the IR slot needs a free bench spot. The fix may be a
  drop, so the owner needs the warning early.
