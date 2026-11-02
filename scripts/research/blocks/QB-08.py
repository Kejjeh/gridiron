"""QB-08: red-zone pass rate over expected of the week's play-caller, per person.

Inside the opponent's 20, whether the touchdown comes through the air (the
quarterback's 4 points) or on the ground is a play call. This block gives
the play-caller's red-zone pass rate over expected (career before this
season plus this season with this team so far, shrunk toward the league),
alone and times the team's implied total from the pre-game lines (more
expected points, more red-zone trips for the tendency to act on).
Pre-registration: docs/research/blocks/QB-08.md. Definitions: _tendencies.py.
"""
from __future__ import annotations

import _tendencies as T

ID = "QB-08"
TITLE = "Play-caller's red-zone pass rate over expected, and times the implied total"
COLUMNS = {"QB": ["pc_rz_proe", "pc_rz_proe_x_implied"]}


def build(ctx):
    keys = ctx.keys_for("QB")[[*T.KEY, "team"]]
    pbp = ctx.pbp(T.history_seasons("OPC", keys), T.RZ_COLUMNS)
    t = T.tendency(ctx, keys, T.rz_proe_obs(pbp), "OPC")
    t["pc_rz_proe"] = T.pooled(t, T.K["rz_proe"])
    r = (keys.merge(t[[*T.TEAM_WEEK, "pc_rz_proe"]], on=T.TEAM_WEEK, how="left")
         .merge(T.implied_totals(ctx, sorted(keys["season"].unique())), on=T.TEAM_WEEK,
                how="left"))
    r["pc_rz_proe_x_implied"] = r["pc_rz_proe"] * r["implied"]
    return r[[*T.KEY, *COLUMNS["QB"]]]
