"""RB-04: committee or bell cow, by play-caller, interacted with the back's role.

The shipped RB set carries his own volume (carries, targets, snaps, expected
points) but nothing about how the person calling the plays spreads the
carries. This block gives the play-caller's carry concentration (the top
back's share of the team's back carries per game, career before this season
plus this season with this team so far, shrunk toward the league), the
back's own share of his team's back carries this season, and their product:
a concentrating play-caller should feed his lead back more and his other
backs less than their recent volume says. Pre-registration:
docs/research/blocks/RB-04.md. Definitions: _tendencies.py.
"""
from __future__ import annotations

import _common as C
import _tendencies as T

ID = "RB-04"
TITLE = "Play-caller's carry concentration, the back's carry share, and their product"
COLUMNS = {"RB": ["pc_rb_conc", "rb_carry_share_std", "rb_conc_x_share"]}


def build(ctx):
    keys = ctx.keys_for("RB")[[*T.KEY, "team"]]
    seasons = T.history_seasons("OPC", keys)
    pbp = ctx.pbp(seasons, T.CARRY_COLUMNS)
    carries = T.back_carries(pbp)
    t = T.tendency(ctx, keys, T.carry_conc_obs(carries), "OPC")
    t["pc_rb_conc"] = T.pooled(t, T.K["carry_conc"])
    hist = T.share_history(T.games_played(ctx, seasons), carries, "bc")
    r = C.as_of(keys[T.KEY], hist, ["gsis_id"], ["bc", "bc_team"])
    r["rb_carry_share_std"] = C.share(r["bc_std"], r["bc_team_std"])
    r = (r[[*T.KEY, "rb_carry_share_std"]].merge(keys, on=T.KEY, how="left")
         .merge(t[[*T.TEAM_WEEK, "pc_rb_conc"]], on=T.TEAM_WEEK, how="left"))
    r["rb_conc_x_share"] = r["pc_rb_conc"] * r["rb_carry_share_std"]
    return r[[*T.KEY, *COLUMNS["RB"]]]
