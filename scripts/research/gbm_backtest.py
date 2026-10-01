"""Does a non-linear learner beat the ridge? Gradient boosting, out of sample.

    PYTHONPATH=src python scripts/research/gbm_backtest.py --tables DIR [--out FILE]

Season-fold cross-validation (2023-2025) on the same rows, universe and
metrics as the other backtests. Candidates, all on the shipped feature set
(`advanced_v1`, `+depth+last`) unless said:

  ridge          the shipped per-position ridge (the baseline that contains
                 ALL existing features, rule #5)
  gbm_sq         HistGradientBoostingRegressor, squared loss, per position
  gbm_abs        the same with absolute-error loss (fits the median — the
                 error metric's target)
  gbm_pool       one model across positions, position as a categorical
                 (more rows per tree)
  gbm_role       gbm_sq + the rest of the role block (share, jump, reserve):
                 the interactions a line cannot express
  ridge+gbm      the mean of ridge and gbm_sq
  stack_*        the same with Sleeper's number as an input (contender form)

Every candidate is also CALIBRATED per position (median-regression line fit
on out-of-fold training predictions, as in stack_calibration.py).
Hyper-parameters are fixed in advance (`PARAMS`), not searched per fold.

Bar (as everywhere): better than the ridge on start/sit AND MAE in every
fold. The strongest candidate's serving form (JSON trees, numpy prediction)
is a separate step; nothing ships from this script.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT

PARAMS = dict(max_iter=400, learning_rate=0.04, max_leaf_nodes=15, min_samples_leaf=40,
              l2_regularization=1.0, random_state=7)
#: Second pass (`--conservative`), declared before it ran: the first pass
#: overfit (about 2,000 rows per position). Early stopping on an internal
#: 15% validation split picks the round count.
STOP = dict(early_stopping=True, validation_fraction=0.15, n_iter_no_change=25, random_state=7)
CONSERVATIVE = {
    "c1": dict(max_iter=600, learning_rate=0.03, max_leaf_nodes=4, min_samples_leaf=100,
               l2_regularization=5.0, **STOP),
    "c2": dict(max_iter=600, learning_rate=0.05, max_depth=3, min_samples_leaf=60,
               l2_regularization=2.0, **STOP),
    "c3": dict(max_iter=900, learning_rate=0.02, max_leaf_nodes=8, min_samples_leaf=150,
               l2_regularization=10.0, **STOP),
}
ROLE_EXTRA = ["opp_share_last", "opp_jump", "snap_jump", "reserve_pickup"]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def lad_line(x, y, iters=60):
    a, b = 0.0, 1.0
    for _ in range(iters):
        w = 1.0 / np.maximum(np.abs(y - a - b * x), 0.05)
        X = np.column_stack([np.ones_like(x), x])
        beta = np.linalg.solve((X * w[:, None]).T @ X + 1e-9 * np.eye(2), (X * w[:, None]).T @ y)
        a, b = float(beta[0]), float(beta[1])
    return a, max(b, 1e-6)


def gbm_fit_predict(train, test, cols, *, loss="squared_error", pooled=False, params=None):
    params = PARAMS if params is None else params
    out = pd.Series(index=test.index, dtype="float64")
    if pooled:
        cats = {p: i for i, p in enumerate(A.POSITIONS)}
        X = train[cols].astype(float).assign(pos=train["position"].map(cats))
        Xt = test[cols].astype(float).assign(pos=test["position"].map(cats))
        m = HistGradientBoostingRegressor(loss=loss, categorical_features=[len(cols)], **params)
        m.fit(X.to_numpy(), train["actual"].to_numpy())
        out[:] = m.predict(Xt.to_numpy())
        return out.clip(lower=0)
    for p in A.POSITIONS:
        tr, te = train["position"] == p, test["position"] == p
        m = HistGradientBoostingRegressor(loss=loss, **params)
        m.fit(train.loc[tr, cols[p]].astype(float).to_numpy(), train.loc[tr, "actual"].to_numpy())
        out[te] = m.predict(test.loc[te, cols[p]].astype(float).to_numpy())
    return out.clip(lower=0)


def ridge_fit_predict(train, test, cols):
    out = pd.Series(index=test.index, dtype="float64")
    for p in A.POSITIONS:
        tr, te = train["position"] == p, test["position"] == p
        m = A.Ridge.fit(train.loc[tr], train.loc[tr, "actual"], cols[p])
        out[te] = m.predict(test.loc[te])
    return out.clip(lower=0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--tables", type=Path, default=None)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--conservative", action="store_true",
                    help="the second-pass configurations with early stopping, no calibration")
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    bt = _load("projection_backtest", "scripts/research/projection_backtest.py")
    ab = _load("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
    cw = Crosswalk.from_csv(args.crosswalk)
    shipped = A.AdvancedModel.load()
    cols = {p: list(shipped.adv[p].cols) for p in A.POSITIONS}
    pooled_cols = sorted({c for v in cols.values() for c in v})
    tables = {}
    for s in args.seasons:
        path = args.tables / f"season_{s}.parquet" if args.tables else None
        if path is not None and path.exists():
            tables[s] = pd.read_parquet(path)
        else:
            tables[s] = ab.season_table(s, cw, bt)
            if path is not None:
                path.parent.mkdir(parents=True, exist_ok=True)
                tables[s].to_parquet(path, index=False)
    keep = lambda d: d.loc[(d["week"] >= args.min_week) & d["sleeper"].notna()].copy()  # noqa: E731
    tables = {s: keep(t) for s, t in tables.items()}

    def systems_for(train, test):
        out = pd.DataFrame(index=test.index)
        out["ridge"] = ridge_fit_predict(train, test, cols)
        out["gbm_sq"] = gbm_fit_predict(train, test, cols)
        out["gbm_abs"] = gbm_fit_predict(train, test, cols, loss="absolute_error")
        out["gbm_pool"] = gbm_fit_predict(train, test, pooled_cols, pooled=True)
        out["gbm_role"] = gbm_fit_predict(train, test, {p: cols[p] + ROLE_EXTRA for p in cols})
        out["ridge+gbm"] = (out["ridge"] + out["gbm_sq"]) / 2
        with_sl = {p: cols[p] + ["sleeper"] for p in cols}
        out["stack_ridge"] = ridge_fit_predict(train, test, with_sl)
        out["stack_gbm"] = gbm_fit_predict(train, test, with_sl)
        out["stack_gbm_abs"] = gbm_fit_predict(train, test, with_sl, loss="absolute_error")
        out["stack_mix"] = (out["stack_ridge"] + out["stack_gbm"]) / 2
        return out

    raw = ["ridge", "gbm_sq", "gbm_abs", "gbm_pool", "gbm_role", "ridge+gbm",
           "stack_ridge", "stack_gbm", "stack_gbm_abs", "stack_mix"]
    systems = ["sleeper", *raw, *(f"{n}_cal" for n in raw)]
    if args.conservative:
        def systems_for(train, test):  # noqa: F811
            out = pd.DataFrame(index=test.index)
            out["ridge"] = ridge_fit_predict(train, test, cols)
            with_sl = {p: cols[p] + ["sleeper"] for p in cols}
            out["stack_ridge"] = ridge_fit_predict(train, test, with_sl)
            for k, prm in CONSERVATIVE.items():
                out[f"gbm_{k}"] = gbm_fit_predict(train, test, cols, params=prm)
                out[f"gbm_{k}_abs"] = gbm_fit_predict(train, test, cols, loss="absolute_error",
                                                      params=prm)
                out[f"ridge+gbm_{k}"] = (out["ridge"] + out[f"gbm_{k}"]) / 2
            out["gbm_pool_c1"] = gbm_fit_predict(train, test, pooled_cols, pooled=True,
                                                 params=CONSERVATIVE["c1"])
            out["stack_gbm_c1_abs"] = gbm_fit_predict(train, test, with_sl, loss="absolute_error",
                                                      params=CONSERVATIVE["c1"])
            out["stack_mix_c1"] = (out["stack_ridge"] + out["stack_gbm_c1_abs"]) / 2
            return out
        raw = ["ridge", "stack_ridge", *(f"gbm_{k}" for k in CONSERVATIVE),
               *(f"gbm_{k}_abs" for k in CONSERVATIVE), *(f"ridge+gbm_{k}" for k in CONSERVATIVE),
               "gbm_pool_c1", "stack_gbm_c1_abs", "stack_mix_c1"]
        systems = ["sleeper", *raw]
    folds = {}
    for test_s in args.seasons:
        train_s = [s for s in args.seasons if s != test_s]
        train = pd.concat([tables[s] for s in train_s])
        test = tables[test_s].copy()
        preds = systems_for(train, test)
        for n in raw:
            test[n] = preds[n]
        if args.conservative:
            inner_pred = inner_rows = None
        else:
            inner_pred = pd.concat([systems_for(pd.concat([tables[t] for t in train_s if t != s]
                                                          or [tables[s]]), tables[s])
                                    for s in train_s])
            inner_rows = pd.concat([tables[s] for s in train_s])
        for n in (() if args.conservative else raw):
            cal = pd.Series(index=test.index, dtype="float64")
            for p in A.POSITIONS:
                tr, te = (inner_rows["position"] == p).to_numpy(), test["position"] == p
                a, b = lad_line(inner_pred[n].to_numpy(dtype=float)[tr],
                                inner_rows["actual"].to_numpy(dtype=float)[tr])
                cal[te] = a + b * preds.loc[te, n]
            test[f"{n}_cal"] = cal.clip(lower=0)
        rel = test.loc[test["sleeper"] >= 5.0]
        pw, cc = bt.pairwise(rel, systems), bt.pairwise(rel, systems, close=3.0)
        folds[test_s] = {n: {"pairwise": round(pw[n], 6), "close": round(cc[n], 6),
                             "mae": round(float((test[n] - test["actual"]).abs().mean()), 4)}
                         for n in systems}
        print(f"fold {test_s} ({pw['n']} pairs)")
        for n in systems:
            f = folds[test_s][n]
            print(f"  {n:<18} {f['pairwise']:.2%} {f['close']:.2%} {f['mae']:.3f}")
    cv = {n: {m: round(float(np.mean([folds[t][n][m] for t in folds])), 4)
              for m in ("pairwise", "close", "mae")} for n in systems}
    print("\ncross-validated mean (pairwise | close | MAE):")
    for n in systems:
        print(f"  {n:<18} {cv[n]['pairwise']:.2%} {cv[n]['close']:.2%} {cv[n]['mae']:.3f}")

    def beats(n, ref):
        return all(folds[t][n]["pairwise"] > folds[t][ref]["pairwise"]
                   and folds[t][n]["mae"] < folds[t][ref]["mae"] for t in folds)
    page = [n for n in systems if not n.startswith("stack") and n not in ("sleeper", "ridge")
            and beats(n, "ridge")]
    cont = [n for n in systems if n.startswith("stack") and beats(n, "sleeper")]
    print(f"\npage candidates beating the ridge on both metrics in every fold: {page or 'none'}")
    print(f"contenders beating Sleeper on both metrics in every fold: {cont or 'none'}")
    if args.out:
        args.out.write_text(json.dumps({"seasons": args.seasons, "params": PARAMS, "folds": folds,
                                        "cv": cv, "page": page, "contenders": cont}, indent=1),
                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
