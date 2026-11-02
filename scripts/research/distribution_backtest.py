"""Outcome distributions against the page's normal, out of sample (MODEL_ROADMAP.md, workstream D).

    PYTHONPATH=src python scripts/research/distribution_backtest.py --block RB-13 [--block TE-10 ...]
    PYTHONPATH=src python scripts/research/distribution_backtest.py --block RB-13 --coverage-only

The harness for the roadmap's DISTRIBUTION ideas (kind D: RB-13, TE-10, K-03,
DEF-03). They are not mean features, so block_runner.py's bar does not apply.
This script imports block_runner.py for the frozen tables, the season folds,
the Context, the leak check and the integrity checks, and
pwin_calibration.py for its synthetic leagues; it changes neither.

THE PAGE'S DISTRIBUTION, the comparator: a normal around the page's mean.
  mean  the shipped ridge refit with the row's season held out, exactly as
        block_runner.py's `shipped` (the shipped columns, lambda 5; K: K_ADV;
        DEF: DEF_ADV), floored at 0 for QB/RB/WR/TE/K as the runner does.
  sd    QB/RB/WR/TE/K: the baseline's sd, max(baseline x WEEKLY_CV, SD_FLOOR)
        (gridiron.projection; the page swaps in the advanced or stacked mean
        and keeps this sd), which is the frozen tables' `sd` column;
        DEF: the advanced model's meta resid_sd['DEF']
        (AdvancedContext.defense_projection).

A CANDIDATE is a block module with KIND = "distribution" (the contract is in
scripts/research/blocks/_p7_distributions.py): `build(ctx)` gives its
pre-game inputs (coverage and leak check as for any block), `fit(train,
crps)` its spread parameters on the training seasons, `pmf(rows, params)`
each row's distribution on a lattice. It keeps the page's mean.

Rows and folds: block_runner.py's. Weeks 4-18 of 2019-2025, players Sleeper
projected (the K and DEF tables for those positions), seven season folds
(a block may declare FOLDS). For each test season s:
  1. means: every row of s from models fit on the other six seasons; every
     TRAINING row of the candidate's position from models fit on the five
     seasons other than s and its own (nested), so the spread is fitted on
     out-of-sample means like the ones it is scored on;
  2. the candidate's `fit` on the training rows; its `pmf` on the test rows
     with `actual` removed;
  3. per row: CRPS, pinball loss at the 10/50/90% quantiles, and whether the
     actual falls inside the 10-90% interval;
  4. P(win): pwin_calibration.py's synthetic 12-team leagues for season s
     (its dealing, seeds and slot pools; weeks 4-17, REPS leagues a week),
     dealt from the same rows with the fold's means and checked against its
     own `week_matchups`. The page's P(win) is Phi(d / s). The candidate's
     gives the touched position's starters their distributions, the rest
     the page's normal: P(win) = sum_d P(D = d) Phi((d + m) / r), exact on
     the lattice (D: the touched starters' margin, m and r: the others'
     margin mean and sd). Brier and log loss; a week-clustered bootstrap
     of the Brier change.

References, reported and never part of the bar:
  fitted normal    N(mu, a + b mu), (a, b) by minimum CRPS on the same
                   training rows: is a gain the shape, or a refitted spread?
  lattice normal   K and DEF: the page's normal put on the integer lattice:
                   how much is only knowing that the points are whole?

THE BAR, pre-registered in each block's doc: a lower mean CRPS than the
page's normal in EVERY fold for the position, AND the pooled P(win) Brier
over the folds' leagues not worse, both decided on unrounded numbers (the
JSON's fold means are rounded for reading). Passing is necessary, not
sufficient (ground rule 3: record-only for two live weeks before it may
serve).

Integrity as block_runner.py: the pre-registration, the module, every helper
(blocks/_*.py) and this harness must be committed and unchanged; each scored
run writes docs/research/blocks/results/<ID>.json and appends one line to
runs/<ID>.csv (the runner's ledger fields). Joins are on ids (rule #3).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.signal import fftconvolve
from scipy.special import ndtr, ndtri

import block_runner as BR
import pwin_calibration as PW
from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import REPO_ROOT

HARNESS = Path(__file__).resolve()
QUANTILES = (0.1, 0.5, 0.9)
LEAGUE_WEEKS = range(4, 18)       # pwin_calibration.py's weeks 4..17
REPS = 32                         # replicate leagues a week (PWIN_CALIBRATION.md's run)
BOOT = 1000
BOOT_SEED = 20261102
SKILL = BR.SKILL
SQRT_PI = math.sqrt(math.pi)


# ------------------------------------------------------------------ scores

def normal_crps(mu, sd, y) -> np.ndarray:
    """Closed-form CRPS of N(mu, sd^2) at y."""
    mu, sd, y = (np.asarray(v, dtype="float64") for v in (mu, sd, y))
    z = (y - mu) / sd
    pdf = np.exp(-0.5 * z * z) / math.sqrt(2 * math.pi)
    return sd * (z * (2 * ndtr(z) - 1) + 2 * pdf - 1 / SQRT_PI)


def lattice_crps(x0, probs, y, step, chunk: int = 4000) -> np.ndarray:
    """CRPS of the lattice distribution P(X = x0 + k step) = probs[k] at y:
    E|X - y| - E|X - X'| / 2, with E|X - X'| = 2 step sum_k F_k (1 - F_k)."""
    x0, y = np.asarray(x0, dtype="float64"), np.asarray(y, dtype="float64")
    out = np.empty(len(y))
    grid = np.arange(probs.shape[1]) * step
    for i in range(0, len(y), chunk):
        p = probs[i:i + chunk]
        f = np.cumsum(p, axis=1)
        x = x0[i:i + chunk, None] + grid[None, :]
        out[i:i + chunk] = ((p * np.abs(x - y[i:i + chunk, None])).sum(axis=1)
                            - step * (f * (1 - f)).sum(axis=1))
    return out


def lattice_quantile(x0, probs, step, tau: float) -> np.ndarray:
    f = np.cumsum(probs, axis=1)
    return np.asarray(x0, dtype="float64") + step * np.argmax(f >= tau - 1e-12, axis=1)


def pinball(y, q, tau: float) -> np.ndarray:
    d = np.asarray(y, dtype="float64") - np.asarray(q, dtype="float64")
    return np.where(d >= 0, tau * d, (tau - 1) * d)


def lattice_normal(mu, sd, step: float = 1.0, lo: int = -40, n: int = 200):
    """The normal on the integer lattice: P(X = k) = Phi((k + 1/2 - mu) / sd) -
    Phi((k - 1/2 - mu) / sd), k = lo .. lo + n - 1, tails folded in."""
    mu, sd = np.asarray(mu, dtype="float64"), np.asarray(sd, dtype="float64")
    k = lo + np.arange(n) * step
    up = ndtr((k[None, :] + step / 2 - mu[:, None]) / sd[:, None])
    up[:, -1] = 1.0
    p = np.diff(np.concatenate([np.zeros((len(mu), 1)), up], axis=1), axis=1)
    return np.full(len(mu), float(lo)), p


def scores_normal(mu, sd, y) -> dict:
    out = {"crps": normal_crps(mu, sd, y)}
    q = {t: mu + sd * ndtri(t) for t in QUANTILES}
    for t in QUANTILES:
        out[f"pinball_{int(t * 100)}"] = pinball(y, q[t], t)
    out["inside_10_90"] = ((y >= q[0.1]) & (y <= q[0.9])).astype(float)
    return out


def scores_lattice(x0, probs, step, y) -> dict:
    out = {"crps": lattice_crps(x0, probs, y, step)}
    q = {t: lattice_quantile(x0, probs, step, t) for t in QUANTILES}
    for t in QUANTILES:
        out[f"pinball_{int(t * 100)}"] = pinball(y, q[t], t)
    out["inside_10_90"] = ((y >= q[0.1] - 1e-9) & (y <= q[0.9] + 1e-9)).astype(float)
    return out


def summarize(sc: dict) -> dict:
    return {k: round(float(np.mean(v)), 5) for k, v in sc.items()}


def fit_normal_ab(mu, y) -> tuple[float, float]:
    """(a, b) for sd = a + b mu by minimum mean CRPS (the fitted-normal reference)."""
    mu, y = np.asarray(mu, dtype="float64"), np.asarray(y, dtype="float64")
    f = lambda v: float(normal_crps(mu, np.maximum(v[0] + v[1] * mu, 0.1), y).mean())  # noqa: E731
    res = minimize(f, np.array([1.5, 0.4]), method="Nelder-Mead",
                   bounds=((0.05, 15.0), (0.0, 1.5)),
                   options={"xatol": 1e-4, "fatol": 1e-7, "maxiter": 600})
    return float(res.x[0]), float(res.x[1])


# ------------------------------------------------------------------- means

def _fit_predict(train: pd.DataFrame, test: pd.DataFrame, cols, floor: bool) -> np.ndarray:
    m = A.Ridge.fit(train, train["actual"], cols)
    pred = BR.predict(m, test)
    return np.maximum(pred, 0.0) if floor else pred


def position_frames(skill, special) -> dict[str, dict[int, pd.DataFrame]]:
    out = {p: {s: t.loc[t["position"] == p].reset_index(drop=True) for s, t in skill.items()}
           for p in SKILL}
    out["K"] = {s: t.reset_index(drop=True) for s, t in special["K"].items()}
    out["DEF"] = {s: t.reset_index(drop=True) for s, t in special["DEF"].items()}
    return out


def mean_columns(base) -> dict[str, tuple[list[str], bool]]:
    return {**{p: (base[p], True) for p in SKILL}, "K": (BR.K_ADV, True), "DEF": (BR.DEF_ADV, False)}


def fold_means(frames, cols, pos: str, fit_seasons, predict_season) -> np.ndarray:
    c, floor = cols[pos]
    train = pd.concat([frames[pos][t] for t in fit_seasons], ignore_index=True)
    return _fit_predict(train, frames[pos][predict_season], c, floor)


# ------------------------------------------------------------------ leagues

def league_universe(rows: dict[str, pd.DataFrame]) -> dict[int, dict[str, pd.DataFrame]]:
    """pwin_calibration.universe on these rows: week -> position -> rows
    (key, mean, sd, actual, _row) best-first, ties by key."""
    cols = ["week", "key", "position", "mean", "sd", "actual", "_row"]
    allrows = pd.concat([rows[p][cols] for p in ("QB", "RB", "WR", "TE", "K", "DEF")],
                        ignore_index=True)
    allrows = allrows.loc[allrows["week"].isin(list(LEAGUE_WEEKS))]
    allrows = allrows.dropna(subset=["mean", "sd", "actual"])
    allrows = allrows.drop_duplicates(subset=["week", "position", "key"])
    out = {}
    for w, g in allrows.groupby("week"):
        g = g.sort_values(["mean", "key"], ascending=[False, True], kind="mergesort")
        out[int(w)] = {p: gp.reset_index(drop=True) for p, gp in g.groupby("position")}
    return out


def deal(season: int, week: int, pos: dict[str, pd.DataFrame], reps: int) -> list[dict]:
    """pwin_calibration.week_matchups' leagues, returning who started for whom
    (the same generator and the same draws in the same order): per matchup,
    each side's starters as (position, row of pos[position]) pairs."""
    out = []
    for rep in range(reps):
        rng = np.random.default_rng([PW.SEED, season, week, rep, 0])
        teams: list[list[tuple[str, int]]] = [[] for _ in range(PW.TEAMS)]
        used: dict[str, set] = {"RB": set(), "WR": set(), "TE": set()}
        for p in ("QB", "RB", "WR", "TE", "K", "DEF"):
            top = pos[p].iloc[:PW.TOP_N[p]]
            need = PW.TEAMS * PW.PER_TEAM[p]
            if len(top) < need:
                raise SystemExit(f"{season} wk{week}: only {len(top)} {p} rows, need {need}")
            idx = rng.permutation(len(top))[:need].reshape(PW.TEAMS, PW.PER_TEAM[p])
            for t in range(PW.TEAMS):
                teams[t] += [(p, int(i)) for i in idx[t]]
            if p in used:
                used[p] = set(top.iloc[idx.ravel()]["key"])
        flex = pd.concat([pos[p].loc[~pos[p]["key"].isin(used[p])].assign(
            _p=p, _i=np.flatnonzero(~pos[p]["key"].isin(used[p]).to_numpy()))
            for p in ("RB", "WR", "TE")])
        flex = flex.sort_values(["mean", "key"], ascending=[False, True],
                                kind="mergesort").iloc[:PW.FLEX_POOL].reset_index(drop=True)
        fidx = rng.permutation(len(flex))[:PW.TEAMS * PW.FLEX_PER_TEAM].reshape(
            PW.TEAMS, PW.FLEX_PER_TEAM)
        for t in range(PW.TEAMS):
            teams[t] += [(str(flex.at[int(i), "_p"]), int(flex.at[int(i), "_i"])) for i in fidx[t]]
        order = rng.permutation(PW.TEAMS)
        for j in range(PW.TEAMS // 2):
            a, b = int(order[2 * j]), int(order[2 * j + 1])
            out.append({"rep": rep, "a": teams[a], "b": teams[b]})
    return out


def _sum_pmf(idx, x0, probs, step):
    xs, p = 0.0, np.array([1.0])
    for i in idx:
        pi = probs[i]
        nz = np.flatnonzero(pi > 1e-13)
        lo, hi = int(nz[0]), int(nz[-1])
        xs += x0[i] + lo * step
        seg = pi[lo:hi + 1]
        p = np.convolve(p, seg) if len(p) * len(seg) < 40000 else fftconvolve(p, seg)
    return xs, np.clip(p, 0.0, None)


def pwin_records(season: int, uni, position: str, cand, fit_sd, step: float, reps: int,
                 check: bool = True) -> pd.DataFrame:
    """One record per matchup: the page's, the candidate's and the fitted
    normal's P(win), and the outcome (a tied margin is skipped, as
    pwin_calibration does)."""
    x0, probs = cand
    recs = []
    for w in sorted(uni):
        pos = uni[w]
        arr = {p: {c: pos[p][c].to_numpy(dtype="float64") for c in ("mean", "sd", "actual", "_row")}
               for p in pos}
        games = deal(season, w, pos, reps)
        ref = PW.week_matchups(season, w, pos, reps) if check else None
        for gi, g in enumerate(games):
            side = {}
            for name in ("a", "b"):
                mu = sum(arr[p]["mean"][i] for p, i in g[name])
                var = sum(arr[p]["sd"][i] ** 2 for p, i in g[name])
                act = sum(arr[p]["actual"][i] for p, i in g[name])
                rows = [int(arr[p]["_row"][i]) for p, i in g[name] if p == position]
                rmu = sum(arr[p]["mean"][i] for p, i in g[name] if p != position)
                rvar = sum(arr[p]["sd"][i] ** 2 for p, i in g[name] if p != position)
                side[name] = (mu, var, act, rows, rmu, rvar)
            (mua, va, aa, ia, rma, rva), (mub, vb, ab, ib, rmb, rvb) = side["a"], side["b"]
            d, var, m = mua - mub, va + vb, aa - ab
            if ref is not None:
                r = ref[gi]
                if not (abs(r["full_d"] - d) < 1e-6 and abs(r["full_s"] - math.sqrt(var)) < 1e-6
                        and abs(r["full_m"] - m) < 1e-6):
                    raise SystemExit(f"{season} wk{w} matchup {gi}: the dealing differs from "
                                     "pwin_calibration.week_matchups")
            if abs(m) < 1e-9:         # a tied margin (points are multiples of 0.1)
                continue
            xa, pa = _sum_pmf(ia, x0, probs, step)
            xb, pb = _sum_pmf(ib, x0, probs, step)
            pdiff = (np.convolve(pa, pb[::-1]) if len(pa) * len(pb) < 40000
                     else fftconvolve(pa, pb[::-1]))
            pdiff = np.clip(pdiff, 0.0, None)
            pdiff /= pdiff.sum()
            dvals = (xa - (xb + (len(pb) - 1) * step)) + np.arange(len(pdiff)) * step
            rest_sd = math.sqrt(rva + rvb)
            p_cand = float(pdiff @ ndtr((dvals + (rma - rmb)) / rest_sd))
            fv = rva + rvb + float((fit_sd[ia] ** 2).sum() + (fit_sd[ib] ** 2).sum())
            recs.append({"season": season, "week": w, "rep": g["rep"], "y": float(m > 0),
                         "page": float(ndtr(d / math.sqrt(var))), "candidate": p_cand,
                         "fitted_normal": float(ndtr(d / math.sqrt(fv)))})
    return pd.DataFrame(recs)


def pwin_summary(rec: pd.DataFrame) -> dict:
    out = {"matchups": int(len(rec))}
    for s in ("page", "candidate", "fitted_normal"):
        p = np.clip(rec[s].to_numpy(), 1e-15, 1 - 1e-15)
        y = rec["y"].to_numpy()
        out[s] = {"brier": round(float(np.mean((p - y) ** 2)), 6),
                  "logloss": round(float(np.mean(-(y * np.log(p) + (1 - y) * np.log(1 - p)))), 6)}
    return out


def brier_boot(rec: pd.DataFrame) -> dict:
    """Week-clustered bootstrap of the pooled Brier change (candidate - page)."""
    y = rec["y"].to_numpy()
    diff = (rec["candidate"].to_numpy() - y) ** 2 - (rec["page"].to_numpy() - y) ** 2
    codes, uniq = pd.factorize(rec["season"] * 100 + rec["week"], sort=True)
    sums = np.bincount(codes, weights=diff, minlength=len(uniq))
    cnt = np.bincount(codes, minlength=len(uniq)).astype(float)
    rng = np.random.default_rng(BOOT_SEED)
    draws = []
    for _ in range(BOOT):
        c = np.bincount(rng.integers(0, len(uniq), len(uniq)), minlength=len(uniq))
        draws.append(float((c @ sums) / (c @ cnt)))
    return {"delta_brier": round(float(diff.mean()), 7),
            "ci95": [round(float(np.percentile(draws, 2.5)), 7),
                     round(float(np.percentile(draws, 97.5)), 7)],
            "share_boot_not_worse": round(float(np.mean(np.array(draws) <= 0)), 4),
            "clusters": int(len(uniq))}


# ------------------------------------------------------------------- folds

def run_folds(mod, frame, key, frames, cols, def_sd: float, folds_for, reps: int,
              check_leagues: bool = True) -> dict:
    pos = next(iter(mod.COLUMNS))
    step = float(mod.LATTICE)
    inputs = sorted({c for cs in mod.COLUMNS.values() for c in cs})
    seasons = BR.SEASONS
    folds = {}
    for s in folds_for:
        others = [t for t in seasons if t != s]
        # 1. test means for every position (the leagues), training means nested
        test_rows = {}
        for p in (*SKILL, "K", "DEF"):
            t = frames[p][s].copy()
            t["mu"] = fold_means(frames, cols, p, others, s)
            test_rows[p] = t
        parts = []
        for t in others:
            tr = frames[pos][t].copy()
            tr["mu"] = fold_means(frames, cols, pos, [u for u in others if u != t], t)
            parts.append(tr)
        train = pd.concat(parts, ignore_index=True)
        train = train.merge(frame[key + inputs], on=key, how="left")
        test = test_rows[pos].merge(frame[key + inputs], on=key, how="left")
        if len(test) != len(test_rows[pos]):
            raise SystemExit(f"{mod.ID}: block rows duplicate the table's")
        y = test["actual"].to_numpy(dtype="float64")
        # 2. fit on training seasons only; predict test rows without their outcome
        params = mod.fit(train, lambda x0, p, yy: lattice_crps(x0, p, yy, step))
        x0, probs = mod.pmf(test.drop(columns=["actual"]), params)
        if not (np.isfinite(x0).all() and np.isfinite(probs).all()):
            raise SystemExit(f"{mod.ID}: pmf returned non-finite values in {s}")
        cmean = x0 + probs @ (np.arange(probs.shape[1]) * step)
        mean_gap = float(np.max(np.abs(cmean - test["mu"].to_numpy())))
        if mean_gap > 1e-6:
            raise SystemExit(f"{mod.ID}: the candidate's mean differs from the page's by {mean_gap}")
        page_sd = (np.full(len(test), def_sd) if pos == "DEF"
                   else test["sd"].to_numpy(dtype="float64"))
        mu = test["mu"].to_numpy(dtype="float64")
        fa, fb = fit_normal_ab(train["mu"].to_numpy(), train["actual"].to_numpy())
        fsd = np.maximum(fa + fb * mu, 0.1)
        sc = {"page": scores_normal(mu, page_sd, y),
              "candidate": scores_lattice(x0, probs, step, y),
              "fitted_normal": scores_normal(mu, fsd, y)}
        if step == 1.0:
            lx, lp = lattice_normal(mu, page_sd)
            sc["lattice_normal"] = scores_lattice(lx, lp, 1.0, y)
        res = {"rows": int(len(test)), "params": params,
               "fitted_normal": {"a": round(fa, 4), "b": round(fb, 4)},
               "train_rows": int(len(train)),
               **{k: summarize(v) for k, v in sc.items()},
               "candidate_sd_mean": round(float(np.mean(np.sqrt(np.maximum(
                   probs @ ((np.arange(probs.shape[1]) * step) ** 2)
                   - (probs @ (np.arange(probs.shape[1]) * step)) ** 2, 0.0)))), 4),
               "page_sd_mean": round(float(np.mean(page_sd)), 4),
               # unrounded: the bar is decided on this, not on the rounded means
               "crps_delta": float(np.mean(sc["candidate"]["crps"] - sc["page"]["crps"]))}
        # 4. leagues
        lrows = {}
        for p, t in test_rows.items():
            f = t.assign(key=t["team"] if p == "DEF" else t["gsis_id"], position=p,
                         mean=t["mu"], _row=np.arange(len(t)))
            f["sd"] = def_sd if p == "DEF" else f["sd"]
            lrows[p] = f
        uni = league_universe(lrows)
        rec = pwin_records(s, uni, pos, (x0, probs), fsd, step, reps, check=check_leagues)
        res["pwin"] = pwin_summary(rec)
        folds[s] = {"res": res, "rec": rec, "scores": sc}
        print(f"  {s}: CRPS page {res['page']['crps']:.4f} candidate {res['candidate']['crps']:.4f}"
              f" (fitted normal {res['fitted_normal']['crps']:.4f}"
              + (f", lattice normal {res['lattice_normal']['crps']:.4f}" if 'lattice_normal' in res else "")
              + f"); Brier page {res['pwin']['page']['brier']:.5f} candidate "
              f"{res['pwin']['candidate']['brier']:.5f} over {res['pwin']['matchups']} matchups;"
              f" params {params if len(str(params)) < 90 else {k: v for k, v in params.items() if k != 'grid'}}",
              flush=True)
    return folds


def pooled_scores(folds: dict) -> dict:
    """Every system's row-weighted mean scores over all the folds' rows."""
    systems = set.intersection(*(set(f["scores"]) for f in folds.values()))
    return {name: {m: round(float(np.mean(np.concatenate(
        [f["scores"][name][m] for f in folds.values()]))), 5)
        for m in next(iter(folds.values()))["scores"][name]} for name in sorted(systems)}


def bar(folds: dict, position: str) -> dict:
    """The pre-registered bar, decided on unrounded numbers: the candidate's
    mean CRPS below the page's in every fold, and the pooled P(win) Brier
    over every fold's leagues not above the page's."""
    wins = [f["res"]["crps_delta"] < 0 for f in folds.values()]
    rec = pd.concat([f["rec"] for f in folds.values()], ignore_index=True)
    pooled = pwin_summary(rec)
    y = rec["y"].to_numpy()
    brier_delta = float(np.mean((rec["candidate"].to_numpy() - y) ** 2)
                        - np.mean((rec["page"].to_numpy() - y) ** 2))
    brier_ok = brier_delta <= 0
    return {"position": position,
            "crps_folds_better": int(sum(wins)), "folds": len(wins),
            "crps_every_fold": bool(all(wins)),
            "mean_crps_delta": round(float(np.mean([f["res"]["crps_delta"] for f in folds.values()])), 5),
            "pooled_scores": pooled_scores(folds),
            "pwin_pooled": pooled, "pwin_brier_delta": brier_delta,
            "pwin_brier_not_worse": bool(brier_ok),
            "pwin_brier_boot": brier_boot(rec),
            "passes": bool(all(wins) and brier_ok)}


# --------------------------------------------------------------------- main

def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


def run(block_id: str, args, crosswalk, skill, special, base) -> dict:
    mod, path = BR.load_block(block_id)
    if getattr(mod, "KIND", None) != "distribution":
        raise SystemExit(f"{block_id}: not a distribution block (KIND); use block_runner.py")
    if len(mod.COLUMNS) != 1:
        raise SystemExit(f"{block_id}: a distribution block touches one position")
    for attr in ("LATTICE", "fit", "pmf"):
        if not hasattr(mod, attr):
            raise SystemExit(f"{block_id}: missing {attr}")
    position = next(iter(mod.COLUMNS))
    prereg = BR.PREREG_DIR / f"{block_id}.md"
    folds_for = tuple(int(x) for x in getattr(mod, "FOLDS", BR.SEASONS))
    blobs = None
    if not args.coverage_only:
        blobs = {"prereg_blob": BR.committed_blob(prereg), "module_blob": BR.committed_blob(path),
                 "helpers_blob": BR.helpers_blob(), "harness_blob": BR.committed_blob(HARNESS)}
    cols = ["season", "week", "gsis_id", "position", "team", "opponent", "game_id", "home"]
    keys = pd.concat([t[cols] for t in skill.values()], ignore_index=True)
    special_keys = {
        "K": pd.concat([t[cols] for t in special["K"].values()], ignore_index=True),
        "DEF": pd.concat([t[[c for c in cols if c != "gsis_id"]] for t in special["DEF"].values()],
                         ignore_index=True)}
    ctx = BR.Context(keys, crosswalk, special_keys=special_keys)
    frame, key = BR.build_block(mod, ctx)
    tabs = special if position in BR.SPECIAL else skill
    cov = BR.coverage(frame, tabs, mod, key)
    print(f"\n{block_id} — {mod.TITLE}")
    for p, cs in cov.items():
        for c, by in cs.items():
            print(f"  coverage {p} {c}: " + ", ".join(f"{s} {v:.0%}" for s, v in by.items()))
    leaks = BR.leak_check(mod, ctx, frame, key)
    for line in leaks:
        print(f"  LEAK {line}")
    print(f"  leak check: {'FAILED' if leaks else 'passed'}")
    if args.coverage_only:
        return {"block": block_id, "coverage": cov, "leaks": leaks}
    if leaks:
        raise SystemExit(f"refused: {block_id} fails the leak check")
    meta = A.AdvancedModel.load().meta
    def_sd = float((meta.get("resid_sd") or {}).get("DEF"))
    frames = position_frames(skill, special)
    folds = run_folds(mod, frame, key, frames, mean_columns(base), def_sd, folds_for, args.reps)
    v = bar(folds, position)
    status = "PASSES" if v["passes"] else "fails"
    print(f"  {position}: lower CRPS in {v['crps_folds_better']}/{v['folds']} folds (mean "
          f"{v['mean_crps_delta']:+.5f}); pooled Brier page {v['pwin_pooled']['page']['brier']:.6f}"
          f" candidate {v['pwin_pooled']['candidate']['brier']:.6f} -> {status}")
    passed = [position] if v["passes"] else []
    b = v["pwin_brier_boot"]
    summary = (f"{'PASS' if passed else 'fail'} ({position}): lower CRPS than the page's normal in "
               f"{v['crps_folds_better']}/{v['folds']} folds (mean {v['mean_crps_delta']:+.4f}); "
               f"P(win) Brier {v['pwin_pooled']['candidate']['brier']:.5f} vs "
               f"{v['pwin_pooled']['page']['brier']:.5f} ({b['delta_brier']:+.6f}, 95% CI "
               f"{b['ci95'][0]:+.6f} to {b['ci95'][1]:+.6f})")
    result = {"block": block_id, "title": mod.TITLE, "kind": "distribution",
              "columns": mod.COLUMNS, "positions": [position], "lattice": float(mod.LATTICE),
              "seasons": list(BR.SEASONS), "folds_tested": list(folds_for), "min_week": BR.MIN_WEEK,
              "league": {"weeks": [LEAGUE_WEEKS[0], LEAGUE_WEEKS[-1]], "reps": args.reps,
                         "teams": PW.TEAMS, "top_n": PW.TOP_N, "flex_pool": PW.FLEX_POOL,
                         "seed": PW.SEED},
              "page_sd": {"skill_and_K": "max(baseline x WEEKLY_CV, SD_FLOOR) (tables' sd)",
                          "DEF": def_sd},
              "coverage": cov, "summary": summary,
              "folds": f"{position} CRPS {v['crps_folds_better']}/{v['folds']}",
              "fold_results": {str(s): f["res"] for s, f in folds.items()},
              "bar": v, "verdict": "PASS " + position if passed else "FAIL", "passed": passed,
              "tables_digest": args.digest, **blobs, "head": BR._git("rev-parse", "HEAD")[:12],
              "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    BR.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (BR.RESULTS_DIR / f"{block_id}.json").write_text(json.dumps(result, indent=1, default=str) + "\n",
                                                     encoding="utf-8")
    BR.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ledger = BR.RUNS_DIR / f"{block_id}.csv"
    new = not ledger.exists()
    with ledger.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=BR.RUN_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"when": result["run_at"], "block": block_id,
                    **{k: blobs[k] for k in ("prereg_blob", "module_blob", "helpers_blob")},
                    "tables_digest": args.digest, "head": result["head"],
                    "verdict": result["verdict"], "positions": position,
                    "folds": " ".join(str(f) for f in folds_for),
                    "folds_passed": f"{position}:{v['crps_folds_better']}/{v['folds']}"})
    print(f"  wrote {_shown(BR.RESULTS_DIR / f'{block_id}.json')} and {_shown(ledger)}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--block", action="append", default=[])
    ap.add_argument("--coverage-only", action="store_true")
    ap.add_argument("--reps", type=int, default=REPS)
    ap.add_argument("--crosswalk", type=Path, default=BR.CROSSWALK)
    args = ap.parse_args(argv)
    if not args.block:
        ap.error("--block is required")
    crosswalk = Crosswalk.from_csv(args.crosswalk)
    skill = BR.load_skill_tables(crosswalk)
    special = BR.load_kdef_tables(crosswalk)
    args.digest = BR.tables_digest([*BR.TABLES.glob("season_*.parquet"),
                                    *BR.KDEF_TABLES.glob("*.parquet")])
    print(f"tables digest {args.digest}")
    base = BR.base_columns()
    for b in args.block:
        run(b, args, crosswalk, skill, special, base)
    return 0


if __name__ == "__main__":
    sys.exit(main())
