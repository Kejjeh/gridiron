"""Shadow projections: an outside system recorded beside ours, never used by it.

The 2025 backtest (docs/research/PROJECTION_BACKTEST_2025.md) found Sleeper's
own weekly projections ordered start/sit pairs better than this repo's
baseline (65.2% vs 62.4%), but Sleeper's historical records were modified
after the games, so the edge may be partly hindsight. Rule #5 says a change
has to win out of sample; the only clean sample is a projection captured
BEFORE kickoff. This module captures it:

  * `fetch_sleeper` GETs Sleeper's public projections for one week (one
    request per position, read-only, no league data, never the player map)
    and scores each projected stat line with the league's own rules
    (`gridiron.scoring.fantasy_points`, rule #2);
  * the pull step writes it beside the cache as `shadow_projections.json`
    — NOT a manifest source: it gates nothing and can never degrade the page;
  * the dashboard copies the numbers for rostered and available players into
    the decision record, each flagged with whether it was captured before
    that player's kickoff;
  * `scripts/weekly/grade_week.py` scores ours, the shadow and their average
    on the same players against actual points, every week.

Nothing reads the shadow to project, rank, gate or recommend. Ids: Sleeper's
projections are keyed by Sleeper id, and the record joins them to players
the page already resolved through the crosswalk (rule #3).
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron.ids import normalize_id
from gridiron.scoring import fantasy_points
from gridiron.sleeper import Fetch, http_fetch

SHADOW_FILE = "shadow_projections.json"
SOURCE = "sleeper"
URL = ("https://api.sleeper.app/projections/nfl/{season}/{week}"
       "?season_type=regular&position%5B%5D={pos}")
POSITIONS = ("QB", "RB", "WR", "TE")
#: A shadow younger than this is reused by the next pull instead of fetched.
REFRESH_HOURS = 2.0

#: Sleeper projected-stat key -> the nflverse column `fantasy_points` reads.
STAT_MAP = {
    "pass_yd": "passing_yards", "pass_td": "passing_tds",
    "pass_int": "passing_interceptions", "rush_yd": "rushing_yards",
    "rush_td": "rushing_tds", "rec": "receptions", "rec_yd": "receiving_yards",
    "rec_td": "receiving_tds", "fum_lost": "rushing_fumbles_lost",
    "pass_2pt": "passing_2pt_conversions", "rush_2pt": "rushing_2pt_conversions",
    "rec_2pt": "receiving_2pt_conversions",
}


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def score_projection(stats: Mapping[str, object]) -> float | None:
    """League points for one projected stat line, or None if it has none."""
    line = {STAT_MAP[k]: _num(v) for k, v in stats.items() if k in STAT_MAP}
    line = {k: v for k, v in line.items() if v is not None}
    return round(fantasy_points(line), 2) if line else None


def fetch_sleeper(season: int, week: int, *, fetch: Fetch = http_fetch,
                  now: datetime | None = None) -> dict:
    """One week of Sleeper projections for QB/RB/WR/TE, scored. Raises when
    no position returned anything, so a failed fetch is never a blank file."""
    players: dict[str, dict] = {}
    for pos in POSITIONS:
        for row in fetch(URL.format(season=season, week=week, pos=pos)) or []:
            if not isinstance(row, Mapping):
                continue
            sid = normalize_id(row.get("player_id"))
            stats = row.get("stats") if isinstance(row.get("stats"), Mapping) else {}
            pts = score_projection(stats)
            if not sid or pts is None:
                continue
            players[sid] = {"points": pts, "position": pos,
                            "team": str(row.get("team") or ""),
                            "opponent": str(row.get("opponent") or "")}
    if not players:
        raise RuntimeError(f"Sleeper returned no projections for {season} week {week}")
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return {"source": SOURCE, "season": int(season), "week": int(week),
            "fetched_at": stamp.isoformat(timespec="seconds"), "players": players}


def write_shadow(directory: Path, blob: Mapping[str, object]) -> Path:
    path = Path(directory) / SHADOW_FILE
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(blob), encoding="utf-8")
    tmp.replace(path)
    return path


def read_shadow(directory: Path, *, season: int, week: int) -> dict | None:
    """The shadow for exactly this season and week, validated, or None. A
    file for another week, or one that does not parse, is simply absent."""
    path = Path(directory) / SHADOW_FILE
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(str(blob["fetched_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if blob.get("source") != SOURCE or int(blob.get("season") or 0) != int(season) \
            or int(blob.get("week") or 0) != int(week) or fetched.tzinfo is None:
        return None
    players = blob.get("players")
    if not isinstance(players, Mapping):
        return None
    clean = {normalize_id(k): {"points": _num(v.get("points"))}
             for k, v in players.items() if isinstance(v, Mapping)}
    blob["players"] = {k: v for k, v in clean.items()
                       if k and v["points"] is not None}
    blob["fetched_at_dt"] = fetched
    return blob


def is_fresh(directory: Path, *, season: int, week: int, now: datetime,
             hours: float = REFRESH_HOURS) -> bool:
    blob = read_shadow(directory, season=season, week=week)
    return blob is not None and now - blob["fetched_at_dt"] < timedelta(hours=hours)


def shadow_block(blob: Mapping[str, object] | None,
                 players: Iterable[tuple[str, datetime | None]]) -> dict | None:
    """The record's `shadow` block for (sleeper_id, kickoff) pairs: the
    outside number for each player it covers, and whether it was captured
    before that player's kickoff — only those are ever graded."""
    if not blob:
        return None
    fetched: datetime = blob["fetched_at_dt"]
    source_players = blob.get("players") or {}
    out: dict[str, dict] = {}
    for sid, kickoff in players:
        sid = normalize_id(sid)
        hit = source_players.get(sid)
        if not sid or hit is None or sid in out:
            continue
        out[sid] = {"points": hit["points"],
                    "pre_kickoff": bool(kickoff is not None and fetched < kickoff)}
    return {"source": blob.get("source"), "week": blob.get("week"),
            "fetched_at": blob.get("fetched_at"), "players": out,
            "note": "shadow only: recorded for grading, never used by any "
                    "projection, gate or action on this page"}
