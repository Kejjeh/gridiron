"""Is the advanced layer weaker early in the season than later? (history)

    PYTHONPATH=src python scripts/research/early_week_pattern.py [--out x.json]

Research only: never imported by src/gridiron, writes nothing under src/,
scripts/weekly/ or the ledger. The live shoot-out graded the page below its
own baseline in weeks 4 and 5 of 2026. This script asks whether history
expected that. Season-fold cross-validation over 2019-2025: each season held
out in turn, the advanced model refit on the other six with the SHIPPED
column list (CORE[pos] + depth_rank + the last-game block, the weights
file's '+depth+last'), then every held-out week scored on its own:
start/sit pairwise (projection_backtest.pairwise: pairs within week x
position, ties in actuals skipped) and MAE for baseline / adv / sleeper on
the harness's relevance universe (week >= 4, Sleeper >= 5, skill positions).

Reported:
  by week      mean (adv - baseline) by week number across the seven seasons;
  buckets      weeks 4-6 versus 7-18: mean delta, and how many season-weeks
               had adv below the baseline (out of how many), overall and by
               position;
  live analog  each season's week 4 and week 5 alone (adv, baseline and
               sleeper pairwise with pair counts) and how many of the 14
               had adv below the baseline; plus how unusual the live
               deficits are against every historical season-week and
               against a within-week bootstrap;
  adjustment   by bucket, mean |adv - baseline|, the correlation and slope
               of (actual - baseline) on (adv - baseline), and how often the
               adjustment pointed the right way;
  shrink       by bucket, pairwise of baseline + k * (adv - baseline).

Season tables come from `advanced_model_backtest.season_table` (the SAME
feature builder the dashboard runs; features for week w from weeks < w) and
are cached to data/research/cache/tables/season<YYYY>_shipped.parquet, written
only after `git check-ignore` confirms the path is ignored. Sleeper's
historical numbers are post-hoc records (they may carry revisions), so
`sleeper` here is context, not a scoreboard. Every join is on gsis id
(rule #3). Output is counts and rates only: no names, no ids.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import REPO_ROOT, RESEARCH_CACHE

from research_common import fit_pos, load_script, predict_pos  # noqa: E402

TABLES = RESEARCH_CACHE / "tables"
SEASONS = list(range(2019, 2026))
SYSTEMS = ["baseline", "adv", "sleeper"]
LAST = ["xfp_last", "opp_last", "snap_last"]
MIN_WEEK, MIN_PROJ = 4, 5.0
EARLY_LAST = 6                      # weeks 4..6 are "early"; 7+ are "later"
LIVE_WEEKS = (4, 5)
SHRINK = (0.0, 0.5, 1.0, 1.5)
#: the live shoot-outs' page - baseline_v1 pairwise deficits (grade_week.py)
#: name -> (pairs graded, page - baseline_v1 pairwise)
LIVE = {"2026 w4": (382, 0.5785 - 0.5995), "2026 w5 old model": (395, 0.5848 - 0.6253),
        "2026 w5 new model": (317, 0.615 - 0.669)}


def ignored(path: Path) -> bool:
    r = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=REPO_ROOT,
                       capture_output=True, check=False)
    return r.returncode == 0


def table(season: int, cw: Crosswalk, bt, ab, tables_dir: Path = TABLES) -> pd.DataFrame:
    path = tables_dir / f"season{season}_shipped.parquet"
    if path.exists():
        t = pd.read_parquet(path)
        print(f"  {season}: {len(t)} player-weeks from cache", file=sys.stderr)
        return t
    t = ab.season_table(season, cw, bt)
    if not ignored(path):
        raise SystemExit(f"refusing to write {path}: not gitignored")
    path.parent.mkdir(parents=True, exist_ok=True)
    t.to_parquet(path, index=False)
    return t


def shipped_columns(ab) -> dict[str, list[str]]:
    """The column list the weights file carries, checked against the fitting
    code's expectation (CORE + DEPTH + the last-game block)."""
    model = A.AdvancedModel.load()
    cols = {p: list(model.adv[p].cols) for p in A.POSITIONS}
    expect = {p: ab.CORE[p] + ab.DEPTH + LAST for p in A.POSITIONS}
    if cols != expect:
        print(f"WARNING shipped columns differ from CORE+DEPTH+LAST: "
              f"{ {p: sorted(set(cols[p]) ^ set(expect[p])) for p in cols} }", file=sys.stderr)
    print(f"shipped feature set {model.meta.get('feature_set')!r}, fit_on "
          f"{model.meta.get('fit_on')}; columns per position: "
          f"{ {p: len(c) for p, c in cols.items()} }", file=sys.stderr)
    return cols


def week_rows(test: pd.DataFrame, season: int, bt) -> list[dict]:
    """One record per week x (ALL + each position): pairwise and MAE per system."""
    out = []
    for w, g in test.groupby("week"):
        for pos, gp in [("ALL", g), *list(g.groupby("position"))]:
            pw = bt.pairwise(gp, SYSTEMS)
            row = {"season": season, "week": int(w), "position": str(pos),
                   "rows": int(len(gp)), "pairs": int(pw["n"])}
            for s in SYSTEMS:
                row[f"pw_{s}"] = pw[s]
                row[f"mae_{s}"] = float((gp[s] - gp["actual"]).abs().mean())
            row["d_pw"] = (row["pw_adv"] - row["pw_baseline"]) if pw["n"] else np.nan
            row["d_mae"] = row["mae_adv"] - row["mae_baseline"]
            out.append(row)
    return out


def _agg(g: pd.DataFrame) -> dict:
    g = g.loc[g["pairs"] > 0]
    n = int(len(g))
    if not n:
        return {"season_weeks": 0}
    return {"season_weeks": n, "pairs": int(g["pairs"].sum()),
            "d_pw_mean": round(float(g["d_pw"].mean()), 4),
            "d_pw_se": round(float(g["d_pw"].std(ddof=1) / np.sqrt(n)), 4) if n > 1 else None,
            "d_pw_pooled": round(float(np.average(g["d_pw"], weights=g["pairs"])), 4),
            "d_mae_mean": round(float(g["d_mae"].mean()), 4),
            "adv_below": int((g["d_pw"] < 0).sum()), "adv_tied": int((g["d_pw"] == 0).sum()),
            "mae_adv_worse": int((g["d_mae"] > 0).sum()),
            "pw_baseline": round(float(g["pw_baseline"].mean()), 4),
            "pw_adv": round(float(g["pw_adv"].mean()), 4),
            "pw_sleeper": round(float(g["pw_sleeper"].mean()), 4),
            "sleeper_minus_baseline": round(float((g["pw_sleeper"] - g["pw_baseline"]).mean()), 4)}


def adjustment(rows: pd.DataFrame) -> dict:
    """How the adjustment (adv - baseline) relates to what happened
    (actual - baseline): size, correlation, slope and direction."""
    adj = (rows["adv"] - rows["baseline"]).to_numpy(dtype=float)
    res = (rows["actual"] - rows["baseline"]).to_numpy(dtype=float)
    live = (adj != 0) & (res != 0)
    out = {"rows": int(len(rows)), "mean_abs_adj": round(float(np.abs(adj).mean()), 3),
           "median_abs_adj": round(float(np.median(np.abs(adj))), 3)}
    if len(rows) > 2 and adj.std() > 0:
        out["corr"] = round(float(np.corrcoef(adj, res)[0, 1]), 4)
        out["slope"] = round(float(np.cov(adj, res)[0, 1] / adj.var(ddof=1)), 3)
        out["right_way"] = round(float((np.sign(adj[live]) == np.sign(res[live])).mean()), 4)
        out["right_way_n"] = int(live.sum())
        big = live & (np.abs(adj) >= 1.0)
        out["right_way_adj_ge_1"] = (round(float((np.sign(adj[big]) == np.sign(res[big])).mean()), 4)
                                     if big.sum() else None)
        out["right_way_adj_ge_1_n"] = int(big.sum())
    return out


def shrink_sweep(rows: pd.DataFrame, bt) -> dict:
    """Pooled pairwise (pairs-weighted over season-weeks) of
    baseline + k * (adv - baseline), by bucket."""
    d = rows.copy()
    names = [f"k{k:g}" for k in SHRINK]
    for k, name in zip(SHRINK, names):
        d[name] = (d["baseline"] + k * (d["adv"] - d["baseline"])).clip(lower=0)
    acc = {b: {n: [0, 0] for n in names} for b in ("4-6", "7-18")}
    for (_, _), g in d.groupby(["season", "week"]):
        b = "4-6" if int(g["week"].iloc[0]) <= EARLY_LAST else "7-18"
        pw = bt.pairwise(g, names)
        for n in names:
            acc[b][n][0] += pw[n] * pw["n"]
            acc[b][n][1] += pw["n"]
    return {b: {n: round(h / c, 4) if c else None for n, (h, c) in v.items()} for b, v in acc.items()}


def live_reference(long_all: pd.DataFrame, rows: pd.DataFrame, bt, draws: int,
                   seed: int = 7) -> dict:
    """How unusual are the live deficits? Three references: (a) the historical
    season-weeks themselves (a full week is ~150-200 relevant players and
    3-5k pairs); (b) a within-week bootstrap of weeks 4-5 at full size; (c)
    draws from each historical week 4/5 thinned to the LIVE pair count (the
    live boards graded 300-400 pairs, i.e. ~45-50 players with Sleeper >= 5),
    which is the sampling noise a live grade actually carries."""
    rng = np.random.default_rng(seed)
    weeks45 = [g for _, g in rows.loc[rows["week"].isin(LIVE_WEEKS)].groupby(["season", "week"])]
    boot = []
    if draws:
        for g in weeks45:
            for _ in range(draws):
                pick = g.iloc[rng.choice(len(g), size=len(g), replace=True)]
                pw = bt.pairwise(pick, ["baseline", "adv"])
                if pw["n"]:
                    boot.append(pw["adv"] - pw["baseline"])
    boot = np.asarray(boot)
    hist = long_all.loc[long_all["pairs"] > 0, "d_pw"].to_numpy(dtype=float)
    early = long_all.loc[(long_all["pairs"] > 0) & long_all["week"].isin(LIVE_WEEKS),
                         "d_pw"].to_numpy(dtype=float)
    out = {"history_season_weeks": int(len(hist)), "history_sd": round(float(hist.std(ddof=1)), 4),
           "history_min": round(float(hist.min()), 4),
           "weeks45_sd": round(float(early.std(ddof=1)), 4), "weeks45_min": round(float(early.min()), 4),
           "bootstrap_draws": int(len(boot)),
           "bootstrap_sd": round(float(boot.std()), 4) if len(boot) else None,
           "live": {}}
    for label, (pairs, deficit) in LIVE.items():
        thin = []
        if draws:
            for g in weeks45:
                full = bt.pairwise(g, ["baseline"])["n"]
                n = max(8, int(round(len(g) * np.sqrt(pairs / full))))   # pairs scale ~ n^2
                for _ in range(draws):
                    pick = g.iloc[rng.choice(len(g), size=min(n, len(g)), replace=False)]
                    pw = bt.pairwise(pick, ["baseline", "adv"])
                    if pw["n"]:
                        thin.append((pw["adv"] - pw["baseline"], pw["n"]))
        thin_d = np.asarray([d for d, _ in thin])
        out["live"][label] = {
            "pairs": pairs, "deficit": round(deficit, 4),
            "history_count_as_bad": int((hist <= deficit).sum()),
            "weeks45_count_as_bad": int((early <= deficit).sum()),
            "bootstrap_share_as_bad": round(float((boot <= deficit).mean()), 4) if len(boot) else None,
            "thinned_draws": int(len(thin)),
            "thinned_mean_pairs": round(float(np.mean([n for _, n in thin])), 1) if thin else None,
            "thinned_sd": round(float(thin_d.std()), 4) if len(thin) else None,
            "thinned_share_as_bad": round(float((thin_d <= deficit).mean()), 4) if len(thin) else None,
            "thinned_share_below_zero": round(float((thin_d < 0).mean()), 4) if len(thin) else None}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=SEASONS)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--tables", type=Path, default=TABLES,
                    help="directory of season<YYYY>_shipped.parquet caches (built when absent)")
    ap.add_argument("--draws", type=int, default=100,
                    help="bootstrap draws per historical week 4/5 for the noise reference (0 skips)")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    bt = load_script("projection_backtest", "scripts/research/projection_backtest.py")
    ab = load_script("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
    cw = Crosswalk.from_csv(args.crosswalk)
    cols = shipped_columns(ab)

    print("season tables (features from earlier weeks only)", file=sys.stderr)
    tables = {s: table(s, cw, bt, ab, args.tables) for s in args.seasons}
    keep = lambda d: d.loc[(d["week"] >= MIN_WEEK) & d["sleeper"].notna()  # noqa: E731
                           & d["position"].isin(A.POSITIONS)].copy()
    tables = {s: keep(t) for s, t in tables.items()}

    long, scored = [], []
    for test_s in args.seasons:
        train = pd.concat([tables[s] for s in args.seasons if s != test_s])
        test = tables[test_s].copy()
        test["adv"] = predict_pos(fit_pos(train, cols, "actual"), test).clip(lower=0)
        rel = test.loc[test["sleeper"] >= MIN_PROJ]
        long += week_rows(rel, test_s, bt)
        scored.append(rel[["season", "week", "position", "gsis_id", "actual", *SYSTEMS]])
        print(f"  fold {test_s}: train {len(train)} rows, test {len(test)}, relevant {len(rel)}",
              file=sys.stderr)
    long = pd.DataFrame(long)
    long["bucket"] = np.where(long["week"] <= EARLY_LAST, "4-6", "7-18")
    rows = pd.concat(scored, ignore_index=True)
    rows["bucket"] = np.where(rows["week"] <= EARLY_LAST, "4-6", "7-18")
    allpos = long.loc[long["position"] == "ALL"]

    res = {"seasons": args.seasons, "min_week": MIN_WEEK, "min_proj": MIN_PROJ,
           "columns": cols, "relevant_rows": int(len(rows)),
           "by_week": {int(w): _agg(g) for w, g in allpos.groupby("week")},
           "by_bucket": {b: _agg(g) for b, g in allpos.groupby("bucket")},
           "by_bucket_position": {f"{p} {b}": _agg(g) for (p, b), g in
                                  long.loc[long["position"] != "ALL"].groupby(["position", "bucket"])},
           "live_analog": [], "adjustment": {}, "shrink": shrink_sweep(rows, bt)}
    la = allpos.loc[allpos["week"].isin(LIVE_WEEKS)].sort_values(["season", "week"])
    for r in la.itertuples():
        res["live_analog"].append({"season": r.season, "week": r.week, "pairs": r.pairs,
                                   "rows": r.rows, "pw_adv": round(r.pw_adv, 4),
                                   "pw_baseline": round(r.pw_baseline, 4),
                                   "pw_sleeper": round(r.pw_sleeper, 4),
                                   "d_pw": round(r.d_pw, 4), "d_mae": round(r.d_mae, 3)})
    res["live_analog_summary"] = {"season_weeks": int(len(la)),
                                  "adv_below_baseline": int((la["d_pw"] < 0).sum()),
                                  "adv_below_sleeper": int((la["pw_adv"] < la["pw_sleeper"]).sum()),
                                  "d_pw_mean": round(float(la["d_pw"].mean()), 4),
                                  "d_pw_min": round(float(la["d_pw"].min()), 4),
                                  "d_pw_max": round(float(la["d_pw"].max()), 4)}
    for b, g in rows.groupby("bucket"):
        res["adjustment"][b] = adjustment(g)
        for p, gp in g.groupby("position"):
            res["adjustment"][f"{p} {b}"] = adjustment(gp)
    res["live_reference"] = live_reference(allpos, rows, bt, args.draws)

    # ---- print, compact
    print(f"\nseason-fold CV {args.seasons[0]}-{args.seasons[-1]}, relevant rows {len(rows)}, "
          f"columns {{pos: n}} = { {p: len(c) for p, c in cols.items()} }")
    print(f"\n{'week':>4} {'seasons':>7} {'pairs':>6} {'base':>6} {'adv':>6} {'slpr':>6} "
          f"{'adv-base':>8} {'pooled':>7} {'dMAE':>6} {'adv<base':>8}")
    for w, m in res["by_week"].items():
        print(f"{w:>4} {m['season_weeks']:>7} {m['pairs']:>6} {m['pw_baseline']:6.3f} {m['pw_adv']:6.3f} "
              f"{m['pw_sleeper']:6.3f} {m['d_pw_mean']:+8.4f} {m['d_pw_pooled']:+7.4f} "
              f"{m['d_mae_mean']:+6.3f} {m['adv_below']:>3}/{m['season_weeks']}")
    print("\nbucket           sw  pairs   base    adv   slpr  adv-base(se)      pooled   dMAE  adv<base maeWorse")
    for key in [*res["by_bucket"], *res["by_bucket_position"]]:
        m = res["by_bucket"].get(key) or res["by_bucket_position"][key]
        print(f"{key:<14} {m['season_weeks']:>4} {m['pairs']:>6} {m['pw_baseline']:6.3f} {m['pw_adv']:6.3f} "
              f"{m['pw_sleeper']:6.3f} {m['d_pw_mean']:+8.4f}({m['d_pw_se']:.4f}) {m['d_pw_pooled']:+8.4f} "
              f"{m['d_mae_mean']:+6.3f} {m['adv_below']:>3}/{m['season_weeks']:<4} "
              f"{m['mae_adv_worse']:>3}/{m['season_weeks']}")
    print("\nlive analog: weeks 4 and 5 of each season")
    print(f"{'season':>6} {'wk':>2} {'rows':>4} {'pairs':>5} {'base':>6} {'adv':>6} {'slpr':>6} {'adv-base':>8} {'dMAE':>6}")
    for r in res["live_analog"]:
        print(f"{r['season']:>6} {r['week']:>2} {r['rows']:>4} {r['pairs']:>5} {r['pw_baseline']:6.3f} "
              f"{r['pw_adv']:6.3f} {r['pw_sleeper']:6.3f} {r['d_pw']:+8.4f} {r['d_mae']:+6.3f}")
    s = res["live_analog_summary"]
    print(f"adv below baseline in {s['adv_below_baseline']}/{s['season_weeks']} season-weeks "
          f"(mean {s['d_pw_mean']:+.4f}, range {s['d_pw_min']:+.4f}..{s['d_pw_max']:+.4f}); "
          f"below sleeper in {s['adv_below_sleeper']}/{s['season_weeks']}")
    print("\nadjustment adv - baseline vs actual - baseline")
    print(f"{'bucket':<10} {'rows':>5} {'|adj|':>6} {'med':>6} {'corr':>7} {'slope':>6} {'right':>6} {'n':>6} {'right|adj>=1':>12} {'n':>6}")
    for k, m in res["adjustment"].items():
        if "corr" not in m:
            continue
        print(f"{k:<10} {m['rows']:>5} {m['mean_abs_adj']:6.3f} {m['median_abs_adj']:6.3f} {m['corr']:7.4f} "
              f"{m['slope']:6.3f} {m['right_way']:6.4f} {m['right_way_n']:>6} "
              f"{(m['right_way_adj_ge_1'] or float('nan')):12.4f} {m['right_way_adj_ge_1_n']:>6}")
    print("\nshrink: pooled pairwise of baseline + k*(adv - baseline)")
    for b, m in res["shrink"].items():
        print(f"  {b:<5} " + "  ".join(f"{n}={v:.4f}" for n, v in m.items()))
    lr = res["live_reference"]
    print(f"\nlive reference: {lr['history_season_weeks']} historical season-weeks, adv - baseline sd "
          f"{lr['history_sd']:.4f} (min {lr['history_min']:+.4f}); weeks 4-5 sd {lr['weeks45_sd']:.4f} "
          f"(min {lr['weeks45_min']:+.4f}); within-week bootstrap sd {lr['bootstrap_sd']} "
          f"over {lr['bootstrap_draws']} draws")
    for k, m in lr["live"].items():
        print(f"  {k:<18} {m['pairs']} pairs, live {m['deficit']:+.4f}: as bad in {m['history_count_as_bad']}/"
              f"{lr['history_season_weeks']} full season-weeks ({m['weeks45_count_as_bad']} of the week-4/5 ones), "
              f"full-size bootstrap share {m['bootstrap_share_as_bad']}; thinned to ~{m['thinned_mean_pairs']} "
              f"pairs: sd {m['thinned_sd']}, share <= live {m['thinned_share_as_bad']}, "
              f"share < 0 {m['thinned_share_below_zero']} ({m['thinned_draws']} draws)")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
        print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
