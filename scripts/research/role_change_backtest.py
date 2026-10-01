"""Does a role-change signal help the weekly model catch a starter's injury faster? Out of sample.

    PYTHONPATH=src python scripts/research/role_change_backtest.py [--seasons 2023 2024 2025] [--save]

The shipped weekly model (`advanced_v1`) reads trailing three-game averages,
so the week after a starter's season-ending injury his backup is still
priced as a backup: the averages carry two backup games, and the vacated-
opportunity feature only sees teammates Out or Doubtful on THIS week's
report — a player placed on injured reserve is not on it. The candidate
block (`gridiron.models.advanced.ROLE_FEATURES`) reads the last game
against the trailing average, the player's share of his team's position-
group opportunities in the team's last game, and the per-game volume of
teammates who have gone missing from the box score (not Questionable —
rule #11).

Judge: season-fold cross-validation over the seasons given — each season is
the TEST season in turn and the others train — with the shipped feature set
as the baseline that contains ALL existing features (rule #5). Every
feature comes from `features_as_of`, the builder the page runs, one call
per week with only earlier weeks visible. Rows: weeks >= 4, players Sleeper
projected (the same universe as the shipped evidence).

Feature sets:
  shipped   the set `advanced_v1` ships (core + depth-chart rank)
  +last         shipped + last-game expected points, opportunities, snap share
  +share        shipped + share of the team's last game + jumps vs the trailing average
  +absent       shipped + volume of teammates missing from the box score
  +reserve      shipped + volume of teammates on a reserve list (weekly rosters)
  +reserve_lag  the same read from the week BEFORE's roster (a build that runs
                before the week's rosters are posted sees this)
  +ewm          shipped + exponentially weighted averages (half-life one game)
  combinations of the above, and +all

Metrics, per fold and as the mean over folds: start/sit pairwise success,
MAE, close calls (Sleeper within 3), and the same on the ROLE-CHANGE subset
(opportunity jump >= 5 or absent volume >= 3 per game) — where the block
has to earn its place. A set is eligible only if it beats `shipped` on
pairwise AND MAE in EVERY fold (a mean can be carried by one season); the
eligible set with the fewest features ships.

`--save` refits the winning skill-position models on every season and
writes them into `src/gridiron/models/advanced_weights.json`, keeping the K
and DEF entries and adding the evidence under `meta.role_change_evidence`.
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

LAST = ["xfp_last", "opp_last", "snap_last"]
SHARE = ["opp_share_last", "opp_jump", "snap_jump"]
ABSENT = ["absent_pickup"]
RESERVE = ["reserve_pickup"]
EWM = ["xfp_ewm", "opp_ewm", "snap_ewm"]
SETS = {"shipped": [], "+last": LAST, "+share": SHARE, "+absent": ABSENT,
        "+reserve": RESERVE, "+reserve_lag": ["reserve_pickup_lag"], "+ewm": EWM,
        "+last+reserve": LAST + RESERVE, "+ewm+reserve": EWM + RESERVE,
        "+last+share+reserve": LAST + SHARE + RESERVE,
        "+all": LAST + SHARE + ABSENT + RESERVE + EWM}
#: A player-week counts as a role change when his last game was a big jump
#: or a meaningful teammate is missing.
JUMP, ABSENT_MIN = 5.0, 3.0


from research_common import load_script as _load  # noqa: E402


def shipped_cols(model: A.AdvancedModel) -> dict[str, list[str]]:
    return {p: [c for c in model.adv[p].cols if c not in A.ROLE_FEATURES] for p in A.POSITIONS}


def fit(train: pd.DataFrame, cols: dict[str, list[str]], extra, *, stack: bool) -> dict:
    return {pos: A.Ridge.fit(train.loc[train["position"] == pos],
                             train.loc[train["position"] == pos, "actual"],
                             cols[pos] + list(extra) + (["sleeper"] if stack else []))
            for pos in A.POSITIONS}


def predict(models: dict, df: pd.DataFrame) -> pd.Series:
    out = pd.Series(index=df.index, dtype="float64")
    for pos, m in models.items():
        sel = df["position"] == pos
        out[sel] = m.predict(df[sel]).clip(min=0.0)
    return out


def role_subset(df: pd.DataFrame) -> pd.Series:
    return (df["opp_jump"].fillna(0) >= JUMP) | (df["absent_pickup"].fillna(0) >= ABSENT_MIN)


def metrics(test: pd.DataFrame, systems, bt) -> dict:
    rel = test.loc[test["sleeper"] >= 5.0]
    pw, cc = bt.pairwise(rel, systems), bt.pairwise(rel, systems, close=3.0)
    role = rel.loc[role_subset(rel)]
    rpw = bt.pairwise(role, systems) if len(role) else {"n": 0}
    out = {"rows": int(len(test)), "pairs": pw["n"], "role_rows": int(len(role)),
           "role_pairs": rpw["n"], "systems": {}}
    for s in systems:
        out["systems"][s] = {
            "mae": round(float((test[s] - test["actual"]).abs().mean()), 3),
            "pairwise": round(pw[s], 4), "close_calls": round(cc[s], 4),
            "role_mae": round(float((role[s] - role["actual"]).abs().mean()), 3) if len(role) else None,
            "role_pairwise": round(rpw[s], 4) if rpw["n"] else None}
    by_pos = {}
    for pos, g in rel.groupby("position"):
        p = bt.pairwise(g, systems)
        by_pos[pos] = {s: round(p[s], 4) for s in systems}
    out["by_position"] = by_pos
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    bt = _load("projection_backtest", "scripts/research/projection_backtest.py")
    ab = _load("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
    cw = Crosswalk.from_csv(args.crosswalk)
    shipped = A.AdvancedModel.load()
    cols = shipped_cols(shipped)
    print("building season tables (features from earlier weeks only)")
    tables = {s: ab.season_table(s, cw, bt, reserve_lag=True) for s in args.seasons}
    keep = lambda d: d.loc[(d["week"] >= args.min_week) & d["sleeper"].notna()]  # noqa: E731
    systems = ["baseline", "sleeper"] + [f"adv{n}" for n in SETS] + [f"stack{n}" for n in SETS]

    folds = {}
    for test_season in args.seasons:
        train_seasons = [s for s in args.seasons if s != test_season]
        train = keep(pd.concat([tables[s] for s in train_seasons]))
        test = keep(tables[test_season]).copy()
        for name, extra in SETS.items():
            test[f"adv{name}"] = predict(fit(train, cols, extra, stack=False), test)
            test[f"stack{name}"] = predict(fit(train, cols, extra, stack=True), test)
        folds[test_season] = metrics(test, systems, bt)
        weeks = []
        for w, g in test.loc[test["sleeper"] >= 5.0].groupby("week"):
            p = bt.pairwise(g, ["advshipped", "adv+all"])
            weeks.append(p["adv+all"] > p["advshipped"])
        folds[test_season]["weeks_role_beats_shipped"] = f"{sum(weeks)}/{len(weeks)}"
        _print(f"fold: test {test_season}, train {train_seasons}", folds[test_season], systems)

    cv = {"systems": {s: {m: round(float(np.mean([folds[t]["systems"][s][m] for t in folds])), 4)
                          for m in ("mae", "pairwise", "close_calls", "role_mae", "role_pairwise")}
                      for s in systems},
          "pairs": int(sum(folds[t]["pairs"] for t in folds)),
          "role_pairs": int(sum(folds[t]["role_pairs"] for t in folds)),
          "rows": int(sum(folds[t]["rows"] for t in folds)),
          "role_rows": int(sum(folds[t]["role_rows"] for t in folds)),
          "by_position": {pos: {s: round(float(np.mean(
              [folds[t]["by_position"][pos][s] for t in folds])), 4) for s in systems}
              for pos in A.POSITIONS}}
    _print(f"cross-validated mean over {args.seasons}", cv, systems)
    def every_fold(name: str) -> bool:
        return all(folds[t]["systems"][f"adv{name}"]["pairwise"] > folds[t]["systems"]["advshipped"]["pairwise"]
                   and folds[t]["systems"][f"adv{name}"]["mae"] < folds[t]["systems"]["advshipped"]["mae"]
                   for t in folds)
    # the bar: better than the shipped set on pairwise AND MAE in EVERY fold;
    # among those, the fewest features (a mean can be carried by one season)
    eligible = [n for n in SETS if n != "shipped" and every_fold(n)]
    best = (min(eligible, key=lambda n: (len(SETS[n]), -cv["systems"][f"adv{n}"]["pairwise"]))
            if eligible else "shipped")
    wins = best != "shipped"
    print(f"\nsets better than shipped in every fold on both metrics: {eligible or 'none'}; "
          f"ships (fewest features): {best}; whole-block weeks beaten per fold: "
          + ", ".join(f"{t} {folds[t]['weeks_role_beats_shipped']}" for t in folds))
    res = {"seasons": args.seasons, "min_week": args.min_week, "sets": SETS, "folds": folds,
           "cv": cv, "eligible": eligible, "winner": best, "ships": bool(wins),
           "role_subset": {"opp_jump_min": JUMP, "absent_pickup_min": ABSENT_MIN}}
    if args.out:
        args.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    if args.save:
        if not wins:
            print("not saved: the candidate did not beat the shipped set out of sample")
            return 1
        everything = keep(pd.concat(tables.values()))
        adv = dict(shipped.adv)
        stack = dict(shipped.stack)
        adv.update(fit(everything, cols, SETS[best], stack=False))
        stack.update(fit(everything, cols, SETS[best], stack=True))
        meta = dict(shipped.meta)
        meta["feature_set"] = f"{meta.get('feature_set', 'core')}{best}"
        meta["fit_on"] = sorted(args.seasons)
        meta["fit_rows"] = int(len(everything))
        meta["role_change_evidence"] = {
            "seasons": args.seasons, "winner": best, "eligible": eligible, "cv": cv,
            "folds": {str(t): {k: v for k, v in f.items() if k != "by_position"}
                      for t, f in folds.items()},
            "doc": "docs/research/ROLE_CHANGE_BACKTEST.md"}
        meta["written"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        A.AdvancedModel(adv, stack, meta).save()
        print(f"saved {A.WEIGHTS_PATH.relative_to(REPO_ROOT)} ({meta['feature_set']}, fit on "
              f"{len(everything)} player-weeks)")
    return 0


def _print(title: str, res: dict, systems) -> None:
    print(f"\n{title}: {res['rows']} rows, {res['pairs']} pairs; role-change subset "
          f"{res['role_rows']} rows, {res['role_pairs']} pairs")
    print(f"{'system':<14} {'MAE':>6} {'pairwise':>9} {'close':>7} {'roleMAE':>8} {'rolePW':>7}")
    for s in systems:
        m = res["systems"][s]
        print(f"{s:<14} {m['mae']:6.3f} {m['pairwise']:9.2%} {m['close_calls']:7.2%} "
              f"{(m['role_mae'] or 0):8.3f} {(m['role_pairwise'] or 0):7.2%}")
    print("by position (pairwise): " + " | ".join(
        f"{pos} " + " ".join(f"{s[3:] if s.startswith('adv') else s}={v[s]:.3f}"
                             for s in systems if s.startswith("adv") or s == "sleeper")
        for pos, v in res["by_position"].items()))


if __name__ == "__main__":
    sys.exit(main())
