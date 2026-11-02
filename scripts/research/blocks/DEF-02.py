"""DEF-02: the opponent's expected starting quarterback, a backup or an inexperienced one.

Pre-registration: docs/research/blocks/DEF-02.md. Kind A in the roadmap, cast
as features on the defense rows (k_def_backtest.py's table).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
import _p2b_events_ramps_defense as H

ID = "DEF-02"
TITLE = "Opponent's expected starting QB: career starts, new since last game, share of the season's starts"
COLUMNS = {"DEF": ["oqb_starts32", "oqb_changed", "oqb_season_share"]}
#: Career starts are counted from the first season nflverse's schedule names
#: starting quarterbacks, and capped (inexperience is the idea, not tenure).
FIRST_SEASON = 1999
CAP = 32


def depth_qbs(ctx, seasons) -> pd.DataFrame:
    """Quarterbacks on each team's depth chart published for the week, in
    order: season, week, team, gsis_id, rank (1 = first). Weekly layout
    (through 2024): offense formation, `depth_team`; timestamped layout (2025
    on): the team's latest snapshot published for the week, `pos_rank`."""
    parts = []
    for dc in H.depth_by_season(ctx, seasons):
        if len(dc) == 0:
            continue
        if "depth_team" in dc.columns:
            q = dc.loc[(dc["formation"] == "Offense") & (dc["depth_position"] == "QB")]
            q = pd.DataFrame({"season": q["season"], "week": q["week"], "team": q["club_code"],
                              "gsis_id": q["gsis_id"],
                              "rank": pd.to_numeric(q["depth_team"], errors="coerce")})
        else:
            q = dc.loc[dc["pos_abb"] == "QB"].copy()
            when = pd.to_datetime(q["dt"], utc=True)
            q = q.loc[when == when.groupby([q["team"], q["week"]]).transform("max")]
            q = pd.DataFrame({"season": q["season"], "week": q["week"], "team": q["team"],
                              "gsis_id": q["gsis_id"],
                              "rank": pd.to_numeric(q["pos_rank"], errors="coerce")})
        parts.append(q)
    if not parts:
        return pd.DataFrame(columns=["season", "week", "team", "gsis_id", "rank"])
    q = pd.concat(parts, ignore_index=True)
    q = q.loc[q["gsis_id"].notna() & q["rank"].notna()]
    q = q.assign(season=q["season"].astype(int), week=q["week"].astype(int),
                 team=q["team"].astype(str), gsis_id=C.ids(q["gsis_id"]).astype(str))
    return (q.groupby(["season", "week", "team", "gsis_id"], as_index=False)["rank"].min()
            .sort_values(["season", "week", "team", "rank", "gsis_id"]).reset_index(drop=True))


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team", "opponent"]].reset_index(drop=True)
    keys = keys.assign(season=keys["season"].astype(int), week=keys["week"].astype(int),
                       team=keys["team"].astype(str), opponent=keys["opponent"].astype(str))
    seasons = sorted(int(s) for s in keys["season"].unique())
    # who started every game (post-game: only games before the row's week are read)
    sr = ctx.schedule_results(range(FIRST_SEASON, max(seasons) + 1))
    starts = pd.concat([
        sr[["season", "week", "home_team", "home_qb_id"]].set_axis(["season", "week", "team", "qb"], axis=1),
        sr[["season", "week", "away_team", "away_qb_id"]].set_axis(["season", "week", "team", "qb"], axis=1)],
        ignore_index=True)
    starts = starts.loc[starts["qb"].notna()]
    starts = starts.assign(qb=C.ids(starts["qb"]).astype(str), team=starts["team"].astype(str),
                           t=starts["season"].astype(int) * 100 + starts["week"].astype(int))
    starts = starts.sort_values("t", kind="stable")
    career = {g: d["t"].to_numpy() for g, d in starts.groupby("qb")}
    team_games = {k: (d["t"].to_numpy(), d["qb"].to_numpy())
                  for k, d in starts.groupby(["season", "team"])}

    # the opponent's expected starter: the depth chart's first QB who is not
    # Out/Doubtful and is on the 53; else last game's starter if he is
    targets = keys[["season", "week", "opponent"]].drop_duplicates().rename(columns={"opponent": "team"})
    dq = depth_qbs(ctx, seasons).merge(targets, on=["season", "week", "team"], how="inner")
    prev = []
    for s, w, t in zip(targets["season"], targets["week"], targets["team"]):
        tt, qq = team_games.get((s, t), (np.array([]), np.array([])))
        j = int(np.searchsorted(tt, s * 100 + w, "left"))
        prev.append(qq[j - 1] if j > 0 else None)
    targets = targets.assign(prev=prev)
    pv = targets.loc[targets["prev"].notna(), ["season", "week", "team", "prev"]].rename(
        columns={"prev": "gsis_id"}).assign(rank=np.inf)
    cand = pd.concat([dq, pv], ignore_index=True).sort_values(["season", "week", "team", "rank"],
                                                             kind="stable")
    cand = cand.drop_duplicates(["season", "week", "team", "gsis_id"], keep="first")
    av = H.availability(ctx, seasons, cand[["season", "week", "team", "gsis_id"]])
    cand = cand.merge(av[["season", "week", "team", "gsis_id", "out"]],
                      on=["season", "week", "team", "gsis_id"], how="left")
    exp = (cand.loc[cand["out"] == 0.0].sort_values(["season", "week", "team", "rank"], kind="stable")
           .drop_duplicates(["season", "week", "team"], keep="first")[["season", "week", "team", "gsis_id"]])
    targets = targets.merge(exp, on=["season", "week", "team"], how="left")

    f_starts, f_changed, f_share = [], [], []
    for s, w, t, g, p in zip(targets["season"], targets["week"], targets["team"],
                             targets["gsis_id"], targets["prev"]):
        if not isinstance(g, str):
            # no listed QB and not last game's starter is available: an
            # emergency starter, with no starts, new, and no share this season
            f_starts.append(0.0), f_changed.append(1.0), f_share.append(0.0)
            continue
        now = s * 100 + w
        f_starts.append(float(min(int(np.searchsorted(career.get(g, np.array([])), now, "left")), CAP)))
        f_changed.append(float(p != g) if isinstance(p, str) else np.nan)
        tt, qq = team_games.get((s, t), (np.array([]), np.array([])))
        before = qq[: int(np.searchsorted(tt, now, "left"))]
        f_share.append(float(np.mean(before == g)) if len(before) else np.nan)
    targets["oqb_starts32"], targets["oqb_changed"], targets["oqb_season_share"] = f_starts, f_changed, f_share
    out = keys.merge(targets.drop(columns=["prev", "gsis_id"]).rename(columns={"team": "opponent"}),
                     on=["season", "week", "opponent"], how="left")
    return out[["season", "week", "team", *COLUMNS["DEF"]]]
