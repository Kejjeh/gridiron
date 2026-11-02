"""K-02: the head coach's 4th-down aggressiveness, go rate over expected.

A head coach who goes for it where the league kicks takes field-goal tries
away from his kicker. The shipped kicker set has the team's field-goal and
extra-point tries per game this season, the kicker's points and the line;
nothing about the head coach as a person. This block gives his go rate over
expected (against the league's go rate in the same distance and field
bucket, same season), career before this season (2018 included, any team)
plus this season with this team so far, shrunk toward the league: on every
4th down, and inside the opponent's 40 only (where the alternative is the
kicker). Pre-registration: docs/research/blocks/K-02.md. Definitions:
_tendencies.py.
"""
from __future__ import annotations

import _tendencies as T

ID = "K-02"
TITLE = "Head coach's 4th-down go rate over expected: all 4th downs and inside the 40"
COLUMNS = {"K": ["hc_goe", "hc_goe_fg"]}


def build(ctx):
    keys = ctx.keys_for("K")[[*T.KEY, "team"]]
    pbp = ctx.pbp(T.history_seasons("HC", keys), T.FOURTH_COLUMNS)
    r = keys.copy()
    for fg, col in ((False, "hc_goe"), (True, "hc_goe_fg")):
        t = T.tendency(ctx, keys, T.fourth_obs(pbp, fg_range_only=fg), "HC")
        t[col] = T.pooled(t, T.K["fourth"])
        r = r.merge(t[[*T.TEAM_WEEK, col]], on=T.TEAM_WEEK, how="left")
    return r[[*T.KEY, *COLUMNS["K"]]]
