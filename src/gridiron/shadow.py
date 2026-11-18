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
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
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


def score_kicker_projection(stats: Mapping[str, object]) -> float | None:
    """League points for a projected kicker line (Sleeper keys, league
    KICKING_SCORING; every `fgmiss_*` bucket is a miss)."""
    from gridiron.league_config import KICKING_SCORING as W
    total, seen = 0.0, False
    for k, v in stats.items():
        x = _num(v)
        if x is None:
            continue
        key = "fgmiss" if str(k).startswith("fgmiss") else str(k)
        if key in W:
            total += W[key] * x
            seen = True
    return round(total, 2) if seen else None


def score_defense_projection(stats: Mapping[str, object]) -> float | None:
    """League points for a projected team-defense line: Sleeper projects the
    counting stats and a probability for each points-allowed tier, so the
    weighted sum over the league's DEFENSE_SCORING keys is the expectation."""
    from gridiron.league_config import DEFENSE_SCORING as W
    hits = [(W[k], _num(v)) for k, v in stats.items() if k in W and _num(v) is not None]
    return round(sum(w * x for w, x in hits), 2) if hits else None


_SCORERS = {"K": score_kicker_projection, "DEF": score_defense_projection}


def fetch_sleeper(season: int, week: int, *, fetch: Fetch = http_fetch,
                  now: datetime | None = None) -> dict:
    """One week of Sleeper projections for every position (K and DEF too), scored. Raises when
    no position returned anything, so a failed fetch is never a blank file."""
    players: dict[str, dict] = {}
    for pos in POSITIONS:
        for row in fetch(URL.format(season=season, week=week, pos=pos)) or []:
            if not isinstance(row, Mapping):
                continue
            sid = normalize_id(row.get("player_id"))
            stats = row.get("stats") if isinstance(row.get("stats"), Mapping) else {}
            pts = _SCORERS.get(pos, score_projection)(stats)
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
    clean = {normalize_id(k): {"points": _num(v.get("points")),
                               **({"position": v["position"]}
                                  if v.get("position") in POSITIONS else {})}
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


# --------------------------------------------------------------------------
# Every high-snap RB starter's number, every build (#29 item 2)
# --------------------------------------------------------------------------
# Owner decision 2026-11-18 (Kejjeh/gridiron#29 item 2, option B: "record
# first, decide later"). Sleeper's player map is read once a day, so a
# Saturday Out can reach the board on Sunday; this projections feed is read
# every build. Whether a starter dropping out of it (or to 0) is a timely,
# reliable sign of a ruling is unmeasured. This records the evidence for
# `scripts/research/feed_reaction.py`; nothing fires on it.
FEED_LISTED, FEED_ABSENT, FEED_UNKNOWN = "LISTED", "ABSENT", "UNKNOWN"
FEED_SUBSET = "RB starters at 45%+ of offensive snaps over their last 3 games " \
              "(gridiron.openings.high_snap_starters)"


def starter_feed_block(blob: Mapping[str, object] | None, starters: Iterable[object], *,
                       week: int, now: datetime,
                       kickoff_of, min_per_position: int,
                       starters_known: bool = True) -> dict:
    """The record's `starter_feed` block: for each watched starter
    (`openings.WatchedStarter`), what this build's projections feed says.

    Three answers, never two (sop-fail-loud): LISTED with his number (0 is a
    number), ABSENT from a feed that lists `min_per_position`+ RBs for this
    week, UNKNOWN otherwise (no feed, another week's, or a thin position,
    which is a failed fetch). `kickoff_of(team)` gives his kickoff or None;
    `pre_kickoff` is then None (unknown), never False. `starters_known`
    False (no usage lines) says the subset itself is UNKNOWN, so an empty
    `players` is never read as "no starters"."""
    starters = list(starters)
    players = (blob or {}).get("players") or {}
    n_rb = sum(1 for v in players.values()
               if isinstance(v, Mapping) and v.get("position") == "RB")
    fetched = (blob or {}).get("fetched_at_dt")
    if not blob or not isinstance(fetched, datetime):
        state, reason = FEED_UNKNOWN, "no projections feed for this week"
    elif int(blob.get("week") or 0) != int(week):
        state, reason = FEED_UNKNOWN, (f"the feed is for another week "
                                       f"(wk{blob.get('week')}, not wk{int(week)})")
    elif n_rb < int(min_per_position):
        state, reason = FEED_UNKNOWN, (f"thin feed: {n_rb} RBs listed (< {min_per_position}) "
                                       f"is a failed fetch, never an absence")
    else:
        state, reason = "FULL", f"{n_rb} RBs listed"
    out: dict[str, dict] = {}
    for s in starters:
        sid = normalize_id(s.back.sleeper_id)
        if not sid or sid in out:
            continue
        kick = kickoff_of(s.team)
        hit = players.get(sid) if state == "FULL" else None
        feed = FEED_UNKNOWN if state != "FULL" else (FEED_LISTED if hit is not None
                                                     else FEED_ABSENT)
        out[sid] = {"gsis_id": s.back.gsis_id, "team": s.team, "position": s.position,
                    "snap_pct": s.back.snap_pct,
                    "feed": feed, "points": None if hit is None else hit.get("points"),
                    "kickoff": None if kick is None else kick.isoformat(),
                    # when Sleeper's number was fetched (the pull reuses a
                    # feed under `REFRESH_HOURS` old), not when the board ran
                    "fetched_at": (blob or {}).get("fetched_at")
                    if isinstance(fetched, datetime) else None,
                    "pre_kickoff": (None if kick is None or not isinstance(fetched, datetime)
                                    else bool(fetched < kick))}
    return {"week": int(week), "subset": FEED_SUBSET, "feed": state, "reason": reason,
            "starters_known": bool(starters_known),
            "rb_listed": n_rb,
            "fetched_at": (blob or {}).get("fetched_at") if isinstance(fetched, datetime)
            else None,
            "age_hours": (round((now - fetched).total_seconds() / 3600, 2)
                          if isinstance(fetched, datetime) else None),
            "players": out,
            "note": "record only (#29 item 2): never read by an alert, a projection, a gate "
                    "or a grade"}


#: The carried change log of `starter_feed` (one row per starter-week each
#: time his feed answer changes). The cloud keeps only ~10 h of decision
#: records, which would erase a Saturday drop before Sunday's kickoff; this
#: small file rides the carry store and the run artifact instead.
FEED_LOG_FIELDS = ("generated", "week", "sleeper_id", "gsis_id", "team", "feed", "points",
                   "fetched_at", "kickoff", "pre_kickoff")


def append_feed_log(path: Path, block: Mapping[str, object] | None,
                    generated: datetime) -> int:
    """Append this build's `starter_feed` answers that CHANGED since the
    log's last row for that starter-week (feed state or number). A build no
    newer than the last row for a starter-week adds nothing for it, so a
    re-run is harmless. Returns the rows written."""
    import csv
    if not block or not isinstance(block.get("players"), Mapping):
        return 0
    path = Path(path)
    last: dict[tuple[str, str], dict] = {}
    if path.exists():
        with path.open(encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                last[(r["sleeper_id"], r["week"])] = r
    stamp = generated.astimezone(timezone.utc).isoformat(timespec="seconds")
    week = str(int(block.get("week") or 0))
    new = []
    for sid, p in sorted(block["players"].items()):
        row = {"generated": stamp, "week": week, "sleeper_id": str(sid),
               "gsis_id": p.get("gsis_id") or "", "team": p.get("team") or "",
               "feed": p.get("feed") or FEED_UNKNOWN,
               "points": "" if p.get("points") is None else f"{float(p['points']):g}",
               "fetched_at": p.get("fetched_at") or "", "kickoff": p.get("kickoff") or "",
               "pre_kickoff": "" if p.get("pre_kickoff") is None else str(p["pre_kickoff"])}
        prev = last.get((row["sleeper_id"], week))
        if prev is not None and (datetime.fromisoformat(prev["generated"]) >= generated
                                 or (prev["feed"], prev["points"]) == (row["feed"], row["points"])):
            continue
        new.append(row)
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists()
    with path.open("a", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FEED_LOG_FIELDS)
        if fresh:
            w.writeheader()
        w.writerows(new)
    return len(new)
