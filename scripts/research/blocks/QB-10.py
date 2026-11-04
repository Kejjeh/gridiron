"""QB-10: the opponent's man/zone mix against the QB's own man/zone split.

Pre-registration: docs/research/blocks/QB-10.md. Shared builders:
_p4_participation_priors.py (participation priors, package P4).
"""
from __future__ import annotations

import _common as C
import _p4_participation_priors as P

ID = "QB-10"
TITLE = "Opponent man rate x the QB's man-minus-zone EPA split (prior season)"
COLUMNS = {"QB": ["man_split_prior", "opp_man_rate_prior", "man_matchup_prior"]}


def build(ctx):
    keys = ctx.keys_for("QB")[["season", "week", "gsis_id", "opponent"]]
    seasons = sorted({int(s) for s in keys["season"]})
    prior = [s - 1 for s in seasons]
    ch = P.charted(ctx, P.dropbacks(ctx, prior), prior)
    split = P.qb_coverage_split(ch).rename(columns={"man_split": "man_split_prior"})
    man = P.defense_man_rate(ch).rename(columns={"team": "opponent",
                                                 "man_rate": "opp_man_rate_prior"})
    r = C.prior_season(keys, split, ["gsis_id"], ["man_split_prior"])
    r = C.prior_season(r, man, ["opponent"], ["opp_man_rate_prior"])
    r["man_matchup_prior"] = r["man_split_prior"] * r["opp_man_rate_prior"]
    return r[["season", "week", "gsis_id", *COLUMNS["QB"]]]
