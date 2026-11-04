"""K-09: extra point or two? His team's two-point tendency after touchdowns.

Pre-registration: docs/research/blocks/K-09.md. Shared definitions and the
prior strengths: _p6_kicker_defense_pbp.py.
"""
from __future__ import annotations

import _common as C
import _p6_kicker_defense_pbp as H

ID = "K-09"
TITLE = ("Two-point tendency: his team's two-point tries per conversion try over the league, "
         "shrunk (season to date, prior season)")
COLUMNS = {"K": ["two_pt_oe", "two_pt_prior_oe"]}
CONVERSION_COLUMNS = ["play_type", "extra_point_attempt", "two_point_attempt"]


def build(ctx):
    keys = H.kicker_keys(ctx)
    seasons = H.seasons_of(keys)
    p = ctx.pbp(range(min(seasons) - 1, max(seasons) + 1), CONVERSION_COLUMNS)
    games = H.team_games(p)
    kick = (p["play_type"] == "extra_point") & H.flag(p, "extra_point_attempt")
    two = H.flag(p, "two_point_attempt") & p["play_type"].isin(["pass", "run"])
    tries = p.loc[kick | two, ["season", "week", "posteam"]].rename(columns={"posteam": "team"})
    tries = tries.assign(tries=1.0, two=two.loc[kick | two].astype(float).to_numpy())
    h = H.per_team_game(games, tries, ["tries", "two"], side="team")
    now = C.as_of(keys, h, ["team"], ["tries", "two"])
    lg = H.league_as_of(keys, h, ["tries", "two"])
    keys["two_pt_oe"] = H.over_league(now["two_std"], now["tries_std"],
                                      C.share(lg["two_lg"], lg["tries_lg"]), H.N0_TWO_POINT)
    season = H.per_season(h, ["tries", "two"])
    league = season.groupby("season")[["tries", "two"]].sum()
    season["two_pt_prior_oe"] = H.over_league(
        season["two"], season["tries"], season["season"].map(league["two"] / league["tries"]),
        H.N0_TWO_POINT)
    keys = C.prior_season(keys, season, ["team"], ["two_pt_prior_oe"])
    return keys[["season", "week", "gsis_id", *COLUMNS["K"]]]
