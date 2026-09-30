"""grade_week.py — grade a finished week's advice against what actually happened.

    PYTHONPATH=src python scripts/weekly/grade_week.py --week 3 \
        --archives data/outputs/cloud/0056_.../ledger/decisions \
        --actuals-record data/outputs/cloud/0056_.../outputs/dashboard/dashboard_latest.json \
        [--before 2026-09-27T17:00:00Z] [--acted KEY ...] [--declined KEY ...]

Rule #7 says grade the choice, not the projection, and do it from numbers
frozen at decision time. `gridiron.decisions.grade_archive` already does
that; nothing ran it. This script:

  1. picks ONE archive for the week — the latest one built before `--before`
     (e.g. the Sunday early kickoff), or by default the latest built while
     the week was still pregame — and says which;
  2. reads actual league points for that week by gsis id — from nflverse
     directly (`--actuals-nflverse`, the most complete: every player with a
     stat line), a season cache (`--actuals-cache`), or a LATER record's
     usage block (`--actuals-record`, offline, but it only holds players who
     are still yours or still available) — never from the archive itself;
  3. prints every graded comparison (names — stdout only), and
  4. upserts ONE aggregate row per (season, week, archive) into
     data/ledger/grades/season<YYYY>.csv — counts and rates only, no player
     names or ids — so accuracy accumulates across the season.

`--acted` / `--declined` take a comparison key exactly as printed
(`start_sit:FLEX:<held id>:<alternative id>`) and are the ONLY way a row
becomes an observed owner decision; the program never infers what the owner
did from the roster.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from gridiron.decisions import (ACTED, DECLINED, Grade, archive_stamp,
                                grade_archive, read_archive)
from gridiron.paths import LEDGER
from gridiron.trends import actuals_from_usage

GRADES_DIR = LEDGER / "grades"
FIELDS = ("season", "week", "archive", "archive_built", "graded", "ungradeable",
          "scorable", "agree", "direction_n", "direction_agree", "withheld",
          "unverified", "decisions", "projection_mae", "projection_n", "graded_at")


def direction(grade: Grade) -> tuple[int, int]:
    """(agree, n) over EVERY graded comparison — withheld and unverified ones
    included. This is the projection's directional hit rate, not the advice's:
    on game days the page withholds most comparisons (stale designations), so
    without it a season could pass with no measure of the model at all. It is
    stored and printed under its own name and never merged with `agree`."""
    rows = [c for c in grade.comparisons if c.status == "graded"
            and c.projected_edge is not None and c.realized_edge is not None
            and c.projected_edge != 0]
    return sum(1 for c in rows if (c.projected_edge > 0) == (c.realized_edge > 0)), len(rows)


def _time(text: str) -> datetime:
    t = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def week_archives(root: Path, season: int, week: int) -> list[tuple[datetime, Path, dict]]:
    """Every readable archive of one week, oldest first. `root` may be the
    decisions dir or its season<YYYY> subdirectory."""
    folder = root / f"season{season}" if (root / f"season{season}").is_dir() else root
    out = []
    for path in folder.glob(f"week{week:02d}_*.json"):
        stamp = archive_stamp(path)
        try:
            blob = read_archive(path)
        except (OSError, ValueError):
            continue
        if stamp is not None and int(blob.get("week") or -1) == week:
            out.append((stamp, path, blob))
    return sorted(out, key=lambda x: (x[0], x[1].name))


def pick_archive(archives: Sequence[tuple[datetime, Path, dict]], *,
                 before: datetime | None) -> tuple[tuple[datetime, Path, dict] | None, str]:
    if before is not None:
        eligible = [a for a in archives if a[0] < before]
        why = f"the latest built before {before.isoformat()}"
    else:
        eligible = [a for a in archives if a[2].get("phase") == "pregame"]
        why = "the latest built while the week was still pregame"
    return (eligible[-1] if eligible else None), why


def actuals_from_record(path: Path, week: int) -> dict[str, float]:
    blob = json.loads(path.read_text(encoding="utf-8"))
    return actuals_from_usage(blob.get("usage"), week)


def actuals_from_cache(season_dir: Path, season: int, week: int) -> dict[str, float]:
    """Actual points from a season cache (weekly_stats + snap_counts +
    crosswalk), scored by `gridiron.usage` — the local path."""
    from gridiron.ids import Crosswalk
    from gridiron.ingest import Manifest
    from gridiron.usage import player_weeks
    m = Manifest.load(season_dir, season)
    weekly = m.read_frame("weekly_stats")
    snaps = m.read_frame("snap_counts")
    cw = m.file("crosswalk")
    if weekly is None or cw is None:
        raise SystemExit(f"[grade] {season_dir} has no weekly_stats/crosswalk")
    frame = player_weeks(weekly, snaps, Crosswalk.from_csv(cw))
    rows = frame.loc[frame["week"] == week]
    return {str(g): float(p) for g, p in zip(rows["gsis_id"], rows["league_points"])
            if g and p == p}


def actuals_from_nflverse(season: int, week: int) -> dict[str, float]:
    """Actual points for EVERY player with a stat line that week, straight from
    nflverse (public, read-only), scored by `gridiron.usage.score_frame`. The
    most complete source: an add claimed by another manager since still has
    a line here, where a later record's usage block no longer holds him."""
    import nflreadpy as nfl
    from gridiron.usage import score_frame
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    rows = score_frame(weekly.loc[weekly["week"] == week])
    return {str(g): float(p) for g, p in zip(rows["player_id"], rows["league_points"])
            if g and p == p}


def aggregate(grade: Grade, season: int, archive: Path, built: datetime) -> dict:
    agree, n = grade.agreement()
    d_agree, d_n = direction(grade)
    return {"season": season, "week": grade.week, "archive": archive.name,
            "archive_built": built.isoformat(timespec="seconds"),
            "graded": sum(1 for c in grade.comparisons if c.status == "graded"),
            "ungradeable": grade.ungradeable, "scorable": n, "agree": agree,
            "direction_n": d_n, "direction_agree": d_agree,
            "withheld": len(grade.withheld), "unverified": len(grade.unverified),
            "decisions": len(grade.decisions),
            "projection_mae": "" if grade.projection_mae is None else grade.projection_mae,
            "projection_n": grade.projection_n,
            "graded_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def upsert(path: Path, row: Mapping[str, object]) -> list[dict]:
    """Replace the row for the same (season, week, archive); keep the rest."""
    rows: list[dict] = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            rows = [r for r in csv.DictReader(fh)
                    if (r["season"], r["week"], r["archive"])
                    != (str(row["season"]), str(row["week"]), str(row["archive"]))]
    rows = [{k: r.get(k, "") for k in FIELDS} for r in rows]
    rows.append({k: row[k] for k in FIELDS})
    rows.sort(key=lambda r: (int(r["season"]), int(r["week"]), r["archive"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    return rows


def season_line(rows: Sequence[Mapping[str, object]]) -> str:
    agree = sum(int(r["agree"]) for r in rows)
    n = sum(int(r["scorable"]) for r in rows)
    maes = [(float(r["projection_mae"]), int(r["projection_n"])) for r in rows
            if str(r["projection_mae"]) not in ("", "None")]
    mae = (sum(m * k for m, k in maes) / sum(k for _, k in maes)) if maes else None
    d_agree = sum(int(r.get("direction_agree") or 0) for r in rows)
    d_n = sum(int(r.get("direction_n") or 0) for r in rows)
    return (f"season to date over {len(rows)} graded archive(s): endorsed advice "
            f"pointed the right way {agree}/{n}"
            + (f" ({agree / n:.0%})" if n else "")
            + f"; projection direction on every graded comparison {d_agree}/{d_n}"
            + (f" ({d_agree / d_n:.0%})" if d_n else "")
            + (f"; roster projection MAE {mae:.2f}" if mae is not None else ""))


def render(grade: Grade, names: Mapping[str, str]) -> list[str]:
    out = ["| kind | slot | held | alternative | projected edge | actual edge | status | key |",
           "|---|---|---|---|---|---|---|---|"]
    for c in grade.comparisons:
        out.append(f"| {c.kind} | {c.slot} | {names.get(c.held_id, c.held_id)} | "
                   f"{names.get(c.alternative_id, c.alternative_id)} | "
                   f"{c.projected_edge if c.projected_edge is not None else '—'} | "
                   f"{c.realized_edge if c.realized_edge is not None else '—'} | "
                   f"{c.status} ({c.label()}) | `{c.key}` |")
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--season", type=int, default=None)
    ap.add_argument("--archives", type=Path, default=LEDGER / "decisions")
    ap.add_argument("--archive", type=Path, default=None, help="grade this file")
    ap.add_argument("--before", default=None, help="ISO time; latest archive before it")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--actuals-record", type=Path)
    src.add_argument("--actuals-cache", type=Path, help="a season<YYYY> cache dir")
    src.add_argument("--actuals-nflverse", action="store_true",
                     help="score the week from nflverse directly (most complete)")
    ap.add_argument("--acted", action="append", default=[])
    ap.add_argument("--declined", action="append", default=[])
    ap.add_argument("--grades-dir", type=Path, default=GRADES_DIR)
    ap.add_argument("--no-save", action="store_true")
    args = ap.parse_args(argv)

    if args.archive:
        blob = read_archive(args.archive)
        chosen = (archive_stamp(args.archive) or datetime.now(timezone.utc),
                  args.archive, blob)
        why = "given with --archive"
    else:
        season = args.season or datetime.now(timezone.utc).year
        archives = week_archives(args.archives, season, args.week)
        chosen, why = pick_archive(archives, before=_time(args.before) if args.before else None)
        if chosen is None:
            print(f"[grade] no week {args.week} archive under {args.archives} "
                  f"qualifies ({why}); {len(archives)} archive(s) for the week",
                  file=sys.stderr)
            return 2
    built, path, archive = chosen
    season = int(archive.get("season") or args.season or built.year)
    if args.actuals_record:
        actuals = actuals_from_record(args.actuals_record, args.week)
    elif args.actuals_cache:
        actuals = actuals_from_cache(args.actuals_cache, season, args.week)
    else:
        actuals = actuals_from_nflverse(season, args.week)
    if not actuals:
        print(f"[grade] no actual points for week {args.week} in the actuals source "
              f"(a record's usage block covers only its last 4 weeks)", file=sys.stderr)
        return 3
    observed = {**{k: ACTED for k in args.acted}, **{k: DECLINED for k in args.declined}}
    grade = grade_archive(archive, actuals, observed=observed)
    names = {str(p.get("sleeper_id")): str(p.get("name")) for p in archive.get("roster") or []}
    for up in archive.get("upgrades") or []:
        add = up.get("add") or {}
        names[str(add.get("sleeper_id"))] = str(add.get("name"))
    print(f"# Week {args.week} grade — archive {path.name} (built {built.isoformat()}; "
          f"{why})\n")
    d_agree, d_n = direction(grade)
    print(grade.summary())
    print(f"projection direction on every graded comparison (advice or not): "
          f"{d_agree}/{d_n}\n")
    print("\n".join(render(grade, names)) + "\n")
    for n in grade.notes:
        print(f"- {n}")
    if not args.no_save:
        rows = upsert(args.grades_dir / f"season{season}.csv",
                      aggregate(grade, season, path, built))
        print(f"\n{season_line(rows)}")
        print(f"[grade] saved to {args.grades_dir / f'season{season}.csv'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
