"""DEF-11: this defense's own regulars who are out this week, front and secondary.

Pre-registration: docs/research/blocks/DEF-11.md. Regulars come from usage
(rule #4): snap counts over the defense's last three games, not the depth
chart's starter tag.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _p2b_events_ramps_defense as H

ID = "DEF-11"
TITLE = "Own defensive regulars out this week (front seven, secondary) and questionable"
COLUMNS = {"DEF": ["own_front_out", "own_back_out", "own_def_q"]}
#: A regular took at least MIN_SHARE of the defense's snaps over its last LAST games.
LAST = 3
MIN_SHARE = 0.5


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team"]].reset_index(drop=True)
    keys = keys.assign(season=keys["season"].astype(int), week=keys["week"].astype(int),
                       team=keys["team"].astype(str))
    seasons = sorted(int(s) for s in keys["season"].unique())
    reg = H.regulars(ctx, seasons, keys, "defense", (*H.FRONT, *H.BACK), last=LAST,
                     min_share=MIN_SHARE)
    av = H.availability(ctx, seasons, reg[["season", "week", "team", "gsis_id"]])
    av = av.merge(reg[["season", "week", "team", "gsis_id", "position"]],
                  on=["season", "week", "team", "gsis_id"], how="left")
    av["front"] = av["position"].isin(H.FRONT).astype(float)
    av["back"] = av["position"].isin(H.BACK).astype(float)
    av["front_out"] = av["out"] * av["front"]
    av["back_out"] = av["out"] * av["back"]
    per = (av.groupby(["season", "week", "team"], as_index=False)
           .agg(own_front_out=("front_out", "sum"), own_back_out=("back_out", "sum"),
                own_def_q=("questionable", "sum")))
    out = keys.merge(per, on=["season", "week", "team"], how="left")
    return out[["season", "week", "team", *COLUMNS["DEF"]]]
