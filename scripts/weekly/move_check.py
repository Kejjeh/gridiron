"""Pre-flight a set of add/drop moves before recommending them. READ-ONLY.

    PYTHONPATH=src python scripts/weekly/move_check.py --record <record> \\
        --add "Kaelon Black,RB" --drop "NO,DEF" [--add ... --drop ...] \\
        [--leaders 8] [--week 9] [--out data/outputs/review/moves_week09.md]

Prints, for the owner's whole roster and for every named add and drop, the
facts that have changed advice before (`gridiron.movecheck`): this week's
platform points and line, the CURRENT injury tag (from Sleeper's week stats,
so an in-game injury shows), byes this week and the next two, the
quarterback a receiver depends on, a falling role, a hot game on a drop, a
returning player the rest-of-season rank undercounts, and whether a player
is actually available. Then whether the roster after the moves can field
next week's lineup. `--leaders N` adds the available pool's best scores this
week by position ("any free agents going off today?").

Sources: the record (ids, roster, pool, usage, ros), nflverse (schedule and
QB box scores, public, read-only) and Sleeper's week stats
(api.sleeper.com/stats, read-only, one call). Names resolve to ids through
the record only (rule #3); a name it cannot resolve is reported, never
guessed. Nothing is submitted anywhere. The output names the owner's
players: keep it under data/outputs/review/ (gitignored).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from gridiron import movecheck as M
from gridiron.external_ranks import RankRow, position_key, resolve, team_key
from gridiron.ids import normalize_id
from gridiron.paths import OUTPUTS

REVIEW_DIR = OUTPUTS / "review"
STATS_URL = "https://api.sleeper.com/stats/nfl/{season}/{week}?season_type=regular"


def universe(ix: M.Index) -> list[tuple[str, str, str, str, str]]:
    return [(sid, str(i.get("name") or ""), position_key(i.get("position")),
             team_key(i.get("team")), str(i.get("held") or "unknown"))
            for sid, i in ix.who.items() if i.get("name")]


def resolve_items(ix: M.Index, items: Sequence[str]) -> tuple[list[str], list[str]]:
    ids, problems = [], []
    uni = universe(ix)
    for raw in items:
        raw = raw.strip()
        if raw.isdigit():
            ids.append(normalize_id(raw))
            continue
        name, _, pos = raw.rpartition(",")
        if not name:
            problems.append(f"{raw!r}: give a sleeper id or 'Name,POS'")
            continue
        if position_key(pos) == "DEF":            # team defenses are keyed by team
            hits = [u for u in uni if u[2] == "DEF" and team_key(name) in (u[3], u[0])]
            if len(hits) == 1:
                ids.append(hits[0][0])
                continue
        [res] = resolve([RankRow("move", 0, name.strip(), position_key(pos), "")], uni)
        if res.matched:
            ids.append(res.sleeper_id)
        else:
            problems.append(f"{raw!r}: {res.status} — {res.note}")
    return ids, problems


def _cell(c: M.Card) -> str:
    lv = c.live
    pts = "no line this week" if lv is None or lv.points is None else \
        f"{lv.points:.1f} ({lv.line})" if lv.line else f"{lv.points:.1f}"
    return (f"| {c.name} ({c.position}, {c.team or '?'}) | {c.held} | {pts} | {c.trend} | "
            f"{c.position}{c.ros_rank if c.ros_rank is not None else '—'} | "
            f"{'; '.join(c.flags) or '—'} |")


HEAD = ["| player | held | this week (platform pts) | usage trend | ROS | facts to weigh |",
        "|---|---|---|---|---|---|"]


def render(record: dict, *, roster: list[M.Card], adds: list[M.Card], drops: list[M.Card],
           plan: list[str], board: dict | None, week: int, problems: Sequence[str],
           live_note: str) -> str:
    out = [f"# Move check — week {week} (record built {record.get('generated')})", "",
           f"_{live_note}_", ""]
    for p in problems:
        out.append(f"- UNRESOLVED {p}")
    out += ["", "## Your roster (every player, flags first)", ""] + HEAD
    out += [_cell(c) for c in sorted(roster, key=lambda c: (not c.flags, c.position, c.name))]
    if adds:
        out += ["", "## Adds", ""] + HEAD + [_cell(c) for c in adds]
    if drops:
        out += ["", "## Drops", ""] + HEAD + [_cell(c) for c in drops]
    out += ["", f"## Week {week + 1} after these moves", ""]
    out += [f"- {p}" for p in plan] or [("- Every slot can be filled by a player not on bye "
                                         "and not tagged to miss the game.")]
    if board:
        out += ["", "## Available pool — best platform scores this week", ""]
        for pos, rows in board.items():
            if rows:
                out.append(f"**{pos}:** " + "; ".join(
                    f"{n} ({t}) {p:.1f}" + (f", {o:.0f} opp" if o else "")
                    for p, n, t, o, _line, _sid in rows))
    out += ["", ("Facts, not verdicts: the move is the owner's, made in Sleeper; nothing "
                 "here is submitted.")]
    return "\n".join(out) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--record", type=Path, required=True)
    ap.add_argument("--add", action="append", default=[], help="sleeper id or 'Name,POS'")
    ap.add_argument("--drop", action="append", default=[], help="sleeper id or 'Name,POS'")
    ap.add_argument("--week", type=int, default=None,
                    help="the week whose games were just played (default: the record's)")
    ap.add_argument("--leaders", type=int, default=0, help="top N pool scores per position")
    ap.add_argument("--live-json", type=Path, default=None,
                    help="a saved Sleeper week-stats response instead of fetching it")
    ap.add_argument("--out", type=Path, default=None, help=f"also write it under {REVIEW_DIR}")
    args = ap.parse_args(argv)

    if not args.record.exists():
        print(f"[moves] no record at {args.record} — run scripts/weekly/fetch_record.py",
              file=sys.stderr)
        return 2
    record = json.loads(args.record.read_text(encoding="utf-8"))
    week = int(args.week or record.get("week") or 0)
    season = int(record.get("season") or 0)
    ix = M.Index.from_record(record)

    if args.live_json:
        rows = json.loads(args.live_json.read_text(encoding="utf-8"))
        live_note = f"week-{week} stats from {args.live_json}"
    else:
        from gridiron.sleeper import http_fetch
        rows = http_fetch(STATS_URL.format(season=season, week=week)) or []
        live_note = f"week-{week} platform stats and tags fetched from Sleeper just now"
    live = M.live_rows(rows)

    import nflreadpy as nfl  # public, read-only
    schedule = nfl.load_schedules([season]).to_pandas()
    stats = nfl.load_player_stats([season]).to_pandas()
    byes = M.bye_weeks(schedule, range(week + 1, week + 3))
    qbs = M.team_qbs(stats, through_week=week)
    kw = {"live": live, "byes": byes, "qbs": qbs}

    add_ids, p1 = resolve_items(ix, args.add)
    drop_ids, p2 = resolve_items(ix, args.drop)
    roster = M.roster_cards(ix, **kw)
    adds = [M.add_check(M.card(s, ix, **kw)) for s in add_ids]
    drops = [M.drop_check(M.card(s, ix, **kw)) for s in drop_ids]
    plan = M.plan_check(roster, adds, drops, slots=record.get("slots") or [], week=week + 1)
    board = M.leaders(ix, live, top=args.leaders) if args.leaders else None
    text = render(record, roster=roster, adds=adds, drops=drops, plan=plan, board=board,
                  week=week, problems=p1 + p2, live_note=live_note)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
