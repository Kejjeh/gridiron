"""RB-07: game-script volume from the spread and the total.

His team's closing spread (+ = favoured), and the spread and the total
(relative to the previous season's mean total) each multiplied by his
season-to-date share of his teams' passing-down targets (3rd or 4th down, or
a half's last two minutes: RB-05's definition, _p1a_pbp_roles.py). The
shipped RB set has the implied total and no spread, nothing that lets the
line move a third-down back and an early-down back apart. QUANT_FOUNDATIONS
§1.4 expects about zero. Pre-registration: docs/research/blocks/RB-07.md.
"""
from __future__ import annotations

import _common as C
import _p1a_pbp_roles as R
import _p5_schedule_weather as H
import pandas as pd

ID = "RB-07"
TITLE = "Spread, and spread and total times his passing-down target share"
COLUMNS = {"RB": ["team_spread", "spread_x_pd_share", "total_x_pd_share"]}


def build(ctx):
    keys = ctx.keys_for("RB")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    tf = H.team_frame(ctx, seasons, with_weather=False)
    r = H.for_rows(keys, tf, ["spread", "total_rel"])
    pbp = ctx.pbp(seasons, sorted({*R.TARGET_COLUMNS, "down", "half_seconds_remaining"}))
    down = pd.to_numeric(pbp["down"], errors="coerce")
    clock = pd.to_numeric(pbp["half_seconds_remaining"], errors="coerce")
    passing_down = R.is_target(pbp) & (down.isin([3, 4]) | (clock <= R.TWO_MINUTES))
    hist = R.event_history(ctx, seasons, pbp, {"pd_tgt": (passing_down, "receiver_player_id")})
    a = C.as_of(keys[R.KEY], hist, ["gsis_id"], ["pd_tgt", "pd_tgt_team"])
    a["pd_share"] = C.share(a["pd_tgt_std"], a["pd_tgt_team_std"])
    r = r.merge(a[[*R.KEY, "pd_share"]], on=R.KEY, how="left")
    r["team_spread"] = r["spread"]
    r["spread_x_pd_share"] = r["spread"] * r["pd_share"]
    r["total_x_pd_share"] = r["total_rel"] * r["pd_share"]
    return r[[*R.KEY, *COLUMNS["RB"]]]
