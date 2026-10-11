"""Has the weekly ridge's strength ever been tuned? Out of sample.

    PYTHONPATH=src python scripts/research/ridge_strength_sweep.py [--lams 1 2 5 10 20 40]
        [--tables DIR]

Season-fold cross-validation (2023-2025) of `advanced_v1`'s shipped feature
set at several ridge penalties (`gridiron.models.advanced.Ridge.fit(lam=)`;
5.0 ships, chosen by habit, never by a test). Same rows, same universe and
same metrics as scripts/research/role_change_backtest.py. `--tables` caches
the season tables as parquet so a sweep does not rebuild features.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT


from research_common import load_script as _load  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--lams", type=float, nargs="+", default=[1.0, 2.0, 5.0, 10.0, 20.0, 40.0])
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--tables", type=Path, default=None, help="parquet cache of season tables")
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
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
    keep = lambda d: d.loc[(d["week"] >= args.min_week) & d["sleeper"].notna()]  # noqa: E731
    systems = [f"lam{g:g}" for g in args.lams]
    folds = {}
    for test_season in args.seasons:
        train = keep(pd.concat([tables[s] for s in args.seasons if s != test_season]))
        test = keep(tables[test_season]).copy()
        for lam, name in zip(args.lams, systems):
            pred = pd.Series(index=test.index, dtype="float64")
            for pos in A.POSITIONS:
                tr = train.loc[train["position"] == pos]
                m = A.Ridge.fit(tr, tr["actual"], cols[pos], lam=lam)
                sel = test["position"] == pos
                pred[sel] = m.predict(test[sel]).clip(min=0.0)
            test[name] = pred
        rel = test.loc[test["sleeper"] >= 5.0]
        pw = bt.pairwise(rel, systems)
        folds[test_season] = {n: {"pairwise": pw[n],
                                  "mae": float((test[n] - test["actual"]).abs().mean())}
                              for n in systems}
        print(f"fold {test_season}: " + "  ".join(
            f"{n} {folds[test_season][n]['pairwise']:.2%}/{folds[test_season][n]['mae']:.3f}"
            for n in systems))
    print("\ncross-validated mean (pairwise / MAE):")
    for n in systems:
        pw = np.mean([folds[t][n]["pairwise"] for t in folds])
        mae = np.mean([folds[t][n]["mae"] for t in folds])
        print(f"  {n:<8} {pw:.2%}  {mae:.3f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
