"""Can any blend of ours and Sleeper's beat Sleeper on BOTH metrics in every season?

    PYTHONPATH=src python scripts/research/blend_search.py --tables DIR [--save]

Season-fold CV over 2019-2025 (the same rows, universe and metrics as
stack_calibration.py, whose helpers this reuses). The two best forms there
had complementary strengths: the shrunk residual (Sleeper + k x ridge
residual) ordered best, the two-stage ridge had the best error. Candidates,
all calibrated per position by the median line fit on out-of-fold training
predictions:

  resid_k      Sleeper + k x ridge(features) fit to actual - Sleeper, k in --shrink
  two_stage    ridge on [adv (out of fold), sleeper, baseline]
  mix_k        the mean of resid_k and two_stage
  zblend_w     within each (week, position): w x z(sleeper) + (1-w) x z(two_stage),
               rescaled to Sleeper's spread and centre — a rank-level ensemble

Bar: MAE better than Sleeper in every fold and ordering never worse in any
fold (tie = under one pair in ten thousand), better on the mean. `--save`
writes the winner only if it has a serving form (resid_k or two_stage; the
mix and the z-blend would need new serving code and are reported only).
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


from research_common import load_script as _load  # noqa: E402


def zblend(test, w):
    out = pd.Series(index=test.index, dtype="float64")
    for _, g in test.groupby(["week", "position"]):
        s, t = g["sleeper"], g["two_stage"]
        zs = (s - s.mean()) / (s.std() or 1.0)
        zt = (t - t.mean()) / (t.std() or 1.0)
        out[g.index] = s.mean() + (s.std() or 1.0) * (w * zs + (1 - w) * zt)
    return out.clip(lower=0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(range(2019, 2026)))
    ap.add_argument("--shrink", type=float, nargs="+", default=[0.3, 0.4, 0.5, 0.6])
    ap.add_argument("--zw", type=float, nargs="+", default=[0.5, 0.6, 0.7])
    ap.add_argument("--tables", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--save-mix", type=float, default=None, metavar="K",
                    help="save mix_K (calibrated) as the skill-position stack: one composed ridge")
    ap.add_argument("--evidence", type=Path, default=None,
                    help="a previous --out file whose folds/cv are recorded with the save")
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

    raw = [f"resid_{k:g}" for k in args.shrink] + ["two_stage"] + \
          [f"mix_{k:g}" for k in args.shrink] + [f"zblend_{w:g}" for w in args.zw]

    def systems_for(train, train_s, test):
        out = pd.DataFrame(index=test.index)
        r = sc.predict_pos(sc.fit_pos(train, cols, "resid"), test)
        for k in args.shrink:
            out[f"resid_{k:g}"] = (test["sleeper"] + k * r).clip(lower=0)
        tr2 = with_adv_oof(train_s)
        test = test.copy()
        test["adv_oof"] = sc.predict_pos(sc.fit_pos(train, cols, "actual"), test).clip(lower=0)
        out["two_stage"] = sc.predict_pos(sc.fit_pos(tr2, two, "actual"), test).clip(lower=0)
        for k in args.shrink:
            out[f"mix_{k:g}"] = (out[f"resid_{k:g}"] + out["two_stage"]) / 2
        test["two_stage"] = out["two_stage"]
        for w in args.zw:
            out[f"zblend_{w:g}"] = zblend(test, w)
        return out

    if args.save_mix is not None:
        return save_mix(args, tables, cols, two, sc, shipped, with_adv_oof)

    systems = ["sleeper", *(f"{n}_cal" for n in raw)]
    folds = {}
    for test_s in args.seasons:
        train_s = [s for s in args.seasons if s != test_s]
        train = pd.concat([tables[s] for s in train_s])
        test = tables[test_s].copy()
        preds = systems_for(train, train_s, test)
        inner = pd.concat([systems_for(pd.concat([tables[t] for t in train_s if t != s]), 
                                       [t for t in train_s if t != s], tables[s]) for s in train_s])
        inner_rows = pd.concat([tables[s] for s in train_s])
        for n in raw:
            test[f"{n}_cal"], _ = sc.calibrate(inner[n], inner_rows, preds[n], test)
        rel = test.loc[test["sleeper"] >= 5.0]
        pw = bt.pairwise(rel, systems)
        folds[test_s] = {n: {"pairwise": round(pw[n], 6),
                             "mae": round(float((test[n] - test["actual"]).abs().mean()), 4)}
                         for n in systems}
        print(f"fold {test_s}: " + "  ".join(f"{n} {folds[test_s][n]['pairwise']:.2%}/{folds[test_s][n]['mae']:.3f}"
                                           for n in systems), flush=True)
    cv = {n: {m: round(float(np.mean([folds[t][n][m] for t in folds])), 4) for m in ("pairwise", "mae")}
          for n in systems}
    print("\ncross-validated mean (pairwise / MAE):")
    for n in systems:
        print(f"  {n:<16} {cv[n]['pairwise']:.2%}  {cv[n]['mae']:.3f}")
    TIE = 1e-4
    def never_worse(n):
        return all(folds[t][n]["pairwise"] >= folds[t]["sleeper"]["pairwise"] - TIE
                   and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds) \
            and cv[n]["pairwise"] > cv["sleeper"]["pairwise"]
    def strict(n):
        return all(folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"]
                   and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds)
    print(f"\nstrict (both better every fold): {[n for n in systems[1:] if strict(n)] or 'none'}")
    print(f"never worse on ordering, MAE better every fold, mean ordering better: "
          f"{[n for n in systems[1:] if never_worse(n)] or 'none'}")
    for n in systems[1:]:
        ahead = sum(folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"] for t in folds)
        mae = sum(folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds)
        print(f"  {n:<16} ordering ahead in {ahead}/{len(folds)}, MAE better in {mae}/{len(folds)}")
    if args.out:
        args.out.write_text(json.dumps({"folds": folds, "cv": cv}, indent=1), encoding="utf-8")
    return 0


def raw_affine(r: A.Ridge) -> tuple[float, dict[str, float], dict[str, float]]:
    """A standardised ridge as (intercept, raw-unit weight per col, fill per col)."""
    w = {c: b / sd for c, b, sd in zip(r.cols, r.beta[1:], r.sd)}
    c0 = r.beta[0] - sum(b * mu / sd for b, mu, sd in zip(r.beta[1:], r.mu, r.sd))
    return float(c0), w, dict(zip(r.cols, r.fill))


def compose_mix(resid: A.Ridge, two: A.Ridge, k: float, a: float, b: float) -> A.Ridge:
    """a + b * (0.5 * (sleeper + k * resid(x)) + 0.5 * two(x)) as ONE ridge with
    mu 0, sd 1 (raw units), fills carried from the parts."""
    r0, rw, rf = raw_affine(resid)
    t0, tw, tf = raw_affine(two)
    weights: dict[str, float] = {}
    fills: dict[str, float] = {}
    for c, wv in rw.items():
        weights[c] = weights.get(c, 0.0) + 0.5 * k * wv
        fills[c] = rf[c]
    for c, wv in tw.items():
        name = "adv" if c == "adv_oof" else c       # serving supplies the page's mean as `adv`
        weights[name] = weights.get(name, 0.0) + 0.5 * wv
        fills.setdefault(name, tf[c])
    weights["sleeper"] = weights.get("sleeper", 0.0) + 0.5
    fills.setdefault("sleeper", tf.get("sleeper", 0.0))
    cols = tuple(weights)
    beta0 = a + b * (0.5 * k * r0 + 0.5 * t0)
    return A.Ridge(cols, tuple(fills[c] for c in cols), tuple(0.0 for _ in cols),
                   tuple(1.0 for _ in cols), (beta0, *(b * weights[c] for c in cols)))


def save_mix(args, tables, cols, two, sc, shipped, with_adv_oof) -> int:
    from datetime import datetime, timezone
    k = args.save_mix
    seasons = list(tables)
    # out-of-fold mix per season (for the calibration line), raw
    oof = []
    for test_s in seasons:
        train_s = [s for s in seasons if s != test_s]
        train = pd.concat([tables[s] for s in train_s])
        test = tables[test_s].copy()
        r = sc.predict_pos(sc.fit_pos(train, cols, "resid"), test)
        test["adv_oof"] = sc.predict_pos(sc.fit_pos(train, cols, "actual"), test).clip(lower=0)
        t = sc.predict_pos(sc.fit_pos(with_adv_oof(train_s), two, "actual"), test)
        mix = ((test["sleeper"] + k * r) + t) / 2         # unclipped: the serving ridge is affine
        oof.append(pd.DataFrame({"mix": mix.to_numpy(), "position": test["position"].to_numpy(),
                                 "actual": test["actual"].to_numpy()}))
    oof = pd.concat(oof, ignore_index=True)
    everything = pd.concat(tables.values())
    resid_fit = sc.fit_pos(everything, cols, "resid")
    two_fit = sc.fit_pos(with_adv_oof(seasons), two, "actual")
    stack, lines = {}, {}
    for p in A.POSITIONS:
        sel = (oof["position"] == p).to_numpy()
        a, b = sc.lad_line(oof.loc[sel, "mix"].to_numpy(dtype=float),
                           oof.loc[sel, "actual"].to_numpy(dtype=float))
        lines[p] = (round(a, 4), round(b, 4))
        stack[p] = compose_mix(resid_fit[p], two_fit[p], k, a, b)
        # the composed ridge must reproduce the parts on real rows, NaNs included
        rows = everything.loc[everything["position"] == p].copy()
        rows["adv"] = sc.predict_pos({p: A.Ridge.fit(rows, rows["actual"], cols[p])}, rows).clip(lower=0)
        direct = a + b * (0.5 * (rows["sleeper"] + k * resid_fit[p].predict(rows))
                          + 0.5 * two_fit[p].predict(rows.rename(columns={"adv": "adv_oof"})))
        got = stack[p].predict(rows)
        err = float(np.abs(got - direct).max())
        assert err < 1e-6, (p, err)
        print(f"  {p}: composed ridge reproduces the mix (max |diff| {err:.2e}); line a={a:.3f} b={b:.3f}")
    ev = json.loads(args.evidence.read_text()) if args.evidence else {}
    name = f"mix_{k:g}_cal"
    folds, cv = ev.get("folds", {}), ev.get("cv", {})
    bar = bool(folds) and all(
        f[name]["pairwise"] >= f["sleeper"]["pairwise"] - 1e-4 and f[name]["mae"] < f["sleeper"]["mae"]
        for f in folds.values()) and cv[name]["pairwise"] > cv["sleeper"]["pairwise"]
    meta = dict(shipped.meta)
    meta["stack_form"] = {
        "kind": "mix_calibrated", "shrink": k, "calibration": lines,
        "inputs": "features (+ baseline, ppg_to_date), adv, sleeper",
        "note": f"a + b * (0.5 * (sleeper + {k:g} * ridge(features) fit to actual - sleeper) + "
                "0.5 * ridge(adv, sleeper, baseline)), composed into one ridge in raw units; "
                "the per-position median line is folded in"}
    meta["stack_evidence"] = {
        "seasons": seasons, "winner": name, "bar_cleared": bar, "cv": cv, "folds": folds,
        "ordering_ahead_folds": sum(f[name]["pairwise"] > f["sleeper"]["pairwise"] for f in folds.values()),
        "mae_better_folds": sum(f[name]["mae"] < f["sleeper"]["mae"] for f in folds.values()),
        "doc": "docs/research/STACK_CALIBRATION.md"}
    meta["written"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    new_stack = dict(shipped.stack)
    new_stack.update(stack)
    A.AdvancedModel(shipped.adv, new_stack, meta).save()
    print(f"saved stack ({name}, bar cleared: {bar}) into {A.WEIGHTS_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
