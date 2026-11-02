"""Loaders package P1b's blocks share (QB-09, RB-08, RB-09, WR-09, TE-11); not a block (no ID).

Two things the runner's Context does not give a block directly:

  * PFR advanced rushing (yards before contact) per player-game. `pfr_rush`
    loads it through the Context's own frame store under the post-game
    `weekly` classification, so the leak check cuts it exactly like every
    other post-game frame (week < w).
  * One team code per franchise. `block_runner.normalize_teams` rewrites
    only object-dtype columns, and under pandas 3 nflverse's text columns
    are a string dtype, so frames come back with the code of the time (PFR:
    OAK in 2018-2019). `codes` maps them whatever the dtype.

Everything else is aggregation helpers in the spirit of `_common`: history
rows with week < the target row's week in the same season.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C

#: block_runner.TEAM_CODES: the franchise's code today.
TEAM_CODES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
FTN_FIRST = 2022
#: Running backs, by the box-score position of the same game.
BACKS = ("RB", "FB")


def codes(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].replace(TEAM_CODES)
    return df


def _nfl():
    """nflreadpy on the research cache, configured as block_runner._nfl does."""
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    from gridiron.paths import RESEARCH_CACHE
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    return nfl


def pfr_rush(ctx, seasons) -> pd.DataFrame:
    """PFR advanced rushing, one row per player-game (regular season):
    season, week, team, opponent, pfr_player_id, carries,
    rushing_yards_before_contact. Post-game: cut like `weekly`."""
    def load(ss, _):
        df = _nfl().load_pfr_advstats(list(ss), stat_type="rush",
                                      summary_level="week").to_pandas()
        return codes(df, ("team", "opponent"))
    return ctx._get("weekly", seasons, load, ["pfr_advstats_rush"])


def back_rushes(ctx, seasons) -> pd.DataFrame:
    """Running backs' rushing per player-game: (season, week, team, opponent,
    ybc, carries). PFR rows join the box score through the crosswalk's pfr ->
    gsis edge (rule #3); a row whose player has no RB/FB box-score line that
    week (an unmapped id, a QB, a receiver) is left out of both sums."""
    r = pfr_rush(ctx, seasons)
    r = r.assign(gsis_id=r["pfr_player_id"].map(lambda p: ctx.pfr_to_gsis.get(str(p))))
    r = r.loc[r["gsis_id"].notna()].copy()
    r["gsis_id"] = C.ids(r["gsis_id"])
    pos = C.player_games(ctx, seasons)[["season", "week", "gsis_id", "position"]]
    r = r.merge(pos, on=["season", "week", "gsis_id"], how="inner")
    r = r.loc[r["position"].isin(BACKS)]
    return pd.DataFrame({
        "season": r["season"].astype(int), "week": r["week"].astype(int),
        "team": r["team"].astype(str), "opponent": r["opponent"].astype(str),
        "ybc": pd.to_numeric(r["rushing_yards_before_contact"], errors="coerce"),
        "carries": pd.to_numeric(r["carries"], errors="coerce")}).dropna(subset=["ybc", "carries"])


def ftn_plays(ctx, seasons, columns, flags) -> pd.DataFrame:
    """Regular-season plays (ctx.pbp, `columns`) that FTN charted, with the
    FTN `flags`. Seasons before 2022 have no charting and return no rows."""
    ss = [int(s) for s in seasons if int(s) >= FTN_FIRST]
    cols = ["season", "week", "game_id", "play_id", "posteam", "defteam", *columns]
    if not ss:
        return pd.DataFrame(columns=cols + list(flags))
    pbp = ctx.pbp(ss, columns)[cols].copy()
    pbp["play_id"] = pd.to_numeric(pbp["play_id"], errors="coerce")
    f = ctx.ftn(ss)[["nflverse_game_id", "nflverse_play_id", *flags]].rename(
        columns={"nflverse_game_id": "game_id", "nflverse_play_id": "play_id"})
    f["play_id"] = pd.to_numeric(f["play_id"], errors="coerce")
    pbp = pbp.dropna(subset=["play_id"])
    f = f.dropna(subset=["play_id"]).drop_duplicates(["game_id", "play_id"])
    pbp["play_id"], f["play_id"] = pbp["play_id"].astype("int64"), f["play_id"].astype("int64")
    return pbp.merge(f, on=["game_id", "play_id"], how="inner")


def charted_team_games(plays: pd.DataFrame) -> pd.DataFrame:
    """(season, week, team, charted=1) for every team in a game FTN charted."""
    sides = [plays[["season", "week", side]].rename(columns={side: "team"})
             for side in ("posteam", "defteam")]
    out = pd.concat(sides).dropna().drop_duplicates()
    return out.assign(charted=1.0)


def charted_history(ctx, seasons, plays, mine, team=None) -> pd.DataFrame:
    """One row per player-game (box scores) with `charted` (1 when FTN charted
    his team's game), his own event counts `mine` (season, week, gsis_id,
    counts...) and optionally his team's `team` (season, week, team,
    counts...); counts are 0 in a charted game without the event and 0 in an
    uncharted one (whose `charted` is 0, so per-charted-game rates skip it)."""
    h = C.player_games(ctx, seasons).merge(charted_team_games(plays),
                                          on=["season", "week", "team"], how="left")
    h["charted"] = h["charted"].fillna(0.0)
    h = h.merge(mine, on=["season", "week", "gsis_id"], how="left")
    count_cols = [c for c in mine.columns if c not in ("season", "week", "gsis_id")]
    if team is not None:
        h = h.merge(team, on=["season", "week", "team"], how="left")
        count_cols += [c for c in team.columns if c not in ("season", "week", "team")]
    for c in count_cols:
        h[c] = h[c].fillna(0.0) * h["charted"]
    return h


def league_as_of(target: pd.DataFrame, history: pd.DataFrame, cols) -> pd.DataFrame:
    """For each target row: sums of `cols` over EVERY history row of the same
    season with week < the row's week (the league to date) as `<col>_lg`,
    plus `rows_lg` (how many history rows, e.g. team-games). Row order is the
    target's; NaN before any history."""
    cols = list(cols)
    t = target[["season", "week"]].reset_index(drop=True).copy()
    t["season"], t["week"] = t["season"].astype("int64"), t["week"].astype("int64")
    t["_order"] = np.arange(len(t))
    g = history.assign(rows=1.0).groupby(["season", "week"])[[*cols, "rows"]].sum()
    g = g.sort_index().groupby(level="season").cumsum().reset_index()
    g["season"], g["week"] = g["season"].astype("int64"), g["week"].astype("int64")
    g = g.rename(columns={c: f"{c}_lg" for c in [*cols, "rows"]})
    res = pd.merge_asof(t.sort_values("week", kind="stable"), g.sort_values("week", kind="stable"),
                        on="week", by="season", allow_exact_matches=False, direction="backward")
    return res.sort_values("_order").drop(columns="_order").reset_index(drop=True)
