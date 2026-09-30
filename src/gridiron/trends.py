"""What a player has actually DONE, week by week, and which way his role moves.

The dashboard's projection answers "what should he score this week". The
owner's other question — "ignore the projection: what is his real usage so
far?" — was being answered by hand, off a separate nflverse download, every
week. This module answers it from the same scored, snap-joined player-week
frame the projection was built from (`gridiron.usage.player_weeks`), so the
usage on the page and the evidence behind the projection are one generation.

Three rules shape it:

  * Rule #6. The trend label reads VOLUME only — snap share and
    opportunities (targets + carries; pass attempts + carries for a QB).
    Points, yards, catch rate and touchdowns are shown but never move the
    label: a two-game efficiency swing is noise, a two-game role swing is
    signal.
  * Rule #3. Every line is keyed by the sleeper id of a player the page
    already resolved through the crosswalk, and carries that player's gsis
    id; the frame is read by gsis id, never by name.
  * No invention. A week with no stat line is recorded as absent (bye,
    inactive or no line — this module cannot tell which), never as zero, and
    a player with fewer than `MIN_GAMES` lines gets no trend at all.

`actuals_from_usage` turns a later record's block into the actual-points
table `gridiron.decisions.grade_archive` needs, so grading a finished week
needs nothing but two published records.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping

import pandas as pd

from gridiron.ids import normalize_id

#: How many most recent weeks each line carries.
WINDOW = 4
#: Games needed before a direction is claimed at all.
MIN_GAMES = 3
#: The "recent" side of the comparison: the last this-many games with a line.
RECENT = 2

RISING, FALLING, STEADY, MIXED, TOO_FEW = (
    "RISING", "FALLING", "STEADY", "MIXED", "TOO FEW GAMES")

#: Thresholds, stated once. Opportunity ratio is recent mean / earlier mean;
#: snap delta is in percentage points of offensive snaps.
OPP_UP, OPP_DOWN = 1.25, 0.75
SNAP_UP_PP, SNAP_DOWN_PP = 15.0, -15.0

BASIS = ("volume only (rule #6): snap share and opportunities — targets + "
         "carries, or pass attempts + carries for a QB — over the last "
         f"{RECENT} games with a stat line against the games before them in a "
         f"{WINDOW}-week window; points and efficiency are shown, never used")


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _round(v: object, nd: int = 1) -> float | None:
    f = _num(v)
    return None if f is None else round(f, nd)


def _opportunities(row: Mapping[str, object]) -> float:
    carries = _num(row.get("carries")) or 0.0
    if str(row.get("position") or "").upper() == "QB":
        return (_num(row.get("attempts")) or 0.0) + carries
    return (_num(row.get("targets")) or 0.0) + carries


def week_line(row: Mapping[str, object]) -> dict:
    """One player-week as the record carries it. Snap share is a percentage
    (0-100) or None when no snap row could be anchored."""
    snap = _num(row.get("offense_pct"))
    return {
        "week": int(row["week"]),
        "points": _round(row.get("league_points")),
        "snap_pct": None if snap is None else round(snap * 100.0, 1),
        "opportunities": round(_opportunities(row), 1),
        "targets": _round(row.get("targets"), 0),
        "target_share": _round(row.get("target_share"), 3),
        "carries": _round(row.get("carries"), 0),
        "receptions": _round(row.get("receptions"), 0),
        "yards": _round((_num(row.get("receiving_yards")) or 0.0)
                        + (_num(row.get("rushing_yards")) or 0.0)
                        + (_num(row.get("passing_yards")) or 0.0), 0),
        "touchdowns": _round((_num(row.get("receiving_tds")) or 0.0)
                             + (_num(row.get("rushing_tds")) or 0.0)
                             + (_num(row.get("passing_tds")) or 0.0), 0),
    }


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None


def trend(weeks: list[Mapping[str, object]]) -> tuple[str, str]:
    """(label, why) from volume only. `weeks` are week lines, any order."""
    lines = sorted(weeks, key=lambda w: int(w["week"]))
    if len(lines) < MIN_GAMES:
        return TOO_FEW, (f"{len(lines)} game(s) with a stat line; a direction "
                         f"needs {MIN_GAMES}")
    recent, earlier = lines[-RECENT:], lines[:-RECENT]
    o_new = _mean([_num(w.get("opportunities")) for w in recent])
    o_old = _mean([_num(w.get("opportunities")) for w in earlier])
    s_new = _mean([_num(w.get("snap_pct")) for w in recent])
    s_old = _mean([_num(w.get("snap_pct")) for w in earlier])
    ratio = None if o_new is None or not o_old else o_new / o_old
    delta = None if s_new is None or s_old is None else s_new - s_old

    up = (ratio is not None and ratio >= OPP_UP) or \
        (delta is not None and delta >= SNAP_UP_PP)
    down = (ratio is not None and ratio <= OPP_DOWN) or \
        (delta is not None and delta <= SNAP_DOWN_PP)
    bits = []
    if o_old is not None and o_new is not None:
        bits.append(f"opportunities {o_old:.1f} -> {o_new:.1f}/game")
    if delta is not None:
        bits.append(f"snaps {s_old:.0f}% -> {s_new:.0f}%")
    why = ", ".join(bits) or "no volume numbers to compare"
    if up and down:
        return MIXED, why
    if up:
        return RISING, why
    if down:
        return FALLING, why
    return STEADY, why


def usage_line(frame: pd.DataFrame, gsis_id: str, *, through_week: int | None,
               window: int = WINDOW) -> dict | None:
    """The record's line for one gsis id, or None when the frame has none.

    Chronological by construction: weeks after `through_week` are never read,
    so a record can never carry a week its projection did not have."""
    gid = normalize_id(gsis_id)
    if not gid or frame is None or len(frame) == 0 or "gsis_id" not in frame:
        return None
    rows = frame.loc[frame["gsis_id"] == gid]
    if through_week is not None:
        rows = rows.loc[rows["week"] <= int(through_week)]
    if len(rows) == 0:
        return None
    rows = rows.sort_values("week").drop_duplicates(subset=["week"], keep="last")
    season_pts = [_num(v) for v in rows["league_points"]]
    games = len(rows)
    total = sum(p for p in season_pts if p is not None)
    last = int(rows["week"].max()) if through_week is None else int(through_week)
    lines = [week_line(r) for _, r in rows.iterrows()
             if int(r["week"]) > last - int(window)]
    label, why = trend(lines)
    return {"gsis_id": gid, "games": games, "points": round(total, 1),
            "ppg": round(total / games, 1) if games else None,
            "weeks": lines, "trend": label, "trend_why": why}


def usage_block(frame: pd.DataFrame | None,
                players: Iterable[tuple[str, str]], *,
                through_week: int | None, window: int = WINDOW) -> dict:
    """The record's `usage` block for (sleeper_id, gsis_id) pairs that the
    page already resolved through the crosswalk. Players with no stat line in
    the frame are simply absent: absence says "no line", not "zero"."""
    out: dict[str, dict] = {}
    if frame is not None and len(frame):
        for sid, gid in players:
            sid = normalize_id(sid)
            if not sid or sid in out:
                continue
            line = usage_line(frame, gid, through_week=through_week, window=window)
            if line is not None:
                out[sid] = line
    return {"through_week": through_week, "window": window, "basis": BASIS,
            "players": out}


def actuals_from_usage(block: Mapping[str, object] | None,
                       week: int) -> dict[str, float]:
    """Actual league points for ONE week, by gsis id, from a record's usage
    block — the table `gridiron.decisions.grade_archive` grades against. A
    player with no line that week is absent (ungradeable), never zero."""
    out: dict[str, float] = {}
    players = (block or {}).get("players") or {}
    if not isinstance(players, Mapping):
        return out
    for line in players.values():
        if not isinstance(line, Mapping):
            continue
        gid = normalize_id(line.get("gsis_id"))
        for w in line.get("weeks") or []:
            if isinstance(w, Mapping) and int(w.get("week") or -1) == int(week):
                pts = _num(w.get("points"))
                if gid and pts is not None:
                    out[gid] = pts
    return out
