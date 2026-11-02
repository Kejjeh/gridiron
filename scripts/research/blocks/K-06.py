"""K-06: the coach's trust in long field goals, and the kicker's accuracy by distance.

Pre-registration: docs/research/blocks/K-06.md. Shared definitions and the
prior strengths: _p6_kicker_defense_pbp.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
import _p6_kicker_defense_pbp as H

ID = "K-06"
TITLE = ("Long field goals: his team's share of attempts from 50+ over the league (season to date, "
         "prior season), and his makes over expected by distance (slow prior)")
COLUMNS = {"K": ["fg50_share_oe", "fg50_share_prior_oe", "k_fg_oe"]}
#: Seasons before the row's in his accuracy prior, and in the league make-rate
#: table (by 5-yard distance bucket) his attempts are measured against.
ACCURACY_SEASONS = 3


def accuracy(keys: pd.DataFrame, fg: pd.DataFrame) -> pd.Series:
    """His field goals made minus expected (the league's make rate at the
    attempt's distance bucket over the three seasons before the row's), over
    his attempts in those seasons and this season before the row's week,
    per attempt, shrunk: (made - expected) / (attempts + N0_KICK_ACCURACY)."""
    fg = fg.assign(bucket=H.bucket(fg["dist"]), n=1.0)
    out = pd.Series(np.nan, index=keys.index, dtype="float64")
    for s in sorted(int(x) for x in keys["season"].unique()):
        rate = fg.loc[fg["season"].between(s - ACCURACY_SEASONS, s - 1)].groupby("bucket")["made"].mean()
        mine = fg.loc[fg["season"].between(s - ACCURACY_SEASONS, s)].copy()
        mine["oe"] = mine["made"] - mine["bucket"].map(rate)
        before = mine.loc[mine["season"] < s].groupby("gsis_id")[["oe", "n"]].sum()
        now = mine.loc[mine["season"] == s].groupby(["season", "week", "gsis_id"], as_index=False)[
            ["oe", "n"]].sum()
        rows = keys.loc[keys["season"] == s, ["season", "week", "gsis_id"]]
        r = C.as_of(rows, now, ["gsis_id"], ["oe", "n"])
        b = before.reindex(rows["gsis_id"].to_numpy())
        oe = b["oe"].fillna(0.0).to_numpy() + r["oe_std"].fillna(0.0).to_numpy()
        n = b["n"].fillna(0.0).to_numpy() + r["n_std"].fillna(0.0).to_numpy()
        out.loc[rows.index] = oe / (n + H.N0_KICK_ACCURACY)
    return out


def build(ctx):
    keys = H.kicker_keys(ctx)
    keys["gsis_id"] = keys["gsis_id"].astype(object)
    seasons = H.seasons_of(keys)
    p = ctx.pbp(range(min(seasons) - ACCURACY_SEASONS, max(seasons) + 1), H.FG_COLUMNS)
    fg = H.field_goals(p)
    games = H.team_games(p)
    # his team's share of its attempts from 50+: this season so far, and last season
    att = H.per_team_game(games, fg.assign(att=1.0), ["att", "long"], side="team")
    now = C.as_of(keys, att, ["team"], ["att", "long"])
    lg = H.league_as_of(keys, att, ["att", "long"])
    keys["fg50_share_oe"] = H.over_league(now["long_std"], now["att_std"],
                                          C.share(lg["long_lg"], lg["att_lg"]), H.N0_LONG_FG)
    season = H.per_season(att, ["att", "long"])
    league = season.groupby("season")[["att", "long"]].sum()
    season["fg50_share_prior_oe"] = H.over_league(
        season["long"], season["att"], season["season"].map(league["long"] / league["att"]),
        H.N0_LONG_FG)
    keys = C.prior_season(keys, season, ["team"], ["fg50_share_prior_oe"])
    # his accuracy by distance, a slow prior (rule #6)
    keys["k_fg_oe"] = accuracy(keys, fg)
    return keys[["season", "week", "gsis_id", *COLUMNS["K"]]]
