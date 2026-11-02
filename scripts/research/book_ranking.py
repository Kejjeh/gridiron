"""Rank the books by how well their player-prop lines predicted the outcome.

    PYTHONPATH=src python scripts/research/book_ranking.py [--season 2026]
        [--raw DIR] [--actuals weekly.parquet] [--markdown] [--out grades.json]

Runs on the LOCAL raw archive (data/research/cache/props/raw/, or a
decrypted copy of the cloud's; scripts/ingest/pull_props.py writes it). Per
book and per market, over every main line whose player played:

  line error     mean |actual - line| and mean (actual - line) (bias)
  calibration    Brier score and log loss of the de-vigged P(over) against
                 whether the stat went over (a push is left out); for the
                 anytime TD, P(yes) against scoring at least one
  consensus gap  mean |line - consensus line| and |P(over) - consensus
                 P(over)|, the consensus being the median of the books

plus a `consensus` row per market scored the same way. With fewer than four
collected weeks it says "needs 4+ collected weeks" and still runs: a
ranking on a few hundred lines is noise, and the roadmap waits for four
weeks before reading one (firm conclusions wait for the offseason).

Actuals are the season's nflverse weekly box scores by gsis id (a prop on a
player who did not play is void, so he is left out). Names reach an id only
through the committed table data/reference/odds_player_names.csv, 'exact'
or 'reviewed' rows (research, record-only); an unmatched name is counted.
The output is aggregate grades only (derived values): no player, no single
line, no price.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from collections.abc import Mapping
from pathlib import Path

from gridiron import props as P
from gridiron.ids import normalize_id
from gridiron.league_config import SEASON_YEAR
from gridiron.paths import RESEARCH_CACHE

MIN_WEEKS = 4
EPS = 1e-6
_STATS = ("passing_yards", "passing_tds", "attempts", "completions", "passing_interceptions",
          "rushing_yards", "carries", "receptions", "receiving_yards", "rushing_tds",
          "receiving_tds", "fg_made", "pat_made")


def load_actuals(season: int, path: Path | None = None) -> dict[tuple[str, int], dict[str, float]]:
    """(gsis_id, week) -> the stats the markets settle on (regular season)."""
    import pandas as pd
    if path is not None:
        frame = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path)
    else:
        from nflreadpy.config import update_config
        update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                      cache_duration=86400, verbose=False)
        import nflreadpy as nfl
        frame = nfl.load_player_stats([season], summary_level="week").to_pandas()
    if "season_type" in frame:
        frame = frame.loc[frame["season_type"] == "REG"]
    out = {}
    for row in frame.to_dict("records"):
        gid = normalize_id(row.get("player_id") or row.get("gsis_id"))
        if not gid:
            continue
        s = {c: float(row.get(c) or 0.0) if row.get(c) == row.get(c) else 0.0 for c in _STATS}
        s["scrimmage_tds"] = s["rushing_tds"] + s["receiving_tds"]
        s["kicking_points"] = (P.BOOK_KICKING_POINTS["fg"] * s["fg_made"]
                               + P.BOOK_KICKING_POINTS["pat"] * s["pat_made"])
        out[(gid, int(row["week"]))] = s
    return out


def graded_lines(snaps: Mapping[tuple[int, str], tuple[Path, dict]], table: P.IdentityTable,
                 actuals: Mapping[tuple[str, int], Mapping[str, float]]):
    """Every book's main line (and the consensus) with its outcome; and the
    counts of what could not be graded."""
    rows, skipped = [], defaultdict(int)
    for (week, _gid), (_path, blob) in sorted(snaps.items()):
        payload = blob.get("payload")
        teams = tuple(t for t in P.event_teams(payload if isinstance(payload, Mapping) else {})
                      if t)
        lines, _ = P.devig(P.parse_event_odds(payload)[0])
        cons = P.consensus(lines)
        pseudo = [P.BookLine(c.event_id, "consensus", c.market, c.player, c.line, c.p_over,
                             None, "median") for c in cons.values()]
        for b in (*lines, *pseudo):
            real = b.book != "consensus"          # count each skip once, on the books
            res = table.resolve(b.player, teams)
            if res.gsis_id is None:
                skipped[f"name {res.reason}"] += real
                continue
            got = actuals.get((res.gsis_id, int(week)))
            if got is None:
                skipped["no box score (did not play: the prop is void)"] += real
                continue
            spec = P.MARKETS[b.market]
            actual = float(got[spec.stat])
            c = cons[(b.event_id, b.player, b.market)]
            if spec.family == P.ANYTIME:
                y, err = float(actual >= 1), None
            else:
                if actual == b.point:
                    skipped["push"] += real
                    continue
                y, err = float(actual > b.point), actual - b.point
            rows.append({"week": int(week), "book": b.book, "market": b.market, "y": y,
                         "p": b.p_over, "err": err,
                         "gap_line": None if b.point is None or c.line is None
                         else abs(b.point - c.line),
                         "gap_p": abs(b.p_over - c.p_over)})
    return rows, dict(skipped)


def grade(rows) -> list[dict]:
    groups = defaultdict(list)
    for r in rows:
        groups[(r["market"], r["book"])].append(r)
    out = []
    for (market, book), g in sorted(groups.items()):
        n = len(g)
        errs = [r["err"] for r in g if r["err"] is not None]
        gaps = [r["gap_line"] for r in g if r["gap_line"] is not None]
        ll = -sum(math.log(min(max(r["p"] if r["y"] else 1 - r["p"], EPS), 1 - EPS))
                  for r in g) / n
        out.append({"market": market, "book": book, "n": n,
                    "line_mae": round(sum(abs(e) for e in errs) / len(errs), 3) if errs else None,
                    "line_bias": round(sum(errs) / len(errs), 3) if errs else None,
                    "brier": round(sum((r["p"] - r["y"]) ** 2 for r in g) / n, 4),
                    "log_loss": round(ll, 4),
                    "gap_line": round(sum(gaps) / len(gaps), 3) if gaps else None,
                    "gap_p": round(sum(r["gap_p"] for r in g) / n, 4)})
    out.sort(key=lambda r: (r["market"], r["brier"], r["book"]))
    return out


def render(grades, *, weeks: int, skipped: Mapping[str, int], markdown: bool) -> list[str]:
    head = [f"Book ranking over {weeks} collected week(s)"
            + ("" if weeks >= MIN_WEEKS else f" — needs {MIN_WEEKS}+ collected weeks; "
               f"read nothing into this yet")]
    if skipped:
        head.append("not graded: " + ", ".join(f"{k} {v}" for k, v in sorted(skipped.items())))
    cols = ("market", "book", "n", "line_mae", "line_bias", "brier", "log_loss", "gap_line",
            "gap_p")
    fmt = (lambda vals: "| " + " | ".join(vals) + " |") if markdown else \
        (lambda vals: "  ".join(f"{v:>12}" for v in vals))
    body = [fmt(cols)]
    if markdown:
        body.append("|" + "---|" * len(cols))
    body += [fmt([("" if g[c] is None else str(g[c])) for c in cols]) for g in grades]
    return head + ([""] if markdown else []) + body


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--raw", type=Path, default=P.RAW_ROOT)
    ap.add_argument("--table", type=Path, default=P.IDENTITY_PATH)
    ap.add_argument("--actuals", type=Path, default=None,
                    help="an nflverse weekly parquet/csv (default: fetch the season)")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--out", type=Path, default=None, help="write the aggregate grades as JSON")
    ap.add_argument("--debug", action="store_true", help="re-raise a failure (local use only)")
    args = ap.parse_args(argv)
    try:
        return rank(args)
    except Exception as exc:                                     # noqa: BLE001
        if args.debug:
            raise
        # The type only: in the cloud this prints to a public summary and log,
        # and an exception's text can quote a name or a line.
        print(f"book ranking failed: {type(exc).__name__} (rerun locally with --debug)")
        return 1


def rank(args: argparse.Namespace) -> int:
    snaps = P.read_raw_snapshots(args.raw, args.season)
    weeks = len({w for (w, _g) in snaps})
    if weeks < MIN_WEEKS:
        print(f"needs {MIN_WEEKS}+ collected weeks (have {weeks}); running anyway")
    if not snaps:
        print("no snapshot in the raw archive: nothing to grade")
        return 0
    rows, skipped = graded_lines(snaps, P.IdentityTable.read(args.table),
                                 load_actuals(args.season, args.actuals))
    grades = grade(rows)
    print("\n".join(render(grades, weeks=weeks, skipped=skipped, markdown=args.markdown)))
    if args.out is not None:
        args.out.write_text(json.dumps({"season": args.season, "weeks": weeks,
                                        "skipped": skipped, "grades": grades}, indent=1) + "\n",
                            encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
