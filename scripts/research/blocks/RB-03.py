"""RB-03: the running back's share of his team's carries inside the opponent's 5.

Reference block for the runner (scripts/research/block_runner.py); the
pre-registration is docs/research/blocks/RB-03.md.
"""
from __future__ import annotations

import _common as C

ID = "RB-03"
TITLE = "Share of the team's carries inside the opponent's 5"
COLUMNS = {"RB": ["gl_share_std", "gl_share_l3", "gl_carries_pg"]}


def build(ctx):
    keys = ctx.keys_for("RB")[["season", "week", "gsis_id"]]
    seasons = sorted(keys["season"].unique())
    pbp = ctx.pbp(seasons, ["rush_attempt", "rusher_player_id", "yardline_100",
                            "two_point_attempt"])
    gl = pbp.loc[(pbp["rush_attempt"] == 1) & (pbp["yardline_100"] <= 5)
                 & (pbp["two_point_attempt"].fillna(0) == 0)]
    team = (gl.groupby(["season", "week", "posteam"]).size().rename("team_gl").reset_index()
            .rename(columns={"posteam": "team"}))
    mine = gl.loc[gl["rusher_player_id"].notna()].assign(gsis_id=lambda d: C.ids(d["rusher_player_id"]))
    mine = mine.groupby(["season", "week", "gsis_id"]).size().rename("gl").reset_index()
    games = C.player_games(ctx, seasons)
    hist = (games.merge(mine, on=["season", "week", "gsis_id"], how="left")
            .merge(team, on=["season", "week", "team"], how="left")
            .fillna({"gl": 0.0, "team_gl": 0.0}))
    r = C.as_of(keys, hist, ["gsis_id"], ["gl", "team_gl"])
    r["gl_share_std"] = C.share(r["gl_std"], r["team_gl_std"])
    r["gl_share_l3"] = C.share(r["gl_l3"], r["team_gl_l3"])
    r["gl_carries_pg"] = r["gl_std"] / r["games_std"]
    return r[["season", "week", "gsis_id", *COLUMNS["RB"]]]
