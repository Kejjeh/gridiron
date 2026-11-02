"""DEF-09: the opposing play-caller's pace and no-huddle rate.

A fast offense runs more plays: more chances for sacks and takeaways, and
more chances to score. The shipped defense set has the opponent's implied
points, sacks allowed and giveaways per game; nothing about the tempo of the
person calling the opponent's plays. This block gives the opposing
play-caller's seconds between snaps when the clock ran and his no-huddle
rate, both on neutral plays, career before this season plus this season with
that team so far, shrunk toward the league. Pre-registration:
docs/research/blocks/DEF-09.md. Definitions: _tendencies.py.
"""
from __future__ import annotations

import _tendencies as T

ID = "DEF-09"
TITLE = "Opposing play-caller's pace and no-huddle rate (neutral, career plus this season)"
COLUMNS = {"DEF": ["opp_pc_pace", "opp_pc_no_huddle"]}


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team", "opponent"]]
    offense = keys[["season", "week", "opponent"]].rename(columns={"opponent": "team"})
    pbp = ctx.pbp(T.history_seasons("OPC", keys),
                  sorted(set(T.PACE_COLUMNS) | set(T.NO_HUDDLE_COLUMNS)))
    pace = T.tendency(ctx, offense, T.pace_obs(pbp), "OPC")
    nh = T.tendency(ctx, offense, T.neutral_rate_obs(pbp, "no_huddle"), "OPC")
    tw = pace[T.TEAM_WEEK].assign(opp_pc_pace=T.pooled(pace, T.K["pace"]))
    tw = tw.merge(nh[T.TEAM_WEEK].assign(opp_pc_no_huddle=T.pooled(nh, T.K["no_huddle"])),
                  on=T.TEAM_WEEK, how="left")
    tw = tw.rename(columns={"team": "opponent"})
    keys = keys.assign(opponent=keys["opponent"].astype(object))
    r = keys.merge(tw, on=["season", "week", "opponent"], how="left")
    return r[["season", "week", "team", *COLUMNS["DEF"]]]
