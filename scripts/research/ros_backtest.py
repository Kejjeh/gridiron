"""Rest-of-season rankings: which way of building them orders players best? Out of sample.

    PYTHONPATH=src python scripts/research/ros_backtest.py [--seasons 2023 2024 2025] [--save]

Season-fold cross-validation: each season in turn is the TEST season and
the others train. At each cut week `w` of the test season (default 4-14)
every candidate
projects each player's points from week `w` through the end of the fantasy
season (week 17, `gridiron.ros.HORIZON_END`), and is scored against the
points the player actually scored over those weeks (a missed game counts 0 —
a ROS ranking has to live with injuries too).

The weekly models are refit on the TRAIN seasons only (the shipped weights
were refit on every season, so using them here would be in-sample), and
each position's method is chosen by its MEAN over the folds — never by one
test season. Every
feature for week `w` comes from earlier weeks, through the same builders
the page runs (`gridiron.models.advanced`, `gridiron.ros`).

Candidates (rate x the games left, byes from the schedule):
  ppg        points per game so far
  base       this repo's baseline weekly projection (no Sleeper, no advanced)
  adv        the advanced weekly model (advanced_v1; K and DEF included)
  adv_sched  adv with each future game nudged by its matchup, through the
             weekly model's own coefficients (gridiron.ros.Schedule)
  ros_model  a per-position ridge on all of the above per-game rates and the
             games played, fit to the actual points per remaining game —
             trained on out-of-fold weekly rates only

Universe per (cut, position): the union of each candidate's top N, among
players who played in week `w` (the same set for every candidate; being
active at the cut is the one piece of hindsight all share). Metrics: rank
correlation (Spearman) and pairwise order within position, averaged over
cuts; the same for the playoff weeks alone.

`--save` writes src/gridiron/models/ros_weights.json: the chosen method per
position, the learned combination (fit on every season's out-of-fold rows)
and the cross-validated evidence.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sys

import numpy as np
import pandas as pd

from gridiron import ros as R
from gridiron.evaluate import chronological_evaluation
from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT
from gridiron.usage import player_weeks

TOP = {"QB": 24, "RB": 48, "WR": 60, "TE": 24, "K": 20, "DEF": 20}
SYSTEMS = ("ppg", "base", "adv", "adv_sched")


from research_common import load_script as _load  # noqa: E402


def skill_season(season: int, cw: Crosswalk) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(player-week rows with features + team, the box-score history, schedule)."""
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
    hist = A.history_frame(frame, xfp, ngs)
    feats = []
    for w in sorted(rows["week"].unique()):
        f = A.features_as_of(hist, int(w), schedule=sched, practice=practice, depth=depth)
        feats.append(f.drop(columns=["position"]).assign(week=int(w)))
    out = rows.merge(pd.concat(feats), on=["gsis_id", "week"], how="left")
    out["season"] = season
    print(f"  {season}: {len(out)} skill player-weeks", file=sys.stderr)
    return out, frame, sched


def kdef_season(season: int, cw: Crosswalk, kd, bt) -> tuple[pd.DataFrame, pd.DataFrame]:
    K, D = kd.season_tables(season, cw, bt)
    import nflreadpy as nfl
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    kteam = (weekly.loc[weekly["position"] == "K", ["player_id", "week", "team"]]
             .rename(columns={"player_id": "gsis_id"}))
    K = K.drop(columns=[c for c in ("team",) if c in K]).merge(kteam, on=["gsis_id", "week"],
                                                               how="left")
    K["position"], D["position"] = "K", "DEF"
    D["gsis_id"] = D["team"]
    D["baseline"] = D["naive"]
    D["ppg_to_date"] = D["naive"]
    return K, D


def fit_weekly(train: dict[str, pd.DataFrame], cols: dict[str, list[str]]) -> dict:
    return {pos: A.Ridge.fit(df, df["actual"], cols[pos]) for pos, df in train.items()}


def spearman(a: pd.Series, b: pd.Series) -> float:
    return float(a.rank().corr(b.rank())) if len(a) > 2 else math_nan()


def math_nan() -> float:
    return float("nan")


def pairwise(df: pd.DataFrame, col: str, truth: str) -> tuple[int, int]:
    hits = n = 0
    for a, b in itertools.combinations(df[[col, truth]].to_numpy(), 2):
        if a[1] == b[1]:
            continue
        n += 1
        if a[0] != b[0] and (a[0] > b[0]) == (a[1] > b[1]):
            hits += 1
    return hits, n


#: Inputs of the learned ROS combination, per position (rates per game).
ROS_FEATURES = {p: ["ppg", "base", "adv", "sched", "games_before"] for p in A.POSITIONS}
ROS_FEATURES["K"] = ["ppg", "base", "adv", "sched", "games_before"]
ROS_FEATURES["DEF"] = ["ppg", "adv", "sched", "games_before"]


def cut_rows(test: dict[str, pd.DataFrame], models: dict, hist: pd.DataFrame,
             sched: pd.DataFrame, cuts) -> pd.DataFrame:
    """One row per (cut, player): every candidate's per-game rate, the games
    left, and what actually happened from the cut to week 17."""
    out = []
    for pos, df in test.items():
        df = df.copy()
        df["adv_rate"] = models[pos].predict(df)
        if pos != "DEF":
            df["adv_rate"] = df["adv_rate"].clip(lower=0)
        for w in cuts:
            now = df.loc[df["week"] == w]
            if len(now) == 0:
                continue
            S = R.Schedule(sched, w, hist if pos in A.POSITIONS else None)
            later = df.loc[(df["week"] >= w) & (df["week"] <= R.HORIZON_END)]
            actual = later.groupby("gsis_id")["actual"].sum()
            actual_po = later.loc[later["week"].isin(R.PLAYOFF_WEEKS)].groupby("gsis_id")["actual"].sum()
            for r in now.to_dict("records"):
                team = str(r.get("team") or "")
                if not team:
                    continue
                flat = S.project(pos, team, float(r["adv_rate"]), adjust=False, clip=pos != "DEF")
                sch = S.project(pos, team, float(r["adv_rate"]), ridge=models[pos],
                                adjust=True, clip=pos != "DEF")
                if not flat.games:
                    continue
                po = sum(1 for v, _, _ in flat.weeks if v in R.PLAYOFF_WEEKS)
                sched_po = sum(p for v, _, p in sch.weeks if v in R.PLAYOFF_WEEKS)
                out.append({"season": r.get("season"), "cut": w, "position": pos,
                            "gsis_id": r["gsis_id"], "games_left": flat.games, "po_games": po,
                            "games_before": float(r.get("games_before") or
                                                  r.get("games_played") or w - 1),
                            "ppg": float(r["ppg_to_date"]), "base": float(r["baseline"]),
                            "adv": float(r["adv_rate"]), "sched": sch.ros / flat.games,
                            "sched_po": sched_po,
                            "actual": float(actual.get(r["gsis_id"], 0.0)),
                            "actual_po": float(actual_po.get(r["gsis_id"], 0.0))})
    rows = pd.DataFrame(out)
    rows["actual_pg"] = rows["actual"] / rows["games_left"]
    return rows


def with_totals(rows: pd.DataFrame, stack: dict | None) -> pd.DataFrame:
    rows = rows.copy()
    for s in ("ppg", "base", "adv"):
        rows[f"{s}_ros"] = rows[s] * rows["games_left"]
        rows[f"{s}_ros_po"] = rows[s] * rows["po_games"]
    rows["adv_sched_ros"] = rows["sched"] * rows["games_left"]
    rows["adv_sched_ros_po"] = rows["sched_po"]
    if stack:
        pg = pd.Series(index=rows.index, dtype="float64")
        for pos, m in stack.items():
            sel = rows["position"] == pos
            pg[sel] = m.predict(rows[sel])
        rows["ros_model_ros"] = pg * rows["games_left"]
        rows["ros_model_ros_po"] = pg * rows["po_games"]
    return rows


def score(rows: pd.DataFrame, systems) -> dict:
    per_pos: dict[str, dict] = {}
    for pos, g in rows.groupby("position"):
        stats = {s: {"rho": [], "rho_po": [], "hits": 0, "n": 0} for s in systems}
        for w, cut in g.groupby("cut"):
            keep = set()
            for s in systems:
                keep |= set(cut.nlargest(TOP[pos], f"{s}_ros")["gsis_id"])
            cut = cut.loc[cut["gsis_id"].isin(keep)]
            for s in systems:
                stats[s]["rho"].append(spearman(cut[f"{s}_ros"], cut["actual"]))
                if w <= R.PLAYOFF_WEEKS[0]:
                    stats[s]["rho_po"].append(spearman(cut[f"{s}_ros_po"], cut["actual_po"]))
                h, n = pairwise(cut, f"{s}_ros", "actual")
                stats[s]["hits"] += h
                stats[s]["n"] += n
        per_pos[pos] = {s: {"spearman": round(float(np.nanmean(v["rho"])), 4),
                            "spearman_playoffs": round(float(np.nanmean(v["rho_po"])), 4),
                            "pairwise": round(v["hits"] / v["n"], 4) if v["n"] else None,
                            "pairs": v["n"]} for s, v in stats.items()}
    return per_pos


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024, 2025])
    ap.add_argument("--cuts", type=int, nargs="+", default=list(range(4, 15)))
    ap.add_argument("--crosswalk", default=str(RESEARCH_CACHE / "season2026" / "crosswalk.csv"))
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    cw = Crosswalk.from_csv(args.crosswalk)
    bt = _load("projection_backtest", "scripts/research/projection_backtest.py")
    kd = _load("k_def_backtest", "scripts/research/k_def_backtest.py")
    shipped = A.AdvancedModel.load()
    cols = {p: list(shipped.adv[p].cols) for p in (*A.POSITIONS, "K", "DEF")}
    skill = {s: skill_season(s, cw) for s in args.seasons}
    kdef = {s: kdef_season(s, cw, kd, bt) for s in args.seasons}

    def by_pos(s):
        rows, _, _ = skill[s]
        out = {p: rows.loc[rows["position"] == p] for p in A.POSITIONS}
        out["K"], out["DEF"] = kdef[s]
        return out

    def weekly_train(seasons):
        tr = {p: pd.concat([by_pos(s)[p] for s in seasons]) for p in cols}
        return fit_weekly({p: d.loc[(d["week"] >= 4) & d["actual"].notna()]
                           for p, d in tr.items()}, cols)

    def rows_for(season, weekly_seasons):
        _, hist_s, sched_s = skill[season]
        return cut_rows(by_pos(season), weekly_train(weekly_seasons), hist_s, sched_s, args.cuts)

    systems = (*SYSTEMS, "ros_model")
    folds, final_rows = {}, []
    for test in args.seasons:
        train = [s for s in args.seasons if s != test]
        # the learned combination sees only OUT-OF-FOLD weekly rates: each
        # train season projected by weekly models fit on the other(s)
        inner = pd.concat([rows_for(s, [t for t in train if t != s] or [s]) for s in train])
        stack = {p: A.Ridge.fit(g, g["actual_pg"], ROS_FEATURES[p])
                 for p, g in inner.groupby("position")}
        test_rows = rows_for(test, train)
        final_rows.append(test_rows)
        folds[test] = score(with_totals(test_rows, stack), systems)
        _print(f"fold: test {test}, train {train}", folds[test], systems)

    cv: dict[str, dict] = {}
    for pos in folds[args.seasons[0]]:
        cv[pos] = {s: {m: round(float(np.mean([folds[t][pos][s][m] for t in folds])), 4)
                       for m in ("spearman", "pairwise", "spearman_playoffs")}
                   for s in systems}
    choice = {pos: max(systems, key=lambda s: v[s]["spearman"]) for pos, v in cv.items()}
    _print(f"cross-validated mean over {args.seasons}", cv, systems)
    print("choice by mean Spearman: " + ", ".join(f"{p} {s}" for p, s in choice.items()))
    if args.save:
        allrows = pd.concat(final_rows)
        stack = {p: A.Ridge.fit(g, g["actual_pg"], ROS_FEATURES[p])
                 for p, g in allrows.groupby("position")}
        R.save_weights({"choice": choice, "features": ROS_FEATURES,
                        "stack": {p: m.to_json() for p, m in stack.items()},
                        "evidence": {"seasons": args.seasons, "cuts": args.cuts,
                                     "horizon_end": R.HORIZON_END, "top": TOP,
                                     "cv": cv, "folds": {str(k): v for k, v in folds.items()},
                                     "doc": "docs/research/ROS_BACKTEST.md"}})
        print(f"saved {R.WEIGHTS_PATH.relative_to(REPO_ROOT)}")
    return 0


def _print(title: str, res: dict, systems) -> None:
    print(f"\n{title} (Spearman | pairwise | playoff-weeks Spearman)")
    print(f"{'pos':<4} " + " ".join(f"{s:>24}" for s in systems))
    for pos, v in res.items():
        print(f"{pos:<4} " + " ".join(
            f"{v[s]['spearman']:>7.3f} {v[s]['pairwise'] or 0:>7.1%} "
            f"{v[s]['spearman_playoffs']:>7.3f}" for s in systems))


if __name__ == "__main__":
    sys.exit(main())
