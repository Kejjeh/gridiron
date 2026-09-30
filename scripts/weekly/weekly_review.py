"""weekly_review.py — the owner's weekly read of one decision record. OFFLINE.

    PYTHONPATH=src python scripts/weekly/weekly_review.py \
        [--record data/outputs/dashboard/dashboard_latest.json] \
        [--ranks trade_chart.csv --ranks ros_tiers.csv] \
        [--watch <sleeper id> --watch "First Last,RB"] \
        [--out data/outputs/review/week04.md]

It answers, from one record and nothing else, the questions the owner asks
every week:

  1. What does the page actually let me do, and is it current?
  2. Does the projection agree with what my players have ACTUALLY done?
     (volume trend per player, `gridiron.trends`; a start/sit the
     projection prefers against the usage is flagged, never overridden)
  3. Which free agents are worth a look — the page's own verdicts, the
     available players whose role is growing, and anyone on my watch list?
  4. Where do outside rankings disagree with my roster?
     (`gridiron.external_ranks`: names resolved to ids once, strictly)
  5. What exactly do I check in Sleeper before acting?

It never recommends past the page's gates: a WITHHELD action stays
withheld here, and nothing is ever submitted anywhere. The output names the
owner's players, so it goes to stdout or under data/outputs/review/, which
is gitignored.

Get a record: `scripts/weekly/fetch_record.py` (the latest cloud build) or
`scripts/weekly/dashboard.py --write` (a local build).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from gridiron.external_ranks import (AMBIGUOUS, NOT_HELD, TEAM_DIFFERS, RankRow,
                                     load_ranks, position_key, ranked_above,
                                     resolve, universe_from_record)
from gridiron.ids import normalize_id
from gridiron.paths import OUTPUTS
from gridiron.trends import FALLING, MIXED, RISING, STEADY, TOO_FEW

DEFAULT_RECORD = OUTPUTS / "dashboard" / "dashboard_latest.json"
REVIEW_DIR = OUTPUTS / "review"

_TREND_RANK = {RISING: 2, STEADY: 1, MIXED: 1, FALLING: 0}
#: How many available players each free-agent list shows.
TOP_N = 8


def _fmt(v: object, nd: int = 1) -> str:
    if v is None:
        return "—"
    try:
        return f"{float(v):.{nd}f}"
    except (TypeError, ValueError):
        return str(v)


class Review:
    """Indexes over one record, so every section reads players by id."""

    def __init__(self, record: Mapping[str, object]):
        self.r = record
        self.roster = {normalize_id(p["sleeper_id"]): p for p in record.get("roster") or []}
        radar = record.get("radar") or {}
        self.pool = {normalize_id(p["id"]): p for p in radar.get("pool") or []}
        self.cand = {normalize_id(c["id"]): c for c in radar.get("candidates") or []}
        usage = record.get("usage") or {}
        self.usage = usage.get("players") or {}
        self.usage_basis = usage.get("basis") or ""
        self.through = usage.get("through_week")

    def name(self, sid: str) -> str:
        p = self.roster.get(sid) or self.pool.get(sid) or {}
        return str(p.get("name") or f"sleeper:{sid}")

    def pos(self, sid: str) -> str:
        p = self.roster.get(sid) or self.pool.get(sid) or {}
        return str(p.get("position") or "")

    def line(self, sid: str) -> Mapping[str, object]:
        return self.usage.get(sid) or {}

    def trend(self, sid: str) -> str:
        if not self.line(sid) and self.pos(sid) in ("K", "DEF"):
            return "n/a"
        return str(self.line(sid).get("trend") or "NO LINE")

    def ppg(self, sid: str) -> float | None:
        v = self.line(sid).get("ppg")
        return None if v is None else float(v)

    def usage_cell(self, sid: str) -> str:
        line = self.line(sid)
        if not line and self.pos(sid) in ("K", "DEF"):
            return "usage is not tracked for K/DEF"
        if not line:
            return "no stat line this season"
        weeks = line.get("weeks") or []
        pts = "/".join(_fmt(w.get("points")) for w in weeks)
        snaps = "/".join(_fmt(w.get("snap_pct"), 0) for w in weeks)
        opps = "/".join(_fmt(w.get("opportunities"), 0) for w in weeks)
        return (f"{_fmt(line.get('ppg'))} ppg over {line.get('games')} g; "
                f"pts {pts}; snap% {snaps}; opps {opps}")


# --------------------------------------------------------------- sections

def header(rv: Review) -> list[str]:
    r = rv.r
    gate = r.get("gate") or {}
    allowed = [k for k, v in gate.items() if isinstance(v, Mapping) and v.get("allowed")]
    held = [k for k, v in gate.items() if isinstance(v, Mapping) and not v.get("allowed")]
    out = [f"# Weekly review — {r.get('season')} week {r.get('week')} ({r.get('phase')})",
           "",
           f"- Record built **{r.get('generated')}**; usage through week {rv.through}.",
           f"- Page state: {'DEGRADED' if r.get('degraded') else 'all inputs current'}; "
           f"gates open: {', '.join(allowed) or 'none'}; "
           f"withheld: {', '.join(held) or 'none'}.",
           f"- {r.get('actionable', 0)} actionable, {r.get('conditional', 0)} conditional "
           f"action(s). Nothing below is advice the page withheld.", ""]
    stale = [s for s in r.get("sources") or [] if " FRESH " not in f" {s} "]
    if stale:
        out += ["**Inputs not current:**", ""] + [f"- `{s[:160]}`" for s in stale] + [""]
    return out


def actions(rv: Review) -> list[str]:
    acts = rv.r.get("actions") or []
    if not acts:
        return ["## Action Desk", "", "No actions on this record.", ""]
    out = ["## Action Desk (as the page states it)", "",
           "| status | action | deadline |", "|---|---|---|"]
    for a in acts:
        out.append(f"| {a.get('status')} | {a.get('headline') or a.get('title')} "
                   f"| {a.get('deadline') or a.get('deadline_note') or '—'} |")
    return out + [""]


def lineup_checks(rv: Review) -> list[str]:
    """Start/sit choices where the projection and the usage point opposite
    ways. Informational: the gate and the projection are unchanged."""
    out = ["## Projection vs actual usage — start/sit", ""]
    pairs: list[tuple[str, str, str, float | None]] = []
    cur, best = rv.r.get("current_lineup") or [], rv.r.get("best_lineup") or []
    for slot, c, b in zip(rv.r.get("slots") or [], cur, best):
        if c and b and c != b:
            pairs.append((slot, normalize_id(b), normalize_id(c), None))
    for a in rv.r.get("alternatives") or []:
        d = a.get("delta_points")
        if a.get("starter_id") and d is not None:
            prefer, other = ((a["bench_id"], a["starter_id"]) if d > 0
                             else (a["starter_id"], a["bench_id"]))
            pairs.append((a.get("slot", ""), normalize_id(prefer), normalize_id(other),
                          abs(float(d))))
    seen, flagged = set(), []
    for slot, prefer, other, d in pairs:
        key = (slot, prefer, other)
        if key in seen:
            continue
        seen.add(key)
        tp, to = rv.trend(prefer), rv.trend(other)
        if tp not in _TREND_RANK or to not in _TREND_RANK:
            continue
        gap = _TREND_RANK[to] - _TREND_RANK[tp]
        pp, po = rv.ppg(prefer), rv.ppg(other)
        if gap >= 2 or (gap == 1 and pp is not None and po is not None and po >= pp):
            flagged.append((slot, prefer, other, d))
    if not flagged:
        return out + ["No start/sit where the projection and the volume trend "
                      "disagree.", ""]
    out += ["| slot | projection prefers | usage | over | usage |", "|---|---|---|---|---|"]
    for slot, prefer, other, d in flagged:
        out.append(f"| {slot} | {rv.name(prefer)}{f' (+{d:.1f})' if d else ''} | "
                   f"{rv.trend(prefer)}, {rv.usage_cell(prefer)} | {rv.name(other)} | "
                   f"{rv.trend(other)}, {rv.usage_cell(other)} |")
    return out + ["", "The projection is not overridden: it models this week's "
                  "matchup; the trend says which way each role is moving. Look "
                  "at both before choosing.", ""]


def roster_usage(rv: Review) -> list[str]:
    order = {"START": 0, "BENCH": 1, "IR": 2}
    rows = sorted(rv.roster.items(), key=lambda kv: (order.get(kv[1].get("lineup"), 3),
                                                     -(rv.ppg(kv[0]) or -1)))
    out = ["## My roster — actual usage", "", f"_{rv.usage_basis}_", "",
           "| player | pos | lineup | this week proj | trend | season so far |",
           "|---|---|---|---|---|---|"]
    for sid, p in rows:
        if p.get("position") in ("K", "DEF"):
            continue
        why = rv.line(sid).get("trend_why") or ""
        out.append(f"| {p.get('name')} | {p.get('position')} | {p.get('lineup')} | "
                   f"{_fmt(p.get('projected'))} | {rv.trend(sid)}"
                   f"{f' ({why})' if why and rv.trend(sid) != TOO_FEW else ''} | "
                   f"{rv.usage_cell(sid)} |")
    return out + [""]


def free_agents(rv: Review, watch: Sequence[str]) -> list[str]:
    out = ["## Free agents", ""]
    page = [c for c in rv.cand.values() if c.get("verdict") in ("LINEUP", "RESEARCH")]
    page.sort(key=lambda c: (c.get("verdict") != "LINEUP",
                             -(c.get("lineup_gain") or c.get("gap") or 0)))
    out += ["**The page's own verdicts** (this week's lineup only):", ""]
    if page:
        out += ["| player | verdict | this week | actual usage |", "|---|---|---|---|"]
        for c in page[:TOP_N]:
            sid = normalize_id(c["id"])
            gain = (f"+{_fmt(c.get('lineup_gain'), 2)} via {c.get('slot')}"
                    if c.get("verdict") == "LINEUP" else f"bench +{_fmt(c.get('gap'), 2)}")
            out.append(f"| {c.get('name')} ({c.get('position')}, {c.get('team')}) | "
                       f"{c.get('verdict')} | {gain} | {rv.trend(sid)}; "
                       f"{rv.usage_cell(sid)} |")
    else:
        out.append("None: no available player improves this week's lineup.")
    risers = [sid for sid in rv.pool if rv.trend(sid) == RISING]
    risers.sort(key=lambda s: -(rv.ppg(s) or 0))
    out += ["", "**Available players whose role is growing** (volume RISING):", ""]
    if risers:
        out += ["| player | actual usage |", "|---|---|"]
        for sid in risers[:TOP_N]:
            out.append(f"| {rv.name(sid)} ({rv.pos(sid)}) | {rv.usage_cell(sid)} |")
    else:
        out.append("None this week.")
    if watch:
        out += ["", "**Watch list:**", "", "| player | held | page verdict | trend | actual usage |",
                "|---|---|---|---|---|"]
        for sid in watch:
            held = "mine" if sid in rv.roster else "available" if sid in rv.pool else "not held"
            verdict = (rv.cand.get(sid) or {}).get("verdict") or "—"
            out.append(f"| {rv.name(sid)} ({rv.pos(sid)}) | {held} | {verdict} | "
                       f"{rv.trend(sid)} | {rv.usage_cell(sid)} |")
    return out + [""]


def rankings(rv: Review, ranks: Sequence[Sequence[RankRow]]) -> list[str]:
    out: list[str] = []
    universe = universe_from_record(rv.r)
    for rows in ranks:
        if not rows:
            continue
        res = resolve(rows, universe)
        by_sid = {r.sleeper_id: r for r in res if r.matched}
        above = ranked_above(res)
        src = rows[0].source
        out += [f"## Outside ranking: {src}", "",
                "| my player | their rank | value | available players ranked above |",
                "|---|---|---|---|"]
        for sid in sorted(above, key=lambda s: by_sid[s].row.rank):
            r = by_sid[sid]
            ups = ", ".join(f"{a.row.name} (#{a.row.rank})" for a in above[sid]) or "none"
            out.append(f"| {rv.name(sid)} | #{r.row.rank} | {_fmt(r.row.value)} | {ups} |")
        unranked = [sid for sid, p in rv.roster.items()
                    if sid not in above and p.get("position") not in ("K", "DEF")]
        if unranked:
            out.append("")
            out.append("Not on this list: " + ", ".join(rv.name(s) for s in unranked) + ".")
        avail = [r for r in res if r.matched and r.held_by == "available"]
        out += ["", "Available players on this list: "
                + (", ".join(f"{r.row.name} #{r.row.rank} ({rv.trend(r.sleeper_id)})"
                             for r in avail) or "none") + "."]
        odd = [r for r in res if r.status in (AMBIGUOUS, TEAM_DIFFERS)]
        if odd:
            out += ["", "Check by hand:"] + [f"- {r.row.name} ({r.row.position}, "
                                              f"#{r.row.rank}): {r.status} — {r.note}"
                                              for r in odd]
        n_other = sum(1 for r in res if r.status == NOT_HELD)
        out += ["", f"{n_other} listed player(s) are neither mine nor in the "
                f"available pool (another manager's, or a spelling this list does "
                f"not share).", ""]
    return out


def sleeper_checks(rv: Review, watch: Sequence[str]) -> list[str]:
    out = ["## Before acting — look in Sleeper (never submit from here)", ""]
    for a in rv.r.get("actions") or []:
        if a.get("status") == "WITHHELD":
            continue
        names = " / ".join(rv.name(normalize_id(i)) for i in a.get("player_ids") or [])
        checks = list(a.get("verify") or []) or ["read each player's status tag"]
        out.append(f"- **{a.get('headline')}** ({names}): " + "; ".join(checks)
                   + (f" — by {a['deadline']}" if a.get("deadline") else ""))
    for sid in watch:
        if sid in rv.pool:
            out.append(f"- {rv.name(sid)}: status tag, and FREE AGENT or the waiver "
                       f"clear time")
    if len(out) == 2:
        out.append("- Nothing actionable; open each player you are considering and "
                   "read the status tag.")
    return out + [""]


def build_review(record: Mapping[str, object], *,
                 ranks: Sequence[Sequence[RankRow]] = (),
                 watch: Sequence[str] = ()) -> str:
    rv = Review(record)
    parts = (header(rv) + actions(rv) + lineup_checks(rv) + roster_usage(rv)
             + free_agents(rv, watch) + rankings(rv, ranks) + sleeper_checks(rv, watch))
    if not record.get("usage"):
        parts.insert(0, "> This record carries no usage block (built before "
                        "usage was recorded); trends read NO LINE.\n")
    return "\n".join(parts).rstrip() + "\n"


def resolve_watch(record: Mapping[str, object], items: Sequence[str]
                  ) -> tuple[list[str], list[str]]:
    """Sleeper ids for `--watch` items: an id is taken as is; "Name,POS"
    goes through the same strict resolver as a ranking row."""
    ids, problems = [], []
    universe = universe_from_record(record)
    for raw in items:
        raw = raw.strip()
        if raw.isdigit() or raw.isalpha() and raw.isupper():
            ids.append(normalize_id(raw))
            continue
        name, _, pos = raw.rpartition(",")
        if not name:
            problems.append(f"{raw!r}: give an id or 'Name,POS'")
            continue
        [res] = resolve([RankRow("watch", 0, name.strip(), position_key(pos), "")],
                        universe)
        if res.matched:
            ids.append(res.sleeper_id)
        else:
            problems.append(f"{raw!r}: {res.status} — {res.note}")
    return ids, problems


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--record", type=Path, default=DEFAULT_RECORD)
    ap.add_argument("--ranks", type=Path, action="append", default=[],
                    help="ranking CSV: rank,name,position,team[,value]")
    ap.add_argument("--watch", action="append", default=[],
                    help="sleeper id or 'Name,POS'")
    ap.add_argument("--out", type=Path, default=None,
                    help=f"write the review here too (keep it under {REVIEW_DIR})")
    args = ap.parse_args(argv)

    if not args.record.exists():
        print(f"[review] no record at {args.record} — run scripts/weekly/"
              f"fetch_record.py or dashboard.py --write first", file=sys.stderr)
        return 2
    record = json.loads(args.record.read_text(encoding="utf-8"))
    watch, problems = resolve_watch(record, args.watch)
    for p in problems:
        print(f"[review] watch: {p}", file=sys.stderr)
    text = build_review(record, ranks=[load_ranks(p) for p in args.ranks], watch=watch)
    print(text)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"[review] wrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
