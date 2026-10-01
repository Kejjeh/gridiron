"""Can NFL advanced stats beat Sleeper's weekly projections? Out of sample.

    PYTHONPATH=src python scripts/research/advanced_model_backtest.py \
        --train 2023 2024 --test 2025 [--save]

Trains per-position ridge regressions on the TRAIN seasons and scores them on
the TEST season, beside this repo's baseline and Sleeper's published
projections, on identical player-weeks. Every feature comes from
`gridiron.models.advanced.features_as_of` — the SAME builder the dashboard
runs — called once per week with only earlier weeks visible.

Candidates:
  adv    baseline + advanced stats, NO Sleeper input (the clean test);
  stack  adv + Sleeper's projection (only a live test grades it fairly —
         Sleeper's historical records were modified after the games).

Feature sets compared (the out-of-sample winner is the one shipped):
  core      expected points, points over expectation, snap / target /
            air-yards share, carries, NGS, implied total, opponent,
            vacated opportunity
  +practice final practice participation and a Questionable tag
  +depth    depth-chart rank before kickoff
  +both     both of the above

`--save` refits the winning set on train + test seasons and writes
`src/gridiron/models/advanced_weights.json` with the out-of-sample evidence
in its metadata (rule #5: the shipped model is the one that was tested).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gridiron.evaluate import chronological_evaluation
from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT
from gridiron.usage import player_weeks

CORE = {
    "QB": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "carries_l3", "ngs_cpoe", "implied", "def_allowed", "vacated_pickup"],
    "RB": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "carries_l3", "tgt_share_l3", "ngs_ryoe", "implied", "def_allowed",
           "vacated_pickup"],
    "WR": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "tgt_share_l3", "ay_share_l3", "ngs_sep", "ngs_yacoe", "implied",
           "def_allowed", "vacated_pickup"],
    "TE": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "tgt_share_l3", "ay_share_l3", "ngs_sep", "ngs_yacoe", "implied",
           "def_allowed", "vacated_pickup"],
}
PRACTICE = ["practice_dnp", "practice_limited", "questionable"]
DEPTH = ["depth_rank"]
SETS = {"core": [], "+practice": PRACTICE, "+depth": DEPTH, "+both": PRACTICE + DEPTH}


def _bt():
    spec = importlib.util.spec_from_file_location(
        "projection_backtest", REPO_ROOT / "scripts" / "research" / "projection_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["projection_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


def season_table(season: int, cw: Crosswalk, bt, *, reserve_lag: bool = False) -> pd.DataFrame:
    """One row per scored player-week with every feature. `reserve_lag` adds
    `reserve_pickup_lag`: the reserve-list feature read from the roster of
    the week BEFORE (what a build that runs before the week's rosters are
    posted would see)."""
    import nflreadpy as nfl
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    weekly = weekly.loc[(weekly["season_type"] == "REG") & weekly["position"].isin(A.POSITIONS)]
    snaps = nfl.load_snap_counts([season]).to_pandas()
    sched = nfl.load_schedules([season]).to_pandas()
    frame = player_weeks(weekly, snaps, cw)
    rows = chronological_evaluation(frame, sched).rows
    xfp = A.xfp_from_ff_opportunity(
        nfl.load_ff_opportunity([season], stat_type="weekly").to_pandas())
    ngs = A.ngs_from_nextgen({k: nfl.load_nextgen_stats([season], stat_type=k).to_pandas()
                              for k in A.NGS_COLUMNS})
    practice = A.practice_from_injuries(nfl.load_injuries([season]).to_pandas())
    depth = A.depth_from_charts(nfl.load_depth_charts([season]).to_pandas(), sched)
    reserve = A.reserve_from_rosters(nfl.load_rosters_weekly([season]).to_pandas())
    offense = A.offense_history(nfl.load_team_stats([season], summary_level="week").to_pandas())

    def pfr_frame(kind):
        try:
            return nfl.load_pfr_advstats([season], stat_type=kind, summary_level="week").to_pandas()
        except Exception as exc:                                  # noqa: BLE001
            print(f"  {season}: PFR {kind} unavailable ({type(exc).__name__})", file=sys.stderr)
            return None
    pfr = A.pfr_from_advstats({k: pfr_frame(k) for k in A.PFR_COLUMNS})
    try:
        routes = A.routes_from_participation(nfl.load_participation([season]).to_pandas(),
                                             nfl.load_pbp([season]).to_pandas())
    except Exception as exc:                                      # noqa: BLE001
        print(f"  {season}: participation unavailable ({type(exc).__name__})", file=sys.stderr)
        routes = None
    hist = A.history_frame(frame, xfp, ngs, pfr, routes)
    feats = []
    for w in sorted(rows["week"].unique()):
        f = A.features_as_of(hist, int(w), schedule=sched, practice=practice, depth=depth,
                             reserve=reserve, offense=offense)
        if reserve_lag:
            lag = A.role_features(hist.loc[hist["week"] < int(w)].sort_values("week"), set(),
                                  A.reserve_ids(reserve, int(w) - 1))
            f = f.merge(lag[["gsis_id", "reserve_pickup"]].rename(
                columns={"reserve_pickup": "reserve_pickup_lag"}), on="gsis_id", how="left")
        feats.append(f.drop(columns=["position", "team"]).assign(week=int(w)))
    out = rows.merge(pd.concat(feats), on=["gsis_id", "week"], how="left")
    sl = bt.sleeper_projections(season, sorted(out["week"].unique().tolist()), cw)
    out = out.merge(sl[["week", "gsis_id", "sleeper"]], on=["week", "gsis_id"], how="left")
    out["season"] = season
    print(f"  {season}: {len(out)} player-weeks, depth rank known for "
          f"{out['depth_rank'].notna().mean():.0%}, xfp for {out['xfp_l3'].notna().mean():.0%}")
    return out


def fit(train: pd.DataFrame, extra, *, stack: bool) -> dict:
    models = {}
    for pos in A.POSITIONS:
        tr = train[train["position"] == pos]
        cols = CORE[pos] + list(extra) + (["sleeper"] if stack else [])
        models[pos] = A.Ridge.fit(tr, tr["actual"], cols)
    return models


def predict(models: dict, df: pd.DataFrame) -> pd.Series:
    out = pd.Series(index=df.index, dtype="float64")
    for pos, m in models.items():
        sel = df["position"] == pos
        out[sel] = m.predict(df[sel]).clip(min=0.0)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--train", type=int, nargs="+", default=[2023, 2024])
    ap.add_argument("--test", type=int, default=2025)
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--save", action="store_true",
                    help="refit the winning set on all seasons and write the weights")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    bt = _bt()
    cw = Crosswalk.from_csv(args.crosswalk)
    print("building season tables (features from earlier weeks only)")
    tables = {s: season_table(s, cw, bt) for s in (*args.train, args.test)}
    keep = lambda d: d.loc[(d["week"] >= args.min_week) & d["sleeper"].notna()]  # noqa: E731
    train = keep(pd.concat([tables[s] for s in args.train]))
    test = keep(tables[args.test]).copy()
    test["blend"] = (test["baseline"] + test["sleeper"]) / 2.0

    variants = {}
    for name, extra in SETS.items():
        test[f"adv{name}"] = predict(fit(train, extra, stack=False), test)
        test[f"stack{name}"] = predict(fit(train, extra, stack=True), test)
        variants[name] = extra
    systems = ["baseline", "sleeper", "blend"] + [f"{k}{n}" for n in SETS for k in ("adv", "stack")]
    rel = test.loc[test["sleeper"] >= 5.0]
    pw, cc = bt.pairwise(rel, systems), bt.pairwise(rel, systems, close=3.0)
    res = {"train": args.train, "test": args.test, "pairs": pw["n"], "systems": {
        s: {"mae": round(float((test[s] - test["actual"]).abs().mean()), 3),
            "pairwise": round(pw[s], 4), "close_calls": round(cc[s], 4)} for s in systems}}
    print(f"\ntest {args.test}: {len(test)} player-weeks, {pw['n']} pairs, {cc['n']} close")
    print(f"{'system':<16} {'MAE':>6} {'pairwise':>9} {'close':>7}")
    for s in systems:
        m = res["systems"][s]
        print(f"{s:<16} {m['mae']:6.2f} {m['pairwise']:9.1%} {m['close_calls']:7.1%}")

    best = max(SETS, key=lambda n: (res["systems"][f"adv{n}"]["pairwise"],
                                    -res["systems"][f"adv{n}"]["mae"]))
    res["winner"] = best
    by_week = []
    for w, g in rel.groupby("week"):
        p = bt.pairwise(g, ["baseline", f"adv{best}", "sleeper", f"stack{best}"])
        by_week.append((int(w), p[f"adv{best}"] - p["baseline"], p[f"stack{best}"] - p["sleeper"]))
    res["weeks_adv_beats_baseline"] = sum(1 for _, a, _ in by_week if a > 0)
    res["weeks_stack_beats_sleeper"] = sum(1 for _, _, s in by_week if s > 0)
    res["weeks"] = len(by_week)
    print(f"\nwinning feature set: {best}; adv beat the baseline in "
          f"{res['weeks_adv_beats_baseline']}/{len(by_week)} weeks, stack beat Sleeper in "
          f"{res['weeks_stack_beats_sleeper']}/{len(by_week)}")

    if args.save:
        everything = pd.concat([train, keep(tables[args.test])])
        adv = fit(everything, SETS[best], stack=False)
        stack = fit(everything, SETS[best], stack=True)
        b, s = res["systems"]["baseline"], res["systems"][f"adv{best}"]
        st, sl = res["systems"][f"stack{best}"], res["systems"]["sleeper"]
        A.AdvancedModel(adv, stack, {
            "name": A.NAME, "feature_set": best,
            "fit_on": sorted({*args.train, args.test}), "fit_rows": int(len(everything)),
            "min_week": args.min_week,
            "evidence": {"train": args.train, "test": args.test, "pairs": pw["n"],
                         "baseline_pairwise": b["pairwise"], "adv_pairwise": s["pairwise"],
                         "baseline_mae": b["mae"], "adv_mae": s["mae"],
                         "sleeper_pairwise": sl["pairwise"], "stack_pairwise": st["pairwise"],
                         "doc": "docs/research/ADVANCED_STATS_BACKTEST_2025.md"},
            "written": datetime.now(timezone.utc).isoformat(timespec="seconds")}).save()
        print(f"saved {A.WEIGHTS_PATH.relative_to(REPO_ROOT)} ({best}, fit on "
              f"{len(everything)} player-weeks)")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
