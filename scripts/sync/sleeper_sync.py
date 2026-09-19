"""Five-minute Sleeper league sync — the command the scheduler and the owner run.

    PYTHONPATH=src python scripts/sync/sleeper_sync.py run      # one sync
    PYTHONPATH=src python scripts/sync/sleeper_sync.py run --if-due
    PYTHONPATH=src python scripts/sync/sleeper_sync.py status   # offline

`run` refreshes league, users, rosters, current-week matchups and NFL state —
five GETs, a second of network, a few hundred KB. That is the whole job. It
does NOT touch nflverse and it does NOT fetch the 16 MB player dump, which
stays on `pull_week.py`'s once-a-day cadence per Sleeper's own guidance. Run
this every five minutes; run that one daily.

`status` is offline and reads only the local sync state: last success, next
due, failure streak, settings drift. It prints no player, no team and no owner
name — safe to paste into an issue.

Nothing here writes to the league. The client is `gridiron.sleeper`, which is
GET-only by construction: no lineup, no claim, no trade, no message.

Exit codes:  0 success (or nothing due) · 1 sync failed · 2 bad invocation
             3 another sync holds the lock (not an error; the schedule is fine)
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from gridiron import livesync as ls
from gridiron.ingest import Manifest, season_cache
from gridiron.league_config import SEASON_YEAR
from gridiron.paths import ensure_dirs
from gridiron.sleeper import SleeperReadOnly, http_fetch, resolve_league_id


def _client(timeout: int, retries: int) -> SleeperReadOnly:
    """A client tuned for a FIVE-MINUTE cadence rather than a weekly one.

    The default adapter waits up to 60s per call and backs off 2s/4s, which is
    right for a job that runs once a week and must not give up. Here a run that
    takes minutes is a run that overlaps the next one, so the window is short
    and the give-up is early: a failed sync costs five minutes of staleness,
    which the status command reports honestly.
    """
    return SleeperReadOnly(
        resolve_league_id(),
        fetch=lambda url: http_fetch(url, timeout=timeout, retries=retries))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="refresh the league snapshot once")
    r.add_argument("--if-due", action="store_true",
                   help="do nothing unless the last success is older than the "
                        "interval (lets a fixed schedule be a floor, not a whip)")
    r.add_argument("--interval", type=int, default=ls.SYNC_INTERVAL_SECONDS,
                   metavar="SECONDS")
    r.add_argument("--timeout", type=int, default=15, metavar="SECONDS")
    r.add_argument("--retries", type=int, default=2)
    r.add_argument("--season", type=int, default=SEASON_YEAR)
    r.add_argument("--quiet", action="store_true")

    s = sub.add_parser("status", help="local sync health; no network")
    s.add_argument("--season", type=int, default=SEASON_YEAR)
    s.add_argument("--interval", type=int, default=ls.SYNC_INTERVAL_SECONDS)

    args = ap.parse_args(argv)
    ensure_dirs()
    now = datetime.now(timezone.utc)
    directory = season_cache(args.season)

    if args.cmd == "status":
        for line in ls.status_lines(ls.SyncState.load(directory), directory,
                                    now=now, interval=args.interval):
            print(line)
        return 0

    state = ls.SyncState.load(directory)
    if args.if_due and not state.is_due(now, interval=args.interval):
        due = state.next_due(interval=args.interval)
        if not args.quiet:
            print(f"not due until {due:%Y-%m-%dT%H:%M:%SZ}")
        return 0

    result = ls.sync_once(_client(args.timeout, args.retries), directory,
                          now=now, season=args.season,
                          manifest=Manifest.load(directory, args.season))
    if not args.quiet or not result.ok:
        print(result.line(), file=sys.stdout if result.ok else sys.stderr)
    if result.drift:
        # Loud, and still not a verification. Rule #1: a human decides.
        print(f"SETTINGS DRIFT: {', '.join(result.drift)} changed since the "
              f"last snapshot. SETTINGS_VERIFIED is untouched; run "
              f"scripts/verify_league_settings.py and decide by hand.",
              file=sys.stderr)
    if result.ok:
        return 0
    return 3 if result.code == "busy" else 1


if __name__ == "__main__":
    sys.exit(main())
