"""QB-11: wind at kickoff, for the quarterbacks who throw deep.

The archived forecast's wind at the venue at kickoff (outdoors; 0 indoors),
above 10 mph, and its product with his season-to-date share of deep aimed
passes (20+ air yards) relative to the league's rate. The shipped QB set has
the team's implied total, which the market already lowers for wind, and no
weather or depth of throw. Pre-registration: docs/research/blocks/QB-11.md.
Weather, transforms and definitions: _p5_schedule_weather.py.
"""
from __future__ import annotations

import _p5_schedule_weather as H
import numpy as np

ID = "QB-11"
TITLE = "Forecast wind above 10 mph, and its product with his deep-pass share"
COLUMNS = {"QB": ["wind10", "wind10_x_deep"]}


def build(ctx):
    keys = ctx.keys_for("QB")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    r = H.for_rows(keys, H.team_frame(ctx, seasons), ["wind10"])
    r = r.merge(H.deep_share(ctx, keys, "passer"), on=H.KEY, how="left")
    r["wind10_x_deep"] = np.where(r["wind10"] == 0, 0.0,
                                  r["wind10"] * (r["deep_share"] - r["deep_prior"]))
    return r[[*H.KEY, *COLUMNS["QB"]]]
