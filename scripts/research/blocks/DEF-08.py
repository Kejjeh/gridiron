"""DEF-08: wind and rain for team defenses.

The archived forecast's wind above 10 mph and precipitation at the venue in
the game's first three hours (outdoors; 0 indoors). The shipped DEF set has
the opponent's implied total, which the market already lowers for weather,
and no weather. Pre-registration: docs/research/blocks/DEF-08.md. Weather and
transforms: _p5_schedule_weather.py.
"""
from __future__ import annotations

import _p5_schedule_weather as H

ID = "DEF-08"
TITLE = "Forecast wind above 10 mph and precipitation at kickoff"
COLUMNS = {"DEF": ["wind10", "precip3h"]}


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    r = H.for_rows(keys, H.team_frame(ctx, seasons), ["wind10", "precip3h"])
    return r[["season", "week", "team", *COLUMNS["DEF"]]]
