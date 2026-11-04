"""RB-11: workload hangover before a short week.

A short-rest flag (5 days or fewer since his team's last game) and his
touches (carries + receptions) in his team's previous game on a short week.
The shipped RB set has his last game's opportunities (`opp_last`) but
nothing from the calendar. Pre-registration: docs/research/blocks/RB-11.md.
Definitions: _p5_schedule_weather.py.
"""
from __future__ import annotations

import _p5_schedule_weather as H
import numpy as np

ID = "RB-11"
TITLE = "Short rest, and his touches in his team's previous game when the week is short"
COLUMNS = {"RB": ["short_rest", "touches_last_x_short"]}


def build(ctx):
    keys = ctx.keys_for("RB")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    tf = H.team_frame(ctx, seasons, with_weather=False)
    r = H.for_rows(keys, tf, ["short_rest"])
    t = H.last_game_touches(ctx, keys)
    r = r.merge(t[[*H.KEY, "touches_last"]], on=H.KEY, how="left")
    r["touches_last_x_short"] = np.where(r["short_rest"] == 0, 0.0,
                                         r["touches_last"] * r["short_rest"])
    return r[[*H.KEY, *COLUMNS["RB"]]]
