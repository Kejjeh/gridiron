"""Helpers package P7's distribution blocks share (RB-13, TE-10, K-03, DEF-03);
not a block (no ID).

Workstream D of docs/research/MODEL_ROADMAP.md: an outcome DISTRIBUTION per
row instead of the page's one normal. A distribution block is a block module
with `KIND = "distribution"`:

  build(ctx)            the distribution's pre-game inputs, one row per target
                        row, exactly like a feature block (the block runner's
                        coverage and leak check apply unchanged);
  fit(train, crps)      the block's spread parameters, fitted on the training
                        seasons only, by minimum mean CRPS (`crps` is the
                        harness's own lattice CRPS, so the fit and the score
                        are one formula);
  pmf(rows, params)     (x0, probs): row i puts probs[i, k] on the value
                        x0[i] + k * LATTICE.

Every candidate keeps the page's MEAN (`mu`, the shipped ridge refit with
the row's season held out): only the shape and the spread change. A
distribution whose mean moved would be a mean change, which the block
runner's bar governs. scripts/research/distribution_backtest.py scores the
candidates against the page's normal.

Point values come from the league's one scoring implementation (rule #2):
`gridiron.scoring.fantasy_points` (read through unit stat lines),
`KICKING_SCORING`, `DEFENSE_SCORING` and `points_allowed_tier`.

Pseudo-counts below are stated, not fitted (as projection.N0_GAMES_RATE is):
a games-denominated n0 of 3-4 for volume rates (target-share stabilisation,
QUANT_FOUNDATIONS §5.3), heavier for efficiency and rare events (rule #6).
Every in-season number is a sum over games with week < the row's week
(`_common.as_of`); priors read season - 1 only, so the inputs pass the leak
check by construction.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import gammainc, ndtr

import _common as C

# ----------------------------------------------------------------- constants

TD_N0_GAMES = 3.0          # games: season-to-date TD share toward the prior
PRIOR_N0_GAMES = 3.0       # games: last season's TD share toward the league's
TEAM_N0_GAMES = 4.0        # team-games: kicking attempts per game toward the league
MAKE_N0 = 20.0             # attempts: a kicker's make rate per tier toward the league
XP_MAKE_N0 = 40.0          # attempts: his extra-point rate toward the league
DEF_N0_GAMES = 3.0         # games: sacks, takeaways, forced fumbles toward the league
DEF_TD_N0_GAMES = 8.0      # games: defensive and return touchdowns (rare: slow)

SKILL_STEP = 0.1           # fantasy points for QB/RB/WR/TE are multiples of 0.1
SKILL_BINS = 901           # lattice 0 .. 90 points (tail mass goes to the last bin)
TD_MAX = 8                 # touchdowns enumerated per game (the rest is folded in)
INT_LO, INT_N = -20, 128   # K and DEF: integer lattice -20 .. 107, FFT length 128

#: K-03 dispersion grid: kappa < 0 is a multinomial over -1/kappa trials (an
#: integer), 0 is Poisson, kappa > 0 a gamma-mixed (negative binomial) rate.
KAPPA_GRID = (-1 / 10, -1 / 12, -1 / 15, -1 / 20, -1 / 30, -1 / 50, 0.0,
              0.05, 0.1, 0.2, 0.35, 0.5)
#: DEF-03 grid for the spread of the opponent's points around the implied total.
PA_SD_GRID = tuple(np.round(np.arange(5.0, 20.01, 0.5), 2))
PA_MAX = 80                # opponent points enumerated (the 35+ tier holds the rest)


# ------------------------------------------------------------ scoring values

def skill_unit_points() -> dict[str, float]:
    """Points per unit of each stat, read from the one scoring formula."""
    from gridiron.scoring import fantasy_points
    cols = ("rushing_yards", "receiving_yards", "receptions", "rushing_tds", "receiving_tds",
            "rushing_2pt_conversions", "receiving_2pt_conversions")
    return {c: float(fantasy_points({c: 1.0})) for c in cols}


def td_points() -> float:
    u = skill_unit_points()
    if u["rushing_tds"] != u["receiving_tds"]:
        raise ValueError("rushing and receiving touchdowns score differently: the TD lattice "
                         "needs one value")
    return u["rushing_tds"]


def kicking_values() -> dict[str, float]:
    """Point value per kicking event group, from KICKING_SCORING."""
    from gridiron.league_config import KICKING_SCORING as W
    if not (W["fgm_0_19"] == W["fgm_20_29"] == W["fgm_30_39"]):
        raise ValueError("field goals inside 40 score differently: regroup the tiers")
    if W["fgmiss"] != W["xpmiss"]:
        raise ValueError("missed field goals and extra points score differently")
    return {"k_made_short": W["fgm_30_39"], "k_made_40": W["fgm_40_49"],
            "k_made_50": W["fgm_50p"], "k_xp_made": W["xpm"], "k_missed": W["fgmiss"]}


#: DEF event groups: (DEFENSE_SCORING key, its nflverse columns via scoring.py)
DEF_GROUPS = {"sacks": ("sack",), "ff": ("ff",), "takeaways": ("int", "fum_rec"),
              "minor2": ("safe", "blk_kick"), "tds": ("def_td", "def_st_td")}


def defense_values() -> dict[str, float]:
    """One point value per DEF event group, from DEFENSE_SCORING."""
    from gridiron.league_config import DEFENSE_SCORING as W
    out = {}
    for g, keys in DEF_GROUPS.items():
        vals = {W[k] for k in keys}
        if len(vals) != 1:
            raise ValueError(f"DEF group {g} mixes point values {vals}")
        out[g] = vals.pop()
    return out


def defense_columns() -> dict[str, tuple[str, ...]]:
    from gridiron.scoring import _DEFENSE_COMPONENTS as COMP
    return {g: tuple(c for k in keys for c in COMP[k]) for g, keys in DEF_GROUPS.items()}


def tier_points(max_pa: int = PA_MAX) -> np.ndarray:
    """The league's points-allowed tier value for 0..max_pa opponent points."""
    from gridiron.league_config import DEFENSE_SCORING as W
    from gridiron.scoring import points_allowed_tier
    return np.array([W.get(points_allowed_tier(k), 0.0) for k in range(max_pa + 1)])


# ------------------------------------------------------------ input builders

def _num(df: pd.DataFrame, cols) -> pd.Series:
    """The sum of `cols`, a null CELL read as zero. A missing COLUMN raises:
    read as zero it would silently remove an event from every row (the
    scoring module's rule for whole columns)."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise KeyError(f"source frame has no column(s) {missing}")
    out = pd.Series(0.0, index=df.index)
    for c in cols:
        out = out + pd.to_numeric(df[c], errors="coerce").fillna(0.0)
    return out


def _reg_games(ctx, seasons) -> set:
    s = ctx.schedule(seasons)
    return set(s["game_id"].astype(str))


def td_share_inputs(ctx, position: str) -> pd.DataFrame:
    """RB-13 / TE-10 inputs: the share of a player's expected fantasy points
    that comes from touchdowns (nflverse ff_opportunity expected values,
    scored with the league's weights), season to date, and its prior.

      td_share_std    sum of expected TD points / sum of expected points over
                      his games this season before the row's week
      td_games_std    how many such games
      td_share_prior  his last-season share shrunk to the position's league
                      share last season, weight g / (g + PRIOR_N0_GAMES)
    """
    keys = ctx.keys_for(position)[["season", "week", "gsis_id"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    allseasons = sorted({*seasons, *(s - 1 for s in seasons)})
    ff = ctx.ff_opportunity(allseasons)
    ff = ff.loc[ff["player_id"].notna() & ff["game_id"].astype(str).isin(_reg_games(ctx, allseasons))]
    ff = ff.assign(gsis_id=C.ids(ff["player_id"]))
    u = skill_unit_points()
    x_td = (u["rushing_tds"] * _num(ff, ["rush_touchdown_exp"])
            + u["receiving_tds"] * _num(ff, ["rec_touchdown_exp"]))
    x_fp = (x_td + u["rushing_yards"] * _num(ff, ["rush_yards_gained_exp"])
            + u["receiving_yards"] * _num(ff, ["rec_yards_gained_exp"])
            + u["receptions"] * _num(ff, ["receptions_exp"])
            + u["rushing_2pt_conversions"] * _num(ff, ["rush_two_point_conv_exp"])
            + u["receiving_2pt_conversions"] * _num(ff, ["rec_two_point_conv_exp"]))
    ff = ff.assign(x_td=x_td, x_fp=x_fp)
    hist = ff.groupby(["season", "week", "gsis_id"], as_index=False)[["x_td", "x_fp"]].sum()
    r = C.as_of(keys, hist, ["gsis_id"], ["x_td", "x_fp"])
    r["td_share_std"] = C.share(r["x_td_std"], r["x_fp_std"])
    r["td_games_std"] = r["games_std"]
    # priors from season - 1: the player's own share, and his position's
    per = hist.groupby(["season", "gsis_id"], as_index=False).agg(
        x_td=("x_td", "sum"), x_fp=("x_fp", "sum"), g=("week", "nunique"))
    pos = ff.loc[ff["position"] == position]
    league = pos.groupby("season").agg(x_td=("x_td", "sum"), x_fp=("x_fp", "sum"))
    league = (league["x_td"] / league["x_fp"]).rename("league_share").reset_index()
    per["share_prev"] = C.share(per["x_td"], per["x_fp"])
    r = C.prior_season(r, per.rename(columns={"g": "g_prev"}), ["gsis_id"], ["share_prev", "g_prev"])
    r = _prior_const(r, league)
    gp = r["g_prev"].fillna(0.0)
    wp = gp / (gp + PRIOR_N0_GAMES)
    r["td_share_prior"] = (wp * r["share_prev"].fillna(0.0) + (1 - wp) * r["league_share"])
    return r[["season", "week", "gsis_id", "td_share_std", "td_games_std", "td_share_prior"]]


def _prior_const(r: pd.DataFrame, league: pd.DataFrame) -> pd.DataFrame:
    """Join season - 1's league share onto every row of a season."""
    p = league.assign(season=league["season"] + 1)
    return r.merge(p, on="season", how="left")


def td_share(rows: pd.DataFrame) -> np.ndarray:
    """The share used: season to date shrunk to the prior, w = g / (g + n0)."""
    g = rows["td_games_std"].fillna(0.0).to_numpy(dtype="float64")
    std = rows["td_share_std"].to_numpy(dtype="float64")
    prior = rows["td_share_prior"].to_numpy(dtype="float64")
    w = np.where(np.isnan(std), 0.0, g / (g + TD_N0_GAMES))
    s = w * np.nan_to_num(std) + (1 - w) * prior
    return np.clip(s, 0.0, 0.95)


KICK_TIERS = {"t29": (("fg_made_0_19", "fg_made_20_29"), ("fg_missed_0_19", "fg_missed_20_29")),
              "t39": (("fg_made_30_39",), ("fg_missed_30_39",)),
              "t49": (("fg_made_40_49",), ("fg_missed_40_49",)),
              "t50": (("fg_made_50_59", "fg_made_60_"), ("fg_missed_50_59", "fg_missed_60_")),
              "xp": (("pat_made",), ("pat_missed",))}


def kicking_inputs(ctx) -> pd.DataFrame:
    """K-03 inputs: expected scoring events per game for the kicker, by the
    league's point values. Attempts (made + missed; a blocked kick scores 0
    and is left out) per team-game in each distance tier, season to date
    shrunk to last season's league rate with TEAM_N0_GAMES; the kicker's make
    rate per tier, season to date shrunk to last season's league rate with
    MAKE_N0 (XP_MAKE_N0 for extra points): efficiency is a slow prior.

      k_made_short  made field goals inside 40 (3 points each)
      k_made_40     made from 40-49 (4)
      k_made_50     made from 50+ (5)
      k_xp_made     made extra points (1)
      k_missed      missed field goals and extra points (-1)
    """
    keys = ctx.keys_for("K")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    allseasons = sorted({*seasons, *(s - 1 for s in seasons)})
    ts = ctx.team_stats(allseasons)
    tg = ts[["season", "week", "team"]].copy()
    for t, (made, missed) in KICK_TIERS.items():
        tg[f"att_{t}"] = _num(ts, made) + _num(ts, missed)
    tg = tg.drop_duplicates(["season", "week", "team"])
    att_cols = [f"att_{t}" for t in KICK_TIERS]
    r = C.as_of(keys, tg, ["team"], att_cols)
    league_att = tg.groupby("season")[att_cols].mean().reset_index()
    r = r.merge(league_att.assign(season=league_att["season"] + 1)
                .rename(columns={c: f"{c}_prior" for c in att_cols}), on="season", how="left")
    g = r["games_std"].fillna(0.0)
    lam = {t: (r[f"att_{t}_std"].fillna(0.0) + TEAM_N0_GAMES * r[f"att_{t}_prior"])
              / (g + TEAM_N0_GAMES) for t in KICK_TIERS}
    # the kicker's own makes (player box scores) toward last season's league
    # rate. Kicking rows are the player-games with a kick in them, read from
    # the box score, not from the roster position tag (rule #4)
    w = ctx.weekly(allseasons)
    kick_cols = [c for made, missed in KICK_TIERS.values() for c in (*made, *missed)]
    k = w.loc[_num(w, kick_cols) > 0].copy()
    k["gsis_id"] = C.ids(k["player_id"])
    kh = k[["season", "week", "gsis_id"]].copy()
    for t, (made, missed) in KICK_TIERS.items():
        kh[f"made_{t}"] = _num(k, made)
        kh[f"miss_{t}"] = _num(k, missed)
    kh = kh.groupby(["season", "week", "gsis_id"], as_index=False).sum()
    mk_cols = [f"{p}_{t}" for t in KICK_TIERS for p in ("made", "miss")]
    km = C.as_of(keys[["season", "week", "gsis_id"]], kh, ["gsis_id"], mk_cols)
    lg = kh.groupby("season")[mk_cols].sum()
    lg_rate = pd.DataFrame({f"p0_{t}": lg[f"made_{t}"] / (lg[f"made_{t}"] + lg[f"miss_{t}"])
                            for t in KICK_TIERS}).reset_index()
    km = km.merge(lg_rate.assign(season=lg_rate["season"] + 1), on="season", how="left")
    p = {}
    for t in KICK_TIERS:
        n0 = XP_MAKE_N0 if t == "xp" else MAKE_N0
        made = km[f"made_{t}_std"].fillna(0.0)
        att = made + km[f"miss_{t}_std"].fillna(0.0)
        p[t] = ((made + n0 * km[f"p0_{t}"]) / (att + n0)).to_numpy()
    out = r[["season", "week", "gsis_id"]].copy()
    lam = {t: v.to_numpy() for t, v in lam.items()}
    out["k_made_short"] = lam["t29"] * p["t29"] + lam["t39"] * p["t39"]
    out["k_made_40"] = lam["t49"] * p["t49"]
    out["k_made_50"] = lam["t50"] * p["t50"]
    out["k_xp_made"] = lam["xp"] * p["xp"]
    out["k_missed"] = sum(lam[t] * (1 - p[t]) for t in KICK_TIERS)
    return out


def defense_inputs(ctx) -> pd.DataFrame:
    """DEF-03 inputs: expected counting events per game for the defense, by
    the league's point values (the points-allowed tier comes from the
    opponent's implied total in the frozen table, not from here).

      dst_rate_1  sacks (the defense's per game and the opponent's sacks
                  allowed per game, each shrunk to the league with
                  DEF_N0_GAMES, averaged) + the defense's forced fumbles
      dst_rate_2  takeaways (interceptions + fumble recoveries, averaged with
                  the opponent's giveaways the same way) + safeties and
                  blocked kicks at last season's league rate
      dst_rate_6  defensive, fumble-return and return touchdowns, the
                  defense's own, shrunk with DEF_TD_N0_GAMES
    """
    keys = ctx.keys_for("DEF")[["season", "week", "team", "opponent"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    allseasons = sorted({*seasons, *(s - 1 for s in seasons)})
    ts = ctx.team_stats(allseasons)
    cols = defense_columns()
    tg = ts[["season", "week", "team"]].copy()
    for gname, cs in cols.items():
        tg[gname] = _num(ts, cs)
    tg["sacks_allowed"] = _num(ts, ["sacks_suffered"])
    tg["giveaways"] = _num(ts, ["passing_interceptions", "sack_fumbles_lost",
                                "rushing_fumbles_lost", "receiving_fumbles_lost"])
    tg = tg.drop_duplicates(["season", "week", "team"])
    stat = ["sacks", "ff", "takeaways", "minor2", "tds", "sacks_allowed", "giveaways"]
    league = tg.groupby("season")[stat].mean()
    prior = league.reset_index().assign(season=lambda d: d["season"] + 1)
    own = C.as_of(keys[["season", "week", "team"]], tg, ["team"], stat)
    own = own.merge(prior.rename(columns={c: f"{c}_lg" for c in stat}), on="season", how="left")
    opp = C.as_of(keys[["season", "week", "opponent"]].rename(columns={"opponent": "team"}),
                  tg, ["team"], ["sacks_allowed", "giveaways"])

    def shrunk(frame, c, n0):
        g = frame["games_std"].fillna(0.0)
        return ((frame[f"{c}_std"].fillna(0.0) + n0 * own[f"{c}_lg"]) / (g + n0)).to_numpy()

    sacks = 0.5 * (shrunk(own, "sacks", DEF_N0_GAMES) + shrunk(opp, "sacks_allowed", DEF_N0_GAMES))
    take = 0.5 * (shrunk(own, "takeaways", DEF_N0_GAMES) + shrunk(opp, "giveaways", DEF_N0_GAMES))
    out = keys[["season", "week", "team"]].reset_index(drop=True).copy()
    out["dst_rate_1"] = sacks + shrunk(own, "ff", DEF_N0_GAMES)
    out["dst_rate_2"] = take + own["minor2_lg"].to_numpy()
    out["dst_rate_6"] = shrunk(own, "tds", DEF_TD_N0_GAMES)
    return out


# --------------------------------------------------------- distributions

def on_grid_mean(x0: np.ndarray, probs: np.ndarray, step: float,
                 mu: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Move each row's lattice distribution (P(X = x0 + k step) = probs[k])
    so its mean is exactly mu while every point stays on the row's grid: by
    n whole steps, and the remaining fraction f of a step as a one-step
    Bernoulli move, X' = X + n step + B step with B ~ Bernoulli(f). For an
    outcome on the grid (whole points for K and DEF, tenths for RB and TE)
    this is never worse in CRPS than sliding the support off the grid by the
    same amount: the slid distribution's CRPS is the f-weighted average of
    the two whole-step shifts' CRPS, and CRPS is convex in the distribution.
    One column is added (the top bin's mass moves up one step)."""
    x0 = np.asarray(x0, dtype="float64")
    mu = np.asarray(mu, dtype="float64")
    k = np.arange(probs.shape[1], dtype="float64")
    d = (mu - (x0 + step * (probs @ k))) / step
    n = np.floor(d)
    f = (d - n)[:, None]
    out = np.zeros((probs.shape[0], probs.shape[1] + 1))
    out[:, :-1] += (1.0 - f) * probs
    out[:, 1:] += f * probs
    return x0 + n * step, out


def td_pmf(mu: np.ndarray, share: np.ndarray, a: float, b: float,
           step: float = SKILL_STEP, bins: int = SKILL_BINS) -> tuple[np.ndarray, np.ndarray]:
    """RB-13 / TE-10: points = TD_POINTS * N + Y, N ~ Poisson(share * mu /
    TD_POINTS), Y ~ gamma with mean (1 - share) * mu and sd a + b * mean,
    discretised to the 0.1 lattice (bin k holds [(k - 1/2) step, (k + 1/2)
    step), bin 0 holds [0, step / 2)), then moved on the 0.1 grid
    (`on_grid_mean`) so the mean is exactly mu."""
    tdp = td_points()
    mu = np.maximum(np.asarray(mu, dtype="float64"), 0.0)
    share = np.asarray(share, dtype="float64")
    lam = share * mu / tdp
    my = (1.0 - share) * mu
    sy = a + b * my
    n = len(mu)
    probs = np.zeros((n, bins))
    edges = (np.arange(bins) + 0.5) * step
    ok = my > 1e-3
    if ok.any():
        shape = (my[ok] / sy[ok]) ** 2
        scale = sy[ok] ** 2 / my[ok]
        cdf = gammainc(shape[:, None], edges[None, :] / scale[:, None])
        py = np.diff(np.concatenate([np.zeros((ok.sum(), 1)), cdf], axis=1), axis=1)
        py[:, -1] += 1.0 - cdf[:, -1]
        probs[ok] = np.clip(py, 0.0, None)
    probs[~ok, 0] = 1.0
    # Poisson touchdowns: shift Y by TD_POINTS per touchdown
    shift = int(round(tdp / step))
    nn = np.arange(TD_MAX + 1)
    logp = nn[None, :] * np.log(np.maximum(lam, 1e-300))[:, None] - lam[:, None] \
        - np.array([math.lgamma(k + 1) for k in nn])[None, :]
    pois = np.where(lam[:, None] > 0, np.exp(logp), (nn[None, :] == 0).astype(float))
    pois[:, -1] += np.clip(1.0 - pois.sum(axis=1), 0.0, None)
    out = np.zeros_like(probs)
    for k in nn:
        off = k * shift
        if off >= bins:
            out[:, -1] += pois[:, k] * 1.0
            continue
        out[:, off:] += pois[:, k, None] * probs[:, :bins - off]
        out[:, -1] += pois[:, k] * probs[:, bins - off:].sum(axis=1)
    out /= out.sum(axis=1, keepdims=True)
    return on_grid_mean(np.zeros(n), out, step, mu)


def _phi_counts(rates: np.ndarray, values: np.ndarray, kappa: float, omega: np.ndarray) -> np.ndarray:
    """Characteristic function of sum_j values[j] * N_j: Poisson counts with
    means `rates` (n x J) (kappa 0), a multinomial over -1/kappa trials
    (kappa < 0) or gamma-mixed rates of shape 1/kappa (kappa > 0)."""
    z = np.exp(1j * omega[:, None] * values[None, :])            # (N, J)
    s = rates @ (1.0 - z).T                                     # (n, N)
    if kappa == 0.0:
        return np.exp(-s)
    if kappa < 0:
        trials = int(round(-1.0 / kappa))
        return (1.0 - s / trials) ** trials
    return (1.0 + kappa * s) ** (-1.0 / kappa)


def _invert(phi: np.ndarray, lo: int, n: int) -> np.ndarray:
    """PMF on lo .. lo + n - 1 from the characteristic function sampled at
    omega_k = 2 pi k / n."""
    k = np.arange(n)
    omega = 2 * np.pi * k / n
    shifted = phi * np.exp(-1j * omega * lo)[None, :]
    p = np.real(np.fft.fft(shifted, axis=1)) / n
    p = np.clip(p, 0.0, None)
    return p / p.sum(axis=1, keepdims=True)


def omega_grid(n: int = INT_N) -> np.ndarray:
    return 2 * np.pi * np.arange(n) / n


def kicker_pmf(mu: np.ndarray, rates: np.ndarray, values: np.ndarray,
               kappa: float) -> tuple[np.ndarray, np.ndarray]:
    """K-03: points = sum_j values[j] * N_j over the kicking event groups,
    every group's rate scaled by one factor c so the mean is exactly mu (a
    higher mean is more kicks, the tier mix kept). A multinomial row whose
    scaled events reach its trials falls back to Poisson."""
    mu = np.maximum(np.asarray(mu, dtype="float64"), 0.0)
    e0 = rates @ values
    c = np.where(e0 > 0, mu / np.where(e0 > 0, e0, 1.0), 0.0)
    scaled = rates * c[:, None]
    omega = omega_grid()
    phi = _phi_counts(scaled, values, kappa, omega)
    if kappa < 0:
        trials = int(round(-1.0 / kappa))
        bad = scaled.sum(axis=1) >= 0.95 * trials
        if bad.any():
            phi[bad] = _phi_counts(scaled[bad], values, 0.0, omega)
    probs = _invert(phi, INT_LO, INT_N)
    # exact mean on whole points (the FFT window holds all but ~1e-12 of the
    # mass, so this moves almost nothing)
    return on_grid_mean(np.full(len(mu), float(INT_LO)), probs, 1.0, mu)


def defense_pmf(mu: np.ndarray, implied: np.ndarray, rates: np.ndarray, values: np.ndarray,
                pa_sd: float) -> tuple[np.ndarray, np.ndarray]:
    """DEF-03: points = tier(PA) + sum_j values[j] * N_j. The opponent's
    points PA are the implied total plus a normal spread `pa_sd`, rounded to
    whole points (everything below 0.5 is a shutout, everything at PA_MAX or
    more is PA_MAX); the tier value comes from DEFENSE_SCORING. The counts
    are independent Poisson. The whole distribution is then moved so its
    mean is exactly mu, staying on whole points (`on_grid_mean`)."""
    mu = np.asarray(mu, dtype="float64")
    implied = np.asarray(implied, dtype="float64")
    tiers = tier_points()
    k = np.arange(PA_MAX + 1)
    upper = ndtr(((k + 0.5)[None, :] - implied[:, None]) / pa_sd)
    upper[:, -1] = 1.0
    ppa = np.diff(np.concatenate([np.zeros((len(mu), 1)), upper], axis=1), axis=1)
    tv = np.unique(tiers)
    ptier = np.stack([ppa[:, tiers == v].sum(axis=1) for v in tv], axis=1)   # (n, T)
    omega = omega_grid()
    phi_t = ptier @ np.exp(1j * omega[None, :] * tv[:, None])               # (n, N)
    phi = phi_t * _phi_counts(rates, values, 0.0, omega)
    probs = _invert(phi, INT_LO, INT_N)
    return on_grid_mean(np.full(len(mu), float(INT_LO)), probs, 1.0, mu)


# ------------------------------------------------------------------ fitting

def fit_grid(values, score) -> tuple[float, dict]:
    """The grid value with the lowest score (ties: the first), and every score."""
    scores = {float(v): float(score(v)) for v in values}
    best = min(scores, key=lambda v: (scores[v], list(scores).index(v)))
    return best, scores


def fit_spread_ab(score, start=(1.0, 0.4), bounds=((0.05, 8.0), (0.0, 1.5))) -> tuple[float, float, float]:
    """(a, b, score) minimising score(a, b) by Nelder-Mead inside the bounds."""
    res = minimize(lambda v: score(float(v[0]), float(v[1])), np.asarray(start, dtype=float),
                   method="Nelder-Mead", bounds=bounds,
                   options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 400})
    return float(res.x[0]), float(res.x[1]), float(res.fun)
