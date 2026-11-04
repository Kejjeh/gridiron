"""DEF-07: the opponent's interception-worthy throws against its interceptions, and its fumble luck.

Pre-registration: docs/research/blocks/DEF-07.md. FTN charting starts in
2022, so the block is scored on the 2022-2025 folds; 2019-2021 still train,
with the two quarterback columns unknown. Shared definitions and the prior
strengths: _p6_kicker_defense_pbp.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
import _p6_kicker_defense_pbp as H

ID = "DEF-07"
TITLE = ("Opponent's quarterback: interception-worthy throws over the league and against his "
         "interceptions (FTN, shrunk); the opponent's fumbles and fumbles lost over expected")
COLUMNS = {"DEF": ["oqb_intworthy_oe", "oqb_worthy_minus_int", "opp_fumbles_pg",
                   "opp_fumbles_lost_oe_pg"]}
FOLDS = (2022, 2023, 2024, 2025)
PLAY_COLUMNS = ["play_type", "pass_attempt", "sack", "interception", "passer_player_id",
                "fumble", "fumble_lost", "fumbled_1_team"]


def expected_passer(keys: pd.DataFrame, throws: pd.DataFrame) -> pd.Series:
    """The opponent's quarterback for the row: the player with the most
    throws for the opponent in its latest game before the row's week this
    season (ties: the smaller id). NaN when it has no earlier game."""
    n = throws.groupby(["season", "week", "team", "gsis_id"]).size().rename("n").reset_index()
    top = (n.sort_values(["season", "week", "team", "n", "gsis_id"],
                         ascending=[True, True, True, False, True], kind="stable")
           .drop_duplicates(["season", "week", "team"])[["season", "week", "team", "gsis_id"]])
    top = top.assign(season=top["season"].astype("int64"), week=top["week"].astype("int64"),
                     team=top["team"].astype(object))
    t = H.opponent_view(keys).reset_index(drop=True)
    t = t.assign(season=t["season"].astype("int64"), week=t["week"].astype("int64"),
                 team=t["team"].astype(object), _order=np.arange(len(t)))
    r = pd.merge_asof(t.sort_values("week", kind="stable"), top.sort_values("week", kind="stable"),
                      on="week", by=["season", "team"], allow_exact_matches=False,
                      direction="backward")
    return r.sort_values("_order")["gsis_id"].reset_index(drop=True)


def build(ctx):
    keys = H.defense_keys(ctx)
    seasons = H.seasons_of(keys)
    p = ctx.pbp(range(min(seasons) - 1, max(seasons) + 1), PLAY_COLUMNS)
    p = H.codes(p[["season", "week", "game_id", "play_id", "posteam", "defteam",
                   *PLAY_COLUMNS]].copy(), ["fumbled_1_team"])
    games = H.team_games(p)

    # 1. the opponent's quarterback on FTN-charted throws, this season so far
    t = p.loc[(p["play_type"] == "pass") & H.flag(p, "pass_attempt") & ~H.flag(p, "sack")
              & p["passer_player_id"].notna()]
    throws = pd.DataFrame({"season": t["season"], "week": t["week"], "game_id": t["game_id"],
                           "play_id": pd.to_numeric(t["play_id"], errors="coerce"),
                           "team": t["posteam"].astype(object),
                           "gsis_id": C.ids(t["passer_player_id"]).astype(object),
                           "ints": H.flag(t, "interception").astype(float)})
    keys["qb"] = expected_passer(keys, throws)
    charted = [s for s in range(min(seasons), max(seasons) + 1) if s >= H.FTN_FIRST]
    f = ctx.ftn(charted)[["nflverse_game_id", "nflverse_play_id", "is_interception_worthy"]]
    f = f.rename(columns={"nflverse_game_id": "game_id", "nflverse_play_id": "play_id"})
    f = f.assign(play_id=pd.to_numeric(f["play_id"], errors="coerce")).dropna(subset=["play_id"])
    f = f.drop_duplicates(["game_id", "play_id"])
    c = throws.dropna(subset=["play_id"]).merge(f, on=["game_id", "play_id"], how="inner")
    c = c.assign(throws=1.0, worthy=c["is_interception_worthy"].fillna(False).astype(bool)
                 .astype(float))
    hist = c.groupby(["season", "week", "gsis_id"], as_index=False)[["throws", "worthy", "ints"]].sum()
    target = keys[["season", "week", "qb"]].rename(columns={"qb": "gsis_id"})
    target["gsis_id"] = target["gsis_id"].fillna("")
    mine = C.as_of(target, hist, ["gsis_id"], ["throws", "worthy", "ints"])
    lg = H.league_as_of(keys, hist, ["throws", "worthy", "ints"])
    m_w = C.share(lg["worthy_lg"], lg["throws_lg"])
    m_i = C.share(lg["ints_lg"], lg["throws_lg"])
    known = keys["qb"].notna()
    keys["oqb_intworthy_oe"] = H.over_league(mine["worthy_std"], mine["throws_std"], m_w,
                                             H.N0_INTERCEPTION).where(known)
    keys["oqb_worthy_minus_int"] = H.over_league(mine["worthy_std"] - mine["ints_std"].fillna(0.0),
                                                 mine["throws_std"], m_w - m_i,
                                                 H.N0_INTERCEPTION).where(known)

    # 2. the opponent's offensive fumbles, lost or not, this season so far
    u = p.loc[p["play_type"].isin(["pass", "run"]) & H.flag(p, "fumble")
              & (p["fumbled_1_team"] == p["posteam"])]
    fum = pd.DataFrame({"season": u["season"], "week": u["week"], "team": u["posteam"],
                        "fumbles": 1.0, "lost": H.flag(u, "fumble_lost").astype(float)})
    h = H.per_team_game(games, fum, ["fumbles", "lost"], side="team")
    opp = C.as_of(H.opponent_view(keys), h, ["team"], ["fumbles", "lost"])
    league = h.groupby("season")[["fumbles", "lost"]].sum()
    lost_share = (keys["season"] - 1).map(league["lost"] / league["fumbles"])
    keys["opp_fumbles_pg"] = H.per_game(opp["fumbles_std"], opp["games_std"])
    keys["opp_fumbles_lost_oe_pg"] = H.per_game(
        opp["lost_std"] - opp["fumbles_std"] * lost_share.to_numpy(), opp["games_std"])
    return keys[["season", "week", "team", *COLUMNS["DEF"]]]
