"""Backtest weekly projection systems on a completed season, out of sample.

    PYTHONPATH=src python scripts/research/projection_backtest.py --season 2025

Question: is there a prediction system with a better weekly success rate than
the baseline this repo ships (`gridiron.projection`)? Every system here is
scored on the SAME player-weeks, with the league's own scoring (rule #2):

  baseline      this repo's projection, predicted for week w from weeks < w
                only (`gridiron.evaluate.chronological_evaluation`)
  ppg_to_date   season points per game through w-1 (naive)
  last_week     last week's points (naive)
  sleeper       Sleeper's published weekly projection for week w — projected
                STAT LINES, scored by `gridiron.scoring.fantasy_points`
  blend         the mean of baseline and sleeper

Metrics, per system:
  MAE / RMSE / bias        point error per player-week
  pairwise                 start/sit success rate: over every pair of players
                           at the same position in the same week, how often
                           the system ordered the pair the way the actual
                           points did (ties in actuals skipped)
  close calls              the same, over pairs whose sleeper projections are
                           within 3 points — the decisions that are hard
  Spearman                 mean rank correlation within position-week

Caveats stated in the report, not hidden: sleeper's number is posted during
the week and updated toward kickoff, so it can carry news (a late
downgrade) that no box-score model has; the rows cover players who recorded
a stat line, so a player projected but inactive is not scored against
anyone. Players are joined by gsis id through the crosswalk (rule #3).

Inputs are public: nflverse via nflreadpy, and Sleeper's projections
endpoint (one request per position per week, cached under
data/research/cache/sleeper_proj_<season>/, gitignored). No league data,
no player-map call.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
import time
import urllib.request
from pathlib import Path

import pandas as pd

from gridiron.evaluate import chronological_evaluation
from gridiron.ids import Crosswalk, normalize_id
from gridiron.paths import RESEARCH_CACHE
from gridiron.scoring import fantasy_points
from gridiron.usage import player_weeks

POSITIONS = ("QB", "RB", "WR", "TE")
SLEEPER_PROJ = ("https://api.sleeper.app/projections/nfl/{season}/{week}"
                "?season_type=regular&position%5B%5D={pos}")

#: Sleeper projected-stat key -> nflverse weekly column `fantasy_points` reads.
STAT_MAP = {
    "pass_yd": "passing_yards", "pass_td": "passing_tds",
    "pass_int": "passing_interceptions", "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds", "rec": "receptions", "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds", "fum_lost": "rushing_fumbles_lost",
    "pass_2pt": "passing_2pt_conversions", "rush_2pt": "rushing_2pt_conversions",
    "rec_2pt": "receiving_2pt_conversions",
}


def sleeper_week(season: int, week: int, pos: str, cache: Path) -> list[dict]:
    path = cache / f"w{week:02d}_{pos}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(SLEEPER_PROJ.format(season=season, week=week, pos=pos),
                                 headers={"User-Agent": "gridiron-research"})
    with urllib.request.urlopen(req, timeout=60) as resp:     # noqa: S310
        blob = json.loads(resp.read())
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob), encoding="utf-8")
    time.sleep(0.5)                                            # be polite
    return blob


def sleeper_projections(season: int, weeks, crosswalk: Crosswalk) -> pd.DataFrame:
    cache = RESEARCH_CACHE / f"sleeper_proj_{season}"
    rows = []
    for week in weeks:
        for pos in POSITIONS:
            for p in sleeper_week(season, week, pos, cache):
                stats = p.get("stats") or {}
                if not stats or stats.get("gp", 1) == 0:
                    continue
                gid = crosswalk.gsis(normalize_id(p.get("player_id")))
                if not gid:
                    continue
                line = {STAT_MAP[k]: float(v) for k, v in stats.items() if k in STAT_MAP}
                rows.append({"week": week, "gsis_id": gid,
                             "sleeper": fantasy_points(line),
                             "sleeper_half_ppr": stats.get("pts_half_ppr")})
    return pd.DataFrame(rows).drop_duplicates(subset=["week", "gsis_id"])


def pairwise(frame: pd.DataFrame, systems, *, close: float | None = None) -> dict:
    hits = {s: 0 for s in systems}
    n = 0
    for _, g in frame.groupby(["week", "position"]):
        rows = g.to_dict("records")
        for a, b in itertools.combinations(rows, 2):
            if a["actual"] == b["actual"]:
                continue
            if close is not None and abs(a["sleeper"] - b["sleeper"]) > close:
                continue
            n += 1
            truth = a["actual"] > b["actual"]
            for s in systems:
                if a[s] != b[s] and (a[s] > b[s]) == truth:
                    hits[s] += 1
    return {"n": n, **{s: hits[s] / n if n else None for s in systems}}


def spearman(frame: pd.DataFrame, systems) -> dict:
    out = {s: [] for s in systems}
    for _, g in frame.groupby(["week", "position"]):
        if len(g) < 5:
            continue
        for s in systems:
            out[s].append(g[s].rank().corr(g["actual"].rank()))   # Spearman, no scipy
    return {s: sum(v) / len(v) if v else None for s, v in out.items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int, default=2025)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--min-proj", type=float, default=5.0,
                    help="pairs only among players sleeper projected >= this")
    ap.add_argument("--out", type=Path, default=None, help="write metrics JSON here")
    args = ap.parse_args(argv)

    import nflreadpy as nfl
    weekly = nfl.load_player_stats([args.season], summary_level="week").to_pandas()
    weekly = weekly.loc[(weekly["season_type"] == "REG")
                        & weekly["position"].isin(POSITIONS)]
    snaps = nfl.load_snap_counts([args.season]).to_pandas()
    sched = nfl.load_schedules([args.season]).to_pandas()
    cw = Crosswalk.from_csv(args.crosswalk)

    frame = player_weeks(weekly, snaps, cw)
    report = chronological_evaluation(frame, sched)
    ours = report.rows
    weeks = sorted(ours["week"].unique().tolist())
    sl = sleeper_projections(args.season, weeks, cw)
    both = ours.merge(sl, on=["week", "gsis_id"], how="inner")
    both["blend"] = (both["baseline"] + both["sleeper"]) / 2.0
    systems = ["baseline", "ppg_to_date", "last_week", "sleeper", "blend"]

    rel = both.loc[both["sleeper"] >= args.min_proj]
    metrics = {"season": args.season, "weeks": weeks, "player_weeks": int(len(both)),
               "ours_rows": int(len(ours)), "sleeper_rows": int(len(sl)),
               "relevant_player_weeks": int(len(rel)), "systems": {}}
    for s in systems:
        err = both[s] - both["actual"]
        metrics["systems"][s] = {"mae": round(err.abs().mean(), 3),
                                 "rmse": round((err ** 2).mean() ** 0.5, 3),
                                 "bias": round(err.mean(), 3)}
    pw, cc = pairwise(rel, systems), pairwise(rel, systems, close=3.0)
    sp = spearman(rel, systems)
    by_pos = {}
    for pos, g in rel.groupby("position"):
        p = pairwise(g, systems)
        by_pos[pos] = {"pairs": p["n"], **{s: round(p[s], 4) for s in systems},
                       **{f"mae_{s}": round((g[s] - g["actual"]).abs().mean(), 3)
                          for s in systems}}
    for s in systems:
        metrics["systems"][s].update({"pairwise": round(pw[s], 4),
                                      "close_calls": round(cc[s], 4) if cc[s] else None,
                                      "spearman": round(sp[s], 4)})
    metrics.update({"pairs": pw["n"], "close_pairs": cc["n"], "by_position": by_pos,
                    "sleeper_score_check_mae_vs_pts_half_ppr": round(
                        (sl["sleeper"] - sl["sleeper_half_ppr"].astype(float)).abs().mean(), 4)})

    print(f"{args.season}: {metrics['player_weeks']} player-weeks scored by all systems "
          f"(weeks {weeks[0]}-{weeks[-1]}); pairs among sleeper >= {args.min_proj}: "
          f"{pw['n']} ({cc['n']} close calls)")
    print(f"{'system':<12} {'MAE':>6} {'RMSE':>6} {'bias':>6} {'pairwise':>9} "
          f"{'close':>7} {'spearman':>9}")
    for s in systems:
        m = metrics["systems"][s]
        print(f"{s:<12} {m['mae']:6.2f} {m['rmse']:6.2f} {m['bias']:+6.2f} "
              f"{m['pairwise']:9.1%} {(m['close_calls'] or 0):7.1%} {m['spearman']:9.3f}")
    print("pairwise by position:", json.dumps(by_pos, indent=None))
    if args.out:
        args.out.write_text(json.dumps(metrics, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
