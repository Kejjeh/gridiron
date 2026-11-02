"""TE-11: the opponent's coverage of tight ends, by the target volume it allows them.

Pre-registration: docs/research/blocks/TE-11.md. The shipped `def_allowed`
already holds the fantasy points the opponent allows to tight ends; this is
the volume side, from the box scores (every season, seven folds).
"""
from __future__ import annotations

import pandas as pd

import _common as C
import _p1b_ftn_pfr_opponent as H

ID = "TE-11"
TITLE = "Opponent's targets allowed to tight ends, per game and as a share, vs the league"
COLUMNS = {"TE": ["opp_te_tgt_pg_rel", "opp_te_tgt_share_rel"]}


def build(ctx):
    keys = ctx.keys_for("TE")[["season", "week", "gsis_id", "opponent"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    w = ctx.weekly(seasons)
    tgt = pd.to_numeric(w["targets"], errors="coerce").fillna(0.0)
    is_te = (w["position"] == "TE").fillna(False).astype(bool)
    d = pd.DataFrame({"season": w["season"], "week": w["week"], "opponent": w["opponent_team"],
                      "tgt": tgt, "te_tgt": tgt.where(is_te, 0.0)}).dropna(subset=["opponent"])
    d = H.codes(d, ["opponent"])
    d = d.groupby(["season", "week", "opponent"], as_index=False)[["te_tgt", "tgt"]].sum()
    r = C.as_of(keys, d, ["opponent"], ["te_tgt", "tgt"])
    lg = H.league_as_of(keys, d, ["te_tgt", "tgt"])
    r["opp_te_tgt_pg_rel"] = (C.share(r["te_tgt_std"], r["games_std"])
                              - C.share(lg["te_tgt_lg"], lg["rows_lg"]))
    r["opp_te_tgt_share_rel"] = (C.share(r["te_tgt_std"], r["tgt_std"])
                                 - C.share(lg["te_tgt_lg"], lg["tgt_lg"]))
    return r[["season", "week", "gsis_id", *COLUMNS["TE"]]]
