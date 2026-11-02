"""DEF-05: this defense's pass rush against the opposing line's protection.

Pre-registration: docs/research/blocks/DEF-05.md. Shared builders:
_p4_participation_priors.py (participation priors, package P4).
"""
from __future__ import annotations

import pandas as pd

import _common as C
import _p4_participation_priors as P

ID = "DEF-05"
TITLE = "Pass rush generated vs the opponent's pressure allowed"
COLUMNS = {"DEF": ["press_gen_prior", "sackhit_gen_std",
                   "opp_press_allowed_prior", "opp_sackhit_allowed_std"]}


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team", "opponent"]]
    seasons = sorted({int(s) for s in keys["season"]})
    prior = [s - 1 for s in seasons]
    drop = P.dropbacks(ctx, sorted(set(seasons) | set(prior)))
    press = P.team_pressure(P.charted(ctx, drop, prior))
    sides = pd.concat([keys[["season", "week", "team"]],
                       keys[["season", "week", "opponent"]].rename(columns={"opponent": "team"})])
    std = P.team_sackhit_as_of(drop.loc[drop["season"].isin(seasons)], sides)

    r = keys.merge(std[["season", "week", "team", "sackhit_gen_std"]],
                   on=["season", "week", "team"], how="left")
    r = r.merge(std[["season", "week", "team", "sackhit_allowed_std"]].rename(
        columns={"team": "opponent", "sackhit_allowed_std": "opp_sackhit_allowed_std"}),
        on=["season", "week", "opponent"], how="left")
    r = C.prior_season(r, press.rename(columns={"press_gen": "press_gen_prior"}),
                       ["team"], ["press_gen_prior"])
    r = C.prior_season(r, press.rename(columns={"team": "opponent",
                                                "press_allowed": "opp_press_allowed_prior"}),
                       ["opponent"], ["opp_press_allowed_prior"])
    return r[["season", "week", "team", *COLUMNS["DEF"]]]
