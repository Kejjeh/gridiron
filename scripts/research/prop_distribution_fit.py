"""Fit the shapes the market projection reads a prop line with.

    PYTHONPATH=src python scripts/research/prop_distribution_fit.py [--save]

A prop is a line and a probability: P(X > line) = p. Turning that into the
EXPECTED stat needs the shape of X's distribution around its expectation,
one parameter per stat (`gridiron.props`). This fits those parameters on
the 2019-2025 regular seasons:

  expectation  Sleeper's pre-game projection of the stat for the
               player-week (the rotowire feed cached under
               data/research/cache/sleeper_proj_<season>/), scaled per stat
               so its mean matches the outcomes' (`calibration` below);
               joined to nflverse ids through the one crosswalk (rule #3)
  outcome      the nflverse weekly box score; a player who took an
               offensive snap (a kicker: a special-teams snap) and has no
               box-score row is a ZERO, so the lower tail is not dropped
  population   the players a book would post the prop for: a floor on the
               projected value, per stat (`POPULATION`)

Yardage and kicking points (gamma, one shape per stat). The projection
reads the mean off a line through mean = line / q(1 - p), where q is the
unit-mean gamma's quantile, so a RELATIVE error in q at the level a line
sits at is exactly the relative error of the mean it implies. De-vigged
main lines sit near P(over) = 0.5, so the shape minimises the squared log
error between the gamma's quantiles and the outcomes' quantiles of
outcome / expectation over the band 0.35-0.65 (`BAND`). Matching the
median alone was tried first and rejected: passing yards are nearly
symmetric but wide, which no gamma is, and the median-only shape (26)
missed the held-out 25/75% quantiles by 0.21. The moment and
censored-likelihood shapes are reported beside the fitted one.

Counts (negative binomial, one size per stat): size by moments,
1/size = sum((x - mu)^2 - mu) / sum(mu^2). The Poisson is used instead
when that is not positive, or when the negative binomial does not have the
lower held-out log loss in more than half of the seasons it is scored on.

Anytime TD: lambda = -ln(1 - p) (a Poisson) is fixed by design; its
calibration on projected touchdowns is reported, not fitted.

Held out: each season in turn is fitted on the other six and scored: for a
gamma the share of outcomes under the fitted quantiles at 0.25, the band
and 0.75; for a count the predicted vs observed P(over) at five half-lines
around the expectation (mean + c * sqrt(mean), c = -1 ... 1), with Brier
and log loss for both families. Only the shape (or size) transfers to a
market, whose line brings its own location, so the held-out season keeps
its own scale: Sleeper's season-level bias (its passing-yards scale ran
0.90-1.03 by season) is the projection's, not the market's, and scoring
it against the shape would blame the shape for it.

The expectation is a projection, not the market: a sharper expectation
leaves less spread, so these shapes are, if anything, too WIDE for a
market, which overstates a yardage mean a little (mean/median falls as the
shape rises). docs/research/PROPS_COLLECTOR.md states the numbers.

--save writes src/gridiron/props_params.json (parameters with evidence).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron import props as P
from gridiron.ids import normalize_id
from gridiron.paths import RESEARCH_CACHE

SEASONS = tuple(range(2019, 2026))
CROSSWALK = RESEARCH_CACHE / "crosswalk_db_playerids.csv"

#: stat -> (family, expectation from Sleeper's projected stats, population floor)
POPULATION = {
    "passing_yards": ("gamma", lambda s: s.get("pass_yd"), ("pass_yd", 150.0)),
    "rushing_yards": ("gamma", lambda s: s.get("rush_yd"), ("rush_yd", 15.0)),
    "receiving_yards": ("gamma", lambda s: s.get("rec_yd"), ("rec_yd", 15.0)),
    "kicking_points": ("gamma", lambda s: _kick(s), ("kick_pts", 4.0)),
    "passing_tds": ("count", lambda s: s.get("pass_td"), ("pass_yd", 150.0)),
    "attempts": ("count", lambda s: s.get("pass_att"), ("pass_yd", 150.0)),
    "completions": ("count", lambda s: s.get("pass_cmp"), ("pass_yd", 150.0)),
    "passing_interceptions": ("count", lambda s: s.get("pass_int"), ("pass_yd", 150.0)),
    "carries": ("count", lambda s: s.get("rush_att"), ("rush_att", 4.0)),
    "receptions": ("count", lambda s: s.get("rec"), ("rec", 1.5)),
    "fg_made": ("count", lambda s: s.get("fgm"), ("fgm", 0.8)),
    "scrimmage_tds": ("anytime", lambda s: _tds(s), ("tds", 0.1)),
}
#: Where de-vigged main lines sit: the quantile levels the gamma shape is fit on.
BAND = (0.35, 0.4, 0.45, 0.5, 0.55, 0.6, 0.65)
#: Reported held out: the band and one level each side of it.
QUANTILES = (0.25, *BAND, 0.75)
#: A held-out season needs this many rows to be scored at all.
MIN_FOLD_ROWS = 50
#: Count check lines: floor(mean + c * sqrt(mean)) + 0.5.
COUNT_LINE_OFFSETS = (-1.0, -0.5, 0.0, 0.5, 1.0)


def _kick(s):
    if s.get("fgm") is None and s.get("xpm") is None:
        return None
    return P.BOOK_KICKING_POINTS["fg"] * float(s.get("fgm") or 0) \
        + P.BOOK_KICKING_POINTS["pat"] * float(s.get("xpm") or 0)


def _tds(s):
    if s.get("rush_td") is None and s.get("rec_td") is None:
        return None
    return float(s.get("rush_td") or 0) + float(s.get("rec_td") or 0)


def _floor_value(stats: dict, key: str):
    if key == "kick_pts":
        return _kick(stats)
    if key == "tds":
        return _tds(stats)
    return stats.get(key)


def load_projections(sleeper_to_gsis: dict[str, str]) -> pd.DataFrame:
    """One row per (season, week, gsis_id): Sleeper's projected stats."""
    rows, unmapped, bad = [], 0, 0
    for season in SEASONS:
        folder = RESEARCH_CACHE / f"sleeper_proj_{season}"
        for path in sorted(folder.glob("w*_*.json")):
            pos = path.stem.split("_", 1)[1]
            if pos not in ("QB", "RB", "WR", "TE", "K"):
                continue
            try:
                blob = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                bad += 1
                continue
            for rec in blob or []:
                gid = sleeper_to_gsis.get(normalize_id(rec.get("player_id")))
                if not gid:
                    unmapped += 1
                    continue
                rows.append({"season": season, "week": int(rec.get("week") or 0),
                             "gsis_id": gid, "proj_pos": pos, "stats": rec.get("stats") or {}})
    print(f"  projections: {len(rows)} player-weeks; {unmapped} without a gsis id in the "
          f"crosswalk; {bad} unreadable file(s)", file=sys.stderr)
    frame = pd.DataFrame(rows)
    return frame.drop_duplicates(["season", "week", "gsis_id"], keep="first")


def load_outcomes() -> pd.DataFrame:
    """nflverse weekly box scores, plus zero rows for players with a snap
    and no box-score row (regular season)."""
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    import nflreadpy as nfl
    from gridiron.ids import Crosswalk
    weekly = nfl.load_player_stats(list(SEASONS), summary_level="week").to_pandas()
    weekly = weekly.loc[weekly["season_type"] == "REG"].copy()
    weekly["gsis_id"] = weekly["player_id"].map(normalize_id)
    cols = ["passing_yards", "passing_tds", "attempts", "completions",
            "passing_interceptions", "rushing_yards", "carries", "receptions",
            "receiving_yards", "rushing_tds", "receiving_tds", "fg_made", "pat_made"]
    out = weekly[["season", "week", "gsis_id", *cols]].copy()
    out[cols] = out[cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    snaps = nfl.load_snap_counts(list(SEASONS)).to_pandas()
    snaps = snaps.loc[snaps["game_type"] == "REG"]
    cw = Crosswalk.from_csv(CROSSWALK)
    pfr_to_gsis = {v: k for k, v in cw._g2p.items()}        # noqa: SLF001 (research read)
    snaps = snaps.assign(gsis_id=snaps["pfr_player_id"].map(lambda p: pfr_to_gsis.get(
        normalize_id(p), "")))
    played = snaps.loc[(snaps["gsis_id"] != "")
                       & ((snaps["offense_snaps"].fillna(0) > 0)
                          | ((snaps["position"] == "K") & (snaps["st_snaps"].fillna(0) > 0)))]
    have = set(zip(out["season"], out["week"], out["gsis_id"]))
    zeros = [{"season": int(s), "week": int(w), "gsis_id": g, **dict.fromkeys(cols, 0.0)}
             for s, w, g in zip(played["season"], played["week"], played["gsis_id"])
             if (int(s), int(w), g) not in have]
    zeros = pd.DataFrame(zeros).drop_duplicates(["season", "week", "gsis_id"])
    print(f"  outcomes: {len(out)} box-score rows + {len(zeros)} snap-only zero rows",
          file=sys.stderr)
    out = pd.concat([out, zeros], ignore_index=True)
    out["kicking_points"] = (P.BOOK_KICKING_POINTS["fg"] * out["fg_made"]
                             + P.BOOK_KICKING_POINTS["pat"] * out["pat_made"])
    out["scrimmage_tds"] = out["rushing_tds"] + out["receiving_tds"]
    return out


def stat_frame(proj: pd.DataFrame, outcomes: pd.DataFrame, stat: str) -> pd.DataFrame:
    family, expect, (floor_key, floor) = POPULATION[stat]
    exp = proj["stats"].map(expect)
    gate = proj["stats"].map(lambda s: _floor_value(s, floor_key))
    keep = proj.assign(expect=pd.to_numeric(exp, errors="coerce"),
                       gate=pd.to_numeric(gate, errors="coerce"))
    keep = keep.loc[(keep["gate"] >= floor) & (keep["expect"] > 0)]
    if stat in ("kicking_points", "fg_made"):
        keep = keep.loc[keep["proj_pos"] == "K"]
    elif stat in ("passing_yards", "passing_tds", "attempts", "completions",
                  "passing_interceptions"):
        keep = keep.loc[keep["proj_pos"] == "QB"]
    else:
        keep = keep.loc[keep["proj_pos"] != "K"]
    m = keep.merge(outcomes[["season", "week", "gsis_id", stat]],
                   on=["season", "week", "gsis_id"], how="inner")
    return m[["season", "week", "gsis_id", "proj_pos", "expect", stat]].rename(
        columns={stat: "x"}).reset_index(drop=True)


# ----------------------------------------------------------------- gamma

def unit_quantile(shape: float, q: float) -> float:
    """The q-quantile of the unit-mean gamma with this shape."""
    return P.gamma_q_inverse(shape, 1.0 - q) / shape


def _golden(f, lo: float, hi: float, iters: int = 60) -> float:
    """Minimise f on [lo, hi] (log-shape scale)."""
    g = (math.sqrt(5) - 1) / 2
    x1, x2 = hi - g * (hi - lo), lo + g * (hi - lo)
    f1, f2 = f(x1), f(x2)
    for _ in range(iters):
        if f1 < f2:
            hi, x2, f2 = x2, x1, f1
            x1 = hi - g * (hi - lo)
            f1 = f(x1)
        else:
            lo, x1, f1 = x1, x2, f2
            x2 = lo + g * (hi - lo)
            f2 = f(x2)
    return 0.5 * (lo + hi)


def band_shape(z: np.ndarray) -> float:
    """The shape whose unit-mean quantiles best match the outcomes' over BAND
    (least squares on log quantiles)."""
    target = np.log(np.quantile(z, BAND))

    def loss(log_k: float) -> float:
        k = math.exp(log_k)
        model = np.log([unit_quantile(k, q) for q in BAND])
        return float(((model - target) ** 2).sum())

    return math.exp(_golden(loss, math.log(0.2), math.log(500.0)))


def gamma_fit(df: pd.DataFrame) -> dict:
    a = float((df["x"] / df["expect"]).mean())          # gamma MLE of a scale factor
    z = (df["x"] / (a * df["expect"])).to_numpy()
    shape = band_shape(z)
    var = float(np.var(z))
    emp = np.quantile(z, BAND)
    rel = [unit_quantile(shape, q) / e - 1.0 for q, e in zip(BAND, emp)]
    return {"shape": shape, "calibration": a, "median_ratio": float(np.median(z)),
            "band_error": {f"{q:.2f}": round(r, 4) for q, r in zip(BAND, rel)},
            "shape_moments": (1.0 / var) if var > 0 else None,
            "shape_mle": gamma_mle(z, (0.5 / (a * df["expect"])).to_numpy()),
            "zero_share": float((df["x"] <= 0).mean())}


def gamma_mle(z: np.ndarray, censor: np.ndarray) -> float:
    """Shape MLE for unit-mean gamma outcomes; an outcome at or below zero is
    left-censored at half a unit of the stat."""
    pos = z > 0
    zp, cens = z[pos], censor[~pos]
    slog, ssum = float(np.log(zp).sum()), float(zp.sum())

    def ll(k: float) -> float:
        val = len(zp) * (k * math.log(k) - math.lgamma(k)) + (k - 1) * slog - k * ssum
        return val + sum(math.log(max(P.gamma_p(k, k * c), 1e-300)) for c in cens)

    return math.exp(_golden(lambda lk: -ll(math.exp(lk)), math.log(0.1), math.log(200.0)))


def gamma_coverage(df: pd.DataFrame, shape: float, a: float) -> dict[str, float]:
    z = (df["x"] / (a * df["expect"])).to_numpy()
    u = np.array([P.gamma_p(shape, shape * v) if v > 0 else 0.0 for v in z])
    return {f"{q:.2f}": round(float((u <= q).mean()), 4) for q in QUANTILES}


# ----------------------------------------------------------------- counts

def count_fit(df: pd.DataFrame) -> dict:
    a = float(df["x"].sum() / df["expect"].sum())        # Poisson MLE of a scale factor
    mu = a * df["expect"]
    inv = float((((df["x"] - mu) ** 2) - mu).sum() / (mu ** 2).sum())
    return {"calibration": a, "inv_size_moments": inv,
            "size_moments": (1.0 / inv) if inv > 0 else None,
            "var_over_mean": float(((df["x"] - mu) ** 2).sum() / mu.sum())}


def count_check(df: pd.DataFrame, a: float, size: float | None) -> dict:
    """P(over) at half-lines around the expectation, held out."""
    mu_all = (a * df["expect"]).to_numpy()
    x_all = df["x"].to_numpy()
    ps, ys = [], []
    for c in COUNT_LINE_OFFSETS:
        lines = np.floor(mu_all + c * np.sqrt(mu_all)) + 0.5
        ok = lines > 0
        ps.extend(P.count_over(L, m, size) for L, m in zip(lines[ok], mu_all[ok]))
        ys.extend((x_all[ok] > lines[ok]).astype(float))
    p, y = np.array(ps), np.array(ys)
    eps = 1e-9
    pc = np.clip(p, eps, 1 - eps)
    return {"stated": round(float(p.mean()), 4), "observed": round(float(y.mean()), 4),
            "brier": round(float(((p - y) ** 2).mean()), 5),
            "logloss": round(float(-(y * np.log(pc) + (1 - y) * np.log(1 - pc)).mean()), 5)}


def anytime_check(df: pd.DataFrame) -> dict:
    a = float(df["x"].sum() / df["expect"].sum())
    lam = a * df["expect"]
    p = 1 - np.exp(-lam)
    y = (df["x"] >= 1).astype(float)
    bins = pd.qcut(lam, 5, duplicates="drop")
    by = {str(k): {"n": int(len(g)), "stated": round(float(p[g.index].mean()), 4),
                   "observed": round(float(y[g.index].mean()), 4)}
          for k, g in df.groupby(bins, observed=True)}
    return {"family": "poisson", "n": int(len(df)), "calibration": a,
            "stated": round(float(p.mean()), 4), "observed": round(float(y.mean()), 4),
            "by_expected_td_quintile": by}


# ----------------------------------------------------------------- main

def fit_all(proj: pd.DataFrame, outcomes: pd.DataFrame) -> dict:
    gamma, counts = {}, {}
    anytime = None
    for stat, (family, _e, (fkey, floor)) in POPULATION.items():
        df = stat_frame(proj, outcomes, stat)
        pop = f"Sleeper projected {fkey} >= {floor:g}; n={len(df)}"
        if family == "anytime":
            anytime = {**anytime_check(df), "population": pop}
            print(f"{stat}: Poisson P(TD) stated {anytime['stated']:.3f} vs observed "
                  f"{anytime['observed']:.3f} (n={len(df)})")
            continue
        folds = {}
        scored = [s for s in SEASONS if (df["season"] == s).sum() >= MIN_FOLD_ROWS]
        if family == "gamma":
            full = gamma_fit(df)
            for s in scored:
                tr, te = df.loc[df["season"] != s], df.loc[df["season"] == s]
                f = gamma_fit(tr)
                # Only the SHAPE transfers to a market (a line brings its own
                # location), so the held-out season keeps its own scale: the
                # projection's season bias (scale 0.90-1.03) is not the market's.
                a_te = float((te["x"] / te["expect"]).mean())
                folds[str(s)] = {"shape": round(f["shape"], 4),
                                 "scale_train": round(f["calibration"], 4),
                                 "scale_test": round(a_te, 4),
                                 "coverage": gamma_coverage(te, f["shape"], a_te),
                                 "n": int(len(te))}
            band = {f"{q:.2f}" for q in BAND}
            worst_band = max(abs(v - float(q)) for f in folds.values()
                             for q, v in f["coverage"].items() if q in band)
            worst = max(abs(v - float(q)) for f in folds.values()
                        for q, v in f["coverage"].items())
            gamma[stat] = {"shape": round(full["shape"], 4), "population": pop, "n": len(df),
                           "calibration": round(full["calibration"], 4),
                           "median_over_mean": round(full["median_ratio"], 4),
                           "band_quantile_error": full["band_error"],
                           "shape_moments": round(full["shape_moments"], 4),
                           "shape_mle_censored": round(full["shape_mle"], 4),
                           "zero_share": round(full["zero_share"], 4),
                           "held_out_worst_band_coverage_error": round(worst_band, 4),
                           "held_out_worst_coverage_error": round(worst, 4),
                           "held_out": folds}
            worst_q = max(abs(v) for v in full["band_error"].values())
            print(f"{stat}: shape {full['shape']:.3f} (moments {full['shape_moments']:.3f}, "
                  f"MLE {full['shape_mle']:.3f}); in-sample band quantile error <= "
                  f"{worst_q:.3f}; held-out coverage error band {worst_band:.3f}, "
                  f"0.25/0.75 included {worst:.3f}; n={len(df)}")
            continue
        full = count_fit(df)
        nb_wins = 0
        for s in scored:
            tr, te = df.loc[df["season"] != s], df.loc[df["season"] == s]
            f = count_fit(tr)
            a_te = float(te["x"].sum() / te["expect"].sum())   # the season's own scale
            nb = count_check(te, a_te, f["size_moments"])
            po = count_check(te, a_te, None)
            better = f["size_moments"] is not None and nb["logloss"] < po["logloss"]
            nb_wins += int(better)
            folds[str(s)] = {"size": None if f["size_moments"] is None
                             else round(f["size_moments"], 3),
                             "negative_binomial": nb, "poisson": po, "n": int(len(te))}
        use_nb = full["size_moments"] is not None and nb_wins > len(scored) / 2
        counts[stat] = {"size": round(full["size_moments"], 3) if use_nb else None,
                        "family": "negative_binomial" if use_nb else "poisson",
                        "population": pop, "n": len(df),
                        "calibration": round(full["calibration"], 4),
                        "var_over_mean": round(full["var_over_mean"], 4),
                        "size_moments": None if full["size_moments"] is None
                        else round(full["size_moments"], 3),
                        "held_out_seasons": len(scored),
                        "held_out_negative_binomial_wins": nb_wins, "held_out": folds}
        print(f"{stat}: {counts[stat]['family']} (size {counts[stat]['size']}); "
              f"var/mean {full['var_over_mean']:.3f}; NB lower held-out log loss in "
              f"{nb_wins}/{len(scored)}; n={len(df)}")
    return {"gamma": gamma, "counts": counts, "anytime_td": anytime}


def compact(fit: dict) -> dict:
    """Keep the committed JSON small: per-fold detail reduced to the
    numbers the doc cites."""
    out = json.loads(json.dumps(fit))
    for v in out["gamma"].values():
        v["held_out"] = {s: {"shape": f["shape"], "scale_test": f["scale_test"],
                             "coverage": f["coverage"]}
                         for s, f in v["held_out"].items()}
    for v in out["counts"].values():
        chosen = "negative_binomial" if v["size"] else "poisson"
        v["held_out"] = {s: {"size": f["size"],
                             "nb_logloss": f["negative_binomial"]["logloss"],
                             "poisson_logloss": f["poisson"]["logloss"],
                             "stated": f[chosen]["stated"],
                             "observed": f[chosen]["observed"]}
                         for s, f in v["held_out"].items()}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", type=Path, default=P.PARAMS_PATH)
    args = ap.parse_args(argv)
    from gridiron.ids import Crosswalk
    cw = Crosswalk.from_csv(CROSSWALK)
    proj = load_projections(cw._s2g)                        # noqa: SLF001 (research read)
    outcomes = load_outcomes()
    fit = fit_all(proj, outcomes)
    blob = {"meta": {
        "fitted": date.today().isoformat(),
        "script": "scripts/research/prop_distribution_fit.py",
        "seasons": list(SEASONS),
        "expectation": "Sleeper's pre-game projection of the stat (rotowire feed), scaled "
                       "per stat by `calibration` so its mean matches the outcomes'",
        "outcome": "nflverse weekly box score, regular season; a snap with no box-score "
                   "row is a zero",
        "gamma": "one shape per stat: least squares on log quantiles of outcome / "
                 "expectation over the band 0.35-0.65 where de-vigged main lines sit "
                 "(a relative quantile error there is the relative error of the mean)",
        "counts": "negative-binomial size by moments; Poisson when not positive or when "
                  "the negative binomial's held-out log loss (five lines around the "
                  "expectation) is not lower in more than half of the scored seasons",
        "caveat": "a projection is a weaker expectation than a market, so these shapes "
                  "are if anything too wide for a market line",
    }, **compact(fit)}
    if args.save:
        args.out.write_text(json.dumps(blob, indent=1) + "\n", encoding="utf-8")
        print(f"[fit] wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
