"""WR-11: deep threats in wind.

The archived forecast's wind at the venue at kickoff (outdoors; 0 indoors),
above 10 mph, and its product with his season-to-date share of targets that
travelled 20+ air yards, relative to the league's rate. The shipped WR set
has his air-yards share (`ay_share_l3`, volume), expected points and the
team's implied total, and no weather. Pre-registration:
docs/research/blocks/WR-11.md. Weather, transforms and definitions:
_p5_schedule_weather.py.
"""
from __future__ import annotations

import _p5_schedule_weather as H
import numpy as np

ID = "WR-11"
TITLE = "Forecast wind above 10 mph, and its product with his deep-target share"
COLUMNS = {"WR": ["wind10", "wind10_x_deep"]}


def build(ctx):
    keys = ctx.keys_for("WR")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    r = H.for_rows(keys, H.team_frame(ctx, seasons), ["wind10"])
    r = r.merge(H.deep_share(ctx, keys, "receiver"), on=H.KEY, how="left")
    r["wind10_x_deep"] = np.where(r["wind10"] == 0, 0.0,
                                  r["wind10"] * (r["deep_share"] - r["deep_prior"]))
    return r[[*H.KEY, *COLUMNS["WR"]]]
