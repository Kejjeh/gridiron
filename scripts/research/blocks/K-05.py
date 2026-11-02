"""K-05: wind, precipitation and altitude for kickers.

The archived forecast's wind above 10 mph and precipitation at the venue in
the game's first three hours (outdoors; 0 indoors), and the stadium's
elevation. The roof is already in the shipped K set (`dome`), so it is not
added again. Pre-registration: docs/research/blocks/K-05.md. Weather and
transforms: _p5_schedule_weather.py.
"""
from __future__ import annotations

import _p5_schedule_weather as H

ID = "K-05"
TITLE = "Forecast wind above 10 mph, precipitation and stadium elevation"
COLUMNS = {"K": ["wind10", "precip3h", "elev_km"]}


def build(ctx):
    keys = ctx.keys_for("K")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    r = H.for_rows(keys, H.team_frame(ctx, seasons), ["wind10", "precip3h", "elev_m"])
    r["elev_km"] = r["elev_m"] / 1000.0
    return r[[*H.KEY, *COLUMNS["K"]]]
