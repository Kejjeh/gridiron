"""How fast does Sleeper's projections feed react when a high-snap RB starter
is ruled out? Research only: never imported by src/gridiron, writes nothing.

Owner decision 2026-11-18 (Kejjeh/gridiron#29 item 2, option B: "record
first, decide later"). Every board build records each watched starter's
feed answer in the record's `starter_feed` block (`gridiron.shadow
.starter_feed_block`): LISTED with a number, ABSENT from a full feed, or
UNKNOWN. The cloud keeps only ~10 h of records, so the evidence is the
carried change log (`--log`, `gridiron.shadow.append_feed_log`: one row per
starter-week each time his answer changes); decision archives (`--archives`)
work too, for a local run. Per starter-week it reports:

  * the first pre-kickoff observation where the feed DROPPED him (ABSENT, or
    LISTED at 0), timed by when Sleeper's feed was FETCHED (the pull reuses a
    feed under 2 h old), and how many hours before kickoff that was;
  * whether he played (`--played`, a CSV `gsis_id,week,played` with 1/0;
    without it, UNKNOWN, never assumed).

Summary: drops that played (false drops), sits the feed never dropped
(misses), sits with no known pre-kickoff answer at all (unanswered, never a
miss), and the lead time of the drops that were right. UNKNOWN observations
are counted, never read as listed or absent. With fewer than `MIN_WEEKS` weeks of
records it says so: the decision (#29 item 2, option C) needs more.

    PYTHONPATH=src python scripts/research/feed_reaction.py \\
        --log <artifact>/ledger/starter_feed/season2026.csv [--played played.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path

MIN_WEEKS = 4
YES, NO, UNKNOWN = "yes", "no", "UNKNOWN"


def _dt(v: object) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v))
    except (TypeError, ValueError):
        return None
    return d if d.tzinfo is not None else None


def records_from_log(path: Path) -> list[dict]:
    """The change log as records (one per build and week that logged a change)."""
    by_build: dict[tuple[str, int], dict] = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            players = by_build.setdefault((r["generated"], int(r["week"])), {})
            players[r["sleeper_id"]] = {
                "gsis_id": r["gsis_id"], "team": r["team"], "feed": r["feed"],
                "points": float(r["points"]) if r["points"] != "" else None,
                "fetched_at": r["fetched_at"] or None, "kickoff": r["kickoff"] or None}
    return [{"generated": g, "starter_feed": {"week": w, "players": players}}
            for (g, w), players in by_build.items()]


def reaction_rows(records: Iterable[Mapping[str, object]]) -> list[dict]:
    """One row per (starter, week) seen in any record's `starter_feed`."""
    seen: dict[tuple[str, int], list[tuple[datetime, Mapping]]] = defaultdict(list)
    for rec in records:
        block = rec.get("starter_feed")
        built = _dt(rec.get("generated"))
        if not isinstance(block, Mapping) or built is None:
            continue
        week = int(block.get("week") or 0)
        for sid, p in (block.get("players") or {}).items():
            if isinstance(p, Mapping):
                seen[(str(sid), week)].append((built, p))
    out = []
    for (sid, week), builds in sorted(seen.items()):
        builds.sort(key=lambda b: b[0])
        last = builds[-1][1]
        kick = _dt(last.get("kickoff"))
        pre = [(t, p) for t, p in builds if kick is None or t < kick]
        known = [(t, p) for t, p in pre if p.get("feed") in ("LISTED", "ABSENT")]
        hit = next(((t, p) for t, p in known if p.get("feed") == "ABSENT"
                    or (p.get("feed") == "LISTED" and p.get("points") == 0)), None)
        # timed by the feed's own fetch, not the board's run, when it says
        dropped = None if hit is None else (_dt(hit[1].get("fetched_at")) or hit[0])
        out.append({"sleeper_id": sid, "gsis_id": str(last.get("gsis_id") or ""),
                    "team": last.get("team"), "week": week,
                    "builds": len(pre), "unknown_builds": len(pre) - len(known),
                    "answered": bool(known),
                    "seen_at": None if hit is None else hit[0].isoformat(),
                    "first_drop": None if dropped is None else dropped.isoformat(),
                    "hours_before_kickoff": (None if dropped is None or kick is None
                                             else round((kick - dropped).total_seconds() / 3600,
                                                        1))})
    return out


def summarize(rows: list[dict], played: Mapping[tuple[str, int], bool]) -> dict:
    """Counts over `rows`; `played` maps (gsis id, week) -> played. A row it
    does not cover is UNKNOWN and counts in no rate."""
    rows = [dict(r) for r in rows]
    for r in rows:
        p = played.get((r["gsis_id"], r["week"]))
        r["played"] = UNKNOWN if p is None else (YES if p else NO)
    known = [r for r in rows if r["played"] != UNKNOWN]
    right = [r for r in known if r["played"] == NO and r["first_drop"]]
    leads = [r["hours_before_kickoff"] for r in right if r["hours_before_kickoff"] is not None]
    weeks = sorted({r["week"] for r in rows})
    return {"rows": rows, "starter_weeks": len(rows), "weeks": weeks,
            "enough_weeks": len(weeks) >= MIN_WEEKS,
            "played_unknown": len(rows) - len(known),
            "dropped": sum(1 for r in rows if r["first_drop"]),
            "false_drops": sum(1 for r in known if r["played"] == YES and r["first_drop"]),
            "sat": sum(1 for r in known if r["played"] == NO),
            "sat_dropped": len(right),
            "sat_missed": sum(1 for r in known if r["played"] == NO and r["answered"]
                              and not r["first_drop"]),
            # no LISTED/ABSENT answer before kickoff: the feed was never read
            "sat_unanswered": sum(1 for r in known if r["played"] == NO
                                  and not r["answered"]),
            "median_lead_hours": statistics.median(leads) if leads else None}


def read_played(path: Path | None) -> dict[tuple[str, int], bool]:
    if path is None:
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {(str(r["gsis_id"]).strip(), int(r["week"])): str(r["played"]).strip() == "1"
                for r in csv.DictReader(fh)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Time Sleeper's feed against RB rulings (#29).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--log", type=Path, help="the carried change log (the evidence)")
    src.add_argument("--archives", type=Path, help="decision archives (a local run)")
    ap.add_argument("--played", type=Path)
    a = ap.parse_args(argv)
    if a.log is not None:
        records, what = records_from_log(a.log), f"log {a.log}"
    else:
        paths = sorted(a.archives.rglob("week*.json"))
        if not paths:
            raise SystemExit(f"no archives under {a.archives}")
        records = [json.loads(p.read_text(encoding="utf-8")) for p in paths]
        what = f"{len(paths)} archives"
    s = summarize(reaction_rows(records), read_played(a.played))
    print(f"{what}; {s['starter_weeks']} starter-weeks in weeks {s['weeks']}")
    if not s["enough_weeks"]:
        print(f"TOO FEW WEEKS: {len(s['weeks'])} < {MIN_WEEKS}; nothing below decides #29 item 2")
    print("| team | week | builds | unknown | first drop | h before kickoff | played |")
    print("|---|---|---|---|---|---|---|")
    for r in s["rows"]:
        print(f"| {r['team']} | {r['week']} | {r['builds']} | {r['unknown_builds']} | "
              f"{r['first_drop'] or '-'} | {r['hours_before_kickoff'] or '-'} | {r['played']} |")
    print({k: v for k, v in s.items() if k not in ("rows", "weeks")})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
