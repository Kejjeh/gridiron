"""More data: more seasons and more metrics, judged out of sample.

    PYTHONPATH=src python scripts/research/feature_expansion_backtest.py \\
        --seasons 2019 2020 2021 2022 2023 2024 2025 --tables DIR [--save]

Two questions, one harness (season-fold CV, the same rows, universe and
metrics as every other backtest here: weeks 4-18, players Sleeper projected).

1. MORE SEASONS. The shipped feature set, fit on the two neighbouring
   seasons (as the shipped evidence did) vs fit on EVERY other season, scored
   on the same held-out seasons. Does a bigger training set help a ridge?

2. MORE METRICS. Blocks of new features (`gridiron.models.advanced
   .EXPANSION_FEATURES`) on top of the shipped set, each season held out in
   turn and all others training:
     +routes  routes run (participation x play-by-play), route share, last
              game's routes, targets per route (RB/WR/TE)
     +xtd     expected touchdowns (last 3, season) and the player's share of
              his team's expected points (role)
     +epa     EPA per game, season (efficiency — slow, rule #6)
     +ngs2    depth of target, share of intended air yards, cushion, stacked
              boxes, rushing efficiency, time to throw, aggressiveness
     +line    the posted spread, wind and temperature
     +team    the offense's plays, pass rate and EPA per game (last 3)
     +pfr     drops, yards before/after contact, pressure and bad-throw rates
     +all     every block
   A block is eligible only if it beats the shipped set on start/sit AND MAE
   in EVERY fold; the eligible block with the fewest features ships (then a
   second pass adds the other eligible blocks to it, same bar).

`--save` refits the winning set on every season and writes the skill
positions of src/gridiron/models/advanced_weights.json (K and DEF kept),
with the evidence under `meta.expansion_evidence`.
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

BLOCKS = {
    "+routes": {"QB": [], "RB": ["routes_l3", "route_share_l3", "routes_last", "tprr_season"],
                "WR": ["routes_l3", "route_share_l3", "routes_last", "tprr_season"],
                "TE": ["routes_l3", "route_share_l3", "routes_last", "tprr_season"]},
    "+xtd": ["xtd_l3", "xtd_season", "xfp_share_l3"],
    "+epa": ["epa_pg"],
    "+ngs2": {"QB": ["ngs_ttt", "ngs_aggr"], "RB": ["ngs_box8", "ngs_rush_eff"],
              "WR": ["ngs_adot", "ngs_iay_share", "ngs_cushion"],
              "TE": ["ngs_adot", "ngs_iay_share", "ngs_cushion"]},
    "+line": ["spread", "wind", "temp"],
    "+team": ["team_plays_l3", "team_pass_rate_l3", "team_epa_l3"],
    "+pfr": {"QB": ["pfr_pressure_pct", "pfr_bad_throw_pct"], "RB": ["pfr_ybc", "pfr_yac"],
             "WR": ["pfr_drop_pct"], "TE": ["pfr_drop_pct"]},
}


def block_cols(name: str, pos: str) -> list[str]:
    if name == "+all":
        return [c for b in BLOCKS for c in block_cols(b, pos)]
    if name == "shipped":
        return []
    b = BLOCKS[name]
    return list(b[pos] if isinstance(b, dict) else b)


from research_common import load_script as _load  # noqa: E402


def fit_predict(train, test, cols):
    out = pd.Series(index=test.index, dtype="float64")
    for p in A.POSITIONS:
        tr, te = train["position"] == p, test["position"] == p
        m = A.Ridge.fit(train.loc[tr], train.loc[tr, "actual"], cols[p])
        out[te] = m.predict(test.loc[te])
    return out.clip(lower=0)


def score(test, systems, bt):
    rel = test.loc[test["sleeper"] >= 5.0]
    pw = bt.pairwise(rel, systems)
    return {n: {"pairwise": round(pw[n], 6),
                "mae": round(float((test[n] - test["actual"]).abs().mean()), 4)} for n in systems}, pw["n"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(range(2019, 2026)))
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--tables", type=Path, default=None)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--save", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--only-seasons", action="store_true",
                    help="question 1 only (and --save refits the shipped set on every season)")
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    bt = _load("projection_backtest", "scripts/research/projection_backtest.py")
    ab = _load("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
    cw = Crosswalk.from_csv(args.crosswalk)
    shipped = A.AdvancedModel.load()
    base = {p: [c for c in shipped.adv[p].cols if c not in A.EXPANSION_FEATURES]
            for p in A.POSITIONS}
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
    for s, t in tables.items():
        have = {c: round(float(t[c].notna().mean()), 2) for c in A.EXPANSION_FEATURES if c in t}
        print(f"  {s}: {len(t)} rows; coverage " + ", ".join(f"{k} {v:.0%}" for k, v in have.items()
                                                          if v < 0.9), file=sys.stderr)

    # 1. more seasons
    print("\n1. MORE SEASONS — shipped set; train on the two neighbours vs every other season")
    more = {}
    for test_s in [s for s in args.seasons if s >= 2023]:
        test = tables[test_s].copy()
        near = sorted(args.seasons, key=lambda s: abs(s - test_s))[1:3]
        rest = [s for s in args.seasons if s != test_s]
        test["near"] = fit_predict(pd.concat([tables[s] for s in near]), test, base)
        test["rest"] = fit_predict(pd.concat([tables[s] for s in rest]), test, base)
        res, n = score(test, ["sleeper", "near", "rest"], bt)
        more[test_s] = {"near_seasons": near, "rest_seasons": rest, **res}
        print(f"  test {test_s}: two neighbours {near} {res['near']['pairwise']:.2%}/{res['near']['mae']:.3f}"
              f"  all {len(rest)} others {res['rest']['pairwise']:.2%}/{res['rest']['mae']:.3f}"
              f"  (Sleeper {res['sleeper']['pairwise']:.2%}/{res['sleeper']['mae']:.3f})")

    seasons_win = all(more[t]["rest"]["pairwise"] > more[t]["near"]["pairwise"]
                      and more[t]["rest"]["mae"] < more[t]["near"]["mae"] for t in more)
    print(f"  every other season beats the two neighbours on both metrics in every fold: {seasons_win}")
    if args.only_seasons:
        if args.save and seasons_win:
            _save(shipped, tables, base, [], args, {"more_seasons": more, "seasons_win": True})
        elif args.save:
            print("not saved: more seasons did not beat the neighbours in every fold")
        return 0

    # 2. more metrics
    print("\n2. MORE METRICS — each block on the shipped set, every season held out")
    names = ["shipped", *BLOCKS, "+all"]
    folds = {}
    for test_s in args.seasons:
        train = pd.concat([tables[s] for s in args.seasons if s != test_s])
        test = tables[test_s].copy()
        for n in names:
            test[n] = fit_predict(train, test, {p: base[p] + block_cols(n, p) for p in A.POSITIONS})
        folds[test_s], pairs = score(test, ["sleeper", *names], bt)
        print(f"  fold {test_s} ({pairs} pairs): " + "  ".join(
            f"{n} {folds[test_s][n]['pairwise']:.2%}/{folds[test_s][n]['mae']:.3f}" for n in names))
    cv = {n: {m: round(float(np.mean([folds[t][n][m] for t in folds])), 4) for m in ("pairwise", "mae")}
          for n in ["sleeper", *names]}
    print("\n  cross-validated mean (pairwise / MAE):")
    for n in ["sleeper", *names]:
        print(f"    {n:<8} {cv[n]['pairwise']:.2%}  {cv[n]['mae']:.3f}")

    def every_fold(n, ref="shipped"):
        return all(folds[t][n]["pairwise"] > folds[t][ref]["pairwise"]
                   and folds[t][n]["mae"] < folds[t][ref]["mae"] for t in folds)
    eligible = [n for n in BLOCKS if every_fold(n)]
    size = lambda n: sum(len(block_cols(n, p)) for p in A.POSITIONS)  # noqa: E731
    winner = min(eligible, key=lambda n: (size(n), -cv[n]["pairwise"])) if eligible else None
    print(f"\n  blocks better than shipped on both metrics in every fold: {eligible or 'none'}; "
          f"ships: {winner or 'nothing'}; +all clears the bar: {every_fold('+all')}")
    combo = None
    if winner and len(eligible) > 1:
        # second pass: add the other eligible blocks to the winner, same bar
        extra = [n for n in eligible if n != winner]
        for test_s in args.seasons:
            train = pd.concat([tables[s] for s in args.seasons if s != test_s])
            test = tables[test_s].copy()
            test["winner"] = fit_predict(train, test, {p: base[p] + block_cols(winner, p) for p in A.POSITIONS})
            test["combo"] = fit_predict(train, test, {p: base[p] + block_cols(winner, p)
                                                      + [c for n in extra for c in block_cols(n, p)]
                                                      for p in A.POSITIONS})
            folds[test_s].update(score(test, ["winner", "combo"], bt)[0])
        if every_fold("combo", "winner"):
            combo = [winner, *extra]
        print(f"  second pass {[winner, *extra]} beats {winner} alone in every fold: {combo is not None}")
    final = combo or ([winner] if winner else [])
    res = {"seasons": args.seasons, "more_seasons": more, "blocks": BLOCKS, "folds": folds, "cv": cv,
           "eligible": eligible, "winner": winner, "final_blocks": final}
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    if args.save and (final or seasons_win):
        ev = {k: res[k] for k in ("seasons", "more_seasons", "cv", "eligible", "winner", "final_blocks")}
        ev["folds"] = {str(t): v for t, v in folds.items()}
        _save(shipped, tables, base, final, args, ev)
    return 0


def _save(shipped, tables, base, final, args, evidence) -> None:
    """Refit the skill positions on every season with the shipped set plus
    the winning blocks (none when only the seasons won); keep K and DEF."""
    everything = pd.concat(tables.values())
    cols = {p: base[p] + [c for n in final for c in block_cols(n, p)] for p in A.POSITIONS}
    adv = dict(shipped.adv)
    for p in A.POSITIONS:
        tr = everything.loc[everything["position"] == p]
        adv[p] = A.Ridge.fit(tr, tr["actual"], cols[p])
    meta = dict(shipped.meta)
    meta["feature_set"] = f"{meta.get('feature_set', 'core')}{''.join(final)}"
    meta["fit_on"] = sorted(args.seasons)
    meta["fit_rows"] = int(len(everything))
    meta["expansion_evidence"] = {**evidence, "doc": "docs/research/FEATURE_EXPANSION.md"}
    meta["written"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    A.AdvancedModel(adv, shipped.stack, meta).save()
    print(f"saved {A.WEIGHTS_PATH.relative_to(REPO_ROOT)} ({meta['feature_set']}, fit on "
          f"{len(everything)} player-weeks over {len(args.seasons)} seasons); K/DEF, the stack and "
          f"the ROS weights must be refit next")


if __name__ == "__main__":
    sys.exit(main())
