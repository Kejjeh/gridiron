"""WR-05: the opponent's man/zone mix against the receiver's own split.

Pre-registration: docs/research/blocks/WR-05.md. Shared builders:
_p4_participation_priors.py (participation priors, package P4).
"""
from __future__ import annotations

import _common as C
import _p4_participation_priors as P

ID = "WR-05"
TITLE = "Opponent man rate x the receiver's man-minus-zone targets per route (prior season)"
COLUMNS = {"WR": ["wr_man_split_prior", "opp_man_rate_prior", "wr_man_matchup_prior"]}


def build(ctx):
    keys = ctx.keys_for("WR")[["season", "week", "gsis_id", "opponent"]]
    seasons = sorted({int(s) for s in keys["season"]})
    prior = [s - 1 for s in seasons]
    ch = P.charted(ctx, P.dropbacks(ctx, prior), prior)
    rt = P.player_routes(ch)
    # the league's split, over every receiver (targeted at least once that season)
    lg = P.receivers(rt).groupby("season")[["routes_man", "targets_man", "routes_zone",
                                            "targets_zone"]].sum()
    lg_split = (lg["targets_man"] / lg["routes_man"]
                - lg["targets_zone"] / lg["routes_zone"]).rename("lg").reset_index()
    rt = rt.merge(lg_split, on="season", how="left")
    rt["wr_man_split_prior"] = P.split_eb(rt["routes_man"], rt["targets_man"], rt["routes_zone"],
                                          rt["targets_zone"], rt["lg"], P.K_TPRR_SPLIT)
    man = P.defense_man_rate(ch).rename(columns={"team": "opponent",
                                                 "man_rate": "opp_man_rate_prior"})
    r = C.prior_season(keys, rt, ["gsis_id"], ["wr_man_split_prior"])
    r = C.prior_season(r, man, ["opponent"], ["opp_man_rate_prior"])
    r["wr_man_matchup_prior"] = r["wr_man_split_prior"] * r["opp_man_rate_prior"]
    return r[["season", "week", "gsis_id", *COLUMNS["WR"]]]
