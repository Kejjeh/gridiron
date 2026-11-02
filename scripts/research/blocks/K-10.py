"""K-10: end-of-half clock management, his team's field goals in a half's last two minutes.

Pre-registration: docs/research/blocks/K-10.md. Shared definitions:
_p6_kicker_defense_pbp.py.
"""
from __future__ import annotations

import pandas as pd

import _common as C
import _p6_kicker_defense_pbp as H

ID = "K-10"
TITLE = ("End-of-half field goals: his team's attempts in the last two minutes of a half, "
         "per game (season to date, prior season)")
COLUMNS = {"K": ["fga_2min_pg", "fga_2min_pg_prior"]}
CLOCK_COLUMNS = ["play_type", "half_seconds_remaining", "game_half"]


def build(ctx):
    keys = H.kicker_keys(ctx)
    seasons = H.seasons_of(keys)
    p = ctx.pbp(range(min(seasons) - 1, max(seasons) + 1), CLOCK_COLUMNS)
    games = H.team_games(p)
    left = pd.to_numeric(p["half_seconds_remaining"], errors="coerce")
    late = p.loc[(p["play_type"] == "field_goal") & p["game_half"].isin(["Half1", "Half2"])
                 & (left <= H.TWO_MINUTES), ["season", "week", "posteam"]]
    late = late.rename(columns={"posteam": "team"}).assign(fga=1.0)
    h = H.per_team_game(games, late, ["fga"], side="team")
    now = C.as_of(keys, h, ["team"], ["fga"])
    keys["fga_2min_pg"] = H.per_game(now["fga_std"], now["games_std"])
    season = H.per_season(h, ["fga"])
    season["fga_2min_pg_prior"] = H.per_game(season["fga"], season["games"])
    keys = C.prior_season(keys, season, ["team"], ["fga_2min_pg_prior"])
    return keys[["season", "week", "gsis_id", *COLUMNS["K"]]]
