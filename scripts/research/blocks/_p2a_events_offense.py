"""Helpers package P2a's blocks share (QB-06, QB-07, RB-06, WR-02, TE-06,
WR-03, TE-07: workstream C, injury and roster-move events, offense); not a
block (no ID).

Availability in week w (pre-game facts only)
  A player is UNAVAILABLE for team T in week w when, before the week's games,
    * the week's final injury report lists him Out or Doubtful (the rule of
      the shipped `vacated_pickup`), or
    * the week's weekly roster lists him on T with a reserve or departure
      status (`RESERVE`, `GONE`), or does not list him on T at all while T
      has a roster that week (released, traded, signed away), or lists him
      on another team only.
  INA (the game-day inactive list, 90 minutes before kickoff) and DEV (the
  practice squad) never count, and Questionable is not Out (rule #11). When T
  has no roster that week and nobody lists him, he is not flagged.
  (Corrected 2026-11-02, see `roster_away`: until then a player missing from
  T's roster was flagged only when another team listed him.)

  The reserve codes differ by era. The 2019-2020 rosters carry PUP, SUS, NWT,
  RSN, RSR and EXE as statuses of their own; from 2021 they are all RES with
  a description code. `RESERVE` lists every one, so a status means the same
  in every fold. (The shipped `reserve_from_rosters` reads RES and EXE.)

Runner bug, worked around here and reported (block_runner.py is not this
package's to change): `Context._get` keeps season_type == "REG" when a frame
has that column, else game_type == "REG". nflverse's 2025 injury file adds
season_type and the 2019-2024 files do not, so ctx.injuries over several
seasons including 2025 drops every 2019-2024 row. The 2025 depth charts
(timestamped) have no game_type, so ctx.depth_charts over several seasons
including 2025 drops every 2025 row. Both frames are read here one season at
a time, which the runner loads correctly (`per_season`).

Teammates
  `teammates` gives, for every week w that team T plays and every player who
  played for T earlier that season: his counts summed over HIS games for T
  before week w and his team's counts in those games (`_common.as_of`),
  whether he played T's previous game, his box-score position in his latest
  game for T, and whether he is unavailable for T in week w. A player's games
  and the play definitions are `_p1a_pbp_roles`'s: offensive snaps or a box
  score with a carry, target or pass attempt; a target is a pass with a
  receiver named, not a two-point try (it matches the box scores exactly).

  A FRESH absence: unavailable this week AND he played T's previous game.
  From an absence's second week on, the shipped last-game features
  (`xfp_last`, `opp_last`, `snap_last`) already show what his teammates do
  without him; in its first week the shipped set has only `vacated_pickup`
  (every position's opportunities pooled, Out/Doubtful only) and a depth
  chart that may lag.

Quarterbacks
  A dropback is qb_dropback 1, not a two-point try, credited to `passer_id`
  (the dropback player, scrambles included). A past game's STARTER is the
  passer of his team's first dropback of the game (play-by-play, post-game:
  read for weeks before the row's only). nflverse's schedule `*_qb_id` is not
  used: it agrees with the first dropback in 98.5-100% of team-games in
  2019-2023 and 2025 but in 93.2% in 2024 (it names one team's backup for
  five games its starter played throughout).

  The EXPECTED starter in week w is the first QB on T's pre-game depth chart
  for week w (2025: the latest snapshot labelled for the week) who is not
  unavailable. Checked before any score against the first-dropback starter,
  2019-2025: it names him in 92.6% of 3,742 team-games (89.7-96.5% per
  season); the chart's QB1 alone, in 87.7% (85.1-90.6%).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
import _p1a_pbp_roles as R

KEY = ["season", "week", "gsis_id"]
TEAM_WEEK = ["season", "week", "team"]
MATE = ["season", "week", "team", "gsis_id"]
REPORT_OUT = ("Out", "Doubtful")
DESIGNATED = ("Out", "Doubtful", "Questionable")
RESERVE = ("RES", "EXE", "PUP", "SUS", "NWT", "RSN", "RSR", "E01", "E14", "RET")
GONE = ("CUT", "TRD", "TRC", "UFA", "RFA")
BACKS = ("RB", "FB")
DROPBACK_COLUMNS = ["qb_dropback", "passer_id", "two_point_attempt"]


# --------------------------------------------------------------- availability

def per_season(load, seasons) -> pd.DataFrame:
    """`load([s])` for each season, concatenated (the runner bug above)."""
    parts = [load([int(s)]) for s in sorted({int(s) for s in seasons})]
    parts = [p for p in parts if len(p)]
    return pd.concat(parts, ignore_index=True) if parts else load([int(min(seasons))]).iloc[0:0]


def flag(df: pd.DataFrame, col: str) -> pd.Series:
    """True where a 0/1 play flag is 1."""
    return pd.to_numeric(df[col], errors="coerce").fillna(0) == 1


def text(df: pd.DataFrame, cols) -> pd.DataFrame:
    """Cast id and team columns to object: pandas 3 turns groupby keys into
    its `str` dtype, and merge_asof will not match `str` with object."""
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(object)
    return df


def reports(ctx, seasons) -> pd.DataFrame:
    """(season, week, gsis_id, report_out, designated) from the final weekly
    injury reports: Out or Doubtful; and any game status (Out, Doubtful,
    Questionable)."""
    inj = per_season(ctx.injuries, seasons)
    status = inj["report_status"].astype(object)
    d = pd.DataFrame({"season": inj["season"].astype(int), "week": inj["week"].astype(int),
                      "gsis_id": C.ids(inj["gsis_id"]),
                      "report_out": status.isin(REPORT_OUT).to_numpy(),
                      "designated": status.isin(DESIGNATED).to_numpy()})
    d = d.loc[d["gsis_id"] != ""]
    return d.groupby(KEY, as_index=False)[["report_out", "designated"]].max()


def rosters(ctx, seasons) -> pd.DataFrame:
    """(season, week, team, gsis_id, status) from the weekly rosters."""
    r = ctx.rosters_weekly(seasons)
    d = pd.DataFrame({"season": r["season"].astype(int), "week": r["week"].astype(int),
                      "team": r["team"].astype(object), "gsis_id": C.ids(r["gsis_id"]),
                      "status": r["status"].astype(object)})
    return d.loc[d["gsis_id"] != ""].drop_duplicates().reset_index(drop=True)


def roster_away(ctx, rows: pd.DataFrame) -> np.ndarray:
    """For rows (season, week, team, gsis_id): the weekly roster of that week
    lists him on the team with a `RESERVE` or `GONE` status (and no available
    row), or does not list him on the team at all while the team has a
    roster that week, or lists him on another team only.

    Correction 2026-11-02 (the near-miss bug hunt): a player missing from his
    team's roster used to be flagged only when another team listed him. The
    rosters keep a released player differently by season: in 2019 a CUT row
    stays on the releasing team for the rest of the season, from 2020 it
    appears about once and a player nobody signs then has no row anywhere.
    So from 2020 a released, unsigned player counted as available, and his
    old share stayed in the teammates' denominators. Off his team's roster
    now means away in every season."""
    ro = rosters(ctx, sorted(rows["season"].unique()))
    own = (ro.assign(away=ro["status"].isin(RESERVE + GONE))
           .groupby(MATE, as_index=False)["away"].min())      # one available row on T: available
    listed = ro[KEY].drop_duplicates().assign(listed=True)
    have = ro[TEAM_WEEK].drop_duplicates().assign(have=True)
    m = (rows[MATE].reset_index(drop=True).merge(own, on=MATE, how="left")
         .merge(listed, on=KEY, how="left").merge(have, on=TEAM_WEEK, how="left"))
    off = m["away"].isna() & (m["listed"].fillna(False).astype(bool)
                              | m["have"].fillna(False).astype(bool))
    return (m["away"].fillna(False).astype(bool) | off).to_numpy()


def unavailable(ctx, rows: pd.DataFrame) -> np.ndarray:
    """For rows (season, week, team, gsis_id): unavailable for that team that
    week (module docstring), from that week's report and roster only."""
    rep = reports(ctx, sorted(rows["season"].unique()))
    r = rows[MATE].reset_index(drop=True)
    out = r.merge(rep[[*KEY, "report_out"]], on=KEY, how="left")["report_out"]
    return out.fillna(False).astype(bool).to_numpy() | roster_away(ctx, r)


# ------------------------------------------------------------------ teammates

def calendar(ctx, seasons) -> pd.DataFrame:
    """(season, week, team, prev_week): every regular-season week a team
    plays, and the week of its previous game that season (-1 for its first)."""
    g = C.team_games(ctx, seasons)[TEAM_WEEK].copy()
    g["team"] = g["team"].astype(object)
    g = g.drop_duplicates().sort_values(["season", "team", "week"]).reset_index(drop=True)
    g["prev_week"] = g.groupby(["season", "team"])["week"].shift(1).fillna(-1).astype(int)
    return g


def history(ctx, seasons, plays: pd.DataFrame, events: dict) -> pd.DataFrame:
    """`_p1a_pbp_roles.event_history` (one row per game a player played, with
    his and his team's count of each event) plus `position`, his box-score
    position in that game (missing for a game with snaps and no box score)."""
    hist = R.event_history(ctx, seasons, plays, events)
    hist["team"] = hist["team"].astype(object)
    pos = C.player_games(ctx, seasons)[[*KEY, "position"]]
    return hist.merge(pos, on=KEY, how="left")


def last_value(rows: pd.DataFrame, hist: pd.DataFrame, by, col: str) -> np.ndarray:
    """For each row, `col` of the latest history row with the same season and
    `by` key and a week before the row's (missing values skipped)."""
    by = list(by)
    h = hist.loc[hist[col].notna(), ["season", "week", *by, col]].sort_values("week")
    t = rows[["season", "week", *by]].reset_index(drop=True)
    t = t.assign(_o=np.arange(len(t))).sort_values("week")
    m = pd.merge_asof(t, h, on="week", by=["season", *by], allow_exact_matches=False,
                      direction="backward")
    return m.sort_values("_o")[col].to_numpy()


def teammates(ctx, seasons, hist: pd.DataFrame, cols) -> pd.DataFrame:
    """One row per (season, week w, team T, gsis_id) for every week T plays
    and every player with a game for T earlier that season: `<col>_std` (his
    games for T before w), `games_std`, `prev_week`, `played_last`,
    `position` and `unavailable` (module docstring)."""
    cal = calendar(ctx, seasons)
    who = hist[["season", "team", "gsis_id"]].drop_duplicates()
    grid = cal.merge(who, on=["season", "team"])
    r = C.as_of(grid[MATE], hist, ["team", "gsis_id"], list(cols))
    r = r.merge(grid[[*MATE, "prev_week"]], on=MATE, how="left")
    r = r.loc[r["games_std"].notna()].reset_index(drop=True)
    last = (hist[["season", "week", "team", "gsis_id"]].drop_duplicates()
            .rename(columns={"week": "prev_week"}).assign(played_last=True))
    r = r.merge(last, on=["season", "prev_week", "team", "gsis_id"], how="left")
    r["played_last"] = r["played_last"].fillna(False).astype(bool)
    r["position"] = last_value(r, hist, ["team", "gsis_id"], "position")
    r["unavailable"] = unavailable(ctx, r)
    return r


def target_rows(ctx, position: str) -> pd.DataFrame:
    """The block's target rows with their team this week (the runner's keys)."""
    k = ctx.keys_for(position)[[*KEY, "team"]].reset_index(drop=True).copy()
    k["team"] = k["team"].astype(object)
    return k


def vacated(rows: pd.DataFrame, mates: pd.DataFrame, share: str, vacate: str) -> np.ndarray:
    """For each target row (season, week, team): the sum of `share` over his
    team's teammates where `vacate` is True that week (0 when none)."""
    m = mates.assign(_v=mates[share].where(mates[vacate], 0.0).fillna(0.0))
    team = m.groupby(TEAM_WEEK, as_index=False)["_v"].sum()
    r = rows[TEAM_WEEK].reset_index(drop=True).merge(team, on=TEAM_WEEK, how="left")
    return r["_v"].fillna(0.0).to_numpy()


def pickup(rows: pd.DataFrame, mates: pd.DataFrame, share: str, vacate: str,
           pool: str, split: str | None = None) -> np.ndarray:
    """Vacated role handed to each target row by his part of what remains.

    For a row (season, week, team T, gsis_id i): V = the sum of `share` over
    T's teammates where `vacate` is True; S = the sum of `split` (default
    `share`) over the teammates in `pool` who are not unavailable this week;
    the row gets V x split_i / S. 0 when nothing is vacated, and for a row
    that is himself unavailable. Missing when something is vacated but i has
    no `split` of his own (no earlier game for T, or the weight is unknown)
    or nobody remaining holds any (S = 0)."""
    split = split or share
    m = mates.assign(_s=mates[split].where(mates[pool] & ~mates["unavailable"], 0.0).fillna(0.0))
    team = m.groupby(TEAM_WEEK, as_index=False)["_s"].sum()
    own = m[[*MATE, split, "unavailable"]]
    r = (rows[MATE].reset_index(drop=True).merge(team, on=TEAM_WEEK, how="left")
         .merge(own, on=MATE, how="left"))
    v = vacated(rows, mates, share, vacate)
    s = r["_s"].fillna(0.0).to_numpy()
    mine = r[split].to_numpy(dtype="float64")
    away = r["unavailable"].fillna(False).astype(bool).to_numpy()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = v * mine / np.where(s > 0, s, np.nan)
    return np.where((v == 0) | away, 0.0, out)


def target_history(ctx, seasons) -> pd.DataFrame:
    """`history` with one event, `tgt` (his targets) and `tgt_team`."""
    pbp = ctx.pbp(seasons, R.TARGET_COLUMNS)
    return history(ctx, seasons, pbp, {"tgt": (R.is_target(pbp), "receiver_player_id")})


def route_shares(ctx, seasons) -> pd.DataFrame:
    """(season, gsis_id, route_share) for each season: his routes (dropbacks
    he was on the field for: participation `offense_players` on qb_dropback
    plays, the shipped `+routes` definition, A.routes_from_participation) over
    his teams' charted dropbacks in the games he ran one, that season.
    Participation stops at 2025, so this is a prior for the next season."""
    from gridiron.models import advanced as A
    seasons = sorted({int(s) for s in seasons if 2016 <= int(s) <= 2025})
    if not seasons:
        return pd.DataFrame(columns=["season", "gsis_id", "route_share"])
    part = ctx.participation(seasons)
    pbp = ctx.pbp(seasons, ["qb_dropback"])
    out = []
    for s in seasons:
        g = A.routes_from_participation(part.loc[part["season"] == s], pbp.loc[pbp["season"] == s])
        if len(g) == 0:
            continue
        g = g.groupby("gsis_id", as_index=False)[["routes", "team_routes"]].sum()
        g["route_share"] = C.share(g["routes"], g["team_routes"]).to_numpy()
        out.append(g.assign(season=s)[["season", "gsis_id", "route_share"]])
    return pd.concat(out, ignore_index=True)


# ---------------------------------------------------------------- quarterbacks

def passer_games(plays: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, week, team, gsis_id) with a dropback: `dropbacks`,
    `first_play` (play_id of his first), `team_dropbacks`, and `is_top`
    (most dropbacks for his team that game; a tie goes to the earlier first
    dropback)."""
    db = plays.loc[flag(plays, "qb_dropback") & ~flag(plays, "two_point_attempt")
                   & plays["passer_id"].notna()]
    d = pd.DataFrame({"season": db["season"].astype(int), "week": db["week"].astype(int),
                      "team": R.team_code(db["posteam"]), "gsis_id": C.ids(db["passer_id"]),
                      "play_id": pd.to_numeric(db["play_id"], errors="coerce")})
    d = d.loc[d["gsis_id"] != ""]
    g = (d.groupby(MATE, as_index=False)
         .agg(dropbacks=("play_id", "size"), first_play=("play_id", "min")))
    g["team_dropbacks"] = g.groupby(TEAM_WEEK)["dropbacks"].transform("sum")
    g = g.sort_values([*TEAM_WEEK, "dropbacks", "first_play"],
                      ascending=[True, True, True, False, True]).reset_index(drop=True)
    g["is_top"] = ~g.duplicated(TEAM_WEEK)
    g["dropbacks"] = g["dropbacks"].astype("float64")
    g["team_dropbacks"] = g["team_dropbacks"].astype("float64")
    return g


def starters(pg: pd.DataFrame) -> pd.DataFrame:
    """(season, week, team, gsis_id, dropbacks, team_dropbacks): each
    team-game's starter, the passer of its first dropback (`passer_games`)."""
    return (pg.sort_values([*TEAM_WEEK, "first_play"]).drop_duplicates(TEAM_WEEK)
            [[*MATE, "dropbacks", "team_dropbacks"]].reset_index(drop=True))


def chart_qbs(ctx, seasons) -> pd.DataFrame:
    """(season, week, team, gsis_id, rank): the quarterbacks on each team's
    pre-game depth chart as published for each week, one season at a time.
    Weekly layout (to 2024): `depth_position` QB of the offense, rank
    `depth_team`. Timestamped (2025): `pos_abb` QB, rank `pos_rank`, from the
    team's latest snapshot carrying that week's label."""
    out = []
    for s in sorted({int(x) for x in seasons}):
        dc = ctx.depth_charts([s])
        if len(dc) == 0:
            continue
        if "depth_team" in dc.columns:
            q = dc.loc[(dc["formation"] == "Offense") & (dc["depth_position"] == "QB")]
            q = pd.DataFrame({"season": q["season"].astype(int), "week": q["week"].astype(int),
                              "team": q["club_code"].astype(object), "gsis_id": C.ids(q["gsis_id"]),
                              "rank": pd.to_numeric(q["depth_team"], errors="coerce")})
        else:
            q = dc.loc[dc["pos_abb"] == "QB"].copy()
            q["when"] = pd.to_datetime(q["dt"], utc=True)
            q = q.loc[q["when"] == q.groupby(["team", "week"])["when"].transform("max")]
            q = pd.DataFrame({"season": q["season"].astype(int), "week": q["week"].astype(int),
                              "team": q["team"].astype(object), "gsis_id": C.ids(q["gsis_id"]),
                              "rank": pd.to_numeric(q["pos_rank"], errors="coerce")})
        out.append(q.loc[(q["gsis_id"] != "") & q["rank"].notna()])
    if not out:
        return pd.DataFrame(columns=[*MATE, "rank"])
    q = pd.concat(out, ignore_index=True)
    return text(q.groupby(MATE, as_index=False)["rank"].min(), ["team", "gsis_id"])


def expected_starter(ctx, seasons) -> pd.DataFrame:
    """(season, week, team, exp_qb) for every week a team plays: the first QB
    on its latest pre-game depth chart for that week (a chart labelled that
    week, else the latest earlier one) who is not unavailable that week.
    Missing when the chart lists no available QB."""
    cal = text(calendar(ctx, seasons)[TEAM_WEEK], ["team"])
    q = chart_qbs(ctx, seasons)
    labels = text(q[["season", "team", "week"]].drop_duplicates()
                  .assign(chart_week=lambda d: d["week"]).sort_values("week"), ["team"])
    m = pd.merge_asof(cal.sort_values("week"), labels, on="week", by=["season", "team"],
                      direction="backward")
    m = m.loc[m["chart_week"].notna()].astype({"chart_week": int})
    rows = m.merge(q.rename(columns={"week": "chart_week"}), on=["season", "team", "chart_week"])
    rows["unavailable"] = unavailable(ctx, rows[MATE])
    first = (rows.loc[~rows["unavailable"]].sort_values([*TEAM_WEEK, "rank", "gsis_id"])
             .drop_duplicates(TEAM_WEEK))
    return cal.merge(first[[*TEAM_WEEK, "gsis_id"]].rename(columns={"gsis_id": "exp_qb"}),
                     on=TEAM_WEEK, how="left")


def dropback_share(rows: pd.DataFrame, pg: pd.DataFrame, qb_col: str) -> np.ndarray:
    """For rows (season, week, team, <qb_col>): that QB's dropbacks for the
    team this season before the row's week, over the team's dropbacks in its
    games before that week. 0 for a QB who has thrown none for the team;
    missing when the QB is missing or the team has no earlier dropback."""
    team = pg.groupby(TEAM_WEEK, as_index=False)["dropbacks"].sum().rename(
        columns={"dropbacks": "tdb"})
    t = C.as_of(rows[TEAM_WEEK], team, ["team"], ["tdb"])
    q = C.as_of(rows[TEAM_WEEK].assign(gsis_id=rows[qb_col].to_numpy()).fillna({"gsis_id": ""}),
                pg[[*MATE, "dropbacks"]], ["team", "gsis_id"], ["dropbacks"])
    mine = q["dropbacks_std"].fillna(0.0).to_numpy()
    total = t["tdb_std"].to_numpy(dtype="float64")
    known = rows[qb_col].notna().to_numpy() & (total > 0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(known, mine / np.where(total > 0, total, np.nan), np.nan)


def qb_change(ctx, position: str) -> pd.DataFrame:
    """WR-02 and TE-06 (one definition, two blocks), for the position's rows:

      qb_change       1 when this week's expected starter (first available QB
                      on the pre-game depth chart) is not the QB with the most
                      dropbacks in the team's previous game, else 0
      qb_share_delta  the expected starter's share of the team's dropbacks this
                      season minus that previous-game QB's share (0 when they
                      are the same; negative: a QB who has thrown less of the
                      team's passes comes in)
      games_with_qb   his games for the team this season, before this week, in
                      which the expected starter had the team's most dropbacks

    Missing when the chart lists no available QB, or (the first two) the
    team has no earlier game with a dropback."""
    rows = target_rows(ctx, position)
    seasons = sorted(rows["season"].unique())
    pbp = ctx.pbp(seasons, DROPBACK_COLUMNS)
    pg = passer_games(pbp)
    top = pg.loc[pg["is_top"], [*TEAM_WEEK, "gsis_id"]].rename(
        columns={"week": "prev_week", "gsis_id": "last_qb"})
    r = (rows.merge(calendar(ctx, seasons), on=TEAM_WEEK, how="left")
         .merge(expected_starter(ctx, seasons), on=TEAM_WEEK, how="left")
         .merge(top, on=["season", "prev_week", "team"], how="left"))
    both = r["exp_qb"].notna() & r["last_qb"].notna()
    r["qb_change"] = np.where(both, (r["exp_qb"] != r["last_qb"]).astype(float), np.nan)
    delta = dropback_share(r, pg, "exp_qb") - dropback_share(r, pg, "last_qb")
    r["qb_share_delta"] = np.where(both, np.where(r["qb_change"] == 0, 0.0, delta), np.nan)
    # his games for the team before this week, with who threw most in each
    games = R.games_played(ctx, seasons)
    games["team"] = games["team"].astype(object)
    games = games.merge(pg.loc[pg["is_top"], [*TEAM_WEEK, "gsis_id"]].rename(
        columns={"gsis_id": "top_qb"}), on=TEAM_WEEK, how="left")
    pairs = r[[*KEY, "team", "exp_qb"]].merge(
        games.rename(columns={"week": "g"}), on=["season", "gsis_id", "team"], how="inner")
    pairs = pairs.loc[(pairs["g"] < pairs["week"]) & (pairs["top_qb"] == pairs["exp_qb"])]
    n = pairs.groupby(KEY).size().rename("n").reset_index()
    r = r.merge(n, on=KEY, how="left")
    r["games_with_qb"] = np.where(r["exp_qb"].notna(), r["n"].fillna(0.0), np.nan)
    return r[[*KEY, "qb_change", "qb_share_delta", "games_with_qb"]]
