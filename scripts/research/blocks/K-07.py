"""K-07: spread and total interplay for kickers, beyond the shipped implied and spread.

The shipped K set has the implied total and the spread, so the total is
already spanned linearly. This block adds the two second-order terms: the
spread times the total (relative to the previous season's mean total), and
a favourite's points in a low total (the spread where favoured times how far
the total sits below that mean). Pre-registration:
docs/research/blocks/K-07.md.
"""
from __future__ import annotations

import _p5_schedule_weather as H

ID = "K-07"
TITLE = "Spread times total, and favoured in a low total"
COLUMNS = {"K": ["spread_x_total", "fav_low_total"]}


def build(ctx):
    keys = ctx.keys_for("K")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    tf = H.team_frame(ctx, seasons, with_weather=False)
    r = H.for_rows(keys, tf, ["spread", "total_rel"])
    r["spread_x_total"] = r["spread"] * r["total_rel"]
    r["fav_low_total"] = r["spread"].clip(lower=0) * (-r["total_rel"]).clip(lower=0)
    return r[[*H.KEY, *COLUMNS["K"]]]
