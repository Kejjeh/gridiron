"""Shared builders for package P4, participation priors: QB-05, QB-10, WR-05,
WR-07, DEF-04 and DEF-05 (not a block: no ID).

nflverse participation (2016-2025) is joined to regular-season play-by-play
dropbacks on (game, play), so its playoff plays never enter. It has no 2026,
so every value taken from it is a PRIOR-SEASON tendency: season s-1 for a
season-s row, joined with `_common.prior_season` (a finished season is known
before the next one starts). Play-by-play and Next Gen Stats feed the
season-to-date parts, from the weeks before the row's week only
(`_common.as_of`).

What participation records on regular-season dropbacks (qb_dropback == 1;
measured on the research cache, 2026-11-02):
  was_pressure           88.7-89.6% of dropbacks 2018-2022, 100% 2023-2025
  defense_man_zone_type  none 2016-2017, 88.6-89.3% 2018-2022, 99.6-99.9%
                         2023-2025
  offense_players        100%: who was on the field, NOT who ran a route
  route                  the TARGETED receiver's route, one per play (2024:
                         98.7% of targeted dropbacks, 12.4% of the others)
DATA_INVENTORY.md's 84-85% for the first two counts play-by-play `pass == 1`
plays, a wider denominator. A "route" below is a dropback the player was on
the field for, other than as its passer: the proxy the FEATURE_EXPANSION
`+routes` block used (gridiron.models.advanced.routes_from_participation).

No roster position tag is read (rule #4): a passer is whoever `passer_id`
names on the play (scrambles and sacks included), and the league's receiver
reference is every player targeted at least once on a dropback that season.

Rates are empirical-Bayes estimates relative to the league: with x events in
n chances and the league's rate p over the same plays, (x - n p) / (n + k),
i.e. the rate shrunk toward the league's with k pseudo-chances, minus the
league's. Each k is p(1-p) / tau^2 (sigma^2 / tau^2 for EPA and for time to
throw), with tau^2 the between-player (or between-team) variance left after
the sampling variance, measured by the method of moments on the inputs
themselves (2018-2025; NGS 2019-2025), with no fantasy points read. The
pre-registrations give the numbers.

One Context behaviour these builders work around (block_runner.py is not
changed): a multi-season `ctx.injuries` call that includes 2025 returns
2025's rows only, and a multi-season `ctx.depth_charts` call that includes
2025 returns no 2025 row. 2025's files carry a column the earlier seasons
lack (`season_type` in injuries; no `game_type` in the depth-chart
snapshots), NaN on the other seasons' rows after the concat, and
`Context._get` keeps only rows whose season_type (or game_type) is "REG".
`depth_qbs` and `not_playing` therefore ask for one season per call.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C

#: The play-by-play columns every P4 block reads.
PBP_COLUMNS = ["qb_dropback", "sack", "qb_hit", "passer_id", "receiver_player_id", "epa"]
MAN, ZONE = "MAN_COVERAGE", "ZONE_COVERAGE"

#: Pseudo-chances each rate is shrunk with (see the module docstring).
K_SACKHIT_ALLOWED = 150.0   # a team's sack-or-hit rate on its own dropbacks (tau 0.028)
K_SACKHIT_GEN = 400.0       # a defense's sack-or-hit rate on the dropbacks it faced (tau 0.018)
K_QB_SACK = 200.0           # a passer's sack rate per dropback (tau 0.017)
K_QB_TTT = 50.0             # a passer's NGS time to throw, per attempt (tau 0.19 s)
K_QB_SPLIT = 900.0          # a passer's EPA per dropback, man minus zone (tau ~0.05)
K_TPRR = 60.0               # a receiver's targets per route (tau 0.046 among WRs)
K_TPRR_SPLIT = 150.0        # a receiver's targets per route, man minus zone (tau 0.032 among WRs)


def _flag(series: pd.Series, true_values, false_values) -> pd.Series:
    """1.0 / 0.0 / NaN from a column of mixed bools and strings."""
    s = series.map(lambda v: 1.0 if v in true_values else (0.0 if v in false_values else np.nan))
    return s.astype("float64")


def eb_rel(x, n, p, k: float) -> pd.Series:
    """(x - n p) / (n + k): the rate x / n shrunk toward p with k pseudo-chances,
    minus p. NaN where x, n or p is NaN."""
    x, n, p = (pd.Series(v, dtype="float64") for v in (x, n, p))
    return ((x - n * p) / (n + k)).astype("float64")


# ------------------------------------------------------------------- plays

def dropbacks(ctx, seasons) -> pd.DataFrame:
    """Regular-season dropbacks (qb_dropback == 1: passes, sacks and
    scrambles) of `seasons`, with `passer` and `receiver` as gsis ids ("" when
    none), `sack` (1/0) and `sackhit` (1 when the play had a sack or a QB hit;
    a sack that is also a hit counts once)."""
    p = ctx.pbp(sorted({int(s) for s in seasons}), PBP_COLUMNS)
    d = p.loc[p["qb_dropback"] == 1].copy()
    d["sack"] = (d["sack"].fillna(0) == 1).astype("float64")
    d["sackhit"] = ((d["sack"] == 1) | (d["qb_hit"].fillna(0) == 1)).astype("float64")
    d["passer"] = C.ids(d["passer_id"])
    d["receiver"] = C.ids(d["receiver_player_id"])
    d["play_id"] = d["play_id"].astype("float64")
    return d


def charted(ctx, drop: pd.DataFrame, seasons) -> pd.DataFrame:
    """The dropbacks of `seasons` (from `dropbacks`) joined to participation
    on (game, play): `pressure` (1/0, NaN where not recorded), `man` (1 man,
    0 zone, NaN where not recorded) and `offense_players`."""
    seasons = sorted({int(s) for s in seasons})
    part = ctx.participation(seasons)
    part = part[["nflverse_game_id", "play_id", "was_pressure", "defense_man_zone_type",
                 "offense_players"]].rename(columns={"nflverse_game_id": "game_id"})
    part = part.assign(play_id=part["play_id"].astype("float64")).drop_duplicates(
        ["game_id", "play_id"])
    d = drop.loc[drop["season"].isin(seasons)]
    m = d.merge(part, on=["game_id", "play_id"], how="left")
    m["pressure"] = _flag(m["was_pressure"], (True, "True", "TRUE", "true", 1),
                          (False, "False", "FALSE", "false", 0))
    m["man"] = _flag(m["defense_man_zone_type"], (MAN,), (ZONE,))
    return m.drop(columns=["was_pressure", "defense_man_zone_type"])


# ------------------------------------------------------- team-level tendencies

def team_pressure(ch: pd.DataFrame) -> pd.DataFrame:
    """(season, team, press_allowed, press_gen): the share of the team's own
    dropbacks (allowed) and of the dropbacks its defense faced (generated)
    that participation marks pressured, among those where it records
    pressure; each minus the league's share that season. Not shrunk: every
    team has a full season of dropbacks."""
    k = ch.loc[ch["pressure"].notna()]
    league = k.groupby("season")["pressure"].mean().rename("lg")
    off = k.groupby(["season", "posteam"])["pressure"].mean().rename("press_allowed")
    dfn = k.groupby(["season", "defteam"])["pressure"].mean().rename("press_gen")
    off.index.names = dfn.index.names = ["season", "team"]
    out = pd.concat([off, dfn], axis=1).reset_index().merge(league.reset_index(), on="season")
    out["press_allowed"] -= out["lg"]
    out["press_gen"] -= out["lg"]
    return out[["season", "team", "press_allowed", "press_gen"]]


def defense_man_rate(ch: pd.DataFrame) -> pd.DataFrame:
    """(season, team, man_rate): the share of the dropbacks a defense faced
    in man coverage, among those with man or zone recorded, minus the
    league's share that season."""
    k = ch.loc[ch["man"].notna()]
    league = k.groupby("season")["man"].mean().rename("lg")
    out = (k.groupby(["season", "defteam"])["man"].mean().rename("man_rate").reset_index()
           .rename(columns={"defteam": "team"}).merge(league.reset_index(), on="season"))
    out["man_rate"] -= out["lg"]
    return out[["season", "team", "man_rate"]]


def _league_rate_as_of(drop: pd.DataFrame, weeks: pd.DataFrame, col: str) -> pd.DataFrame:
    """(season, week, lg_rate): the league's mean of `col` per dropback over
    the season's games before each (season, week) in `weeks`."""
    h = (drop.groupby(["season", "week"]).agg(n=(col, "size"), x=(col, "sum")).reset_index()
         .assign(lg="all"))
    t = weeks[["season", "week"]].drop_duplicates().assign(lg="all")
    a = C.as_of(t, h, ["lg"], ["n", "x"])
    a["lg_rate"] = C.share(a["x_std"], a["n_std"])
    return a[["season", "week", "lg_rate"]]


def team_sackhit_as_of(drop: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    """For each (season, week, team) in `target`, over the team's games this
    season before the week: `sackhit_allowed_std`, its own dropbacks with a
    sack or a QB hit, and `sackhit_gen_std`, the same for the dropbacks its
    defense faced; each `eb_rel` against the league's share over the same
    weeks (K_SACKHIT_ALLOWED, K_SACKHIT_GEN). NaN with no earlier game."""
    off = (drop.groupby(["season", "week", "posteam"])
           .agg(n_off=("sackhit", "size"), x_off=("sackhit", "sum")).reset_index()
           .rename(columns={"posteam": "team"}))
    dfn = (drop.groupby(["season", "week", "defteam"])
           .agg(n_def=("sackhit", "size"), x_def=("sackhit", "sum")).reset_index()
           .rename(columns={"defteam": "team"}))
    hist = off.merge(dfn, on=["season", "week", "team"], how="outer").fillna(0.0)
    t = target[["season", "week", "team"]].drop_duplicates()
    a = C.as_of(t, hist, ["team"], ["n_off", "x_off", "n_def", "x_def"])
    a = a.merge(_league_rate_as_of(drop, t, "sackhit"), on=["season", "week"], how="left")
    a["sackhit_allowed_std"] = eb_rel(a["x_off_std"], a["n_off_std"], a["lg_rate"],
                                      K_SACKHIT_ALLOWED)
    a["sackhit_gen_std"] = eb_rel(a["x_def_std"], a["n_def_std"], a["lg_rate"], K_SACKHIT_GEN)
    return a[["season", "week", "team", "sackhit_allowed_std", "sackhit_gen_std"]]


# --------------------------------------------------------- player-level priors

def split_eb(n_man, s_man, n_zone, s_zone, lg_split, k: float) -> np.ndarray:
    """A man-minus-zone split relative to the league's, empirical Bayes:
    w * (s_man/n_man - s_zone/n_zone - lg_split), w = h / (h + k), with
    h = n_man n_zone / (n_man + n_zone) the split's effective sample size.
    0 (the league's split) when either side is empty; NaN when both are."""
    n_man, n_zone = np.asarray(n_man, "float64"), np.asarray(n_zone, "float64")
    s_man, s_zone = np.asarray(s_man, "float64"), np.asarray(s_zone, "float64")
    lg_split = np.asarray(lg_split, "float64")
    both = (n_man > 0) & (n_zone > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        raw = s_man / n_man - s_zone / n_zone
        h = n_man * n_zone / (n_man + n_zone)
        val = h / (h + k) * (raw - lg_split)
    out = np.where(both, val, 0.0)
    return np.where(n_man + n_zone > 0, out, np.nan)


def qb_coverage_split(ch: pd.DataFrame) -> pd.DataFrame:
    """(season, gsis_id, man_split): the passer's EPA per dropback against man
    minus against zone, on the season's dropbacks with man or zone recorded,
    `split_eb` against the league's split that season (K_QB_SPLIT)."""
    k = ch.loc[ch["man"].notna() & (ch["passer"] != "") & ch["epa"].notna()]
    k = k.assign(epa_man=k["epa"] * k["man"], epa_zone=k["epa"] * (1 - k["man"]),
                 n_man=k["man"], n_zone=1 - k["man"])
    sums = ["n_man", "epa_man", "n_zone", "epa_zone"]
    g = k.groupby(["season", "passer"])[sums].sum().reset_index().rename(
        columns={"passer": "gsis_id"})
    lg = k.groupby("season")[sums].sum()
    lg_split = (lg["epa_man"] / lg["n_man"] - lg["epa_zone"] / lg["n_zone"]).rename("lg")
    g = g.merge(lg_split.reset_index(), on="season")
    g["man_split"] = split_eb(g["n_man"], g["epa_man"], g["n_zone"], g["epa_zone"], g["lg"],
                              K_QB_SPLIT)
    return g[["season", "gsis_id", "man_split"]]


def player_routes(ch: pd.DataFrame) -> pd.DataFrame:
    """(season, gsis_id, routes, targets, routes_man, targets_man,
    routes_zone, targets_zone): the season's dropbacks the player was on the
    field for (participation `offense_players`), other than as the play's
    passer, and how many of them targeted him; then the same on the
    dropbacks with man or with zone coverage recorded."""
    m = ch.loc[ch["offense_players"].notna() & (ch["offense_players"].astype(str) != ""),
               ["season", "game_id", "play_id", "passer", "receiver", "man", "offense_players"]]
    ex = m.assign(gsis_id=m["offense_players"].astype(str).str.split(";")).explode("gsis_id")
    ex["gsis_id"] = ex["gsis_id"].str.strip()
    ex = ex.loc[(ex["gsis_id"] != "") & (ex["gsis_id"] != ex["passer"])]
    ex = ex.drop_duplicates(["game_id", "play_id", "gsis_id"])
    tgt = (ex["receiver"] == ex["gsis_id"]).astype("float64")
    ex = ex.assign(routes=1.0, targets=tgt,
                   routes_man=(ex["man"] == 1).astype("float64"),
                   targets_man=tgt * (ex["man"] == 1),
                   routes_zone=(ex["man"] == 0).astype("float64"),
                   targets_zone=tgt * (ex["man"] == 0))
    cols = ["routes", "targets", "routes_man", "targets_man", "routes_zone", "targets_zone"]
    return ex.groupby(["season", "gsis_id"])[cols].sum().reset_index()


def receivers(routes: pd.DataFrame) -> pd.DataFrame:
    """The league's receiver reference: players targeted at least once on a
    dropback that season (leaves out linemen by usage, rule #4)."""
    return routes.loc[routes["targets"] > 0]


# ----------------------------------------------------------- opponent's QB

#: Weekly-roster statuses that mean "not playing for weeks" (the shipped
#: advanced.RESERVE_STATUSES). INA, the game-day inactive list, is never read.
RESERVE = ("RES", "EXE")


def depth_qbs(ctx, seasons) -> pd.DataFrame:
    """(season, week, team, gsis_id, rank): every quarterback on the team's
    depth chart published for that week (pre-game), with his rank. Weekly
    layout through 2024 (`depth_team` at `depth_position` QB, offense); from
    2025 the latest timestamped snapshot the Context assigned to that week
    (`pos_rank` at `pos_abb` QB). One season per Context call (see the
    module docstring)."""
    parts = []
    for season in sorted({int(s) for s in seasons}):
        dc = ctx.depth_charts([season])
        if "depth_team" in dc.columns:
            w = dc.loc[dc["depth_team"].notna()]
            w = w.loc[(w["formation"] == "Offense") & (w["depth_position"] == "QB")]
            parts.append(pd.DataFrame({"season": w["season"], "week": w["week"],
                                       "team": w["club_code"], "gsis_id": w["gsis_id"],
                                       "rank": pd.to_numeric(w["depth_team"], errors="coerce")}))
        elif "dt" in dc.columns:
            s = dc.loc[dc["dt"].notna() & (dc["pos_abb"] == "QB")].copy()
            s["when"] = pd.to_datetime(s["dt"], utc=True)
            s = s.loc[s["when"] == s.groupby(["season", "week", "team"])["when"].transform("max")]
            parts.append(pd.DataFrame({"season": s["season"], "week": s["week"],
                                       "team": s["team"], "gsis_id": s["gsis_id"],
                                       "rank": pd.to_numeric(s["pos_rank"], errors="coerce")}))
    cols = ["season", "week", "team", "gsis_id", "rank"]
    if not parts:
        return pd.DataFrame(columns=cols)
    q = pd.concat(parts, ignore_index=True)
    q["gsis_id"] = C.ids(q["gsis_id"])
    q = q.loc[(q["gsis_id"] != "") & q["rank"].notna()]
    q["season"], q["week"] = q["season"].astype(int), q["week"].astype(int)
    return q.sort_values("rank").drop_duplicates(["season", "week", "team", "gsis_id"])[cols]


def not_playing(ctx, seasons) -> pd.DataFrame:
    """(season, week, gsis_id) known before the week's games not to play:
    listed Out or Doubtful on the week's final injury report, or on a reserve
    list (RESERVE) on the week's roster. Both frames are pre-game; one season
    per Context call (see the module docstring)."""
    parts = []
    for season in sorted({int(s) for s in seasons}):
        i = ctx.injuries([season])
        i = i.loc[i["report_status"].isin(["Out", "Doubtful"])]
        parts.append(i[["season", "week", "gsis_id"]])
        r = ctx.rosters_weekly([season])
        r = r.loc[r["status"].isin(RESERVE)]
        parts.append(r[["season", "week", "gsis_id"]])
    out = pd.concat(parts, ignore_index=True)
    out["gsis_id"] = C.ids(out["gsis_id"])
    out["season"], out["week"] = out["season"].astype(int), out["week"].astype(int)
    return out.loc[out["gsis_id"] != ""].drop_duplicates()


def starting_qb(ctx, seasons) -> pd.DataFrame:
    """(season, week, team, gsis_id): the team's expected starting quarterback,
    from pre-game facts only: the best-ranked QB on the week's depth chart
    who is not `not_playing` that week. A team-week with no chart, no such QB,
    or two different QBs sharing that best rank is left out (unknown)."""
    q = depth_qbs(ctx, seasons)
    gone = not_playing(ctx, seasons).assign(gone=True)
    q = q.merge(gone, on=["season", "week", "gsis_id"], how="left")
    q = q.loc[q["gone"].isna()]
    top = q["rank"] == q.groupby(["season", "week", "team"])["rank"].transform("min")
    q = q.loc[top]
    n = q.groupby(["season", "week", "team"])["gsis_id"].transform("nunique")
    return q.loc[n == 1, ["season", "week", "team", "gsis_id"]].reset_index(drop=True)


def qb_sack_rate_as_of(drop: pd.DataFrame, target: pd.DataFrame) -> pd.DataFrame:
    """For each (season, week, gsis_id) in `target`: his sacks per dropback as
    the play's passer (any team) in the season's games before the week,
    `eb_rel` against the league's rate over the same weeks (K_QB_SACK). NaN
    when he has no dropback before the week."""
    d = drop.loc[drop["passer"] != ""]
    hist = (d.groupby(["season", "week", "passer"]).agg(n=("sack", "size"), x=("sack", "sum"))
            .reset_index().rename(columns={"passer": "gsis_id"}))
    t = target[["season", "week", "gsis_id"]].drop_duplicates()
    a = C.as_of(t, hist, ["gsis_id"], ["n", "x"])
    a = a.merge(_league_rate_as_of(drop, t, "sack"), on=["season", "week"], how="left")
    a["qb_sack_rate"] = eb_rel(a["x_std"], a["n_std"], a["lg_rate"], K_QB_SACK)
    return a[["season", "week", "gsis_id", "qb_sack_rate"]]


def _nfl():
    """nflreadpy on the research cache, configured as block_runner._nfl does."""
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    from gridiron.paths import RESEARCH_CACHE
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    return nfl


def ngs_passing(ctx, seasons) -> pd.DataFrame:
    """Next Gen Stats weekly passing rows, regular season, week >= 1 only (the
    week-0 rows are season totals that include later weeks, never read). The
    Context has no NGS loader, so this one goes through the Context's own
    frame store under the post-game `weekly` classification: the runner's
    leak-check cut (week < w) applies to it like any other post-game frame."""
    def load(ss, _):
        df = _nfl().load_nextgen_stats(list(ss), stat_type="passing").to_pandas()
        return df.loc[df["week"] >= 1]
    return ctx._get("weekly", seasons, load, ["ngs_passing"])


def qb_ttt_as_of(ctx, target: pd.DataFrame) -> pd.DataFrame:
    """For each (season, week, gsis_id) in `target`: his attempt-weighted
    mean NGS time to throw over his weekly rows this season before the week,
    `eb_rel` against the league's attempt-weighted mean over the same weeks
    (K_QB_TTT pseudo-attempts). NaN with no such row (NGS lists a passer's
    game from 15 attempts)."""
    t = target[["season", "week", "gsis_id"]].drop_duplicates()
    n = ngs_passing(ctx, sorted(t["season"].unique()))
    n = n.loc[n["avg_time_to_throw"].notna() & (n["attempts"] > 0)]
    h = pd.DataFrame({"season": n["season"], "week": n["week"],
                      "gsis_id": C.ids(n["player_gsis_id"]),
                      "att": n["attempts"].astype("float64"),
                      "tx": n["avg_time_to_throw"].astype("float64") * n["attempts"]})
    h = h.loc[h["gsis_id"] != ""].groupby(["season", "week", "gsis_id"], as_index=False).sum()
    a = C.as_of(t, h, ["gsis_id"], ["att", "tx"])
    lg = h.groupby(["season", "week"], as_index=False)[["att", "tx"]].sum().assign(lg="all")
    la = C.as_of(t[["season", "week"]].drop_duplicates().assign(lg="all"), lg, ["lg"],
                 ["att", "tx"])
    la["lg_ttt"] = C.share(la["tx_std"], la["att_std"])
    a = a.merge(la[["season", "week", "lg_ttt"]], on=["season", "week"], how="left")
    a["qb_ttt"] = eb_rel(a["tx_std"], a["att_std"], a["lg_ttt"], K_QB_TTT)
    return a[["season", "week", "gsis_id", "qb_ttt"]]
