"""Render the weekly roster report from the season cache. OFFLINE.

    PYTHONPATH=src python scripts/ingest/pull_week.py
    PYTHONPATH=src python scripts/weekly/report.py
    PYTHONPATH=src python scripts/weekly/report.py --owner Kejjeh --write

Reads only what `pull_week.py` cached, so the report can be re-rendered
without touching the network and always states the as-of time of every input
it used. A source that is missing or stale is named at the top of the output;
it is never substituted for.

The report does not recommend a lineup. There is no projection model past the
rule #5 gate yet, so it prints measured usage, league points, the injury
designation and the market's implied team total, and stops there.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gridiron import ingest as ing
from gridiron.freshness import WeekContext
from gridiron.ids import Crosswalk, sleeper_gsis_overlay
from gridiron.league_config import (MY_SLEEPER_USERNAME, SEASON_YEAR,
                                    SETTINGS_VERIFIED)
from gridiron.paths import OUTPUTS, ensure_dirs
from gridiron.sleeper import owner_roster
from gridiron.usage import player_weeks, season_to_date, weeks_present
from gridiron.weekly import build_report

SOURCES = ("sleeper_league", "injuries", "schedules", "weekly_stats",
           "snap_counts", "crosswalk")


def kickoffs_for(schedule: pd.DataFrame | None, week: int) -> list[datetime]:
    if schedule is None or len(schedule) == 0:
        return []
    wk = schedule.loc[schedule["week"] == int(week)]
    out: list[datetime] = []
    for row in wk.itertuples():
        day, time_ = str(getattr(row, "gameday", "")), str(getattr(row, "gametime", ""))
        if not day or day == "nan":
            continue
        try:
            # nflverse gameday/gametime are US/Eastern local.
            stamp = pd.Timestamp(f"{day} {time_ or '13:00'}", tz="America/New_York")
        except (ValueError, TypeError):
            continue
        out.append(stamp.tz_convert("UTC").to_pydatetime())
    return out


def find_owner_id(snapshot: dict, owner: str) -> str | None:
    """Match the owner by user_id or display name. This is the ONE place a
    human handle is used, and it resolves to an id immediately — it never
    becomes a join key for player data (rule #3)."""
    owner = str(owner)
    for u in snapshot.get("users") or []:
        if str(u.get("user_id")) == owner:
            return owner
        if str(u.get("display_name") or "").lower() == owner.lower():
            return str(u.get("user_id"))
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--week", type=int, default=None,
                    help="report week; defaults to Sleeper's current week")
    ap.add_argument("--owner", default=MY_SLEEPER_USERNAME)
    ap.add_argument("--cache-root", type=Path, default=None)
    ap.add_argument("--write", action="store_true",
                    help="also write markdown + csv under data/outputs/")
    ap.add_argument("--anonymous", action="store_true",
                    help="omit the league name from the rendered header")
    ap.add_argument("--fail-on-degraded", action="store_true",
                    help="exit 1 if any input is stale or missing (for cron)")
    args = ap.parse_args(argv)

    if not SETTINGS_VERIFIED:
        print("REFUSING: league_config.SETTINGS_VERIFIED is False (rule #1). "
              "Pull the real settings and flip it in the same commit as the "
              "values — never to unblock a run.", file=sys.stderr)
        return 3

    now = datetime.now(timezone.utc)
    directory = ing.season_cache(args.season, args.cache_root)
    manifest = ing.Manifest.load(directory, args.season)
    if not manifest.entries:
        print(f"No ingest manifest at {directory}. Run:\n"
              f"  PYTHONPATH=src python scripts/ingest/pull_week.py",
              file=sys.stderr)
        return 2

    snapshot = manifest.read_json("sleeper_league") or {}
    state = snapshot.get("state") or {}
    report_week = int(args.week or state.get("week") or 1)

    schedule = manifest.read_frame("schedules")
    weekly = manifest.read_frame("weekly_stats")
    snaps = manifest.read_frame("snap_counts")
    injuries = manifest.read_frame("injuries")
    players = manifest.read_json("sleeper_players") or {}

    ctx = WeekContext.build(
        season=int(state.get("season") or args.season),
        report_week=report_week,
        stats_weeks=weeks_present(weekly) if weekly is not None else (),
        kickoffs=kickoffs_for(schedule, report_week),
        now=now,
    )
    sources = manifest.freshness_report(SOURCES, now=now,
                                        required_week=report_week)

    cross_path = manifest.file("crosswalk")
    crosswalk = Crosswalk.from_csv(cross_path) if cross_path else Crosswalk({}, {})
    if players:
        crosswalk = crosswalk.with_overlay(sleeper_gsis_overlay(players))

    std = pd.DataFrame(columns=["gsis_id"])
    if weekly is not None and len(weekly) and ctx.stats_through is not None:
        skill = weekly.loc[weekly["position"].isin(["QB", "RB", "WR", "TE", "K"])]
        std = season_to_date(player_weeks(skill, snaps, crosswalk),
                             through_week=ctx.stats_through)

    owner_id = find_owner_id(snapshot, args.owner)
    if owner_id is None:
        print(f"Owner {args.owner!r} not found in the cached league users.",
              file=sys.stderr)
        return 2
    roster = owner_roster(snapshot.get("rosters") or [], owner_id)
    if roster is None:
        print(f"No roster owned by {args.owner!r} in the cached league.",
              file=sys.stderr)
        return 2

    report = build_report(
        context=ctx, sources=sources, roster=roster, sleeper_players=players,
        crosswalk=crosswalk, std=std,
        schedule=schedule if schedule is not None else pd.DataFrame(),
        injuries=injuries if injuries is not None else pd.DataFrame(),
    )
    text = report.to_markdown(include_names=not args.anonymous)
    print(text)

    if args.write:
        ensure_dirs()
        stem = f"week{report_week:02d}_report"
        written = []
        for name in (stem, "weekly_report_latest"):
            md = OUTPUTS / f"{name}.md"
            csv = OUTPUTS / f"{name}.csv"
            md.write_text(text, encoding="utf-8")
            report.rows.to_csv(csv, index=False)
            written += [md, csv]
        # The `_latest` pair is the stable path golden_run.py A/Bs; the
        # week-stamped pair is the record for that week.
        for path in written:
            print(f"[report] wrote {path}", file=sys.stderr)

    return 1 if (args.fail_on_degraded and report.degraded) else 0


if __name__ == "__main__":
    sys.exit(main())
