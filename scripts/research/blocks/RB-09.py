"""RB-09: his own line's run blocking, by the team's yards before contact per RB carry.

Pre-registration: docs/research/blocks/RB-09.md. PFR advanced rushing covers
every season (2018 on), so the block is scored on all seven folds. ESPN's
run-block win rates are not free and are deferred.
"""
from __future__ import annotations

import _common as C
import _p1b_ftn_pfr_opponent as H

ID = "RB-09"
TITLE = "Own team's yards before contact per RB carry, season to date and prior season"
COLUMNS = {"RB": ["team_ybc_std", "team_ybc_prior"]}


def build(ctx):
    keys = ctx.keys_for("RB")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    rush = H.back_rushes(ctx, [seasons[0] - 1, *seasons])
    d = rush.groupby(["season", "week", "team"], as_index=False)[["ybc", "carries"]].sum()

    r = C.as_of(keys, d, ["team"], ["ybc", "carries"])
    lg = H.league_as_of(keys, d, ["ybc", "carries"])
    r["team_ybc_std"] = (C.share(r["ybc_std"], r["carries_std"])
                         - C.share(lg["ybc_lg"], lg["carries_lg"]))

    per = d.groupby(["season", "team"], as_index=False)[["ybc", "carries"]].sum()
    league = d.groupby("season")[["ybc", "carries"]].sum()
    per["team_ybc_prior"] = (C.share(per["ybc"], per["carries"])
                             - per["season"].map(league["ybc"] / league["carries"]))
    r = C.prior_season(r, per, ["team"], ["team_ybc_prior"])
    return r[["season", "week", "gsis_id", *COLUMNS["RB"]]]
