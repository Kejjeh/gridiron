"""WR-02: a change of quarterback this week, for wide receivers.

  qb_change       1 when this week's expected starter (the first QB on the
                  pre-game depth chart who is not unavailable) is not the QB
                  with the most dropbacks in the team's previous game
  qb_share_delta  the expected starter's share of the team's dropbacks this
                  season minus that previous-game QB's (0 without a change)
  games_with_qb   his games for the team this season, before this week, in
                  which the expected starter had the team's most dropbacks

One definition with TE-06 (_p2a_events_offense.qb_change). Pre-registration:
docs/research/blocks/WR-02.md.
"""
from __future__ import annotations

import _p2a_events_offense as E

ID = "WR-02"
TITLE = "Change of quarterback this week (wide receivers)"
COLUMNS = {"WR": ["qb_change", "qb_share_delta", "games_with_qb"]}


def build(ctx):
    return E.qb_change(ctx, "WR")
