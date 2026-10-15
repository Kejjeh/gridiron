"""Rule #5 gate for serving the stack as the page's number.

    PYTHONPATH=src python scripts/research/stack_vs_page.py \
        [--tables data/research/cache/tables] [--out FILE.json]

Season-fold CV over 2019-2025 on the cached season tables
(`season{YYYY}_shipped.parquet`, built by `early_week_pattern.py` with the
shipped feature set). Per held-out season, on the harness universe (week >= 4,
Sleeper >= 5, skill positions):

  adv        the page today: per-position ridge on the shipped columns, fit on
             the other six seasons (out of fold)
  stack      the shipped stack form (`stack_form.kind == mix_calibrated`): the
             mean of (Sleeper + 0.5 x residual ridge) and the two-stage ridge on
             [adv_oof, sleeper, baseline], calibrated per position by the
             median line fit on inner-fold predictions — exactly
             `blend_search.py`'s mix_0.5_cal
  stack_mean the same mix BEFORE the median line: the mean-scale number the
             page serves, so it sums and compares with every other number on
             the page (the line is monotone per position, so its start/sit
             ordering equals `stack`'s; only the error differs)
  baseline   the usage baseline; sleeper: Sleeper's (historical) number;
  blend_b    (baseline + sleeper) / 2, a live-week curiosity

Reports pairwise start/sit and MAE per fold, and the bar: stack better than adv
on BOTH metrics in EVERY fold. Also by position per fold and by Sleeper tier
(both players in 5-10, 10-15, 15+, mixed), because the live shoot-out lives in
the 5-10 band. Research only; nothing here writes weights.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.models import advanced as A

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_common import calibrate, fit_pos, load_script, predict_pos  # noqa: E402

SEASONS = list(range(2019, 2026))
SHRINK = 0.5


def tier(a: float, b: float) -> str:
    def t(x):
        return "5-10" if x < 10 else ("10-15" if x < 15 else "15+")
    return t(a) if t(a) == t(b) else "mixed"


def pairwise_by(frame: pd.DataFrame, systems, key) -> dict:
    """bt.pairwise semantics (within week x position, ties skipped), split by
    `key(a, b)` on the two players' Sleeper numbers."""
    import itertools
    hits: dict = {}
    n: dict = {}
    for _, g in frame.groupby(["week", "position"]):
        rows = g.to_dict("records")
        for a, b in itertools.combinations(rows, 2):
            if a["actual"] == b["actual"]:
                continue
            k = key(a, b)
            n[k] = n.get(k, 0) + 1
            truth = a["actual"] > b["actual"]
            for s in systems:
                if a[s] != b[s] and (a[s] > b[s]) == truth:
                    hits[(k, s)] = hits.get((k, s), 0) + 1
    return {k: {"pairs": n[k], **{s: round(hits.get((k, s), 0) / n[k], 4) for s in systems}}
            for k in n}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tables", type=Path, default=Path("data/research/cache/tables"))
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    bt = load_script("projection_backtest", "scripts/research/projection_backtest.py")
    shipped = A.AdvancedModel.load()
    cols = {p: list(shipped.adv[p].cols) for p in A.POSITIONS}
    two = {p: ["adv_oof", "sleeper", "baseline"] for p in A.POSITIONS}
    keep = lambda d: d.loc[(d["week"] >= 4) & d["sleeper"].notna()].copy()  # noqa: E731
    tables = {s: keep(pd.read_parquet(args.tables / f"season{s}_shipped.parquet")) for s in SEASONS}
    for t in tables.values():
        t["resid"] = t["actual"] - t["sleeper"]

    def with_adv_oof(seasons):
        parts = []
        for s in seasons:
            others = [t for t in seasons if t != s] or [s]
            m = fit_pos(pd.concat([tables[t] for t in others]), cols, "actual")
            d = tables[s].copy()
            d["adv_oof"] = predict_pos(m, d).clip(lower=0)
            parts.append(d)
        return pd.concat(parts)

    def systems_for(train, train_s, test):
        """adv (out of fold) and the raw mix for `test`, models fit on `train`."""
        out = pd.DataFrame(index=test.index)
        out["adv"] = predict_pos(fit_pos(train, cols, "actual"), test).clip(lower=0)
        r = predict_pos(fit_pos(train, cols, "resid"), test)
        resid = (test["sleeper"] + SHRINK * r).clip(lower=0)
        tr2 = with_adv_oof(train_s)
        test = test.assign(adv_oof=out["adv"])
        two_stage = predict_pos(fit_pos(tr2, two, "actual"), test).clip(lower=0)
        out["mix"] = (resid + two_stage) / 2
        return out

    systems = ["baseline", "sleeper", "adv", "stack", "stack_mean", "blend_b"]
    folds, by_pos, by_tier = {}, {}, {}
    for test_s in SEASONS:
        train_s = [s for s in SEASONS if s != test_s]
        train = pd.concat([tables[s] for s in train_s])
        test = tables[test_s].copy()
        preds = systems_for(train, train_s, test)
        inner = pd.concat([systems_for(pd.concat([tables[t] for t in train_s if t != s]),
                                       [t for t in train_s if t != s], tables[s]) for s in train_s])
        inner_rows = pd.concat([tables[s] for s in train_s])
        test["adv"] = preds["adv"]
        test["stack"], lines = calibrate(inner["mix"], inner_rows, preds["mix"], test)
        test["stack_mean"] = preds["mix"]
        test["blend_b"] = (test["baseline"] + test["sleeper"]) / 2
        rel = test.loc[test["sleeper"] >= 5.0]
        pw = bt.pairwise(rel, systems)
        folds[test_s] = {s: {"pairwise": round(pw[s], 6),
                             "mae": round(float((test[s] - test["actual"]).abs().mean()), 4)}
                         for s in systems}
        folds[test_s]["pairs"] = pw["n"]
        folds[test_s]["by_pos_mae"] = {
            pos: {s: round(float((g[s] - g["actual"]).abs().mean()), 4) for s in systems}
            for pos, g in test.groupby("position")}
        by_pos[test_s] = {}
        for pos, g in rel.groupby("position"):
            p = bt.pairwise(g, systems)
            by_pos[test_s][pos] = {"pairs": p["n"], **{s: round(p[s], 4) for s in systems}}
        by_tier[test_s] = pairwise_by(rel, systems, lambda a, b: tier(a["sleeper"], b["sleeper"]))
        print(f"fold {test_s} ({pw['n']} pairs): " + "  ".join(
            f"{s} {folds[test_s][s]['pairwise']:.2%}/{folds[test_s][s]['mae']:.3f}" for s in systems),
            flush=True)

    cv = {s: {m: round(float(np.mean([folds[t][s][m] for t in SEASONS])), 4)
              for m in ("pairwise", "mae")} for s in systems}
    print("\ncross-validated mean (pairwise / MAE):")
    for s in systems:
        print(f"  {s:<9} {cv[s]['pairwise']:.2%}  {cv[s]['mae']:.3f}")

    def every_fold(better, worse):
        pw_ok = [folds[t][better]["pairwise"] > folds[t][worse]["pairwise"] for t in SEASONS]
        mae_ok = [folds[t][better]["mae"] < folds[t][worse]["mae"] for t in SEASONS]
        return {"pairwise_folds": sum(pw_ok), "mae_folds": sum(mae_ok),
                "both_every_fold": all(pw_ok) and all(mae_ok),
                "pairwise_delta_min": round(min(folds[t][better]["pairwise"]
                                                - folds[t][worse]["pairwise"] for t in SEASONS), 5),
                "pairwise_delta_mean": round(cv[better]["pairwise"] - cv[worse]["pairwise"], 5),
                "mae_delta_mean": round(cv[better]["mae"] - cv[worse]["mae"], 4)}

    bars = {"stack_vs_adv": every_fold("stack", "adv"),
            "stack_mean_vs_adv": every_fold("stack_mean", "adv"),
            "stack_mean_vs_sleeper": every_fold("stack_mean", "sleeper"),
            "stack_vs_baseline": every_fold("stack", "baseline"),
            "stack_vs_sleeper": every_fold("stack", "sleeper"),
            "adv_vs_baseline": every_fold("adv", "baseline"),
            "blend_b_vs_adv": every_fold("blend_b", "adv"),
            "stack_vs_blend_b": every_fold("stack", "blend_b")}
    print("\nbars (better on both metrics in every fold?):")
    for k, v in bars.items():
        print(f"  {k:<18} pairwise {v['pairwise_folds']}/7  MAE {v['mae_folds']}/7  both every fold: "
              f"{v['both_every_fold']}  (min pw delta {v['pairwise_delta_min']:+.4f}, "
              f"mean {v['pairwise_delta_mean']:+.4f}, MAE {v['mae_delta_mean']:+.3f})")

    print("\nby position, stack - adv pairwise per fold (stack_mean orders identically):")
    for pos in A.POSITIONS:
        ds = [by_pos[t][pos]["stack"] - by_pos[t][pos]["adv"] for t in SEASONS if pos in by_pos[t]]
        print(f"  {pos}: " + " ".join(f"{d:+.3f}" for d in ds) + f"  | ahead {sum(d > 0 for d in ds)}/{len(ds)}")
    print("\nby position, MAE per fold (adv -> stack_mean):")
    for pos in A.POSITIONS:
        cells = []
        for t in SEASONS:
            g = tables[t]
            # MAE by position needs the per-row predictions; recorded below
            cells.append(f"{folds[t]['by_pos_mae'][pos]['adv']:.2f}->{folds[t]['by_pos_mae'][pos]['stack_mean']:.2f}")
        ok = sum(folds[t]['by_pos_mae'][pos]['stack_mean'] < folds[t]['by_pos_mae'][pos]['adv'] for t in SEASONS)
        print(f"  {pos}: " + " ".join(cells) + f"  | better {ok}/7")
    print("\nby Sleeper tier (pooled over folds), pairwise:")
    for k in ("5-10", "10-15", "15+", "mixed"):
        pairs = sum(by_tier[t].get(k, {}).get("pairs", 0) for t in SEASONS)
        line = []
        for s in systems:
            hits = sum(by_tier[t][k][s] * by_tier[t][k]["pairs"] for t in SEASONS if k in by_tier[t])
            line.append(f"{s} {hits / pairs:.1%}")
        ahead = sum(by_tier[t][k]["stack"] > by_tier[t][k]["adv"] for t in SEASONS if k in by_tier[t])
        print(f"  {k:<6} {pairs:>6} pairs: " + "  ".join(line) + f"  | stack>adv in {ahead}/7 folds")

    res = {"seasons": SEASONS, "universe": "week >= 4, Sleeper >= 5, skill positions",
           "stack_form": "mix_0.5_cal (blend_search.py), adv out of fold", "folds": folds,
           "cv": cv, "bars": bars, "by_position": by_pos,
           "by_tier": {str(t): v for t, v in by_tier.items()}}
    if args.out:
        args.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
