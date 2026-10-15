"""How often does a skill player missing from Sleeper's weekly projections play?

    PYTHONPATH=src python scripts/research/feed_absence.py \
        [--tables data/research/cache/tables] [--board WEEK=PATH ...]

Two directions, both id-keyed (rule #3):

  history  2019-2025 season tables (`season{YYYY}_shipped.parquet`): rows are
           player-weeks that were PLAYED (a box score exists); the share whose
           Sleeper projection is missing is the rate at which the page's
           feed-absence rule would wrongly abstain a player who plays.
  live     decision records (roster + radar candidates projected at 5+): of
           the ones the record's shadow does not list, how many have no box
           score that week (did not play), from the season cache's actuals.

Backs `gridiron.models.advanced.FEED_MIN_PER_POSITION` and
`gridiron.dashboard.feed_absence` (docs/research/STACK_SERVING.md).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
SKILL = ("QB", "RB", "WR", "TE")
BOARDS = {4: "data/outputs/cloud/0098_37853128349/ledger/decisions/season2026/"
             "week04_20261004T124837Z_c11d026e.json",
          5: "data/ledger/decisions/season2026/week05_20261011T163751Z_a073fe54.json"}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--tables", type=Path, default=ROOT / "data/research/cache/tables")
    ap.add_argument("--cache", type=Path, default=ROOT / "data/research/cache/season2026")
    ap.add_argument("--board", action="append", default=[], metavar="WEEK=PATH")
    args = ap.parse_args(argv)

    played = missing = 0
    for s in range(2019, 2026):
        t = pd.read_parquet(args.tables / f"season{s}_shipped.parquet")
        t = t[(t["week"] >= 4) & t["position"].isin(SKILL) & (t["baseline"] >= 5)]
        m = int(t["sleeper"].isna().sum())
        played += len(t)
        missing += m
        print(f"history {s}: {len(t):5} played player-weeks (baseline >= 5), "
              f"{m:3} missing from Sleeper's feed ({m / len(t):.2%})")
    print(f"history all: {missing} of {played} ({missing / played:.2%}) played but missing")

    spec = importlib.util.spec_from_file_location("gw_fa", ROOT / "scripts/weekly/grade_week.py")
    gw = importlib.util.module_from_spec(spec)
    sys.modules["gw_fa"] = gw
    spec.loader.exec_module(gw)
    boards = dict(BOARDS)
    for b in args.board:
        w, _, p = b.partition("=")
        boards[int(w)] = p
    absent_n = absent_dnp = listed_n = listed_dnp = 0
    for week, path in sorted(boards.items()):
        d = json.loads((ROOT / path).read_text(encoding="utf-8"))
        feed = (d.get("shadow") or {}).get("players") or {}
        actual = gw.actuals_from_cache(args.cache, 2026, week)
        rows = list((d.get("radar") or {}).get("candidates") or []) + list(d.get("roster") or [])
        a_n = a_d = l_n = l_d = 0
        for r in rows:
            if r.get("position") not in SKILL or r.get("withheld") or (r.get("projected") or 0) < 5:
                continue
            sid = str(r.get("id") or r.get("sleeper_id"))
            dnp = actual.get(gw.actual_key(r)) is None
            if sid in feed:
                l_n, l_d = l_n + 1, l_d + dnp
            else:
                a_n, a_d = a_n + 1, a_d + dnp
        print(f"live week {week}: missing from the feed {a_d} of {a_n} did not play; "
              f"listed {l_d} of {l_n} did not play")
        absent_n, absent_dnp, listed_n, listed_dnp = (absent_n + a_n, absent_dnp + a_d,
                                                      listed_n + l_n, listed_dnp + l_d)
    print(f"live all: missing {absent_dnp} of {absent_n} did not play; "
          f"listed {listed_dnp} of {listed_n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
