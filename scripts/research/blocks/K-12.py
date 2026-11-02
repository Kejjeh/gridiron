"""K-12: surface, and outdoor games late in the season, for kickers.

Artificial turf (1) or grass (0) from the pre-game schedule, and an outdoor
game (roof outdoors or open) in December or January. The shipped K set has
the roof (`dome`) and nothing on the surface or the calendar.
Pre-registration: docs/research/blocks/K-12.md.
"""
from __future__ import annotations

import _p5_schedule_weather as H
import numpy as np

ID = "K-12"
TITLE = "Artificial turf, and an outdoor game in December or January"
COLUMNS = {"K": ["turf", "outdoor_dec"]}
LATE_MONTHS = (12, 1)


def build(ctx):
    keys = ctx.keys_for("K")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    tf = H.team_frame(ctx, seasons, with_weather=False)
    r = H.for_rows(keys, tf, ["turf", "indoor", "month"])
    late = r["month"].isin(LATE_MONTHS)
    r["outdoor_dec"] = np.where(r["indoor"].isna(), np.nan,
                                ((r["indoor"] == 0) & late).astype(float))
    return r[[*H.KEY, *COLUMNS["K"]]]
