# Board and Game Day invariants

Moved verbatim from CLAUDE.md's "Decision board" line (2026-09-30) to keep
that file under its line budget; CLAUDE.md keeps a headline and a pointer.

A pickup is a move ONLY if it improves THIS WEEK's lineup (`gridiron.waivers`):
no cross-position "depth", bench-only adds are research, an acquisition is
CONDITIONAL (availability UNVERIFIED); the Free Agent Radar (`gridiron.radar`,
under the Action Desk) verdicts every projected pool player; rollover = "no
comparison".

Game Day (`scripts/weekly/gameday.py`, scenarios A-F in `gameday_scenarios.py
--screenshot --browser`): platform actuals only, game status OBSERVED or
UNKNOWN (never inferred from the clock), every archived move re-judged NOW by
player id against freshness, slot eligibility, designation, placement and
lock (the archive's ACTIONABLE is history, not authority), no live win odds,
and a one-tap read-only refresh inside the artifact file itself.
