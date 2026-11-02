"""Play-caller and head-coach tendencies (workstream B) for package P3: QB-03,
QB-04, QB-08, RB-04, WR-08, TE-04, TE-05, K-02 and DEF-09. Not a block (no ID).

WHO. `data/reference/play_callers.csv` (docs/research/PLAY_CALLERS.md) has one
row per (season, team, role, person, week range). `in_charge` expands it to
one row per (season, team, week): the OPC (the offensive play-caller, only
where a public source says who called the plays) or the HC (head coach).
A team-week with no row has no person, and every tendency is unknown there
(NaN; the runner enters it as 0 plus `__known`). Nothing assumes the OC or
the head coach called plays. The name is the person's key (one spelling per
person in the table), so a tendency follows him when he changes teams. The
table starts in 2019. Head coaches of 2018 come from the pre-game schedule
(`home_coach`/`away_coach`, which splits 2018's two in-season changes, CLE
and GB), so 2018 play-by-play is history for head coaches. No play-caller
is known before 2019, so a play-caller's history starts in 2019.

The person in charge in week w is the one the table names for week w. An
in-season change (a firing, a demotion, a head coach taking the calls) is
normally announced before the next game; a change made during a game and
named only afterwards would leak who was in charge that week, never a play
from week w.

WHAT. A tendency is a value per observation (a play, a pair of snaps, a
4th-down decision, a team-game, a target), measured against the league in
the same season and, where it has one, the same bucket:

    deviation = value - the league's mean value in that season (and bucket)

For a target row (season s, week w, team T) whose person is P:

  career   P's observations in seasons < s, in every team-week the table (or,
           for 2018 head coaches, the schedule) gives him the role, any team;
           each season's deviations against that season's full league mean.
  season   P's observations in season s with team T, weeks < w (the weeks he
           held the role there); deviations against the league's mean over
           season s, weeks < w (as of the target week: no later play).

Each comes with n, the observations behind it. The blocks read

    pooled   = (career_dev + season_dev) / (career_n + season_n + k)
    career   = career_dev / (career_n + k)
    season   = season_dev / (season_n + k)

shrunk toward the league (0) by k observations (`K`, below). Each is NaN
where the person is unknown or has no observation behind it (n = 0).

Every observation of season s is from a week < w, and every career season
is complete before season s starts, so the blocks pass the runner's leak
check by construction (the play-caller table is a static reference file).

Plays (nflverse play-by-play, ordered by `order_sequence` within a game):

  scrimmage  `play_type` "pass" or "run", not a two-point try, not an aborted
             snap. No-plays (penalties), spikes and kneels have their own
             play types and never count.
  neutral    0.2 <= wp <= 0.8, quarters 1-3, and not the last two minutes of
             the first half (`half_seconds_remaining` > 120).

Tendencies (`*_obs`):

  proe        neutral scrimmage plays with `pass_oe`: value `pass_oe` (pass
              rate over expected, percentage points).
  rz_proe     scrimmage plays inside the opponent's 20 (`yardline_100` <=
              20) with `pass_oe`, 0.1 <= wp <= 0.9, regulation, not the last
              two minutes of either half: value `pass_oe`.
  pace        two consecutive pbp rows of one game, both scrimmage plays of
              the same drive (`fixed_drive`) and quarter, where the earlier
              play left the clock running (not an incompletion, not out of
              bounds, no penalty flag) and the later play is neutral: value
              the game-clock seconds between the two snaps, kept when
              0 < seconds <= 60. A timeout or any other row between them
              breaks the pair. Lower is faster.
  no_huddle   neutral scrimmage plays: value pbp `no_huddle` (0/1).
  fourth      4th downs whose `play_type` is pass, run, punt or field goal
              (not a two-point try, not an aborted snap), 0.1 <= wp <= 0.9,
              regulation, not the last two minutes of either half: value 1
              when the offense went for it (pass or run, fakes included).
              Bucket: yards to go (1, 2-3, 4-6, 7+) x field position
              (`yardline_100` 1-10, 11-25, 26-40, 41-60, 61-99). The
              deviation is the go rate over the league's go rate in the same
              bucket and season: go rate over expected.
  carry_conc  team-games with at least 10 back carries: value the top back's
              share of them. A back carry is a designed run (`play_type`
              run, `rush_attempt` 1, not a scramble, not a two-point try, not
              an aborted snap) by a player who is not one of that team-game's
              quarterbacks (the passer on a dropback or the runner on a
              scramble): the role comes from the play, not a position tag.
  personnel   neutral scrimmage plays with participation `offense_personnel`
              (2016-2025 only): value 1 when it lists 2 or more TEs (`te2`),
              or 3 or more (`te3`).
  te_targets  targets (`play_type` pass, `pass_attempt` 1, a receiver named,
              not a two-point try) whose receiver has a box score that week:
              value 1 when that box score's position is TE (the position
              label defines the group; no role is read from it).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

import _common as C
from gridiron.paths import REPO_ROOT
from gridiron.vegas import implied_totals_from_nflverse

TABLE = REPO_ROOT / "data" / "reference" / "play_callers.csv"
TABLE_FIRST = 2019            # the play-caller table's first season
HC_HISTORY_FIRST = 2018       # head coaches: the 2018 schedule names them
KEY = ["season", "week", "gsis_id"]
TEAM_WEEK = ["season", "week", "team"]
#: Shrinkage toward the league, in observations of each tendency's unit:
#: about (noise per observation / spread between people)^2, set from the
#: unit's binomial or timing noise before any score was seen.
K = {"proe": 100, "rz_proe": 100, "pace": 50, "no_huddle": 100, "fourth": 50,
     "carry_conc": 5, "personnel": 200, "te_targets": 100}
MIN_BACK_CARRIES = 10
FG_RANGE = 40                 # yardline_100 <= 40: the opponent's 40 and in
DISTANCE_EDGES = [0, 1, 3, 6, 99]
FIELD_EDGES = [0, 10, 25, 40, 60, 99]

SCRIMMAGE_COLUMNS = ["play_type", "two_point_attempt", "aborted_play"]
NEUTRAL_COLUMNS = ["wp", "qtr", "half_seconds_remaining"]
PROE_COLUMNS = [*SCRIMMAGE_COLUMNS, *NEUTRAL_COLUMNS, "pass_oe"]
RZ_COLUMNS = [*PROE_COLUMNS, "yardline_100"]
PACE_COLUMNS = [*SCRIMMAGE_COLUMNS, *NEUTRAL_COLUMNS, "game_seconds_remaining", "fixed_drive",
                "order_sequence", "incomplete_pass", "out_of_bounds", "penalty"]
NO_HUDDLE_COLUMNS = [*SCRIMMAGE_COLUMNS, *NEUTRAL_COLUMNS, "no_huddle"]
PASS_RATE_COLUMNS = [*SCRIMMAGE_COLUMNS, *NEUTRAL_COLUMNS, "pass"]
FOURTH_COLUMNS = [*SCRIMMAGE_COLUMNS, *NEUTRAL_COLUMNS, "down", "ydstogo", "yardline_100"]
CARRY_COLUMNS = [*SCRIMMAGE_COLUMNS, "rush_attempt", "qb_scramble", "rusher_player_id",
                 "qb_dropback", "passer_player_id"]
TARGET_COLUMNS = ["play_type", "pass_attempt", "receiver_player_id", "two_point_attempt"]
_TE = re.compile(r"(\d+)\s*TE\b")


# --------------------------------------------------------------------- who

def history_seasons(role: str, keys: pd.DataFrame) -> list[int]:
    """The seasons whose play-by-play can carry a person's history for
    `role`: from 2018 for head coaches, from 2019 (the table) for the
    play-caller, through the target rows' last season."""
    first = HC_HISTORY_FIRST if role == "HC" else TABLE_FIRST
    return list(range(first, int(keys["season"].max()) + 1))


def in_charge(ctx, role: str, seasons) -> pd.DataFrame:
    """(season, team, week, person): who held `role` ("OPC" or "HC") in each
    team-week of `seasons` (module docstring). Absent team-weeks are unknown."""
    seasons = sorted({int(s) for s in seasons})
    t = pd.read_csv(TABLE)
    t = t.loc[(t["role"] == role) & t["season"].isin(seasons)]
    rows = [(int(r.season), str(r.team), w, str(r.name))
            for r in t.itertuples() for w in range(int(r.week_from), int(r.week_to) + 1)]
    out = pd.DataFrame(rows, columns=["season", "team", "week", "person"])
    pre = [s for s in seasons if s < TABLE_FIRST]
    if role == "HC" and pre:
        s = ctx.schedule(pre)
        s = s.loc[s["season"] >= HC_HISTORY_FIRST]
        sides = pd.concat([
            s[["season", "week", "home_team", "home_coach"]].set_axis(
                ["season", "week", "team", "person"], axis=1),
            s[["season", "week", "away_team", "away_coach"]].set_axis(
                ["season", "week", "team", "person"], axis=1)])
        sides = sides.loc[sides["person"].notna()]
        out = pd.concat([out, sides[["season", "team", "week", "person"]]], ignore_index=True)
    out["team"] = out["team"].astype(object)
    out["person"] = out["person"].astype(object)
    out["season"], out["week"] = out["season"].astype("int64"), out["week"].astype("int64")
    if out.duplicated(["season", "team", "week"]).any():
        raise RuntimeError(f"{role}: two people in charge of one team-week")
    return out


# ------------------------------------------------------------- observations

def _flag(p: pd.DataFrame, col: str) -> pd.Series:
    """A 0/1 play flag as booleans (missing = 0)."""
    return pd.to_numeric(p[col], errors="coerce").fillna(0) == 1


def scrimmage(p: pd.DataFrame) -> pd.Series:
    return (p["play_type"].isin(["pass", "run"]) & ~_flag(p, "two_point_attempt")
            & ~_flag(p, "aborted_play"))


def neutral(p: pd.DataFrame) -> pd.Series:
    wp = pd.to_numeric(p["wp"], errors="coerce")
    return (wp.between(0.2, 0.8) & (pd.to_numeric(p["qtr"], errors="coerce") <= 3)
            & (pd.to_numeric(p["half_seconds_remaining"], errors="coerce") > 120))


def _obs(rows: pd.DataFrame, value, bucket=0) -> pd.DataFrame:
    out = rows[["season", "week"]].copy()
    out["team"] = rows["posteam"].astype(object)
    out["bucket"] = bucket if np.isscalar(bucket) else np.asarray(bucket)
    out["value"] = pd.to_numeric(pd.Series(value, index=rows.index), errors="coerce").astype(float)
    return out.loc[out["team"].notna() & out["value"].notna()].reset_index(drop=True)


def proe_obs(p: pd.DataFrame) -> pd.DataFrame:
    m = scrimmage(p) & neutral(p) & p["pass_oe"].notna()
    return _obs(p.loc[m], p.loc[m, "pass_oe"])


def rz_proe_obs(p: pd.DataFrame) -> pd.DataFrame:
    wp = pd.to_numeric(p["wp"], errors="coerce")
    m = (scrimmage(p) & p["pass_oe"].notna()
         & (pd.to_numeric(p["yardline_100"], errors="coerce") <= 20) & wp.between(0.1, 0.9)
         & (pd.to_numeric(p["qtr"], errors="coerce") <= 4)
         & (pd.to_numeric(p["half_seconds_remaining"], errors="coerce") > 120))
    return _obs(p.loc[m], p.loc[m, "pass_oe"])


def neutral_rate_obs(p: pd.DataFrame, col: str) -> pd.DataFrame:
    """Neutral scrimmage plays: value `col` (0/1; `no_huddle`, or `pass` for
    the league's neutral pass rate)."""
    m = scrimmage(p) & neutral(p)
    return _obs(p.loc[m], pd.to_numeric(p.loc[m, col], errors="coerce").fillna(0))


def pace_obs(p: pd.DataFrame) -> pd.DataFrame:
    q = p.sort_values(["game_id", "order_sequence", "play_id"]).reset_index(drop=True)
    q["_scr"] = scrimmage(q)
    q["_running"] = ~(_flag(q, "incomplete_pass") | _flag(q, "out_of_bounds") | _flag(q, "penalty"))
    prev = q.groupby("game_id", sort=False)[["_scr", "_running", "fixed_drive", "qtr", "posteam",
                                             "game_seconds_remaining"]].shift(1)
    gap = (pd.to_numeric(prev["game_seconds_remaining"], errors="coerce")
           - pd.to_numeric(q["game_seconds_remaining"], errors="coerce"))
    m = (q["_scr"] & neutral(q) & prev["_scr"].fillna(False).astype(bool)
         & prev["_running"].fillna(False).astype(bool)
         & (prev["fixed_drive"] == q["fixed_drive"]) & (prev["qtr"] == q["qtr"])
         & (prev["posteam"] == q["posteam"]) & (gap > 0) & (gap <= 60))
    return _obs(q.loc[m], gap.loc[m])


def fourth_obs(p: pd.DataFrame, fg_range_only: bool = False) -> pd.DataFrame:
    wp = pd.to_numeric(p["wp"], errors="coerce")
    ytg = pd.to_numeric(p["ydstogo"], errors="coerce")
    yl = pd.to_numeric(p["yardline_100"], errors="coerce")
    m = ((pd.to_numeric(p["down"], errors="coerce") == 4)
         & p["play_type"].isin(["pass", "run", "punt", "field_goal"])
         & ~_flag(p, "two_point_attempt") & ~_flag(p, "aborted_play") & wp.between(0.1, 0.9)
         & (pd.to_numeric(p["qtr"], errors="coerce") <= 4)
         & (pd.to_numeric(p["half_seconds_remaining"], errors="coerce") > 120)
         & ytg.between(1, 99) & yl.between(1, 99))
    if fg_range_only:
        m &= yl <= FG_RANGE
    rows = p.loc[m]
    dist = np.searchsorted(DISTANCE_EDGES, ytg.loc[m].to_numpy(), side="left")
    field = np.searchsorted(FIELD_EDGES, yl.loc[m].to_numpy(), side="left")
    go = rows["play_type"].isin(["pass", "run"]).astype(float)
    return _obs(rows, go, bucket=dist * 10 + field)


def back_carries(p: pd.DataFrame) -> pd.DataFrame:
    """One row per back carry (module docstring): season, week, game_id,
    team, gsis_id."""
    des = p.loc[(p["play_type"] == "run") & _flag(p, "rush_attempt") & ~_flag(p, "qb_scramble")
                & ~_flag(p, "two_point_attempt") & ~_flag(p, "aborted_play")
                & p["rusher_player_id"].notna(),
                ["season", "week", "game_id", "posteam", "rusher_player_id"]]
    des = des.rename(columns={"posteam": "team", "rusher_player_id": "gsis_id"})
    des["gsis_id"] = C.ids(des["gsis_id"])
    passers = p.loc[_flag(p, "qb_dropback") & p["passer_player_id"].notna(),
                    ["game_id", "posteam", "passer_player_id"]].set_axis(
                        ["game_id", "team", "gsis_id"], axis=1)
    scramblers = p.loc[_flag(p, "qb_scramble") & p["rusher_player_id"].notna(),
                       ["game_id", "posteam", "rusher_player_id"]].set_axis(
                           ["game_id", "team", "gsis_id"], axis=1)
    qbs = pd.concat([passers, scramblers], ignore_index=True)
    qbs["gsis_id"] = C.ids(qbs["gsis_id"])
    qbs = qbs.drop_duplicates().assign(_qb=True)
    des = des.merge(qbs, on=["game_id", "team", "gsis_id"], how="left")
    des = des.loc[des["_qb"].isna() & (des["gsis_id"] != "")].drop(columns="_qb")
    des["team"] = des["team"].astype(object)
    return des.reset_index(drop=True)


def carry_conc_obs(carries: pd.DataFrame) -> pd.DataFrame:
    """Team-games with >= MIN_BACK_CARRIES back carries: the top back's share."""
    per = carries.groupby(["season", "week", "team", "gsis_id"]).size().rename("n").reset_index()
    g = per.groupby(["season", "week", "team"])["n"].agg(["sum", "max"]).reset_index()
    g = g.loc[g["sum"] >= MIN_BACK_CARRIES]
    out = g[["season", "week", "team"]].copy()
    out["bucket"] = 0
    out["value"] = (g["max"] / g["sum"]).astype(float)
    return out.reset_index(drop=True)


def te_count(personnel: pd.Series) -> pd.Series:
    """Tight ends in a participation `offense_personnel` string (NaN if none listed)."""
    def one(s):
        if not isinstance(s, str):
            return np.nan
        m = _TE.search(s)
        return float(m.group(1)) if m else 0.0
    return personnel.map(one)


def personnel_obs(p: pd.DataFrame, part: pd.DataFrame, at_least: int) -> pd.DataFrame:
    """Neutral scrimmage plays with a listed personnel: value 1 with
    `at_least` or more TEs on the field."""
    m = scrimmage(p) & neutral(p)
    plays = p.loc[m, ["season", "week", "game_id", "play_id", "posteam"]]
    pa = part[["nflverse_game_id", "play_id", "offense_personnel"]].rename(
        columns={"nflverse_game_id": "game_id"})
    pa = pa.assign(n_te=te_count(pa["offense_personnel"]))
    pa = pa.loc[pa["n_te"].notna()].drop_duplicates(["game_id", "play_id"])
    plays = plays.merge(pa[["game_id", "play_id", "n_te"]], on=["game_id", "play_id"], how="inner")
    return _obs(plays, (plays["n_te"] >= at_least).astype(float))


def targets(p: pd.DataFrame) -> pd.DataFrame:
    """One row per target: season, week, game_id, team, gsis_id (receiver)."""
    m = ((p["play_type"] == "pass") & _flag(p, "pass_attempt") & p["receiver_player_id"].notna()
         & ~_flag(p, "two_point_attempt"))
    t = p.loc[m, ["season", "week", "game_id", "posteam", "receiver_player_id"]].rename(
        columns={"posteam": "team", "receiver_player_id": "gsis_id"})
    t["gsis_id"] = C.ids(t["gsis_id"])
    t["team"] = t["team"].astype(object)
    return t.loc[t["gsis_id"] != ""].reset_index(drop=True)


def box_positions(ctx, seasons) -> pd.DataFrame:
    """(season, week, gsis_id, position) from the weekly box scores."""
    w = ctx.weekly(seasons)[["season", "week", "player_id", "position"]].rename(
        columns={"player_id": "gsis_id"})
    w["gsis_id"] = C.ids(w["gsis_id"])
    return w.drop_duplicates(KEY)


def te_target_obs(tg: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Targets whose receiver has a box-score position that week: value 1 for a TE."""
    t = tg.merge(positions, on=KEY, how="inner")
    out = t[["season", "week", "team"]].copy()
    out["bucket"] = 0
    out["value"] = (t["position"] == "TE").astype(float)
    return out.reset_index(drop=True)


# ---------------------------------------------------------------- as of

def _asof_sums(target: pd.DataFrame, hist: pd.DataFrame, by, cols) -> pd.DataFrame:
    """For each target row, sums of `cols` over `hist` rows of the same season
    and `by` key with week < the target row's week (NaN where none)."""
    by = list(by)
    h = hist.groupby(["season", *by, "week"], as_index=False)[cols].sum()
    h = h.sort_values(["season", *by, "week"]).reset_index(drop=True)
    h[cols] = h.groupby(["season", *by], sort=False)[cols].cumsum()
    t = target.copy()
    t["_i"] = np.arange(len(t))
    for c in ["season", "week"]:
        t[c], h[c] = t[c].astype("int64"), h[c].astype("int64")
    for c in by:
        t[c], h[c] = t[c].astype(object), h[c].astype(object)
    r = pd.merge_asof(t.sort_values("week", kind="stable"), h.sort_values("week", kind="stable"),
                      on="week", by=["season", *by], allow_exact_matches=False,
                      direction="backward")
    return r.sort_values("_i").drop(columns="_i").reset_index(drop=True)


def tendency(ctx, target: pd.DataFrame, obs: pd.DataFrame, role: str) -> pd.DataFrame:
    """Per unique (season, week, team) of `target`: the person holding `role`
    that team-week and his `career_dev`, `career_n`, `season_dev`,
    `season_n` (module docstring). Person unknown: all NaN."""
    tw = target[TEAM_WEEK].drop_duplicates().reset_index(drop=True)
    tw["team"] = tw["team"].astype(object)
    tw["season"], tw["week"] = tw["season"].astype("int64"), tw["week"].astype("int64")
    seasons = sorted(set(obs["season"].astype(int)) | set(tw["season"].astype(int)))
    who = in_charge(ctx, role, seasons)
    tw = tw.merge(who, on=["season", "team", "week"], how="left")

    o = obs.copy()
    o["season"], o["week"] = o["season"].astype("int64"), o["week"].astype("int64")
    o["team"] = o["team"].astype(object)
    agg = (o.groupby(["season", "week", "team", "bucket"])["value"].agg(["sum", "size"])
           .rename(columns={"sum": "s", "size": "n"}).reset_index())
    agg["n"] = agg["n"].astype(float)
    agg = agg.merge(who, on=["season", "team", "week"], how="left")

    # career: each person-season's deviations against that season's full league mean
    league = agg.groupby(["season", "bucket"])[["s", "n"]].sum()
    league["mean"] = league["s"] / league["n"]
    mine = agg.loc[agg["person"].notna()].merge(league["mean"].reset_index(),
                                                on=["season", "bucket"])
    mine["dev"] = mine["s"] - mine["n"] * mine["mean"]
    ps = mine.groupby(["person", "season"])[["dev", "n"]].sum().reset_index()
    ps = ps.sort_values(["person", "season"]).reset_index(drop=True)
    ps[["dev", "n"]] = ps.groupby("person", sort=False)[["dev", "n"]].cumsum()
    ps = ps.rename(columns={"dev": "career_dev", "n": "career_n"})
    known = tw.loc[tw["person"].notna()].copy()
    known["_i"] = np.arange(len(known))
    ps["season"] = ps["season"].astype("int64")
    ps["person"] = ps["person"].astype(object)
    car = pd.merge_asof(known.sort_values("season", kind="stable"),
                        ps.sort_values("season", kind="stable"), on="season", by="person",
                        allow_exact_matches=False, direction="backward")
    car = car.sort_values("_i").reset_index(drop=True)
    known = known.sort_values("_i").reset_index(drop=True)
    known["career_dev"] = car["career_dev"].fillna(0.0).to_numpy()
    known["career_n"] = car["career_n"].fillna(0.0).to_numpy()

    # this season with this team, weeks < w, against the league as of week w
    buckets = sorted(agg["bucket"].unique())
    ex = known[["_i", "season", "week", "team", "person"]].merge(
        pd.DataFrame({"bucket": buckets}), how="cross")
    lw = agg.groupby(["season", "week", "bucket"])[["s", "n"]].sum().reset_index()
    lg = _asof_sums(ex, lw, ["bucket"], ["s", "n"]).rename(columns={"s": "ls", "n": "ln"})
    me = _asof_sums(ex, agg.loc[agg["person"].notna()], ["team", "person", "bucket"], ["s", "n"])
    ex["ps"], ex["pn"] = me["s"].fillna(0.0).to_numpy(), me["n"].fillna(0.0).to_numpy()
    ex["lmean"] = (lg["ls"] / lg["ln"].where(lg["ln"] > 0)).to_numpy()
    ex["dev"] = np.where(ex["pn"] > 0, ex["ps"] - ex["pn"] * ex["lmean"].fillna(0.0), 0.0)
    sea = ex.groupby("_i")[["dev", "pn"]].sum()
    known["season_dev"] = sea["dev"].reindex(known["_i"]).fillna(0.0).to_numpy()
    known["season_n"] = sea["pn"].reindex(known["_i"]).fillna(0.0).to_numpy()

    out = tw.merge(known[[*TEAM_WEEK, "career_dev", "career_n", "season_dev", "season_n"]],
                   on=TEAM_WEEK, how="left")
    return out


def pooled(t: pd.DataFrame, k: float) -> pd.Series:
    n = t["career_n"] + t["season_n"]
    return ((t["career_dev"] + t["season_dev"]) / (n + k)).where(n > 0)


def career(t: pd.DataFrame, k: float) -> pd.Series:
    return (t["career_dev"] / (t["career_n"] + k)).where(t["career_n"] > 0)


def this_season(t: pd.DataFrame, k: float) -> pd.Series:
    return (t["season_dev"] / (t["season_n"] + k)).where(t["season_n"] > 0)


def league_prior_season(obs: pd.DataFrame) -> pd.Series:
    """season s -> the league's mean value over season s - 1 (complete before
    season s starts): a constant every week of season s may use."""
    m = obs.groupby("season")["value"].mean()
    m.index = m.index + 1
    return m


def implied_totals(ctx, seasons) -> pd.DataFrame:
    """(season, week, team, implied): the team's implied points from the
    pre-game schedule's `total_line` and `spread_line` (gridiron.vegas, the
    nflverse sign). Games without both lines are absent."""
    s = ctx.schedule(seasons)
    rows = []
    for r in s.itertuples():
        total, spread = getattr(r, "total_line"), getattr(r, "spread_line")
        if pd.isna(total) or pd.isna(spread) or float(total) <= 0:
            continue
        home, away = implied_totals_from_nflverse(float(total), float(spread))
        rows += [(int(r.season), int(r.week), r.home_team, home),
                 (int(r.season), int(r.week), r.away_team, away)]
    out = pd.DataFrame(rows, columns=[*TEAM_WEEK, "implied"])
    out["team"] = out["team"].astype(object)
    return out.drop_duplicates(TEAM_WEEK)


# --------------------------------------------------------- player shares

def games_played(ctx, seasons) -> pd.DataFrame:
    """(season, week, gsis_id, team): the regular-season games a player was on
    the field for on offense: offensive snaps > 0 in the snap counts (pfr id
    -> gsis id through the one crosswalk), or a box score with a carry, a
    target or a pass attempt. The snap counts' team wins where both have it."""
    s = ctx.snaps(seasons)
    s = s.loc[pd.to_numeric(s["offense_snaps"], errors="coerce").fillna(0) > 0,
              ["season", "week", "gsis_id", "team"]].copy()
    s["gsis_id"] = C.ids(s["gsis_id"])
    w = ctx.weekly(seasons)
    opps = sum(pd.to_numeric(w[c], errors="coerce").fillna(0)
               for c in ("carries", "targets", "attempts"))
    w = w.loc[opps > 0, ["season", "week", "player_id", "team"]].rename(
        columns={"player_id": "gsis_id"})
    w["gsis_id"] = C.ids(w["gsis_id"])
    g = pd.concat([s, w], ignore_index=True)
    g = g.loc[g["gsis_id"] != ""].copy()
    g["team"] = g["team"].astype(object)
    return g.drop_duplicates(KEY).reset_index(drop=True)


def share_history(games: pd.DataFrame, events: pd.DataFrame, name: str) -> pd.DataFrame:
    """Player-game history for `_common.as_of`: per game he played, his count
    of `events` rows (`<name>`) and his team's in that game (`<name>_team`)."""
    team = events.groupby(TEAM_WEEK).size().rename(f"{name}_team").reset_index()
    mine = events.groupby(KEY).size().rename(name).reset_index()
    team["team"] = team["team"].astype(object)
    h = games.merge(mine, on=KEY, how="left").merge(team, on=TEAM_WEEK, how="left")
    h[[name, f"{name}_team"]] = h[[name, f"{name}_team"]].fillna(0.0)
    return h
