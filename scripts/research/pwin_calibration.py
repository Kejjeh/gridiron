"""P(win) calibration: does one spread factor k beat the page's raw formula out of sample?

    PYTHONPATH=src python scripts/research/pwin_calibration.py [--reps 8] [--boot 500]

The page (src/gridiron/dashboard.py `_matchup`, src/gridiron/winprob.py) sums
each side's starters: mean = sum of projected means, variance = sum of sd^2
(independence), P(win) = Phi((mu_a - mu_b) / sqrt(var_a + var_b)). The
candidate is ONE parameter k (grid 0.80..2.00, step 0.01, k >= 0.5):

    P(win) = Phi((mu_a - mu_b) / (k * sqrt(var_a + var_b)))

fit by maximum likelihood (minimum log loss) on 2019-2022 and scored on
2023-2025. Rule #5: it ships only if it beats k = 1 out of sample on BOTH log
loss and Brier.

Data: synthetic 12-team leagues, slots QB RB RB WR WR TE FLEX FLEX K DEF.
Each week 4..17, 8 replicate leagues (seeded): per slot type the top-N by
projected mean (QB 14, RB 30, WR 34, TE 14, K 14, DEF 14; FLEX from the best
24 RB/WR/TE not already dealt) are permuted and dealt so every team is full,
then the 12 teams are paired at random. A matchup whose actual margin is
exactly 0 is skipped.

  skill  data/research/cache/tables/season{S}_shipped.parquet: sd (the
         page's sd, gridiron.projection via gridiron.evaluate) and actual
         points. PRIMARY mean = advanced_v1 refit leave-one-season-out on the
         shipped columns (`loso_advanced`): the number the page sums when
         Sleeper's projection is absent and the stack's input, out of sample
         for every row. The baseline mean is a sensitivity (the first run
         used it, and with it k fit ~1.15 -- but the page never sums
         baseline skill means, so that k does not describe the page).
  K      k_def_backtest.season_tables: baseline mean and its sd
  DEF    mean = the advanced DEF ridge (AdvancedModel.adv['DEF']), sd = the
         model's resid_sd['DEF']; dst_ppg_season if the ridge is unavailable

K/DEF tables are built once per season and cached (gitignored) as
data/research/cache/tables/kdef_{S}.parquet.

Uncertainty: bootstrap clustered by week (resample season-weeks), refitting k
on resampled fit weeks and scoring the resampled test weeks with that k.
Sensitivities: baseline skill mean; skill-only leagues (no K/DEF); a DNP shock (each skill starter
scores 0 with probability 3%, seeded) because the tables hold only players
who PLAYED; Sleeper's projection as the skill mean (post-hoc pull: those
numbers were fetched after the fact, so they carry the usual hindsight
caveat). Joins are on gsis_id (skill, K) and team (DEF) only (rule #3).
Prints no names and no league ids.
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
import zlib
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT

from research_common import load_script  # noqa: E402

TABLES = RESEARCH_CACHE / "tables"
OUT_JSON = TABLES / "pwin_calibration.json"
SEED = 20261015
TEAMS = 12
TOP_N = {"QB": 14, "RB": 30, "WR": 34, "TE": 14, "K": 14, "DEF": 14}
PER_TEAM = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "K": 1, "DEF": 1}
FLEX_POOL, FLEX_PER_TEAM = 24, 2
SKILL = ("QB", "RB", "WR", "TE")
GRID = np.round(np.arange(0.80, 2.0 + 1e-9, 0.01), 2)
K1 = int(np.argmin(np.abs(GRID - 1.0)))
BINS = [(0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 0.80),
        (0.80, 0.90), (0.90, 1.0000001)]
DNP_P = 0.03

_erfc = np.frompyfunc(math.erfc, 1, 1)


def phi_cdf(x: np.ndarray) -> np.ndarray:
    """Standard normal CDF, accurate in both tails (erfc, no scipy)."""
    return 0.5 * _erfc(-np.asarray(x, dtype="float64") / math.sqrt(2.0)).astype("float64")


def _ignored(path: Path) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=REPO_ROOT)
    return r.returncode == 0


# ------------------------------------------------------------------ inputs

def kdef_table(season: int, cw: Crosswalk | None, bt) -> pd.DataFrame:
    """K and DEF rows for one season, cached as kdef_{season}.parquet."""
    path = TABLES / f"kdef_{season}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    if not _ignored(path):
        raise SystemExit(f"refusing to write {path}: not gitignored")
    kd = load_script("k_def_backtest", "scripts/research/k_def_backtest.py")
    K, D = kd.season_tables(season, cw, bt)
    k = K[["week", "gsis_id", "baseline", "sd", "actual", "sleeper", "naive"]].copy()
    k["kind"] = "K"
    d = D[["week", "team", *A.DEFENSE_FEATURES, "naive", "actual", "sleeper"]].copy()
    for c in A.DEFENSE_FEATURES:
        d[c] = pd.to_numeric(d[c], errors="coerce").astype("float64")
    d["kind"] = "DEF"
    out = pd.concat([k, d], ignore_index=True)
    out["season"] = season
    out.to_parquet(path, index=False)
    return out


def loso_advanced(seasons=range(2019, 2026)) -> dict[int, np.ndarray]:
    """advanced_v1's mean for every row of every season table, from a ridge
    refit with THAT season held out (the shipped columns, the shipped
    training universe: week >= 4 with a Sleeper number, lambda 5). This is
    the number the page serves when Sleeper's projection is missing, and the
    stack's own input, out of sample for every row. Row order = the table's."""
    model = A.AdvancedModel.load()
    cols = {p: list(model.adv[p].cols) for p in A.POSITIONS}
    tabs = {s: pd.read_parquet(TABLES / f"season{s}_shipped.parquet") for s in seasons}
    out: dict[int, np.ndarray] = {}
    for s in seasons:
        tr = pd.concat([tabs[o] for o in seasons if o != s])
        tr = tr.loc[(tr["week"] >= 4) & tr["sleeper"].notna()]
        fits = {p: A.Ridge.fit(tr[tr["position"] == p], tr.loc[tr["position"] == p, "actual"],
                               cols[p], lam=5.0) for p in A.POSITIONS}
        t = tabs[s]
        pred = np.full(len(t), np.nan)
        for pos, m in fits.items():
            sel = (t["position"] == pos).to_numpy()
            if sel.any():
                pred[sel] = np.maximum(0.0, m.predict(t[sel]))
        out[s] = pred
        print(f"  {s}: advanced_v1 refit without {s} on {len(tr)} rows", flush=True)
    return out


def universe(season: int, kdef: pd.DataFrame, skill_mean: str, def_ridge,
             def_sd: float, weeks: range,
             extra: dict[int, np.ndarray] | None = None) -> dict[int, dict[str, pd.DataFrame]]:
    """week -> position -> rows (key, mean, sd, actual) sorted best-first.
    `skill_mean` is a table column, or "adv_loso" for `loso_advanced`."""
    sk = pd.read_parquet(TABLES / f"season{season}_shipped.parquet",
                         columns=["week", "gsis_id", "position", "baseline", "sd",
                                  "sleeper", "actual"])
    if skill_mean == "adv_loso":
        sk["adv_loso"] = (extra or {})[season]
    sk = sk.loc[sk["week"].isin(list(weeks)) & sk["position"].isin(SKILL)].copy()
    sk["mean"] = sk[skill_mean]
    sk = sk.dropna(subset=["mean", "sd", "actual"]).rename(columns={"gsis_id": "key"})
    k = kdef.loc[(kdef["kind"] == "K") & kdef["week"].isin(list(weeks))].copy()
    k = k.dropna(subset=["baseline", "sd", "actual"])
    k = k.assign(key=k["gsis_id"], position="K", mean=k["baseline"])
    d = kdef.loc[(kdef["kind"] == "DEF") & kdef["week"].isin(list(weeks))].copy()
    d = d.dropna(subset=["actual"])
    if def_ridge is not None:
        d["mean"] = def_ridge.predict(d)
    else:
        d["mean"] = d["naive"]
    d = d.dropna(subset=["mean"]).assign(key=d["team"], position="DEF", sd=def_sd)
    cols = ["week", "key", "position", "mean", "sd", "actual"]
    allrows = pd.concat([sk[cols], k[cols], d[cols]], ignore_index=True)
    allrows = allrows.drop_duplicates(subset=["week", "position", "key"])
    out: dict[int, dict[str, pd.DataFrame]] = {}
    for w, g in allrows.groupby("week"):
        g = g.sort_values(["mean", "key"], ascending=[False, True], kind="mergesort")
        out[int(w)] = {p: gp.reset_index(drop=True) for p, gp in g.groupby("position")}
    return out


# ------------------------------------------------------------------ leagues

def week_matchups(season: int, week: int, pos: dict[str, pd.DataFrame], reps: int,
                  variant_seed: int = 0) -> list[dict]:
    """`reps` seeded leagues; one record per matchup with every variant's sums."""
    recs = []
    for rep in range(reps):
        rng = np.random.default_rng([SEED, season, week, rep, variant_seed])
        dnp_rng = np.random.default_rng([SEED, season, week, rep, variant_seed, 3])
        tot = {k: np.zeros(TEAMS) for k in ("mu_s", "var_s", "act_s", "act_s_dnp",
                                              "mu_kd", "var_kd", "act_kd")}
        used: dict[str, set] = {"RB": set(), "WR": set(), "TE": set()}

        def add(rows: pd.DataFrame, team: int, kind: str) -> None:
            mu, sd, act = rows["mean"].to_numpy(), rows["sd"].to_numpy(), rows["actual"].to_numpy()
            if kind == "s":
                tot["mu_s"][team] += mu.sum()
                tot["var_s"][team] += (sd ** 2).sum()
                tot["act_s"][team] += act.sum()
                shock = dnp_rng.random(len(act)) < DNP_P
                tot["act_s_dnp"][team] += np.where(shock, 0.0, act).sum()
            else:
                tot["mu_kd"][team] += mu.sum()
                tot["var_kd"][team] += (sd ** 2).sum()
                tot["act_kd"][team] += act.sum()

        for p in ("QB", "RB", "WR", "TE", "K", "DEF"):
            top = pos[p].iloc[:TOP_N[p]]
            need = TEAMS * PER_TEAM[p]
            if len(top) < need:
                raise SystemExit(f"{season} wk{week}: only {len(top)} {p} rows, need {need}")
            idx = rng.permutation(len(top))[:need].reshape(TEAMS, PER_TEAM[p])
            for t in range(TEAMS):
                add(top.iloc[idx[t]], t, "s" if p in SKILL else "kd")
            if p in used:
                used[p] = set(top.iloc[idx.ravel()]["key"])
        flex = pd.concat([pos[p].loc[~pos[p]["key"].isin(used[p])] for p in ("RB", "WR", "TE")])
        flex = flex.sort_values(["mean", "key"], ascending=[False, True],
                                kind="mergesort").iloc[:FLEX_POOL].reset_index(drop=True)
        fidx = rng.permutation(len(flex))[:TEAMS * FLEX_PER_TEAM].reshape(TEAMS, FLEX_PER_TEAM)
        for t in range(TEAMS):
            add(flex.iloc[fidx[t]], t, "s")
        order = rng.permutation(TEAMS)
        for j in range(TEAMS // 2):
            a, b = int(order[2 * j]), int(order[2 * j + 1])
            r = {"season": season, "week": week, "rep": rep}
            for name, mu, var, act in (
                    ("full", tot["mu_s"] + tot["mu_kd"], tot["var_s"] + tot["var_kd"],
                     tot["act_s"] + tot["act_kd"]),
                    ("skill", tot["mu_s"], tot["var_s"], tot["act_s"]),
                    ("dnp", tot["mu_s"] + tot["mu_kd"], tot["var_s"] + tot["var_kd"],
                     tot["act_s_dnp"] + tot["act_kd"])):
                r[f"{name}_d"] = mu[a] - mu[b]
                r[f"{name}_s"] = math.sqrt(var[a] + var[b])
                r[f"{name}_m"] = act[a] - act[b]
                r[f"{name}_lineup_sd"] = math.sqrt(var[a])
            recs.append(r)
    return recs


def variant_frame(m: pd.DataFrame, name: str) -> pd.DataFrame:
    f = pd.DataFrame({"season": m["season"], "week": m["week"], "rep": m["rep"],
                      "d": m[f"{name}_d"], "s": m[f"{name}_s"], "m": m[f"{name}_m"],
                      "lineup_sd": m[f"{name}_lineup_sd"]})
    f = f.loc[f["m"] != 0].reset_index(drop=True)
    f["y"] = (f["m"] > 0).astype("float64")
    f["cluster"] = f["season"] * 100 + f["week"]
    return f


# ------------------------------------------------------------------ scoring

def loss_matrices(f: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Per-matchup log loss and Brier for every k on the grid (n x len(GRID))."""
    x = (f["d"] / f["s"]).to_numpy()[:, None] / GRID[None, :]
    y = f["y"].to_numpy()[:, None]
    p = np.clip(phi_cdf(x), 1e-15, 1 - 1e-15)
    q = np.clip(phi_cdf(-x), 1e-15, 1 - 1e-15)          # 1 - p, accurate in the tail
    ll = -(y * np.log(p) + (1 - y) * np.log(q))
    br = (p - y) ** 2
    return ll, br


def cluster_sums(f: pd.DataFrame, mat: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    codes, uniq = pd.factorize(f["cluster"], sort=True)
    sums = np.zeros((len(uniq), mat.shape[1]))
    np.add.at(sums, codes, mat)
    return sums, np.bincount(codes, minlength=len(uniq)).astype("float64"), np.asarray(uniq)


def fit_k(ll: np.ndarray) -> float:
    return float(GRID[int(np.argmin(ll.mean(axis=0)))])


def reliability(f: pd.DataFrame, k: float) -> list[dict]:
    p = phi_cdf((f["d"] / (k * f["s"])).to_numpy())
    y = f["y"].to_numpy()
    fav = np.where(p >= 0.5, p, 1 - p)
    won = np.where(p >= 0.5, y, 1 - y)
    out = []
    for lo, hi in BINS:
        sel = (fav >= lo) & (fav < hi)
        n = int(sel.sum())
        out.append({"bin": f"{int(lo * 100)}-{min(int(round(hi * 100)), 100)}", "n": n,
                    "stated": round(float(fav[sel].mean()), 4) if n else None,
                    "observed": round(float(won[sel].mean()), 4) if n else None})
    return out


def metrics(f: pd.DataFrame, ll: np.ndarray, br: np.ndarray, k: float) -> dict:
    j = int(np.argmin(np.abs(GRID - k)))
    z = ((f["m"] - f["d"]) / f["s"]).to_numpy()
    return {"n": int(len(f)), "logloss": round(float(ll[:, j].mean()), 5),
            "brier": round(float(br[:, j].mean()), 5),
            "z_sd": round(float(z.std(ddof=1) / k), 4)}


def analyse(f: pd.DataFrame, fit_seasons, test_seasons, boot: int, label: str) -> dict:
    fit = f.loc[f["season"].isin(fit_seasons)].reset_index(drop=True)
    test = f.loc[f["season"].isin(test_seasons)].reset_index(drop=True)
    ll_f, br_f = loss_matrices(fit)
    ll_t, br_t = loss_matrices(test)
    k = fit_k(ll_f)
    # bootstrap: resample fit weeks -> k_b; resample test weeks -> improvement at k_b
    cf_ll, nf, _ = cluster_sums(fit, ll_f)
    ct_ll, nt, _ = cluster_sums(test, ll_t)
    ct_br, _, _ = cluster_sums(test, br_t)
    rng = np.random.default_rng([SEED, 500, zlib.crc32(label.encode())])
    ks, dll, dbr = [], [], []
    for _ in range(boot):
        cf = np.bincount(rng.integers(0, len(nf), len(nf)), minlength=len(nf))
        kb_i = int(np.argmin((cf @ cf_ll) / (cf @ nf)))
        ct = np.bincount(rng.integers(0, len(nt), len(nt)), minlength=len(nt))
        n = ct @ nt
        dll.append(float((ct @ ct_ll[:, K1] - ct @ ct_ll[:, kb_i]) / n))
        dbr.append(float((ct @ ct_br[:, K1] - ct @ ct_br[:, kb_i]) / n))
        ks.append(float(GRID[kb_i]))
    ks, dll, dbr = np.array(ks), np.array(dll), np.array(dbr)
    q = lambda a: [round(float(np.percentile(a, 2.5)), 5), round(float(np.percentile(a, 97.5)), 5)]  # noqa: E731
    raw, cal = metrics(test, ll_t, br_t, 1.0), metrics(test, ll_t, br_t, k)
    per_season = {}
    for s in sorted(set(f["season"])):
        g = f.loc[f["season"] == s].reset_index(drop=True)
        lg, bg = loss_matrices(g)
        per_season[int(s)] = {"role": "fit" if s in fit_seasons else "test",
                              "n": int(len(g)), "k_in_season": fit_k(lg),
                              "raw": metrics(g, lg, bg, 1.0), "calibrated": metrics(g, lg, bg, k)}
    def slope(g: pd.DataFrame) -> dict:
        # actual margin on projected margin, both in units of the stated SD:
        # m/s = b * d/s + e. Under normality P(win) = Phi(b x / sd(e)), so the
        # sign-only k is roughly sd(e) / b (shrunk projections give b > 1).
        x, zm = (g["d"] / g["s"]).to_numpy(), (g["m"] / g["s"]).to_numpy()
        b = float((x * zm).sum() / (x * x).sum())
        e = float((zm - b * x).std(ddof=1))
        return {"b": round(b, 4), "resid_sd": round(e, 4), "implied_k": round(e / b, 4)}
    zt = ((test["m"] - test["d"]) / test["s"]).to_numpy()
    zf = ((fit["m"] - fit["d"]) / fit["s"]).to_numpy()
    return {
        "label": label, "k_fit": k, "k_at_grid_edge": k in (float(GRID[0]), float(GRID[-1])),
        "k_ci": [round(float(np.percentile(ks, 2.5)), 2), round(float(np.percentile(ks, 97.5)), 2)],
        "k_in_sample_test": fit_k(ll_t),
        "n_fit_matchups": int(len(fit)), "n_test_matchups": int(len(test)),
        "n_fit_weeks": int(len(nf)), "n_test_weeks": int(len(nt)),
        "test": {"raw": raw, "calibrated": cal},
        "improvement": {"logloss": round(raw["logloss"] - cal["logloss"], 5),
                        "brier": round(raw["brier"] - cal["brier"], 5)},
        "improvement_ci": {"logloss": q(dll), "brier": q(dbr),
                           "share_reps_better_both": round(float(np.mean((dll > 0) & (dbr > 0))), 4)},
        "bar_cleared": bool(cal["logloss"] < raw["logloss"] and cal["brier"] < raw["brier"]),
        "reliability_test_raw": reliability(test, 1.0),
        "reliability_test_calibrated": reliability(test, k),
        "margin_z_sd": {"fit_raw": round(float(zf.std(ddof=1)), 4),
                        "test_raw": raw["z_sd"], "test_calibrated": cal["z_sd"],
                        "test_robust_raw": round(float(np.median(np.abs(zt)) / 0.6745), 4)},
        "margin_slope": {"fit": slope(fit), "test": slope(test)},
        "mean_lineup_sd": round(float(f["lineup_sd"].mean()), 2),
        "mean_margin_sd": round(float(f["s"].mean()), 2),
        "per_season": per_season,
    }


# ------------------------------------------------------------------ report

def show(r: dict) -> None:
    print(f"\n== {r['label']}: k = {r['k_fit']:.2f}  (95% CI {r['k_ci'][0]:.2f}-{r['k_ci'][1]:.2f}"
          f"{', AT GRID EDGE' if r['k_at_grid_edge'] else ''}); in-sample test k "
          f"{r['k_in_sample_test']:.2f}")
    print(f"   fit {r['n_fit_matchups']} matchups / {r['n_fit_weeks']} weeks; test "
          f"{r['n_test_matchups']} / {r['n_test_weeks']}; mean lineup SD "
          f"{r['mean_lineup_sd']}, margin SD {r['mean_margin_sd']}")
    t = r["test"]
    print(f"   test raw   logloss {t['raw']['logloss']:.5f}  brier {t['raw']['brier']:.5f}  "
          f"z-SD {t['raw']['z_sd']:.3f}")
    print(f"   test cal   logloss {t['calibrated']['logloss']:.5f}  brier "
          f"{t['calibrated']['brier']:.5f}  z-SD {t['calibrated']['z_sd']:.3f}")
    ci = r["improvement_ci"]
    print(f"   improvement logloss {r['improvement']['logloss']:+.5f} "
          f"[{ci['logloss'][0]:+.5f}, {ci['logloss'][1]:+.5f}]  brier "
          f"{r['improvement']['brier']:+.5f} [{ci['brier'][0]:+.5f}, {ci['brier'][1]:+.5f}]  "
          f"reps better on both {ci['share_reps_better_both']:.1%}  -> bar "
          f"{'CLEARED' if r['bar_cleared'] else 'NOT cleared'}")
    print(f"   margin z-SD: fit raw {r['margin_z_sd']['fit_raw']:.3f}, test raw "
          f"{r['margin_z_sd']['test_raw']:.3f} (robust {r['margin_z_sd']['test_robust_raw']:.3f})")
    ms = r["margin_slope"]
    print(f"   margin slope b (actual on projected, SD units): fit {ms['fit']['b']:.3f} resid SD "
          f"{ms['fit']['resid_sd']:.3f} -> k~{ms['fit']['implied_k']:.3f}; test {ms['test']['b']:.3f} "
          f"resid SD {ms['test']['resid_sd']:.3f} -> k~{ms['test']['implied_k']:.3f}")
    for s, v in r["per_season"].items():
        print(f"   {s} {v['role']:<4} n={v['n']:4d} k_in={v['k_in_season']:.2f}  raw ll "
              f"{v['raw']['logloss']:.4f} br {v['raw']['brier']:.4f} z {v['raw']['z_sd']:.3f} | cal ll "
              f"{v['calibrated']['logloss']:.4f} br {v['calibrated']['brier']:.4f} z "
              f"{v['calibrated']['z_sd']:.3f}")


def show_rel(r: dict) -> None:
    print(f"\n   reliability, favourite's side, test {r['label']}:")
    print(f"   {'bin':<7} {'n':>5} {'raw stated':>11} {'raw obs':>8} | {'n':>5} "
          f"{'cal stated':>11} {'cal obs':>8}")
    for a, b in zip(r["reliability_test_raw"], r["reliability_test_calibrated"]):
        fa = lambda v: "   -" if v is None else f"{v:.3f}"  # noqa: E731
        print(f"   {a['bin']:<7} {a['n']:5d} {fa(a['stated']):>11} {fa(a['observed']):>8} | "
              f"{b['n']:5d} {fa(b['stated']):>11} {fa(b['observed']):>8}")


def main(argv=None) -> int:
    global SEED
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--fit", type=int, nargs="+", default=[2019, 2020, 2021, 2022])
    ap.add_argument("--test", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--reps", type=int, default=8)
    ap.add_argument("--boot", type=int, default=500)
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--max-week", type=int, default=17)
    ap.add_argument("--seed", type=int, default=SEED, help="league-dealing seed (sensitivity)")
    ap.add_argument("--out", type=Path, default=OUT_JSON, help="JSON summary path (gitignored)")
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    args = ap.parse_args(argv)
    SEED = args.seed
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    seasons = [*args.fit, *args.test]
    weeks = range(args.min_week, args.max_week + 1)
    need_build = [s for s in seasons if not (TABLES / f"kdef_{s}.parquet").exists()]
    cw = Crosswalk.from_csv(args.crosswalk) if need_build else None
    bt = load_script("projection_backtest", "scripts/research/projection_backtest.py") \
        if need_build else None
    model = A.AdvancedModel.load()
    def_ridge = model.adv.get("DEF") if model is not None else None
    def_sd = float(((model.meta.get("resid_sd") or {}).get("DEF")) if model else 5.96)
    def_source = ("advanced DEF ridge (AdvancedModel.adv['DEF'], fit on "
                  f"{(model.meta.get('k_def_evidence') or {}).get('train')} + "
                  f"{(model.meta.get('k_def_evidence') or {}).get('test')})"
                  if def_ridge is not None else "dst_ppg_season (naive)")
    print(f"DEF mean: {def_source}; DEF sd {def_sd}")

    loso = loso_advanced(range(2019, 2026))
    frames = {"adv_loso": [], "baseline": [], "sleeper": []}
    for s in seasons:
        kdef = kdef_table(s, cw, bt)
        for mean_col in ("adv_loso", "baseline", "sleeper"):
            uni = universe(s, kdef, mean_col, def_ridge, def_sd, weeks, extra=loso)
            for w in sorted(uni):
                frames[mean_col].extend(week_matchups(s, w, uni[w], args.reps))
        print(f"  {s}: {sum(1 for r in frames['adv_loso'] if r['season'] == s)} matchups")
    served = pd.DataFrame(frames["adv_loso"])
    base = pd.DataFrame(frames["baseline"])
    slp = pd.DataFrame(frames["sleeper"])

    # PRIMARY: the mean the page sums (advanced_v1, out of sample for every
    # row). The baseline-mean league is the first run's setup, kept as a
    # sensitivity: the page never sums baseline means for skill players.
    res = {
        "full": analyse(variant_frame(served, "full"), args.fit, args.test, args.boot,
                        "full league, served mean (advanced_v1 out of sample)"),
        "served_dnp": analyse(variant_frame(served, "dnp"), args.fit, args.test, args.boot,
                              f"served mean + {DNP_P:.0%} skill DNP shock"),
        "baseline_mean": analyse(variant_frame(base, "full"), args.fit, args.test, args.boot,
                                 "full league, baseline skill mean"),
        "skill_only": analyse(variant_frame(served, "skill"), args.fit, args.test, args.boot,
                              "skill-only league (no K/DEF), served mean"),
        "dnp": analyse(variant_frame(base, "dnp"), args.fit, args.test, args.boot,
                       f"baseline mean + {DNP_P:.0%} skill DNP shock"),
        "sleeper": analyse(variant_frame(slp, "full"), args.fit, args.test, args.boot,
                           "full league, Sleeper skill mean (post-hoc)"),
    }
    for r in res.values():
        show(r)
    show_rel(res["full"])

    f = res["full"]
    summary = {
        "written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "script": "scripts/research/pwin_calibration.py",
        "formula": "P(win) = Phi((mu_a - mu_b) / (k * sqrt(var_a + var_b)))",
        "skill_mean_source": "advanced_v1 refit leave-one-season-out (the page's mean "
                             "without Sleeper's number; out of sample for every row)",
        "grid": [float(GRID[0]), float(GRID[-1]), 0.01],
        "k_fit": f["k_fit"], "k_ci": f["k_ci"], "k_at_grid_edge": f["k_at_grid_edge"],
        "fit_seasons": args.fit, "test_seasons": args.test,
        "n_fit_matchups": f["n_fit_matchups"], "n_test_matchups": f["n_test_matchups"],
        "n_fit_weeks": f["n_fit_weeks"], "n_test_weeks": f["n_test_weeks"],
        "test": {"raw": {"logloss": f["test"]["raw"]["logloss"], "brier": f["test"]["raw"]["brier"]},
                 "calibrated": {"logloss": f["test"]["calibrated"]["logloss"],
                                "brier": f["test"]["calibrated"]["brier"]}},
        "improvement": f["improvement"], "improvement_ci": f["improvement_ci"],
        "bar_cleared": f["bar_cleared"],
        "reliability_test_raw": f["reliability_test_raw"],
        "reliability_test_calibrated": f["reliability_test_calibrated"],
        "margin_z_sd": f["margin_z_sd"], "margin_slope": f["margin_slope"],
        "mean_lineup_sd": f["mean_lineup_sd"], "mean_margin_sd": f["mean_margin_sd"],
        "per_season": f["per_season"],
        "dnp_sensitivity_k": {"without": f["k_fit"], "with": res["served_dnp"]["k_fit"],
                              "with_ci": res["served_dnp"]["k_ci"], "dnp_prob": DNP_P,
                              "test": res["served_dnp"]["test"],
                              "bar_cleared": res["served_dnp"]["bar_cleared"]},
        "baseline_mean_k": {"k": res["baseline_mean"]["k_fit"], "k_ci": res["baseline_mean"]["k_ci"],
                            "test": res["baseline_mean"]["test"],
                            "improvement_ci": res["baseline_mean"]["improvement_ci"],
                            "bar_cleared": res["baseline_mean"]["bar_cleared"],
                            "note": "the first run's setup; the page never sums baseline skill means"},
        "skill_only_k": {"k": res["skill_only"]["k_fit"], "k_ci": res["skill_only"]["k_ci"],
                         "test": res["skill_only"]["test"],
                         "improvement_ci": res["skill_only"]["improvement_ci"],
                         "bar_cleared": res["skill_only"]["bar_cleared"],
                         "mean_margin_sd": res["skill_only"]["mean_margin_sd"]},
        "sleeper_mean_k": {"k": res["sleeper"]["k_fit"], "k_ci": res["sleeper"]["k_ci"],
                           "test": res["sleeper"]["test"],
                           "improvement_ci": res["sleeper"]["improvement_ci"],
                           "bar_cleared": res["sleeper"]["bar_cleared"],
                           "note": "Sleeper projections pulled after the fact (hindsight caveat); "
                                   "skill sd stays the baseline sd"},
        "def_mean_source": def_source, "def_sd": def_sd,
        "league": {"teams": TEAMS, "slots": "QB RB RB WR WR TE FLEX FLEX K DEF",
                   "top_n": TOP_N, "flex_pool": FLEX_POOL, "reps_per_week": args.reps,
                   "weeks": [args.min_week, args.max_week], "seed": SEED,
                   "bootstrap": {"reps": args.boot, "cluster": "season-week"}},
        "variants": {k: {kk: v[kk] for kk in ("k_fit", "k_ci", "test", "improvement",
                                               "improvement_ci", "bar_cleared", "margin_z_sd",
                                               "margin_slope")}
                     for k, v in res.items()},
    }
    out = args.out.resolve()
    if not _ignored(out):
        raise SystemExit(f"refusing to write {out}: not gitignored")
    out.write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"\nwrote {out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
