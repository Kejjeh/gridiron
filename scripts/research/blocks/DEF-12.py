"""DEF-12: return-unit quality, the team's kick and punt returns.

Pre-registration: docs/research/blocks/DEF-12.md. Shared definitions and the
prior strengths: _p6_kicker_defense_pbp.py.
"""
from __future__ import annotations

import pandas as pd

import _common as C
import _p6_kicker_defense_pbp as H

ID = "DEF-12"
TITLE = ("Return units: kick and punt return yards over the league per return, shrunk, and "
         "return touchdowns per game (season to date, prior season)")
COLUMNS = {"DEF": ["ret_yoe", "ret_yoe_prior", "ret_td_pg", "ret_td_pg_prior"]}
RETURN_COLUMNS = ["play_type", "kickoff_returner_player_id", "punt_returner_player_id",
                  "touchback", "kickoff_fair_catch", "punt_fair_catch", "return_yards",
                  "return_touchdown", "td_team"]
COUNTS = ["kr_n", "kr_y", "pr_n", "pr_y", "td"]


def yards_over(n_k, y_k, n_p, y_p, lg_k, lg_p) -> pd.Series:
    """Return yards over the league's average for the kind (kick, punt), per
    return, shrunk with N0_RETURN."""
    over = (y_k - n_k * lg_k) + (y_p - n_p * lg_p)
    return (over / (n_k + n_p + H.N0_RETURN)).astype("float64")


def build(ctx):
    keys = H.defense_keys(ctx)
    seasons = H.seasons_of(keys)
    p = ctx.pbp(range(min(seasons) - 1, max(seasons) + 1), RETURN_COLUMNS)
    games = H.team_games(p)
    r = H.returns(p)
    kick = r["kind"] == "kick"
    r = r.assign(kr_n=kick.astype(float), kr_y=r["yards"].where(kick, 0.0),
                 pr_n=(~kick).astype(float), pr_y=r["yards"].where(~kick, 0.0), td=0.0)
    scored = H.flag(p, "return_touchdown") & (
        ((p["play_type"] == "kickoff") & (p["td_team"] == p["posteam"]))
        | ((p["play_type"] == "punt") & (p["td_team"] == p["defteam"])))
    tds = p.loc[scored, ["season", "week", "td_team"]].rename(columns={"td_team": "team"})
    tds = tds.assign(team=tds["team"].astype(object), td=1.0,
                     **{c: 0.0 for c in COUNTS if c != "td"})
    h = H.per_team_game(games, pd.concat([r[["season", "week", "team", *COUNTS]], tds],
                                         ignore_index=True), COUNTS, side="team")
    now = C.as_of(keys, h, ["team"], COUNTS)
    lg = H.league_as_of(keys, h, COUNTS)
    keys["ret_yoe"] = yards_over(now["kr_n_std"].fillna(0.0), now["kr_y_std"].fillna(0.0),
                                 now["pr_n_std"].fillna(0.0), now["pr_y_std"].fillna(0.0),
                                 C.share(lg["kr_y_lg"], lg["kr_n_lg"]),
                                 C.share(lg["pr_y_lg"], lg["pr_n_lg"]))
    keys["ret_td_pg"] = H.per_game(now["td_std"], now["games_std"])
    season = H.per_season(h, COUNTS)
    league = season.groupby("season")[COUNTS].sum()
    season["ret_yoe_prior"] = yards_over(
        season["kr_n"], season["kr_y"], season["pr_n"], season["pr_y"],
        season["season"].map(league["kr_y"] / league["kr_n"]),
        season["season"].map(league["pr_y"] / league["pr_n"]))
    season["ret_td_pg_prior"] = H.per_game(season["td"], season["games"])
    keys = C.prior_season(keys, season, ["team"], ["ret_yoe_prior", "ret_td_pg_prior"])
    return keys[["season", "week", "team", *COLUMNS["DEF"]]]
