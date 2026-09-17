"""Re-verify league_config against the live Sleeper league. Network; read-only.

    PYTHONPATH=src python scripts/verify_league_settings.py

Rule #1 says flipping `SETTINGS_VERIFIED` requires pulling the real settings
from the platform. That was done on 2026-09-08. This script is the standing
check that the flipped values are still TRUE — leagues get edited mid-season
(a scoring tweak, a roster slot, a playoff week), and a constant that silently
stops matching the league is worse than one that was never verified.

It NEVER edits `league_config.py` and NEVER flips a flag. It prints what it
compared and exits:

    0  every checked constant matches the live league
    1  drift found — the diff is printed; a human decides what is right
    2  could not reach the league (no verdict either way)

`--json` prints the comparison as machine-readable output for a cron check.
"""
from __future__ import annotations

import argparse
import json
import sys

from gridiron import league_config as lc
from gridiron.sleeper import SleeperReadOnly

#: Sleeper scoring_settings key -> the ScoringRules field it must equal.
SCORING_MAP = {
    "pass_yd": "pass_yd", "pass_td": "pass_td", "pass_int": "interception",
    "rush_yd": "rush_yd", "rush_td": "rush_td", "rec": "reception",
    "rec_yd": "rec_yd", "rec_td": "rec_td", "fum_lost": "fumble_lost",
    "pass_2pt": "two_pt", "rush_2pt": "two_pt", "rec_2pt": "two_pt",
}

#: Sleeper league `settings` key -> the league_config constant it must equal.
SETTINGS_MAP = {
    "num_teams": "NUM_TEAMS",
    "playoff_teams": "PLAYOFF_TEAMS",
    "playoff_week_start": "PLAYOFF_START_WEEK",
    "trade_deadline": "TRADE_DEADLINE_WEEK",
    "waiver_budget": "WAIVER_BUDGET",
    "waiver_bid_min": "WAIVER_MIN_BID",
    "waiver_day_of_week": "WAIVER_CLEAR_WEEKDAY",
    "waiver_clear_days": "WAIVER_CLEAR_DAYS",
    "trade_review_days": "TRADE_REVIEW_DAYS",
    "max_keepers": "MAX_KEEPERS",
    "reserve_slots": "IR_SLOTS",
}


def expected_roster_positions() -> list[str]:
    """ROSTER_SLOTS rebuilt into Sleeper's `roster_positions` vocabulary."""
    out: list[str] = []
    for slot, key in (("QB", "QB"), ("RB", "RB"), ("WR", "WR"), ("TE", "TE"),
                      ("FLEX", "FLEX"), ("K", "K"), ("DEF", "DST"),
                      ("BN", "BENCH")):
        out += [slot] * int(lc.ROSTER_SLOTS.get(key, 0))
    return out


def compare(league: dict) -> list[dict]:
    """Every checked constant, with the live value beside it."""
    rows: list[dict] = []

    def add(field: str, ours, theirs) -> None:
        rows.append({"field": field, "ours": ours, "live": theirs,
                     "match": ours == theirs})

    add("LEAGUE_NAME", lc.LEAGUE_NAME, league.get("name"))
    add("SEASON_YEAR", lc.SEASON_YEAR, int(league.get("season") or 0))
    add("NUM_TEAMS", lc.NUM_TEAMS, int(league.get("total_rosters") or 0))
    add("ROSTER_SLOTS", expected_roster_positions(),
        list(league.get("roster_positions") or []))

    scoring = league.get("scoring_settings") or {}
    for key, field in SCORING_MAP.items():
        add(f"ScoringRules.{field} ({key})",
            float(getattr(lc.DEFAULT_SCORING, field)),
            None if key not in scoring else float(scoring[key]))

    settings = league.get("settings") or {}
    for key, const in SETTINGS_MAP.items():
        if key not in settings:
            continue
        add(const, getattr(lc, const), int(settings[key]))

    for key, weight in sorted(lc.KICKING_SCORING.items()):
        if key in scoring:
            add(f"KICKING_SCORING[{key}]", float(weight), float(scoring[key]))
    for key, weight in sorted(lc.DEFENSE_SCORING.items()):
        if key in scoring:
            add(f"DEFENSE_SCORING[{key}]", float(weight), float(scoring[key]))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        league = SleeperReadOnly().league()
    except Exception as exc:
        print(f"could not reach Sleeper: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2
    if not league:
        print("Sleeper returned no league object — check the league id.",
              file=sys.stderr)
        return 2

    rows = compare(league)
    drift = [r for r in rows if not r["match"]]
    if args.json:
        print(json.dumps({"settings_verified": lc.SETTINGS_VERIFIED,
                          "checked": len(rows), "drift": drift}, indent=1,
                         default=str))
    else:
        print(f"SETTINGS_VERIFIED = {lc.SETTINGS_VERIFIED}")
        print(f"checked {len(rows)} constants against league "
              f"{league.get('league_id')} ({league.get('status')})")
        for r in drift:
            print(f"  DRIFT {r['field']}: ours={r['ours']!r} "
                  f"live={r['live']!r}")
        print("  no drift" if not drift else
              f"  {len(drift)} constant(s) differ — a human decides which is "
              f"right. Do NOT flip a flag to make this pass.")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
