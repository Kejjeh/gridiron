"""Structural variants of the mix's residual model. Out of sample, seven folds.

    PYTHONPATH=src python scripts/research/resid_variants.py --tables DIR [--out FILE]

The mix (blend_search.py) is behind Sleeper on ordering in one season of
seven; its blend knobs do not move that (mix_select.py), so the information
in the base models is what is left. Variants of the residual ridge (fit to
actual - Sleeper), each declared, none tuned to a season:

  base      the shipped form: every row Sleeper projected, the shipped features
  univ      trained on the evaluation universe only (Sleeper >= 5)
  gap       + Sleeper's own number and its gap to our baseline and to our
            advanced mean as features (where the two disagree, who is right?)
  fe        within (week, position) fixed effects: features and target demeaned
            by group — the metric only compares players within a group
  univ_gap  univ + gap
  univ_fe   univ + fe

Each goes into the fixed mix (k 0.5, equal parts) with the two-stage ridge,
calibrated per position. Bar: beats Sleeper on both metrics in every fold.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT

GAP = ["sleeper", "gap_base", "gap_adv"]


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def demean(df: pd.DataFrame, cols) -> pd.DataFrame:
    out = df.copy()
    g = out.groupby(["week", "position"])
    for c in cols:
        out[c] = out[c] - g[c].transform("mean")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(range(2019, 2026)))
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--role", action="store_true",
                    help="instead: the role block's untested members (reserve list, absent "
                         "teammates, team share and jumps) as residual features")
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    bt = _load("projection_backtest", "scripts/research/projection_backtest.py")
    sc = _load("stack_calibration", "scripts/research/stack_calibration.py")
    shipped = A.AdvancedModel.load()
    cols = {p: list(shipped.adv[p].cols) for p in A.POSITIONS}
    two = {p: ["adv_oof", "sleeper", "baseline"] for p in A.POSITIONS}
    tables = {s: pd.read_parquet(args.tables / f"season_{s}.parquet") for s in args.seasons}
    keep = lambda d: d.loc[(d["week"] >= 4) & d["sleeper"].notna()].copy()  # noqa: E731
    tables = {s: keep(t) for s, t in tables.items()}
    for t in tables.values():
        t["resid"] = t["actual"] - t["sleeper"]
        t["gap_base"] = t["sleeper"] - t["baseline"]

    def with_adv_oof(seasons):
        parts = []
        for s in seasons:
            others = [t for t in seasons if t != s] or [s]
            m = sc.fit_pos(pd.concat([tables[t] for t in others]), cols, "actual")
            d = tables[s].copy()
            d["adv_oof"] = sc.predict_pos(m, d).clip(lower=0)
            d["gap_adv"] = d["sleeper"] - d["adv_oof"]
            parts.append(d)
        return pd.concat(parts)

    EXTRA = {"reserve": ["reserve_pickup"], "absent": ["absent_pickup"],
             "share": ["opp_share_last", "opp_jump", "snap_jump"],
             "role": ["reserve_pickup", "absent_pickup", "opp_share_last", "opp_jump", "snap_jump"]}
    variants = (["base", "univ", "gap", "fe", "univ_gap", "univ_fe"] if not args.role
                else ["base", *EXTRA])

    def resid_predict(train, test, variant):
        tr = train.loc[train["sleeper"] >= 5.0] if "univ" in variant else train
        c = {p: cols[p] + (GAP if "gap" in variant else []) + EXTRA.get(variant, [])
             for p in A.POSITIONS}
        if "fe" in variant:
            out = pd.Series(index=test.index, dtype="float64")
            for p in A.POSITIONS:
                trp = demean(tr.loc[tr["position"] == p], c[p] + ["resid"])
                tep = demean(test.loc[test["position"] == p], c[p])
                m = A.Ridge.fit(trp, trp["resid"], c[p])
                out[tep.index] = m.predict(tep)
            return out
        return sc.predict_pos(sc.fit_pos(tr, c, "resid"), test)

    def bases(train_s, test):
        """Everything needed for the mixes on `test`, from models fit on the
        seasons `train_s` (the advanced mean out of fold inside them)."""
        train = with_adv_oof(train_s)
        test = test.copy()
        test["adv_oof"] = sc.predict_pos(sc.fit_pos(train, cols, "actual"), test).clip(lower=0)
        test["gap_adv"] = test["sleeper"] - test["adv_oof"]
        out = test[["position", "week", "actual", "sleeper", "baseline"]].copy()
        t2 = sc.predict_pos(sc.fit_pos(train, two, "actual"), test)
        for v in variants:
            r = resid_predict(train, test, v)
            out[v] = 0.5 * (test["sleeper"] + 0.5 * r) + 0.5 * t2
        return out

    systems = ["sleeper", *(f"{v}_cal" for v in variants)]
    folds = {}
    for test_s in args.seasons:
        train_s = [s for s in args.seasons if s != test_s]
        tb = bases(train_s, tables[test_s])
        inner = pd.concat([bases([t for t in train_s if t != s], tables[s]) for s in train_s],
                          ignore_index=True)
        test = tb.copy()
        for v in variants:
            test[f"{v}_cal"], _ = sc.calibrate(inner[v], inner, tb[v], tb)
        pw = bt.pairwise(test.loc[test["sleeper"] >= 5.0], systems)
        folds[test_s] = {n: {"pairwise": round(pw[n], 6),
                             "mae": round(float((test[n] - test["actual"]).abs().mean()), 4)}
                         for n in systems}
        print(f"fold {test_s}: " + "  ".join(
            f"{n} {folds[test_s][n]['pairwise']:.2%}/{folds[test_s][n]['mae']:.3f}" for n in systems), flush=True)
    cv = {n: {m: round(float(np.mean([folds[t][n][m] for t in folds])), 4) for m in ("pairwise", "mae")}
          for n in systems}
    print("\ncross-validated mean (pairwise / MAE):")
    for n in systems:
        print(f"  {n:<13} {cv[n]['pairwise']:.2%}  {cv[n]['mae']:.3f}")
    for n in systems[1:]:
        ahead = sum(folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"] for t in folds)
        mae = sum(folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds)
        worst = min(folds[t][n]["pairwise"] - folds[t]["sleeper"]["pairwise"] for t in folds)
        print(f"  {n:<13} ordering ahead {ahead}/{len(folds)} (worst {worst:+.4f}), MAE better {mae}/{len(folds)}")
    strict = [n for n in systems[1:] if all(
        folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"] and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"]
        for t in folds)]
    print(f"\nbeats Sleeper on both metrics in EVERY fold: {strict or 'none'}")
    if args.out:
        args.out.write_text(json.dumps({"folds": folds, "cv": cv, "strict": strict}, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
