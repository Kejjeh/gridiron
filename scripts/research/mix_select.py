"""Pick the mix's two knobs by ordering, on inner folds only. Out of sample.

    PYTHONPATH=src python scripts/research/mix_select.py --tables DIR [--out FILE]

The fixed mix — mean of (Sleeper + k x residual ridge) and the two-stage
ridge, k = 0.5, equal parts — beats Sleeper in six of seven seasons on
ordering. Here k and the part weight w (on the residual form) are chosen per
held-out season from a small grid by start/sit pairwise success on the
TRAINING seasons' out-of-fold predictions — the metric the page cares about,
never the test season. Two variants: one (k, w) per position, and one
pooled pair for all positions (lower variance). Calibrated per position
afterwards (ordering untouched). Bar: beats Sleeper on both metrics in
every fold.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT

KS = (0.3, 0.5, 0.7)
WS = (0.25, 0.5, 0.75)


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def mix(b: pd.DataFrame, k: float, w: float) -> pd.Series:
    return w * (b["sleeper"] + k * b["resid_pred"]) + (1 - w) * b["two_stage"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(range(2019, 2026)))
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
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

    def with_adv_oof(seasons):
        parts = []
        for s in seasons:
            others = [t for t in seasons if t != s] or [s]
            m = sc.fit_pos(pd.concat([tables[t] for t in others]), cols, "actual")
            d = tables[s].copy()
            d["adv_oof"] = sc.predict_pos(m, d).clip(lower=0)
            parts.append(d)
        return pd.concat(parts)

    def bases(train, train_s, test):
        out = test[["position", "week", "actual", "sleeper", "baseline"]].copy()
        out["resid_pred"] = sc.predict_pos(sc.fit_pos(train, cols, "resid"), test)
        t = test.copy()
        t["adv_oof"] = sc.predict_pos(sc.fit_pos(train, cols, "actual"), test).clip(lower=0)
        out["two_stage"] = sc.predict_pos(sc.fit_pos(with_adv_oof(train_s), two, "actual"), t)
        return out

    grid = list(itertools.product(KS, WS))
    gname = lambda k, w: f"k{k:g}_w{w:g}"  # noqa: E731
    raw = ["fixed", "sel_pos", "sel_pool"]
    systems = ["sleeper", *(f"{n}_cal" for n in raw)]
    folds, chosen = {}, {}
    for test_s in args.seasons:
        train_s = [s for s in args.seasons if s != test_s]
        train = pd.concat([tables[s] for s in train_s])
        tb = bases(train, train_s, tables[test_s])
        inner = pd.concat([bases(pd.concat([tables[t] for t in train_s if t != s]),
                                 [t for t in train_s if t != s], tables[s]) for s in train_s],
                          ignore_index=True)
        for k, w in grid:
            inner[gname(k, w)] = mix(inner, k, w)
        rel = inner.loc[inner["sleeper"] >= 5.0]
        names = [gname(k, w) for k, w in grid]
        per_pos = {p: bt.pairwise(rel.loc[rel["position"] == p], names) for p in A.POSITIONS}
        pooled = bt.pairwise(rel, names)
        pick_pos = {p: max(grid, key=lambda kw: per_pos[p][gname(*kw)]) for p in A.POSITIONS}
        pick_pool = max(grid, key=lambda kw: pooled[gname(*kw)])
        chosen[test_s] = {"per_position": {p: list(v) for p, v in pick_pos.items()}, "pooled": list(pick_pool)}
        tb["fixed"], inner["fixed"] = mix(tb, 0.5, 0.5), mix(inner, 0.5, 0.5)
        tb["sel_pool"], inner["sel_pool"] = mix(tb, *pick_pool), mix(inner, *pick_pool)
        tb["sel_pos"] = pd.Series(index=tb.index, dtype="float64")
        inner["sel_pos"] = pd.Series(index=inner.index, dtype="float64")
        for p, kw in pick_pos.items():
            tb.loc[tb["position"] == p, "sel_pos"] = mix(tb.loc[tb["position"] == p], *kw)
            inner.loc[inner["position"] == p, "sel_pos"] = mix(inner.loc[inner["position"] == p], *kw)
        test = tb.copy()
        for n in raw:
            test[f"{n}_cal"], _ = sc.calibrate(inner[n], inner, tb[n], tb)
        pw = bt.pairwise(test.loc[test["sleeper"] >= 5.0], systems)
        folds[test_s] = {n: {"pairwise": round(pw[n], 6),
                             "mae": round(float((test[n] - test["actual"]).abs().mean()), 4)}
                         for n in systems}
        print(f"fold {test_s}: " + "  ".join(
            f"{n} {folds[test_s][n]['pairwise']:.2%}/{folds[test_s][n]['mae']:.3f}" for n in systems)
            + f"   chosen pooled {pick_pool}, per position {pick_pos}", flush=True)
    cv = {n: {m: round(float(np.mean([folds[t][n][m] for t in folds])), 4) for m in ("pairwise", "mae")}
          for n in systems}
    print("\ncross-validated mean (pairwise / MAE):")
    for n in systems:
        print(f"  {n:<12} {cv[n]['pairwise']:.2%}  {cv[n]['mae']:.3f}")
    for n in systems[1:]:
        ahead = sum(folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"] for t in folds)
        mae = sum(folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds)
        worst = min(folds[t][n]["pairwise"] - folds[t]["sleeper"]["pairwise"] for t in folds)
        print(f"  {n:<12} ordering ahead {ahead}/{len(folds)} (worst {worst:+.4f}), MAE better {mae}/{len(folds)}")
    strict = [n for n in systems[1:] if all(
        folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"] and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"]
        for t in folds)]
    print(f"\nbeats Sleeper on both metrics in EVERY fold: {strict or 'none'}")
    if args.out:
        args.out.write_text(json.dumps({"folds": folds, "cv": cv, "chosen": chosen, "strict": strict},
                                       indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
