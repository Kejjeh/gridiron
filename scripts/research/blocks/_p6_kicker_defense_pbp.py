"""Helpers package P6's blocks share (K-04, K-06, K-09, K-10, K-11, DEF-07,
DEF-12: kickers and defenses from play-by-play); not a block (no ID).

Play and drive definitions, pinned once so every block counts the same plays:

  snap        a play-by-play row with play_type pass, run, field_goal, punt,
              qb_kneel, qb_spike or no_play (a penalized snap still says
              where the ball was), not an extra-point or two-point try.
              Kickoffs are not snaps.
  drive       nflverse `fixed_drive` within a game, credited to the team with
              the ball on its snaps (`posteam`). `best` is the closest it got
              (the smallest `yardline_100` at a snap); `td` is 1 when
              `fixed_drive_result` is "Touchdown" (the offense scored; "Opp
              touchdown" is not one).
  conversion  an extra-point kick (play_type extra_point) or a two-point try
              (two_point_attempt, play_type pass or run); a penalized try
              (no_play) is not one. `posteam` is the team that just scored.
  field goal  play_type field_goal, `kick_distance`, made when
              `field_goal_result` is "made" (blocked counts as a miss),
              credited to `kicker_player_id` and his team (`posteam`).
  throw       play_type pass with pass_attempt 1 and no sack (spikes are
              play_type qb_spike), credited to `passer_player_id`.
  fumble      an offensive fumble: play_type pass or run, `fumble` 1 and the
              offense fumbled (`fumbled_1_team` is `posteam`); lost when
              `fumble_lost` is 1.
  return      a kickoff (play_type kickoff; the receiving team is `posteam`)
              or a punt (play_type punt; the receiving team is `defteam`)
              with a named returner, no touchback and no fair catch. A return
              touchdown is a kickoff or punt play with `return_touchdown` 1
              scored by the receiving team (`td_team`).

A team's games are the regular-season games it has plays in (a game the
schedule lists but nobody played, 2022 week 17 BUF-CIN, is not a game).

`N0_*` are prior strengths: pseudo-counts for empirical-Bayes shrinkage
toward the league, (x + n0 * m) / (n + n0) (QUANT_FOUNDATIONS §5.1,
n0 = m(1 - m) / v_true - 1, v_true = Var(p_i) - mean[p_i(1 - p_i)/n_i]).
They come from the method of moments on the 2012-2018 regular seasons,
seasons no fold scores, from the play-by-play alone (never from points):
`PYTHONPATH=src python scripts/research/blocks/_p6_kicker_defense_pbp.py`
recomputes them. A rate enters as the shrunk rate minus the league's,
(x - n * m) / (n + n0) (`over_league`): efficiency as a slow prior (rule
#6), and a league-wide shift moves no team.

Every in-season number is a sum over games with week < the row's week
(`_common.as_of`, `league_as_of`); prior-season numbers read season - 1
only, so the blocks pass the leak check by construction.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C

#: block_runner.TEAM_CODES. The runner maps the team columns it lists;
#: `fumbled_1_team` is not one of them, so `codes` maps it here.
TEAM_CODES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
SNAP_TYPES = ("pass", "run", "field_goal", "punt", "qb_kneel", "qb_spike", "no_play")
RED_ZONE = 20             # yardline_100 at a snap: the opponent's 20 or closer
FG_RANGE = 30             # the opponent's 30 or closer
LONG_FG = 50              # kick_distance, yards
TWO_MINUTES = 120         # half_seconds_remaining
FTN_FIRST = 2022

# Prior strengths, method of moments on 2012-2018 (module docstring);
# `prior_strengths()` prints the unrounded values.
N0_RZ_OFFENSE = 141       # red-zone trips: an offense's touchdown rate
N0_RZ_DEFENSE = 275       # red-zone trips: a defense's touchdown rate allowed
N0_TWO_POINT = 49         # conversion tries: a team's two-point rate
N0_LONG_FG = 126          # field-goal attempts: a team's share from 50+
N0_KICK_ACCURACY = 82     # field-goal attempts: a kicker's makes over expected
N0_INTERCEPTION = 1091    # throws: a passer's interception rate (FTN starts in 2022)
N0_RETURN = 143           # kick and punt returns: yards over the league average

DRIVE_COLUMNS = ["play_type", "yardline_100", "fixed_drive", "fixed_drive_result",
                 "extra_point_attempt", "two_point_attempt"]
FG_COLUMNS = ["play_type", "kick_distance", "field_goal_result", "kicker_player_id"]


def flag(p: pd.DataFrame, col: str) -> pd.Series:
    """True where a 0/1 play-by-play flag is 1 (missing is 0)."""
    return pd.to_numeric(p[col], errors="coerce").fillna(0) == 1


def codes(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        if c in df.columns:
            df[c] = df[c].astype(object).replace(TEAM_CODES)
    return df


def seasons_of(keys: pd.DataFrame) -> list[int]:
    return sorted(int(s) for s in keys["season"].unique())


def _typed(k: pd.DataFrame) -> pd.DataFrame:
    k = k.reset_index(drop=True).copy()
    k["season"], k["week"] = k["season"].astype(int), k["week"].astype(int)
    for c in ("team", "opponent"):
        k[c] = k[c].astype(object).where(k[c].notna(), None)
    return k


def kicker_keys(ctx) -> pd.DataFrame:
    """The kicker rows: season, week, gsis_id, team, opponent (pre-game keys)."""
    return _typed(ctx.keys_for("K")[["season", "week", "gsis_id", "team", "opponent"]])


def defense_keys(ctx) -> pd.DataFrame:
    """The defense rows: season, week, team, opponent (pre-game keys)."""
    return _typed(ctx.keys_for("DEF")[["season", "week", "team", "opponent"]])


def opponent_view(keys: pd.DataFrame) -> pd.DataFrame:
    """The rows as the opponent: (season, week, team = the row's opponent)."""
    return keys[["season", "week", "opponent"]].rename(columns={"opponent": "team"})


def team_games(p: pd.DataFrame) -> pd.DataFrame:
    """(season, week, game_id, team, opponent): one row per regular-season
    team-game with plays."""
    g = p.loc[p["posteam"].notna() & p["defteam"].notna(),
              ["season", "week", "game_id", "posteam", "defteam"]]
    g = g.drop_duplicates(["season", "week", "game_id", "posteam"])
    g = g.rename(columns={"posteam": "team", "defteam": "opponent"})
    g = g.assign(team=g["team"].astype(object), opponent=g["opponent"].astype(object))
    return g.drop_duplicates(["season", "week", "team"]).reset_index(drop=True)


def drives(p: pd.DataFrame) -> pd.DataFrame:
    """One row per drive (module docstring): season, week, game_id, team,
    opponent, best, td. `p` needs DRIVE_COLUMNS."""
    s = p.loc[p["play_type"].isin(SNAP_TYPES) & ~flag(p, "extra_point_attempt")
              & ~flag(p, "two_point_attempt") & p["posteam"].notna() & p["fixed_drive"].notna()]
    d = s.groupby(["season", "week", "game_id", "posteam", "defteam", "fixed_drive"],
                  as_index=False).agg(best=("yardline_100", "min"),
                                      result=("fixed_drive_result", "first"))
    d["td"] = (d["result"] == "Touchdown").astype(float)
    return d.rename(columns={"posteam": "team", "defteam": "opponent"}).drop(columns=["result"])


def field_goals(p: pd.DataFrame) -> pd.DataFrame:
    """One row per field-goal attempt: season, week, team, gsis_id (the
    kicker), dist, made, long (50+). `p` needs FG_COLUMNS."""
    f = p.loc[(p["play_type"] == "field_goal") & p["kick_distance"].notna()]
    return pd.DataFrame({
        "season": f["season"].astype(int), "week": f["week"].astype(int),
        "team": f["posteam"].astype(object),
        "gsis_id": C.ids(f["kicker_player_id"]).astype(object),
        "dist": pd.to_numeric(f["kick_distance"], errors="coerce"),
        "made": (f["field_goal_result"] == "made").astype(float),
        "long": (pd.to_numeric(f["kick_distance"], errors="coerce") >= LONG_FG).astype(float),
    }).reset_index(drop=True)


def per_team_game(games: pd.DataFrame, events: pd.DataFrame, cols, side: str = "team") -> pd.DataFrame:
    """One row per team-game in `games` with the sums of `cols` over the
    `events` rows of that week credited to `side` ("team": the offense,
    "opponent": the defense facing it); 0 where it had none."""
    cols = list(cols)
    e = (events.groupby(["season", "week", side], as_index=False)[cols].sum()
         .rename(columns={side: "team"}))
    e["team"] = e["team"].astype(object)
    h = games[["season", "week", "team"]].merge(e, on=["season", "week", "team"], how="left")
    h[cols] = h[cols].fillna(0.0).astype("float64")
    return h.assign(games=1.0)


def per_season(history: pd.DataFrame, cols) -> pd.DataFrame:
    """Season sums of `cols` (and `games`) per team, for `_common.prior_season`."""
    return history.groupby(["season", "team"], as_index=False)[[*cols, "games"]].sum()


def league_as_of(target: pd.DataFrame, history: pd.DataFrame, cols) -> pd.DataFrame:
    """For each target row: sums of `cols` over EVERY history row of the same
    season with week < the row's week (the league to date), as `<col>_lg`.
    Row order is the target's; NaN before any history."""
    cols = list(cols)
    t = target[["season", "week"]].reset_index(drop=True).copy()
    t["season"], t["week"] = t["season"].astype("int64"), t["week"].astype("int64")
    t["_order"] = np.arange(len(t))
    g = history.groupby(["season", "week"])[cols].sum()
    g = g.sort_index().groupby(level="season").cumsum().reset_index()
    g["season"], g["week"] = g["season"].astype("int64"), g["week"].astype("int64")
    g = g.rename(columns={c: f"{c}_lg" for c in cols})
    res = pd.merge_asof(t.sort_values("week", kind="stable"), g.sort_values("week", kind="stable"),
                        on="week", by="season", allow_exact_matches=False, direction="backward")
    return res.sort_values("_order").drop(columns="_order").reset_index(drop=True)


def over_league(x, n, m, n0: float) -> pd.Series:
    """(x - n * m) / (n + n0): `x` events in `n` chances over the league's
    rate `m`, per chance, shrunk. It is the shrunk rate (x + n0 * m) / (n +
    n0) minus `m`, so a league-wide shift (a rule change, a trend) moves
    nobody; 0 with no chances yet, NaN only where `m` is unknown."""
    x = pd.Series(x, dtype="float64").fillna(0.0)
    n = pd.Series(n, dtype="float64").fillna(0.0)
    m = pd.Series(m, dtype="float64")
    return ((x - n * m) / (n + n0)).astype("float64")


def per_game(x, games) -> pd.Series:
    return C.share(x, games)


# ------------------------------------------------------------------ priors

def _nfl():
    """nflreadpy on the research cache, configured as block_runner._nfl does."""
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    from gridiron.paths import RESEARCH_CACHE
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    return nfl


def _mom(x: pd.Series, n: pd.Series) -> float:
    p = x / n
    m = x.sum() / n.sum()
    v_true = p.var(ddof=1) - float(np.mean(p * (1 - p) / n))
    return float(m * (1 - m) / v_true - 1)


def prior_strengths(seasons=range(2012, 2019)) -> dict[str, float]:
    """The method-of-moments prior strengths behind N0_* (module docstring),
    on regular-season team-, kicker- and passer-seasons of `seasons`."""
    cols = ["season", "week", "season_type", "game_id", "play_id", "posteam", "defteam",
            "play_type", "yardline_100", "fixed_drive", "fixed_drive_result",
            "extra_point_attempt", "two_point_attempt", "kick_distance", "field_goal_result",
            "kicker_player_id", "return_yards", "kickoff_returner_player_id",
            "punt_returner_player_id", "touchback", "punt_fair_catch", "kickoff_fair_catch",
            "pass_attempt", "sack", "interception", "passer_player_id"]
    p = _nfl().load_pbp(list(seasons)).select(cols).to_pandas()
    p = codes(p.loc[p["season_type"] == "REG"].copy(), ("posteam", "defteam"))
    out = {}
    d = drives(p)
    rz = d.loc[d["best"] <= RED_ZONE]
    for name, side in (("N0_RZ_OFFENSE", "team"), ("N0_RZ_DEFENSE", "opponent")):
        g = rz.groupby(["season", side])["td"].agg(["sum", "size"])
        out[name] = _mom(g["sum"].astype(float), g["size"].astype(float))
    tries = p.loc[((p["play_type"] == "extra_point") & flag(p, "extra_point_attempt"))
                  | (flag(p, "two_point_attempt") & p["play_type"].isin(["pass", "run"]))]
    g = tries.assign(two=flag(tries, "two_point_attempt").astype(float)).groupby(
        ["season", "posteam"])["two"].agg(["sum", "size"])
    out["N0_TWO_POINT"] = _mom(g["sum"], g["size"].astype(float))
    fg = field_goals(p)
    g = fg.groupby(["season", "team"])["long"].agg(["sum", "size"])
    out["N0_LONG_FG"] = _mom(g["sum"], g["size"].astype(float))
    fg = fg.assign(bucket=bucket(fg["dist"]))
    fg["exp"] = fg["bucket"].map(fg.groupby("bucket")["made"].mean())
    k = fg.groupby(["season", "gsis_id"]).agg(made=("made", "sum"), exp=("exp", "sum"),
                                              n=("made", "size"),
                                              pv=("exp", lambda e: float((e * (1 - e)).sum())))
    k = k.loc[k["n"] >= 10]
    oe = (k["made"] - k["exp"]) / k["n"]
    v_true = oe.var(ddof=1) - float(np.mean(k["pv"] / k["n"] ** 2))
    out["N0_KICK_ACCURACY"] = float(k["pv"].sum() / k["n"].sum() / v_true)
    t = p.loc[(p["play_type"] == "pass") & flag(p, "pass_attempt") & ~flag(p, "sack")
              & p["passer_player_id"].notna()]
    g = t.assign(i=flag(t, "interception").astype(float)).groupby(
        ["season", "passer_player_id"])["i"].agg(["sum", "size"])
    g = g.loc[g["size"] >= 150]
    out["N0_INTERCEPTION"] = _mom(g["sum"], g["size"].astype(float))
    r = returns(p)
    r["oe"] = r["yards"] - r.groupby(["season", "kind"])["yards"].transform("mean")
    g = r.groupby(["season", "team"])["oe"].agg(["mean", "size", "var"])
    v_true = g["mean"].var(ddof=1) - float(np.mean(g["var"] / g["size"]))
    out["N0_RETURN"] = float(r["oe"].var() / v_true)
    return out


def bucket(dist: pd.Series) -> pd.Series:
    """Field-goal distance in 5-yard buckets (15-19, 20-24, ..., 60+)."""
    return (pd.to_numeric(dist, errors="coerce").clip(upper=60) // 5 * 5).astype("float64")


def returns(p: pd.DataFrame) -> pd.DataFrame:
    """One row per kick or punt return (module docstring): season, week,
    team (the receiving team), kind ("kick" or "punt"), yards."""
    tb = flag(p, "touchback")
    k = p.loc[(p["play_type"] == "kickoff") & p["kickoff_returner_player_id"].notna() & ~tb
              & ~flag(p, "kickoff_fair_catch")]
    u = p.loc[(p["play_type"] == "punt") & p["punt_returner_player_id"].notna() & ~tb
              & ~flag(p, "punt_fair_catch")]
    parts = [pd.DataFrame({"season": k["season"], "week": k["week"], "team": k["posteam"],
                           "kind": "kick", "yards": pd.to_numeric(k["return_yards"], errors="coerce")}),
             pd.DataFrame({"season": u["season"], "week": u["week"], "team": u["defteam"],
                           "kind": "punt", "yards": pd.to_numeric(u["return_yards"], errors="coerce")})]
    r = pd.concat(parts, ignore_index=True).dropna(subset=["yards"])
    return r.assign(team=r["team"].astype(object))


if __name__ == "__main__":
    for k, v in prior_strengths().items():
        print(f"{k:<18} {v:8.1f}")
