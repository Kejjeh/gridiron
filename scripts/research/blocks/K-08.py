"""K-08: kicker changes and call-ups, the kicker's tenure with the team this week.

Pre-registration: docs/research/blocks/K-08.md. Kind A in the roadmap, cast as
features on the kicker rows (k_def_backtest.py's table).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _p2b_events_ramps_defense as H

ID = "K-08"
TITLE = "Kicker changes and call-ups: games with this team, first game, last game's kicker"
COLUMNS = {"K": ["k_new_team", "k_team_games", "k_team_changed"]}
#: Seasons of box scores before the first target season (enough for the cap).
HISTORY = 4
#: Games with the team are counted up to one full season.
CAP = 16


def build(ctx):
    keys = ctx.keys_for("K")[["season", "week", "gsis_id", "team"]].reset_index(drop=True)
    keys = keys.assign(season=keys["season"].astype(int), week=keys["week"].astype(int),
                       gsis_id=keys["gsis_id"].astype(str), team=keys["team"].astype(str))
    seasons = sorted(int(s) for s in keys["season"].unique())
    bx = H.box(ctx, range(min(seasons) - HISTORY, max(seasons) + 1))
    # a kicking game: at least one field-goal or extra-point attempt (usage, rule #4)
    kk = bx.loc[bx["kicks"] > 0, ["season", "week", "gsis_id", "team", "kicks"]].copy()
    kk["t"] = kk["season"] * 100 + kk["week"]
    kk = kk.sort_values(["t", "kicks", "gsis_id"], kind="stable")
    games = {key: d["t"].to_numpy() for key, d in kk.groupby(["gsis_id", "team"])}
    # the team's kicker in each game: the most attempts (ties: the larger id, stable)
    team_k = kk.drop_duplicates(["season", "week", "team"], keep="last")
    by_team = {t: (d["t"].to_numpy(), d["gsis_id"].to_numpy()) for t, d in team_k.groupby("team")}
    new, n_games, changed = [], [], []
    for s, w, g, t in zip(keys["season"], keys["week"], keys["gsis_id"], keys["team"]):
        now = s * 100 + w
        tt = games.get((g, t), np.array([]))
        n = int(np.searchsorted(tt, now, "left"))
        new.append(float(n == 0))
        n_games.append(float(min(n, CAP)))
        bt, bg = by_team.get(t, (np.array([]), np.array([])))
        j = int(np.searchsorted(bt, now, "left"))
        changed.append(float(bg[j - 1] != g) if j > 0 else np.nan)
    keys["k_new_team"], keys["k_team_games"], keys["k_team_changed"] = new, n_games, changed
    return keys[["season", "week", "gsis_id", *COLUMNS["K"]]]
