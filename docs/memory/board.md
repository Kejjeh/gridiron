# Board and Game Day invariants

Moved verbatim from CLAUDE.md's "Decision board" line (2026-09-30) to keep
that file under its line budget; CLAUDE.md keeps a headline and a pointer.

A pickup is a move ONLY if it improves THIS WEEK's lineup (`gridiron.waivers`):
no cross-position "depth", bench-only adds are research, an acquisition is
CONDITIONAL (availability UNVERIFIED); the Free Agent Radar (`gridiron.radar`,
under the Action Desk) verdicts every projected pool player; rollover = "no
comparison".

A free agent the radar cannot rank is still NAMED (2026-10-21,
`gridiron.notable`, docs/research/RETURNER_BACKTEST.md): last season's
starters (league ppg rank within LAST season's position, 4+ games) and drops
by this league's teams in the last 10 days (the snapshot's `transactions`)
that Sleeper ranks a starter or that reach the owner's weakest eligible
starter, each with status, ours/Sleeper, and the waiver run the league's
settings imply ("verify in Sleeper"). The first-screen alert is a transition:
first listed in the last 48 h (first-seen carried record to record) or
inside an open waiver window. "Can't project — Sleeper's number" lists every
ABSTAINED pool player Sleeper projects, labelled as Sleeper's; withheld
(Out, bye) players are never on it. Neither list is a move, and no returner
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
