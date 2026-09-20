"""Move frozen decision records between an ephemeral runner and a private store.

    PYTHONPATH=src python scripts/cloud/carryover.py restore --store .carry
    PYTHONPATH=src python scripts/cloud/carryover.py publish --store .carry

Offline. It moves two things, and the difference between them matters:

  the RECORDS in `data/ledger/decisions/` — the pages this project froze at
  decision time, which is what "since the last snapshot" compares against and
  what a grader reads next week; and

  the INPUTS in the season cache — the manifest and the frames the page is
  rendered FROM. Carrying records alone was not enough. A hosted runner whose
  refresh fails has an empty cache, so it cannot rebuild any page at all: the
  render exited 2 and the run produced nothing. Carried inputs keep their
  original `as_of` and are marked as NOT refreshed by this run, so the page
  they produce shows the last known picture, dated, with every action
  withheld. `--no-inputs` restores or publishes records only.

`gridiron.carryover` holds the reasoning, the validation and the retention
limits; this file is the command line around it.

Both subcommands are deliberately non-fatal by default. A first run has no
history to restore, a run that froze nothing has nothing to publish, and
neither is an error worth failing a build over: the page states when it has
no previous snapshot. `--require` turns an empty restore into a failure for
a caller that wants to know.

Nothing printed here names a player. The output is filenames, counts and
reasons, so a public run log of a private repository's job still exposes no
roster (rule #10).
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

from gridiron import carryover
from gridiron.ingest import season_cache
from gridiron.league_config import SEASON_YEAR
from gridiron.paths import LEDGER

DEFAULT_LEDGER = LEDGER / "decisions"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("action", choices=("restore", "publish"))
    ap.add_argument("--store", type=Path, required=True,
                    help="the private store directory (a restored cache in CI)")
    ap.add_argument("--ledger", type=Path, default=None,
                    help=f"decision ledger root (default {DEFAULT_LEDGER})")
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--keep", type=int, default=carryover.DEFAULT_KEEP)
    ap.add_argument("--max-age-days", type=int, default=carryover.RESTORE_MAX_AGE_DAYS)
    ap.add_argument("--now", default=None, help="ISO timestamp (tests/replay)")
    ap.add_argument("--cache-root", type=Path, default=None,
                    help="ingest cache root (default data/research/cache/)")
    ap.add_argument("--no-inputs", action="store_true",
                    help="carry decision records only, not the ingest cache")
    ap.add_argument("--require", action="store_true",
                    help="exit non-zero when nothing was carried")
    args = ap.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ledger = args.ledger or DEFAULT_LEDGER

    cache = season_cache(args.season, args.cache_root)

    reports = []
    if args.action == "restore":
        reports.append(carryover.restore(args.store, ledger, season=args.season,
                                         now=now, keep=args.keep,
                                         max_age_days=args.max_age_days))
        if not args.no_inputs:
            reports.append(carryover.restore_inputs(
                args.store, cache, season=args.season, now=now))
    else:
        reports.append(carryover.publish(ledger, args.store, season=args.season,
                                         now=now, keep=args.keep))
        if not args.no_inputs:
            reports.append(carryover.publish_inputs(
                cache, args.store, season=args.season, now=now))

    for line in carryover.describe(reports):
        print(line)
    weeks = carryover.weeks_in(ledger, args.season)
    print("ledger holds week(s): "
          + (", ".join(str(w) for w in weeks) if weeks else "none"))
    # `--require` is about the RECORDS: an empty input carry is normal on a
    # run whose refresh worked, and failing on it would fail every good run.
    if args.require and not reports[0].accepted:
        print(f"REQUIRED: {args.action} carried no decision record.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
