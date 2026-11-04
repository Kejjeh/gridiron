"""DEF-04: the opposing starting QB's sack rate and time to throw.

Pre-registration: docs/research/blocks/DEF-04.md. Shared builders:
_p4_participation_priors.py (package P4; this block reads no participation).
"""
from __future__ import annotations

import _p4_participation_priors as P

ID = "DEF-04"
TITLE = "Opposing starting QB (depth chart, pre-game): sack rate and NGS time to throw, season to date"
COLUMNS = {"DEF": ["opp_qb1_sack_rate_std", "opp_qb1_ttt_std"]}


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team", "opponent"]]
    seasons = sorted({int(s) for s in keys["season"]})
    qb1 = P.starting_qb(ctx, seasons).rename(columns={"team": "opponent"})
    t = keys.merge(qb1, on=["season", "week", "opponent"], how="left")
    named = t.loc[t["gsis_id"].notna(), ["season", "week", "gsis_id"]]
    sack = P.qb_sack_rate_as_of(P.dropbacks(ctx, seasons), named)
    ttt = P.qb_ttt_as_of(ctx, named)
    r = (t.merge(sack, on=["season", "week", "gsis_id"], how="left")
         .merge(ttt, on=["season", "week", "gsis_id"], how="left")
         .rename(columns={"qb_sack_rate": "opp_qb1_sack_rate_std", "qb_ttt": "opp_qb1_ttt_std"}))
    return r[["season", "week", "team", *COLUMNS["DEF"]]]
