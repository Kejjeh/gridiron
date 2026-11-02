"""WR-07: targets per route run, last season, as a prior.

Pre-registration: docs/research/blocks/WR-07.md. Shared builders:
_p4_participation_priors.py (participation priors, package P4).
"""
from __future__ import annotations

import numpy as np

import _common as C
import _p4_participation_priors as P

ID = "WR-07"
TITLE = "Targets per route, prior season (and times this season's snap share)"
COLUMNS = {"WR": ["tprr_prior", "tprr_prior_x_snap_l3"]}


def build(ctx):
    keys = ctx.keys_for("WR")[["season", "week", "gsis_id"]]
    seasons = sorted({int(s) for s in keys["season"]})
    prior = [s - 1 for s in seasons]
    rt = P.player_routes(P.charted(ctx, P.dropbacks(ctx, prior), prior))
    lg = P.receivers(rt).groupby("season")[["targets", "routes"]].sum()
    rt = rt.merge((lg["targets"] / lg["routes"]).rename("lg").reset_index(), on="season")
    # shrunk toward the league's rate with K_TPRR pseudo-routes (a rate, not centered)
    rt["tprr_prior"] = rt["lg"] + P.eb_rel(rt["targets"], rt["routes"], rt["lg"], P.K_TPRR)
    r = C.prior_season(keys, rt, ["gsis_id"], ["tprr_prior"])

    # this season's offensive snap share, mean of his last 3 games before the week
    sn = ctx.snaps(seasons)
    hist = sn.loc[sn["gsis_id"].notna(), ["season", "week", "gsis_id", "offense_pct"]].copy()
    hist["gsis_id"] = C.ids(hist["gsis_id"])
    hist = (hist.loc[(hist["gsis_id"] != "") & hist["offense_pct"].notna()]
            .sort_values("offense_pct", ascending=False)
            .drop_duplicates(["season", "week", "gsis_id"]))
    a = C.as_of(keys, hist, ["gsis_id"], ["offense_pct"])
    snap_l3 = a["offense_pct_l3"] / np.minimum(a["games_std"], 3)
    r = r.merge(a[["season", "week", "gsis_id"]].assign(snap_l3=snap_l3),
                on=["season", "week", "gsis_id"], how="left")
    r["tprr_prior_x_snap_l3"] = r["tprr_prior"] * r["snap_l3"]
    return r[["season", "week", "gsis_id", *COLUMNS["WR"]]]
