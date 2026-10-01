"""Render the Game Day page from the season cache. OFFLINE.

    PYTHONPATH=src python scripts/weekly/gameday.py              # prints a summary
    PYTHONPATH=src python scripts/weekly/gameday.py --write      # + HTML/JSON under data/outputs/dashboard/
    PYTHONPATH=src python scripts/weekly/gameday.py --write --anonymous

Reads only what `sleeper_sync.py` and `pull_week.py` cached — the league
snapshot (matchup rows with the platform's own points), the player dump
(names, positions, teams, the live designation), the schedule (expected
kickoffs, for locks) and the per-game status feed — plus this week's
decision-time archive, if one was frozen before kickoff. Never opens a
socket; tests/test_gameday_cli.py breaks the socket and renders.

The page it writes carries a small inline script that can refresh scores
and statuses from Sleeper's read-only API when the owner taps Refresh. That
is the LIVE mode. Until then, and whenever a refresh fails, the page is a
dated SNAPSHOT and says so. Opening or reloading the file does not fetch.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from gridiron import ingest as ing
from gridiron.freshness import Status
from gridiron.gameday import GameFeed, build_gameday
from gridiron.league_config import (MY_SLEEPER_USERNAME, SEASON_YEAR,
                                    SETTINGS_VERIFIED)
from gridiron.livesync import GAME_STATUS_NAME, current_snapshot
from gridiron.paths import OUTPUTS, ensure_dirs
from gridiron.sleeper import resolve_league_id

#: Every source this page READS. If a value from a source reaches the page,
#: its as-of line is on the page (tests/test_gameday_cli.py re-derives this
#: from the file's own syntax, the same way test_dashboard_cli does).
SOURCES = ("sleeper_league", "sleeper_players", "schedules", GAME_STATUS_NAME)

DASHBOARD_DIR = OUTPUTS / "dashboard"


def find_owner_id(snapshot: dict, owner: str) -> str | None:
    """The ONE place a human handle is used; it resolves to an id at once."""
    owner = str(owner)
    for u in snapshot.get("users") or []:
        if str(u.get("user_id")) == owner:
            return owner
        if str(u.get("display_name") or "").lower() == owner.lower():
            return str(u.get("user_id"))
    return None


def _read_previous(out_dir: Path, week: int) -> dict | None:
    """The previous game-day record written to the same directory, if any.
    Read as data; a file that does not parse is simply no history."""
    p = out_dir / f"week{week:02d}_gameday.json"
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return blob if isinstance(blob, dict) else None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--week", type=int, default=None,
                    help="week to render; defaults to the week the snapshot says")
    ap.add_argument("--owner", default=MY_SLEEPER_USERNAME)
    ap.add_argument("--cache-root", type=Path, default=None)
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="where to write the HTML/JSON (default data/outputs/dashboard/)")
    ap.add_argument("--archive-root", type=Path, default=None,
                    help="where the decision archives are (default data/ledger/decisions/)")
    ap.add_argument("--write", action="store_true", help="write HTML + JSON")
    ap.add_argument("--anonymous", action="store_true",
                    help="omit the league name from the page")
    ap.add_argument("--now", default=None,
                    help="ISO-8601 UTC instant to render AS OF (locks, freshness); default: now")
    ap.add_argument("--api-base", action="append", default=None,
                    help=argparse.SUPPRESS)   # scenario harness only: a fixture server
    args = ap.parse_args(argv)

    if not SETTINGS_VERIFIED:
        print("REFUSING: league_config.SETTINGS_VERIFIED is False (rule #1).", file=sys.stderr)
        return 3

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    directory = ing.season_cache(args.season, args.cache_root)
    manifest = ing.Manifest.load(directory, args.season)
    if not manifest.entries:
        print(f"No ingest manifest at {directory}. Run:\n"
              f"  PYTHONPATH=src python scripts/sync/sleeper_sync.py run", file=sys.stderr)
        return 2
    if manifest.season != args.season:
        print(f"REFUSING: the cache at {directory} is season {manifest.season}, "
              f"not {args.season}.", file=sys.stderr)
        return 2

    snap_path = current_snapshot(directory, manifest)
    snapshot = manifest.read_json("sleeper_league") or {}
    if not snapshot and snap_path is not None:
        snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    if not snapshot:
        print("No league snapshot in the cache; nothing to score.", file=sys.stderr)
        return 2
    state = snapshot.get("state") or {}
    state_season = int(state.get("season") or args.season)
    if state_season != args.season:
        print(f"REFUSING: the cached Sleeper snapshot is season {state_season}, "
              f"the requested page is {args.season}.", file=sys.stderr)
        return 2
    week = int(args.week or snapshot.get("week") or state.get("week") or 0)
    if week < 1:
        print(f"No in-season week to render: Sleeper state is season {state_season}, "
              f"week {state.get('week')!r}. Pass --week for a past week.", file=sys.stderr)
        return 2

    sources = manifest.freshness_report(SOURCES, now=now, required_week=week)
    by_name = {s.name: s for s in sources}
    players = manifest.read_json("sleeper_players") or {}
    schedule = manifest.read_frame("schedules")
    feed = GameFeed.from_blob(manifest.read_json(GAME_STATUS_NAME), week=week,
                              freshness=by_name.get(GAME_STATUS_NAME), now=now)
    players_source = by_name.get("sleeper_players")

    owner_id = find_owner_id(snapshot, args.owner)
    if owner_id is None:
        print(f"Owner {args.owner!r} not found in the cached league users.", file=sys.stderr)
        return 2
    league_id = str(snapshot.get("league_id") or (snapshot.get("league") or {}).get("league_id")
                    or resolve_league_id())

    out_dir = args.out_dir or DASHBOARD_DIR
    previous = _read_previous(out_dir, week)
    day = build_gameday(
        season=args.season, week=week, league_id=league_id, owner_id=owner_id,
        snapshot=snapshot, sleeper_players=players,
        players_as_of=players_source.as_of if players_source else None,
        players_fresh=players_source is not None and players_source.status is Status.FRESH,
        schedule=schedule, feed=feed, sources=sources, now=now,
        archive_root=args.archive_root, previous=previous,
        api_bases=tuple(args.api_base or ()))

    # Summary to stdout: no player names, so a log of this run exposes nothing.
    s = day.score
    print(f"{day.season} week {day.week} game day as of {now:%Y-%m-%d %H:%M UTC}")
    print("DEGRADED" if day.degraded else "All inputs current.")
    for n in day.notes:
        print(f"  - {n}")
    opp = f"roster #{s.opp.roster_id}" if s.opp else "no opponent"
    print(f"score: {s.mine.platform_points} vs {opp} {s.opp.platform_points if s.opp else '—'}"
          f" — {s.lead()}")
    print(f"you: {s.mine.exposure()}")
    if s.opp:
        print(f"they: {s.opp.exposure()}")
    print(f"actions still available: {sum(1 for a in day.actions if a.available)} of {len(day.actions)}"
          f" ({day.pregame.note.split(' (')[0]})")
    print(f"since last snapshot: {len(day.changes.items)} change(s)"
          + (f" — {day.changes.note}" if day.changes.note else ""))

    if args.write:
        ensure_dirs()
        out_dir.mkdir(parents=True, exist_ok=True)
        html = day.to_html(include_names=not args.anonymous)
        record = json.dumps(day.record(), indent=1, default=str)
        for stem in (f"week{week:02d}_gameday", "gameday_latest"):
            (out_dir / f"{stem}.html").write_text(html, encoding="utf-8")
            (out_dir / f"{stem}.json").write_text(record, encoding="utf-8")
            print(f"[gameday] wrote {out_dir / (stem + '.html')}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
