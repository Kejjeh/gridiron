"""Can the stack (our features + Sleeper's number) beat Sleeper on BOTH ordering and error?

    PYTHONPATH=src python scripts/research/stack_calibration.py --tables DIR [--save]

The shipped stack orders start/sit pairs a hair better than Sleeper out of
sample but its point error is WORSE (a ridge with every feature free drifts
from a well-calibrated input). Variants, all season-fold CV 2023-2025 on the
same rows as the other backtests:

  sleeper        Sleeper's projection, scored with the league's rules
  stack          the shipped stack: ridge on features + sleeper
  resid          sleeper + ridge(features) fit to (actual - sleeper): the
                 Sleeper weight is not penalised toward zero
  resid_k        sleeper + k * ridge-residual, k in --shrink (shrunk toward
                 Sleeper where the residual model is noise)
  two_stage      ridge on [adv (out-of-fold), sleeper, baseline]: two good
                 numbers blended, nothing else

Every variant is also CALIBRATED (`_cal`): a per-position median-regression
line fit on out-of-fold training predictions. The line is monotone, so the
start/sit ordering is exactly the raw variant's; only the point error moves.

The bar: point error better than Sleeper in EVERY fold, ordering never worse
in any fold (a tie stands: within one pair in ten thousand) and better on
the mean. The strict version (both
better in every fold) is printed too. The calibrated two-stage blend is the
form wired for serving (`--save` writes it as the skill-position `stack` of
advanced_weights.json, the line folded into the coefficients); it stays a
live CONTENDER — the shoot-out decides what drives the page (docs/DECISIONS.md).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT


from research_common import (calibrate, fit_pos, lad_line, load_script as _load,  # noqa: E402
                             predict_pos)

#: Ordering differences below one pair in ten thousand are a tie.
TIE = 1e-4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--shrink", type=float, nargs="+", default=[0.5, 0.7, 0.85])
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--tables", type=Path, default=None)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--save-contender", action="store_true",
                    help="save the calibrated two-stage form even when the bar is not cleared, "
                         "recording that it was not (it is a shoot-out contender, not the page)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    bt = _load("projection_backtest", "scripts/research/projection_backtest.py")
    ab = _load("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
    cw = Crosswalk.from_csv(args.crosswalk)
    shipped = A.AdvancedModel.load()
    cols = {p: list(shipped.adv[p].cols) for p in A.POSITIONS}
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
    for s in tables:
        tables[s] = keep(tables[s])
        tables[s]["resid"] = tables[s]["actual"] - tables[s]["sleeper"]
    two = {p: ["adv_oof", "sleeper", "baseline"] for p in A.POSITIONS}
    #: the two numbers plus the news-type features Sleeper might lag on
    plus = {p: ["adv_oof", "sleeper", "baseline", "snap_last", "opp_last", "depth_rank",
                "vacated_pickup"] for p in A.POSITIONS}

    def with_adv_oof(seasons):
        """Each season's rows with `adv_oof`: the adv model fit on the OTHER
        seasons of `seasons` (one season alone: fit on itself, flagged)."""
        parts = []
        for s in seasons:
            others = [t for t in seasons if t != s] or [s]
            m = fit_pos(pd.concat([tables[t] for t in others]), cols, "actual")
            d = tables[s].copy()
            d["adv_oof"] = predict_pos(m, d).clip(lower=0)
            parts.append(d)
        return pd.concat(parts)

    raw = ["stack", "resid"] + [f"resid_{k:g}" for k in args.shrink] + ["two_stage", "two_plus"]
    systems = ["sleeper", *raw, *(f"{n}_cal" for n in raw)]
    cal_lines: dict = {}
    folds = {}
    oof_two: list[pd.DataFrame] = []       # every season's two_stage, out of fold

    def systems_for(train, train_s, test):
        """Every raw system's prediction for `test`, from models fit on `train`."""
        out = pd.DataFrame(index=test.index)
        out["stack"] = predict_pos(fit_pos(train, {p: cols[p] + ["sleeper"] for p in cols},
                                           "actual"), test).clip(lower=0)
        r = predict_pos(fit_pos(train, cols, "resid"), test)
        out["resid"] = (test["sleeper"] + r).clip(lower=0)
        for k in args.shrink:
            out[f"resid_{k:g}"] = (test["sleeper"] + k * r).clip(lower=0)
        tr2 = with_adv_oof(train_s)
        test = test.copy()
        test["adv_oof"] = predict_pos(fit_pos(train, cols, "actual"), test).clip(lower=0)
        out["two_stage"] = predict_pos(fit_pos(tr2, two, "actual"), test).clip(lower=0)
        out["two_plus"] = predict_pos(fit_pos(tr2, plus, "actual"), test).clip(lower=0)
        return out

    for test_s in args.seasons:
        train_s = [s for s in args.seasons if s != test_s]
        train = pd.concat([tables[s] for s in train_s])
        test = tables[test_s].copy()
        preds = systems_for(train, train_s, test)
        # out-of-fold predictions INSIDE the train seasons, for the calibration
        inner = []
        for s in train_s:
            others = [t for t in train_s if t != s] or [s]
            inner.append(systems_for(pd.concat([tables[t] for t in others]), others, tables[s]))
        inner_pred, inner_rows = pd.concat(inner), pd.concat([tables[s] for s in train_s])
        for n in raw:
            test[n] = preds[n]
            test[f"{n}_cal"], lines = calibrate(inner_pred[n], inner_rows, preds[n], test)
            cal_lines.setdefault(n, {})[test_s] = lines
        rel = test.loc[test["sleeper"] >= 5.0]
        pw, cc = bt.pairwise(rel, systems), bt.pairwise(rel, systems, close=3.0)
        folds[test_s] = {n: {"pairwise": round(pw[n], 6), "close": round(cc[n], 6),
                             "mae": round(float((test[n] - test["actual"]).abs().mean()), 4)}
                         for n in systems}
        oof_two.append(pd.DataFrame({"two_stage": preds["two_stage"]}, index=test.index))
        print(f"fold {test_s} ({pw['n']} pairs): " + "  ".join(
            f"{n} {folds[test_s][n]['pairwise']:.2%}/{folds[test_s][n]['mae']:.3f}" for n in systems))
    cv = {n: {m: round(float(np.mean([folds[t][n][m] for t in folds])), 4)
              for m in ("pairwise", "close", "mae")} for n in systems}
    print("\ncross-validated mean (pairwise | close | MAE):")
    for n in systems:
        print(f"  {n:<10} {cv[n]['pairwise']:.2%} {cv[n]['close']:.2%} {cv[n]['mae']:.3f}")

    def beats_sleeper(n):
        return all(folds[t][n]["pairwise"] > folds[t]["sleeper"]["pairwise"]
                   and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds)

    def never_worse(n):
        """MAE better in every fold; ordering never worse in any fold (a tie
        stands — within TIE, one pair in ten thousand) and better on the mean."""
        return all(folds[t][n]["pairwise"] >= folds[t]["sleeper"]["pairwise"] - TIE
                   and folds[t][n]["mae"] < folds[t]["sleeper"]["mae"] for t in folds) \
            and cv[n]["pairwise"] > cv["sleeper"]["pairwise"]
    strict = [n for n in systems if n != "sleeper" and beats_sleeper(n)]
    winners = [n for n in systems if n != "sleeper" and never_worse(n)]
    print(f"\nbeats Sleeper on pairwise AND MAE in every fold: {strict or 'none'}")
    print(f"MAE better every fold, ordering never worse in any fold and better on the mean: "
          f"{winners or 'none'}")
    res = {"seasons": args.seasons, "folds": folds, "cv": cv, "winners": winners, "strict": strict,
           "calibration_lines": {n: {str(t): v for t, v in d.items()} for n, d in cal_lines.items()}}
    print("calibration lines (a, b) by fold, two_stage:", json.dumps(cal_lines.get("two_stage", {}), default=str))
    if args.out:
        args.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    if (args.save and winners) or args.save_contender:
        if args.save and winners and "two_stage_cal" not in winners:
            raise SystemExit(f"winners {winners}: only the calibrated two-stage form is wired "
                             "for serving")
        # final fit: two-stage ridge on every season's out-of-fold adv, then
        # the median line on every season's OUT-OF-FOLD two_stage prediction,
        # folded into the ridge (a + b * ridge(x) is still a ridge)
        every = with_adv_oof(args.seasons)
        two_fit = fit_pos(every, two, "actual")
        # folds ran in args.seasons order, so the out-of-fold predictions line
        # up positionally with the rows (season indices overlap: never join on them)
        oof = pd.concat(oof_two)["two_stage"].to_numpy(dtype=float)
        rows = pd.concat([tables[t] for t in args.seasons])
        assert len(oof) == len(rows)
        stack, lines = {}, {}
        for p, m in two_fit.items():
            sel = (rows["position"] == p).to_numpy()
            a, b = lad_line(oof[sel], rows.loc[sel, "actual"].to_numpy(dtype=float))
            b = max(b, 1e-6)
            lines[p] = (round(a, 4), round(b, 4))
            beta = (a + b * m.beta[0], *(b * x for x in m.beta[1:]))
            stack[p] = A.Ridge(("adv", "sleeper", "baseline"), m.fill, m.mu, m.sd, beta)
        meta = dict(shipped.meta)
        meta["stack_form"] = {
            "kind": "two_stage_calibrated", "inputs": ["adv", "sleeper", "baseline"],
            "calibration": lines,
            "note": "ridge on the page's advanced mean (out of fold in training), Sleeper's "
                    "projection and the baseline, then a per-position median-regression line "
                    "folded into the coefficients; ordering is the ridge's, the error is the line's"}
        meta["stack_evidence"] = {"seasons": args.seasons, "winner": "two_stage_cal",
                                  "bar_cleared": "two_stage_cal" in winners,
                                  "winners": winners, "strict": strict, "cv": cv,
                                  "folds": {str(t): v for t, v in folds.items()},
                                  "doc": "docs/research/STACK_CALIBRATION.md"}
        meta["written"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        new_stack = dict(shipped.stack)
        new_stack.update(stack)
        A.AdvancedModel(shipped.adv, new_stack, meta).save()
        print(f"saved stack (two_stage_cal, bar cleared: {'two_stage_cal' in winners}, lines "
              f"{lines}) into {A.WEIGHTS_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
