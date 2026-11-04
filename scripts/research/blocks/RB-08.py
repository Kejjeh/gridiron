"""RB-08: the opponent's run defense, by yards before contact allowed and the box it shows.

Pre-registration: docs/research/blocks/RB-08.md. Scored on all seven folds:
the PFR part covers every season; the FTN box count starts in 2022 and is
unknown before it.
"""
from __future__ import annotations

import _common as C
import _p1b_ftn_pfr_opponent as H

ID = "RB-08"
TITLE = "Opponent's yards before contact allowed per RB carry, and its box on runs"
COLUMNS = {"RB": ["opp_ybc_allowed_std", "opp_box_std"]}


def build(ctx):
    keys = ctx.keys_for("RB")[["season", "week", "gsis_id", "opponent"]]
    seasons = sorted(int(s) for s in keys["season"].unique())

    # yards before contact the defense allowed to running backs, per carry
    rush = H.back_rushes(ctx, seasons)
    d = rush.groupby(["season", "week", "opponent"], as_index=False)[["ybc", "carries"]].sum()
    r = C.as_of(keys, d, ["opponent"], ["ybc", "carries"])
    lg = H.league_as_of(keys, d, ["ybc", "carries"])
    r["opp_ybc_allowed_std"] = (C.share(r["ybc_std"], r["carries_std"])
                                - C.share(lg["ybc_lg"], lg["carries_lg"]))

    # defenders in the box on the designed runs it faced (FTN, 2022 on)
    plays = H.ftn_plays(ctx, seasons, ["play_type", "qb_scramble", "two_point_attempt"],
                        ["n_defense_box"])
    runs = plays.loc[(plays["play_type"] == "run") & (plays["qb_scramble"].fillna(0) == 0)
                     & (plays["two_point_attempt"].fillna(0) == 0)
                     & (plays["n_defense_box"].fillna(0) > 0)]
    b = (runs.assign(box=runs["n_defense_box"].astype(float), runs=1.0)
         .groupby(["season", "week", "defteam"], as_index=False)[["box", "runs"]].sum()
         .rename(columns={"defteam": "opponent"}))
    rb = C.as_of(keys, b, ["opponent"], ["box", "runs"])
    lgb = H.league_as_of(keys, b, ["box", "runs"])
    r["opp_box_std"] = C.share(rb["box_std"], rb["runs_std"]) - C.share(lgb["box_lg"], lgb["runs_lg"])
    return r[["season", "week", "gsis_id", *COLUMNS["RB"]]]
