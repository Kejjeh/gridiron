"""QB-04: pace and no-huddle rate of the week's play-caller, per person.

The offense's plays per game over its last 3 games (+team, FEATURE_EXPANSION.md)
was a near miss (4 of 7 folds). This block measures the PERSON's tempo
instead of the team's play count: seconds between snaps when the clock ran,
and how often he goes no-huddle, both in neutral situations, career before
this season plus this season with this team so far, shrunk toward the league.
Pre-registration: docs/research/blocks/QB-04.md. Definitions: _tendencies.py.
"""
from __future__ import annotations

import _tendencies as T

ID = "QB-04"
TITLE = "Play-caller's pace and no-huddle rate (neutral, career plus this season)"
COLUMNS = {"QB": ["pc_pace", "pc_no_huddle"]}


def build(ctx):
    keys = ctx.keys_for("QB")[[*T.KEY, "team"]]
    pbp = ctx.pbp(T.history_seasons("OPC", keys),
                  sorted(set(T.PACE_COLUMNS) | set(T.NO_HUDDLE_COLUMNS)))
    pace = T.tendency(ctx, keys, T.pace_obs(pbp), "OPC")
    nh = T.tendency(ctx, keys, T.neutral_rate_obs(pbp, "no_huddle"), "OPC")
    pace["pc_pace"] = T.pooled(pace, T.K["pace"])
    nh["pc_no_huddle"] = T.pooled(nh, T.K["no_huddle"])
    r = (keys.merge(pace[[*T.TEAM_WEEK, "pc_pace"]], on=T.TEAM_WEEK, how="left")
         .merge(nh[[*T.TEAM_WEEK, "pc_no_huddle"]], on=T.TEAM_WEEK, how="left"))
    return r[[*T.KEY, *COLUMNS["QB"]]]
