"""Reconcile `gridiron.scoring.defense_points` against Sleeper's own DEF points.

    PYTHONPATH=src python scripts/research/defense_scoring_reconcile.py --season 2025

Rule #1: scoring constants are verified against the platform. The DEF
weights were verified with the league settings; this checks the COLUMN
MAPPING — nflverse team-weekly stats + the opponent's score — by scoring
every team-week of a finished season and comparing with Sleeper's weekly
actual DEF points (public stats endpoint, one request per week, cached under
data/research/cache/sleeper_stats_<season>/, gitignored). Prints the share
of exact matches, the MAE and the largest mismatches with their stat lines.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request

import pandas as pd

from gridiron.ids import nflverse_team
from gridiron.paths import RESEARCH_CACHE
from gridiron.scoring import defense_points

URL = ("https://api.sleeper.app/stats/nfl/{season}/{week}"
       "?season_type=regular&position%5B%5D=DEF")


def sleeper_def(season: int, week: int) -> list[dict]:
    cache = RESEARCH_CACHE / f"sleeper_stats_{season}"
    path = cache / f"w{week:02d}_DEF.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    req = urllib.request.Request(URL.format(season=season, week=week),
                                 headers={"User-Agent": "gridiron-research"})
    with urllib.request.urlopen(req, timeout=60) as resp:     # noqa: S310
        blob = json.loads(resp.read())
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(blob), encoding="utf-8")
    time.sleep(0.5)
    return blob


def team_weeks(season: int) -> pd.DataFrame:
    """One row per team-week: nflverse team stats + points allowed + our DEF points."""
    import nflreadpy as nfl
    t = nfl.load_team_stats([season], summary_level="week").to_pandas()
    t = t.loc[t["season_type"] == "REG"]
    s = nfl.load_schedules([season]).to_pandas()
    s = s.loc[s["game_type"] == "REG"]
    allowed = {}
    for r in s.itertuples():
        if r.home_score == r.home_score:
            allowed[(int(r.week), str(r.home_team))] = float(r.away_score)
            allowed[(int(r.week), str(r.away_team))] = float(r.home_score)
    t = t.assign(points_allowed=[allowed.get((int(w), str(tm))) for w, tm in
                                 zip(t["week"], t["team"])])
    t["dst_points"] = [defense_points(r, r["points_allowed"]) for r in t.to_dict("records")]
    return t


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int, default=2025)
    args = ap.parse_args(argv)
    ours = team_weeks(args.season)
    rows = []
    for week in sorted(ours["week"].unique()):
        for p in sleeper_def(args.season, int(week)):
            st = p.get("stats") or {}
            if "pts_half_ppr" not in st:
                continue
            rows.append({"week": int(week), "team": nflverse_team(p.get("player_id")),
                         "sleeper": float(st["pts_half_ppr"]), "sl": st})
    theirs = pd.DataFrame(rows)
    m = ours.merge(theirs, on=["week", "team"], how="inner")
    m["diff"] = m["dst_points"] - m["sleeper"]
    exact = (m["diff"].abs() < 1e-6).mean()
    print(f"{args.season}: {len(m)} team-weeks; exact {exact:.1%}; within 1 pt "
          f"{(m['diff'].abs() <= 1).mean():.1%}; MAE {m['diff'].abs().mean():.3f}; "
          f"bias {m['diff'].mean():+.3f}")
    cols = ["def_sacks", "def_interceptions", "fumble_recovery_opp", "def_fumbles_forced",
            "def_tds", "def_safeties", "def_punt_blocks", "def_fg_blocks",
            "def_pat_blocks", "special_teams_tds", "points_allowed"]
    worst = m.reindex(m["diff"].abs().sort_values(ascending=False).index).head(8)
    for r in worst.to_dict("records"):
        sl = {k: v for k, v in r["sl"].items() if not k.startswith(("pts_", "adp", "pos_", "gp", "yds_allow"))
              or k.startswith("pts_allow")}
        print(f"  wk{r['week']} {r['team']}: ours {r['dst_points']:.1f} sleeper "
              f"{r['sleeper']:.1f} | nflverse {({c: r.get(c) for c in cols})} | sleeper {sl}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
