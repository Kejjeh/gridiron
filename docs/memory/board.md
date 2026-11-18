# Board and Game Day invariants

Moved verbatim from CLAUDE.md's "Decision board" line (2026-09-30) to keep
that file under its line budget; CLAUDE.md keeps a headline and a pointer.

A pickup is a move ONLY if it improves THIS WEEK's lineup (`gridiron.waivers`):
no cross-position "depth", bench-only adds are research, an acquisition is
CONDITIONAL (availability UNVERIFIED); the Free Agent Radar (`gridiron.radar`,
under the Action Desk) verdicts every projected pool player; rollover = "no
comparison".

IR-slot lock (2026-11-15, ADR-0002, `gridiron.irslot`): a player in the IR
slot whose tag is not in `league_config.IR_ELIGIBLE_DESIGNATIONS` (verified
against Sleeper's `reserve_allow_*`) makes Sleeper block every lineup move,
add and drop. First-screen `_ir_alert` above every other alert, with the
deadline (the first kickoff among ALL rostered players, not 1 PM) and the
tags' as-of. Record `ir_traps`; review header; pre-flight flag from the live
week-stats tag. Unknown tag = flagged. Standing blocker, not a transition.

Usage before projections (2026-11-08, `gridiron.openings`, own list
`#work` under the radar, own first-screen alert): every pool player whose
last 3 games average a starter's snaps AND opportunities (RB 40%/10, WR
65%/6, TE 60%/4), and the 2 pool players highest on Sleeper's depth chart
behind a role vacated THIS week (recent work, last line within 2 weeks, by a
player Sleeper designates Out/IR/PUP/SUS/NA/Doubtful — never Questionable —
or lists on another team or none). Ids through the crosswalk only. A row
alerts 48 h from first seen against a comparable record carrying
`work_since`; a legacy record alerts nothing.

Starter feed record (2026-11-18, #29 item 2 option B, `shadow.starter_feed_block`):
every build's record key `starter_feed` holds Sleeper's projection for each
RB starter at 45%+ of snaps (`openings.high_snap_starters`, the role-at-risk
subset), owned or not: LISTED with a number (0 is a number), ABSENT from a
full feed, or UNKNOWN (no feed, another week's, a thin RB list = a failed
fetch). Each entry has its fetched-at time and `pre_kickoff`, where None
means unknown. Record-only: no alert, gate, projection or grade reads it, and
it is never rendered into the public HTML. The cloud keeps ~10 h of records,
so `dashboard.py --feed-log` appends each starter-week's CHANGES to a carried
log (`.carry/starter_feed/season<YYYY>.csv`, staged into the artifact as
`ledger/starter_feed/`). `scripts/research/feed_reaction.py --log` times a
drop by Sleeper's fetch time against kickoff; a week with no known answer is
"unanswered", never a miss.

A free agent the radar cannot rank is still NAMED (2026-10-21,
`gridiron.notable`, docs/research/RETURNER_BACKTEST.md): last season's
starters (league ppg rank within LAST season's position, 4+ games) and drops
by this league's teams in the last 10 days (the snapshot's `transactions`)
that Sleeper ranks a starter or that reach the owner's weakest eligible
starter (an empty slot counts as 0; never a player designated Out), each
with status, ours/Sleeper, and the waiver clock from the league's settings
(`waiver_clock`: waivers 2 days, then a free agent until his kickoff — or
the week's last kickoff, with no game of his own — then locked until the
Wednesday run, then free; "his Sleeper card is the
authority"). The first-screen alert is a transition, 48 h each, read off
this build's data: each change of that clock; the one carried comparison
(`PreviousWatch`) is a pool entry (never a team defense), against a
previous record under 24 h old with a pool, only while the drops are known.
Never the owner's own drop. "Can't project — Sleeper's number" lists every
ABSTAINED pool player Sleeper projects, labelled as Sleeper's; withheld or
Sleeper-Out players are never on it. Neither list is a move, and no returner
number of ours is served (the backtest cleared its bar and still
under-projects last season's starters).

Game Day (`scripts/weekly/gameday.py`, scenarios A-F in `gameday_scenarios.py
--screenshot --browser`): platform actuals only, game status OBSERVED or
UNKNOWN (never inferred from the clock), every archived move re-judged NOW by
player id against freshness, slot eligibility, designation, placement and
lock (the archive's ACTIONABLE is history, not authority), no live win odds,
and a one-tap read-only refresh inside the artifact file itself.

The page's NUMBER (2026-10-15, docs/research/STACK_SERVING.md): for a
QB/RB/WR/TE whose pre-kickoff Sleeper projection is in the cache and under
36 h old, `stack_v1` (the mix of advanced_v1 and that number, mean scale);
advanced_v1 otherwise and for K/DEF; the usage baseline when the advanced
inputs are missing or stale; NO number for a skill player a complete fresh
feed omits (`feed_absence`; never 0, the reason is shown). Every record row says which (`system`), the
status line counts how many got the stack, and `contenders` keeps
`baseline_v1`, `adv_v1` and `stack` for the shoot-out. A number fetched after
the player's kickoff is never stacked (the grader's own pre-kickoff rule).
