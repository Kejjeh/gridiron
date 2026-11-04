"""RB-05: the running back's early-down vs passing-down role.

His share of his team's designed carries on 1st and 2nd down, and his share
of its targets on 3rd and 4th down or in the two-minute drill, season to
date and over his last 3 games. The shipped set has his raw carries
(`carries_l3`) and overall target share (`tgt_share_l3`) but no split by
situation and no carry share. Pre-registration: docs/research/blocks/RB-05.md.
Play definitions: _p1a_pbp_roles.py.
"""
from __future__ import annotations

import pandas as pd

import _common as C
import _p1a_pbp_roles as R

ID = "RB-05"
TITLE = "Early-down carry share and passing-down target share"
COLUMNS = {"RB": ["early_carry_share_std", "early_carry_share_l3",
                  "pd_tgt_share_std", "pd_tgt_share_l3"]}


def build(ctx):
    keys = ctx.keys_for("RB")[R.KEY]
    seasons = sorted(keys["season"].unique())
    pbp = ctx.pbp(seasons, sorted({*R.RUN_COLUMNS, *R.TARGET_COLUMNS, "down",
                                   "half_seconds_remaining"}))
    down = pd.to_numeric(pbp["down"], errors="coerce")
    clock = pd.to_numeric(pbp["half_seconds_remaining"], errors="coerce")
    early = R.is_designed_run(pbp) & down.isin([1, 2])
    passing_down = R.is_target(pbp) & (down.isin([3, 4]) | (clock <= R.TWO_MINUTES))
    hist = R.event_history(ctx, seasons, pbp, {
        "early_carry": (early, "rusher_player_id"),
        "pd_tgt": (passing_down, "receiver_player_id")})
    r = C.as_of(keys, hist, ["gsis_id"],
                ["early_carry", "early_carry_team", "pd_tgt", "pd_tgt_team"])
    r = R.shares(r, ["early_carry", "pd_tgt"])
    return r[[*R.KEY, *COLUMNS["RB"]]]
