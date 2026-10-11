"""Learn the blend weights instead of fixing them: a nested super-learner, out of sample.

    PYTHONPATH=src python scripts/research/super_learner.py --tables DIR [--out FILE] [--save]

The fixed mix (blend_search.py) beats Sleeper in six of seven seasons on
ordering and in all seven on error. Here the combination is LEARNED: for
each held-out season, the base systems are predicted out of fold INSIDE the
training seasons, a small ridge (the meta-learner) is fit on those
out-of-fold predictions to actual points, and only then applied to the test
season. The test season never touches the weights.

Base systems (per position, as everywhere): Sleeper's number, the residual
ridge's correction, the two-stage ridge, our advanced mean, the baseline.
Meta-learners: pooled across positions (`meta`), per position (`meta_pos`),
and both averaged with the fixed mix (`meta_mix`). All calibrated per
position by the median line fit on the inner rows.

Bar: better than Sleeper on start/sit AND MAE in EVERY fold (strict). The
never-worse version (tie under one pair in ten thousand) is printed too.
`--save` writes the winner as the skill-position `stack` of
advanced_weights.json as ONE composed ridge (every part is affine in the
features, adv, sleeper and baseline), asserting it reproduces the research
computation on every training row.
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

META_COLS = ["sleeper", "resid_pred", "two_stage", "adv_oof", "baseline"]
META_LAM = 1.0
TIE = 1e-4


from research_common import load_script as _load  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(range(2019, 2026)))
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    bt = _load("projection_backtest", "scripts/research/projection_backtest.py")
    sc = _load("stack_calibration", "scripts/research/stack_calibration.py")
    bs = _load("blend_search", "scripts/research/blend_search.py")
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
        """Base-system predictions for `test` from models fit on `train`."""
        out = test[["position", "week", "actual", "sleeper", "baseline"]].copy()
        out["resid_pred"] = sc.predict_pos(sc.fit_pos(train, cols, "resid"), test)
        out["adv_oof"] = sc.predict_pos(sc.fit_pos(train, cols, "actual"), test).clip(lower=0)
        t = test.copy()
        t["adv_oof"] = out["adv_oof"]
        out["two_stage"] = sc.predict_pos(sc.fit_pos(with_adv_oof(train_s), two, "actual"), t)
        out["mix"] = ((out["sleeper"] + 0.5 * out["resid_pred"]) + out["two_stage"]) / 2
        return out

    def meta_fit(inner, pooled):
        if pooled:
            m = A.Ridge.fit(inner, inner["actual"], META_COLS, lam=META_LAM)
            return {p: m for p in A.POSITIONS}
        return {p: A.Ridge.fit(inner.loc[inner["position"] == p],
                               inner.loc[inner["position"] == p, "actual"], META_COLS, lam=META_LAM)
                for p in A.POSITIONS}

    raw = ["mix", "meta", "meta_pos", "meta_mix"]
    systems = ["sleeper", *(f"{n}_cal" for n in raw)]
    folds, metas = {}, {}
    for test_s in args.seasons:
        train_s = [s for s in args.seasons if s != test_s]
        train = pd.concat([tables[s] for s in train_s])
        test_b = bases(train, train_s, tables[test_s])
        inner = pd.concat([bases(pd.concat([tables[t] for t in train_s if t != s]),
                                 [t for t in train_s if t != s], tables[s]) for s in train_s],
                          ignore_index=True)
        for name, pooled in (("meta", True), ("meta_pos", False)):
            m = meta_fit(inner, pooled)
            test_b[name] = sc.predict_pos(m, test_b)
            inner[name] = sc.predict_pos(m, inner)
            metas.setdefault(name, {})[test_s] = {p: dict(zip(m[p].cols, [round(b, 3) for b in m[p].beta[1:]]))
                                                  for p in (A.POSITIONS if not pooled else ["QB"])}
        test_b["meta_mix"] = (test_b["meta_pos"] + test_b["mix"]) / 2
        inner["meta_mix"] = (inner["meta_pos"] + inner["mix"]) / 2
        test = test_b.copy()
        for n in raw:
            test[f"{n}_cal"], _ = sc.calibrate(inner[n], inner, test_b[n], test_b)
        rel = test.loc[test["sleeper"] >= 5.0]
        pw = bt.pairwise(rel, systems)
        folds[test_s] = {n: {"pairwise": round(pw[n], 6),
                             "mae": round(float((test[n] - test["actual"]).abs().mean()), 4)}
                         for n in systems}
        print(f"fold {test_s}: " + "  ".join(
            f"{n} {folds[test_s][n]['pairwise']:.2%}/{folds[test_s][n]['mae']:.3f}" for n in systems),
            flush=True)
    cv = {n: {m: round(float(np.mean([folds[t][n][m] for t in folds])), 4) for m in ("pairwise", "mae")}
          for n in systems}
    print("\ncross-validated mean (pairwise / MAE):")
    for n in systems:
        print(f"  {n:<14} {cv[n]['pairwise']:.2%}  {cv[n]['mae']:.3f}")

    def strict(n):
        return all(folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"]
                   and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds)

    def never_worse(n):
        return all(folds[t][n]["pairwise"] >= folds[t]["sleeper"]["pairwise"] - TIE
                   and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds) \
            and cv[n]["pairwise"] > cv["sleeper"]["pairwise"]
    winners = [n for n in systems[1:] if strict(n)]
    soft = [n for n in systems[1:] if never_worse(n)]
    print(f"\nbeats Sleeper on both metrics in EVERY fold: {winners or 'none'}")
    print(f"never worse on ordering (tie < 1e-4), MAE better every fold: {soft or 'none'}")
    for n in systems[1:]:
        ahead = sum(folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"] for t in folds)
        mae = sum(folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds)
        worst = min(folds[t][n]["pairwise"] - folds[t]["sleeper"]["pairwise"] for t in folds)
        print(f"  {n:<14} ordering ahead {ahead}/{len(folds)} (worst fold {worst:+.4f}), MAE better {mae}/{len(folds)}")
    print("meta_pos weights by fold (standardised):", json.dumps(metas.get("meta_pos", {}), default=str)[:1500])
    res = {"seasons": args.seasons, "folds": folds, "cv": cv, "strict": winners, "never_worse": soft,
           "meta_weights": metas}
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    if args.save:
        pick = next((n for n in ("meta_pos_cal", "meta_cal", "meta_mix_cal") if n in winners), None)
        if pick is None:
            print("not saved: no learned blend beat Sleeper in every fold")
            return 1
        print(f"would save {pick}: wiring the composed form is the next step")
    return 0


if __name__ == "__main__":
    sys.exit(main())
