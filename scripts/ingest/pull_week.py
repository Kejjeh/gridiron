"""Pull this week's inputs into the season cache. Network; run before report.py.

    PYTHONPATH=src python scripts/ingest/pull_week.py            # normal
    PYTHONPATH=src python scripts/ingest/pull_week.py --force    # ignore ages
    PYTHONPATH=src python scripts/ingest/pull_week.py --no-players  # skip 16MB

Sources
  nflverse (nflreadpy) : weekly player stats, snap counts, schedules, injuries
  Sleeper (read-only)  : NFL state, league, users, rosters, matchups, players
  dynastyprocess       : the id crosswalk (rule #3)

Every pull is recorded in the season manifest with its as-of timestamp, row
count and covered weeks. A source that FAILS is recorded as a failure — the
report then degrades visibly instead of quietly rendering last week's numbers
as if they were this week's.

Nothing here writes to the league. gridiron.sleeper is GET-only by
construction; this script never submits a lineup, claim, trade or message.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from gridiron import ingest as ing
from gridiron.freshness import CADENCES
from gridiron.ids import CROSSWALK_URL
from gridiron.league_config import SEASON_YEAR
from gridiron.paths import ensure_dirs
from gridiron.scoring import scoring_coverage
from gridiron.sleeper import USER_AGENT, SleeperReadOnly

NFLVERSE_SOURCES = ("weekly_stats", "snap_counts", "schedules", "injuries")


def atomic(path: Path, write) -> None:
    """Write through a temp file and rename into place.

    A pull that dies halfway must not leave a truncated parquet sitting where
    the last good one was. `os.replace` is atomic on both POSIX and Windows,
    so the cached file is either the previous pull or the new one, never a
    fragment of the new one.
    """
    tmp = path.with_name(path.name + ".part")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _weeks(frame) -> list[int]:
    """Which weeks a pulled pandas frame actually covers."""
    if frame is None or "week" not in getattr(frame, "columns", []):
        return []
    return sorted({int(w) for w in frame["week"].dropna().unique()})


def pull_nflverse(manifest: ing.Manifest, season: int, now: datetime,
                  force: bool) -> None:
    import nflreadpy as nfl

    jobs = {
        "weekly_stats": (lambda: nfl.load_player_stats([season], summary_level="week"),
                         "nflreadpy.load_player_stats"),
        "snap_counts": (lambda: nfl.load_snap_counts([season]),
                        "nflreadpy.load_snap_counts"),
        "schedules": (lambda: nfl.load_schedules([season]),
                      "nflreadpy.load_schedules"),
        "injuries": (lambda: nfl.load_injuries([season]),
                     "nflreadpy.load_injuries"),
    }
    for name, (fn, source) in jobs.items():
        cadence = CADENCES[name]
        if not force and manifest.age_ok(name, now, cadence.max_age_hours / 2):
            print(f"  {name}: cached, skipping")
            continue
        path = manifest.directory / f"{name}.parquet"
        try:
            df = fn()
            pdf = df.to_pandas() if hasattr(df, "to_pandas") else df
            atomic(path, lambda t: pdf.to_parquet(t, index=False))
            manifest.record(name, path=path, rows=len(pdf), source=source,
                            weeks=_weeks(pdf))
            print(f"  {name}: {len(pdf)} rows, weeks {_weeks(pdf) or '—'}")
        except Exception as exc:  # a failed source is data, not a crash
            manifest.record_failure(name, source=source,
                                    error=f"{type(exc).__name__}: {exc}")
            print(f"  {name}: FAILED {type(exc).__name__}: {exc}", file=sys.stderr)


def pull_crosswalk(manifest: ing.Manifest, now: datetime, force: bool) -> None:
    name = "crosswalk"
    if not force and manifest.age_ok(name, now, CADENCES[name].max_age_hours / 2):
        print("  crosswalk: cached, skipping")
        return
    path = manifest.directory / "crosswalk.csv"
    try:
        req = urllib.request.Request(CROSSWALK_URL,
                                     headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=120) as resp:
            body = resp.read()
        atomic(path, lambda t: t.write_bytes(body))
        rows = sum(1 for _ in path.open(encoding="utf-8")) - 1
        manifest.record(name, path=path, rows=rows, source=CROSSWALK_URL)
        print(f"  crosswalk: {rows} rows")
    except Exception as exc:
        manifest.record_failure(name, source=CROSSWALK_URL,
                                error=f"{type(exc).__name__}: {exc}")
        print(f"  crosswalk: FAILED {exc}", file=sys.stderr)


def pull_sleeper(manifest: ing.Manifest, now: datetime, force: bool,
                 with_players: bool) -> int:
    client = SleeperReadOnly()
    snap_path = manifest.directory / "sleeper_league.json"
    week = 0
    try:
        snapshot = client.snapshot()
        atomic(snap_path, lambda t: t.write_text(json.dumps(snapshot, indent=1),
                                                 encoding="utf-8"))
        week = int(snapshot["week"])
        manifest.record("sleeper_league", path=snap_path,
                        rows=len(snapshot.get("rosters") or []),
                        source="api.sleeper.app (read-only)", weeks=[week])
        print(f"  sleeper_league: week {week}, "
              f"{len(snapshot.get('rosters') or [])} rosters")
    except Exception as exc:
        manifest.record_failure("sleeper_league",
                                source="api.sleeper.app (read-only)",
                                error=f"{type(exc).__name__}: {exc}")
        print(f"  sleeper_league: FAILED {exc}", file=sys.stderr)

    name = "sleeper_players"
    if not with_players:
        print("  sleeper_players: skipped (--no-players)")
    elif not force and manifest.age_ok(name, now, 24.0):
        print("  sleeper_players: cached, skipping")
    else:
        path = manifest.directory / "sleeper_players.json"
        try:
            players = client.players()
            atomic(path, lambda t: t.write_text(json.dumps(players),
                                                encoding="utf-8"))
            manifest.record(name, path=path, rows=len(players),
                            source="api.sleeper.app/v1/players/nfl (read-only)")
            print(f"  sleeper_players: {len(players)} players")
        except Exception as exc:
            manifest.record_failure(name, source="api.sleeper.app",
                                    error=f"{type(exc).__name__}: {exc}")
            print(f"  sleeper_players: FAILED {exc}", file=sys.stderr)
    return week


def check_scoring_inputs(manifest: ing.Manifest) -> ing.Entry | None:
    """Check the pulled frame against the league's scoring rules, and RECORD
    the answer in the manifest.

    A silently missing stat column reads as a zero, which is a wrong number
    rather than a missing one — and a warning on stderr dies with the run that
    printed it, leaving the next reader nothing to go on. So the verdict is
    written into the manifest entry, where `report.py` and anyone reading
    `manifest.json` can see it without re-opening the parquet.

    The check is alias-aware (`scoring_coverage`), so a cache predating the
    nflreadpy 0.1.x rename scores through its legacy columns and is not
    reported as broken.
    """
    frame = manifest.read_frame("weekly_stats")
    if frame is None:
        return None
    coverage = scoring_coverage(frame.columns)
    entry = manifest.note_missing_columns("weekly_stats",
                                          coverage.missing_columns)
    if not coverage.complete:
        print(f"  WARNING: weekly_stats cannot be scored — {coverage.reason()}. "
              f"gridiron.scoring would read the absent column(s) as zero, so "
              f"report.py will BLANK points for "
              f"{', '.join(sorted(coverage.affected_groups))} positions until "
              f"this is re-pulled.", file=sys.stderr)
    elif coverage.legacy_used:
        print(f"  note: weekly_stats scored through legacy column(s) "
              f"{', '.join(coverage.legacy_used)} (pre-0.1.x nflverse schema)")
    return entry


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--force", action="store_true",
                    help="re-pull every source regardless of cached age")
    ap.add_argument("--no-players", action="store_true",
                    help="skip the ~16 MB Sleeper player dump")
    ap.add_argument("--cache-root", type=Path, default=None)
    args = ap.parse_args(argv)

    ensure_dirs()
    now = datetime.now(timezone.utc)
    directory = ing.season_cache(args.season, args.cache_root)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = ing.Manifest.load(directory, args.season)

    print(f"[pull] season {args.season} -> {directory}")
    print("[pull] nflverse")
    pull_nflverse(manifest, args.season, now, args.force)
    print("[pull] crosswalk")
    pull_crosswalk(manifest, now, args.force)
    print("[pull] sleeper (read-only)")
    pull_sleeper(manifest, now, args.force, not args.no_players)
    check_scoring_inputs(manifest)
    manifest.save()

    failed = [e.name for e in manifest.entries.values() if e.error]
    unscorable = [e.name for e in manifest.entries.values() if e.missing_columns]
    print(f"[pull] manifest written: {manifest.path}")
    if failed:
        print(f"[pull] {len(failed)} source(s) failed: {failed} — the report "
              f"will show them as MISSING", file=sys.stderr)
    if unscorable:
        print(f"[pull] {len(unscorable)} source(s) pulled but incomplete: "
              f"{unscorable} — recorded in the manifest; the report will blank "
              f"the affected points rather than publish a wrong one",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
