"""Move frozen decision records between an ephemeral runner and a private store.

    PYTHONPATH=src python scripts/cloud/carryover.py restore --store .carry
    PYTHONPATH=src python scripts/cloud/carryover.py publish --store .carry

Offline, and it never touches the data cache — only `data/ledger/decisions/`,
which holds the pages this project froze at decision time. `gridiron.carryover`
holds the reasoning, the validation and the retention limits; this file is the
command line around it.

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
    ap.add_argument("--require", action="store_true",
                    help="exit non-zero when nothing was carried")
    args = ap.parse_args(argv)

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ledger = args.ledger or DEFAULT_LEDGER

    if args.action == "restore":
        report = carryover.restore(args.store, ledger, season=args.season, now=now,
                                   keep=args.keep, max_age_days=args.max_age_days)
    else:
        report = carryover.publish(ledger, args.store, season=args.season, now=now,
                                   keep=args.keep)

    for line in carryover.describe([report]):
        print(line)
    weeks = carryover.weeks_in(ledger, args.season)
    print("ledger holds week(s): "
          + (", ".join(str(w) for w in weeks) if weeks else "none"))
    if args.require and not report.accepted:
        print(f"REQUIRED: {args.action} carried nothing.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
