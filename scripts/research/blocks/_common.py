"""Leak-safe helpers the position blocks share (not a block: no ID).

Every helper aggregates history rows with week < the target row's week, in
the same season, so a block built from them passes the runner's leak check by
construction. Prior-season helpers read season - 1 only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from gridiron.ids import normalize_id


def ids(series: pd.Series) -> pd.Series:
    return series.map(normalize_id)


def player_games(ctx, seasons) -> pd.DataFrame:
    """One row per regular-season game a player recorded a box score in:
    (season, week, gsis_id, team, position)."""
    w = ctx.weekly(seasons)
    g = w[["season", "week", "player_id", "team", "position"]].rename(
        columns={"player_id": "gsis_id"})
    g["gsis_id"] = ids(g["gsis_id"])
    return g.drop_duplicates(["season", "week", "gsis_id"])


def team_games(ctx, seasons) -> pd.DataFrame:
    """One row per regular-season team-game: (season, week, team, opponent,
    home, game_id), from the pre-game schedule."""
    s = ctx.schedule(seasons)
    home = s[["season", "week", "game_id", "home_team", "away_team"]].rename(
        columns={"home_team": "team", "away_team": "opponent"}).assign(home=1.0)
    away = s[["season", "week", "game_id", "away_team", "home_team"]].rename(
        columns={"away_team": "team", "home_team": "opponent"}).assign(home=0.0)
    return pd.concat([home, away], ignore_index=True)


def share(num, den) -> pd.Series:
    num, den = pd.Series(num, dtype="float64"), pd.Series(den, dtype="float64")
    return (num / den.where(den > 0)).astype("float64")


def as_of(target: pd.DataFrame, history: pd.DataFrame, by, cols, last: int = 3) -> pd.DataFrame:
    """For each target row, sums of `cols` over the history rows of the same
    season and `by` key with week < the row's week: season to date
    (`<col>_std`) and over the last `last` such rows (`<col>_l<last>`), plus
    `games_std` (how many history rows). NaN where there is no earlier row.

    `history` must hold one row per game for its key (a player-game, a
    team-game); a game the key did not play is simply absent."""
    by = list(by)
    h = history.sort_values(["season", *by, "week"]).reset_index(drop=True)
    grp = h.groupby(["season", *by], sort=False)
    out = h[["season", "week", *by]].copy()
    for c in cols:
        v = h[c].astype("float64")
        out[f"{c}_std"] = v.groupby([h["season"], *[h[b] for b in by]]).cumsum()
        out[f"{c}_l{last}"] = (v.groupby([h["season"], *[h[b] for b in by]])
                               .transform(lambda x: x.rolling(last, min_periods=1).sum()))
    out["games_std"] = grp.cumcount().astype("float64") + 1
    out = out.sort_values("week", kind="stable")
    t = target.reset_index(drop=True).copy()
    t["_order"] = np.arange(len(t))
    t = t.sort_values("week", kind="stable")
    for c in ["season", "week", *by]:
        t[c] = t[c].astype(out[c].dtype) if c in out else t[c]
    res = pd.merge_asof(t, out, on="week", by=["season", *by], allow_exact_matches=False,
                        direction="backward")
    return res.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def prior_season(target: pd.DataFrame, per_season: pd.DataFrame, by, cols) -> pd.DataFrame:
    """Join season - 1 values of `cols` (one row per season and `by` key) onto
    each target row. A prior season is complete before the target season
    starts, so it is known at every week."""
    p = per_season[["season", *by, *cols]].copy()
    p["season"] = p["season"] + 1
    return target.merge(p, on=["season", *by], how="left")
