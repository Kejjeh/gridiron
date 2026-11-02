"""DEF-06: the opponent's offensive-line regulars who are out or questionable this week.

Pre-registration: docs/research/blocks/DEF-06.md. Regulars come from usage
(rule #4): snap counts over the opponent's last three games, not the depth
chart's starter tag.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _p2b_events_ramps_defense as H

ID = "DEF-06"
TITLE = "Opponent's offensive-line regulars out, and questionable, this week"
COLUMNS = {"DEF": ["opp_ol_out", "opp_ol_q"]}
#: A regular took at least MIN_SHARE of the offense's snaps over its last LAST games.
LAST = 3
MIN_SHARE = 0.5


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team", "opponent"]].reset_index(drop=True)
    keys = keys.assign(season=keys["season"].astype(int), week=keys["week"].astype(int),
                       team=keys["team"].astype(str), opponent=keys["opponent"].astype(str))
    seasons = sorted(int(s) for s in keys["season"].unique())
    targets = keys[["season", "week", "opponent"]].drop_duplicates().rename(columns={"opponent": "team"})
    reg = H.regulars(ctx, seasons, targets, "offense", H.OL, last=LAST, min_share=MIN_SHARE)
    av = H.availability(ctx, seasons, reg[["season", "week", "team", "gsis_id"]])
    per = (av.groupby(["season", "week", "team"], as_index=False)
           .agg(opp_ol_out=("out", "sum"), opp_ol_q=("questionable", "sum")))
    # a team-week with snap history but no regular (cannot happen with 5 linemen
    # on the field) would be absent here and come back missing, like no history
    out = keys.merge(per.rename(columns={"team": "opponent"}), on=["season", "week", "opponent"],
                     how="left")
    return out[["season", "week", "team", *COLUMNS["DEF"]]]
