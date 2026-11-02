"""RB-10: role at risk from transactions, a running back new to the team's 53 this week.

Pre-registration: docs/research/blocks/RB-10.md. Kind A in the roadmap
(record-only first), cast as features on every running back of the team.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _p2b_events_ramps_defense as H

ID = "RB-10"
TITLE = "Running backs new to the team's 53 this week (signings, claims, trades, call-ups)"
COLUMNS = {"RB": ["rb_new_ext", "rb_new_int", "rb_new_opp"]}
#: The arrivals' workload: opportunities per game over their last RECENT games.
RECENT = 8


def build(ctx):
    keys = ctx.keys_for("RB")[["season", "week", "gsis_id", "team"]].reset_index(drop=True)
    keys = keys.assign(season=keys["season"].astype(int), week=keys["week"].astype(int),
                       gsis_id=keys["gsis_id"].astype(str), team=keys["team"].astype(str))
    seasons = sorted(int(s) for s in keys["season"].unique())
    ros = H.roster(ctx, seasons)
    # the team's roster weeks (one per team game) and the one before each
    tw = ros[["season", "team", "week"]].drop_duplicates().sort_values(["season", "team", "week"])
    tw["prev"] = tw.groupby(["season", "team"])["week"].shift(1)
    tw = tw.loc[tw["prev"].notna()].astype({"prev": int})
    now = ros.loc[ros["status"].isin(H.ON_53) & (ros["position"] == "RB"),
                  ["season", "week", "team", "gsis_id"]]
    now = now.merge(tw, on=["season", "team", "week"], how="inner")
    before = ros[["season", "week", "team", "gsis_id", "status"]].rename(
        columns={"week": "prev", "status": "prev_status"})
    now = now.merge(before, on=["season", "prev", "team", "gsis_id"], how="left")
    now["ext"] = now["prev_status"].isna()                               # not with the team at all
    now["int"] = now["prev_status"].notna() & ~now["prev_status"].isin(H.ON_53)  # its PS or a list
    new = now.loc[now["ext"] | now["int"]].copy()

    # each arrival's opportunities per game over his last RECENT games before the week
    bx = H.box(ctx, [min(seasons) - 1, *seasons])
    bx = bx.assign(opps=bx["carries"] + bx["targets"], t=bx["season"] * 100 + bx["week"])
    bx = bx.sort_values("t")
    hist = {g: (d["t"].to_numpy(), d["opps"].to_numpy()) for g, d in bx.groupby("gsis_id")}
    opp = []
    for s, w, g in zip(new["season"], new["week"], new["gsis_id"]):
        t, o = hist.get(g, (np.array([]), np.array([])))
        n = int(np.searchsorted(t, s * 100 + w, "left"))
        recent = o[max(0, n - RECENT):n]
        # only this season and last (a box score two seasons back says little)
        recent = recent[t[max(0, n - RECENT):n] >= (s - 1) * 100]
        opp.append(float(recent.mean()) if len(recent) else 0.0)
    new["opp_pg"] = opp

    out = keys.merge(tw[["season", "team", "week"]].assign(_have=1.0),
                     on=["season", "team", "week"], how="left")
    agg = {}
    for (s, w, t), d in new.groupby(["season", "week", "team"]):
        agg[(s, w, t)] = list(zip(d["gsis_id"], d["ext"], d["int"], d["opp_pg"]))
    ext, inn, opp = [], [], []
    for s, w, t, g, have in zip(out["season"], out["week"], out["team"], out["gsis_id"], out["_have"]):
        if have != 1.0:                         # no roster this week, or none before: unknown
            ext.append(np.nan), inn.append(np.nan), opp.append(np.nan)
            continue
        rows = [r for r in agg.get((s, w, t), []) if r[0] != g]   # never himself
        ext.append(float(sum(r[1] for r in rows)))
        inn.append(float(sum(r[2] for r in rows)))
        opp.append(float(sum(r[3] for r in rows)))
    out["rb_new_ext"], out["rb_new_int"], out["rb_new_opp"] = ext, inn, opp
    return out[["season", "week", "gsis_id", *COLUMNS["RB"]]]
