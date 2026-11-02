"""pull_props.py — snapshot player-prop markets before each kickoff. Network.

    PYTHONPATH=src python scripts/ingest/pull_props.py             # collect what is due
    PYTHONPATH=src python scripts/ingest/pull_props.py --dry-run   # say what is due; no call
    PYTHONPATH=src python scripts/ingest/pull_props.py rederive    # rebuild derived/ from raw/
    PYTHONPATH=src python scripts/ingest/pull_props.py summary     # last run, counts only
    PYTHONPATH=src python scripts/ingest/pull_props.py names       # LOCAL: seed the name table

Provider: The Odds API (owner decision 4, 2026-11-02: a sanctioned free
tier, never a sportsbook scrape). docs/research/PROPS_COLLECTOR.md has the
design, the credit math and what the owner sets up.

* Due rule. A game is due when 0 < kickoff - now <= 150 minutes and it has
  no snapshot yet; one snapshot per game, its lead time recorded. Kickoffs
  come from the nflverse schedule (`gridiron.lineup.kickoff_index`, the
  board's own reader), never from the provider, and an untimed game is
  never due.
* Calls. GET /events (free: "This endpoint does not count against the usage
  quota", the v4 guide, read 2026-11-02) at most once per run, and only in
  a run with a game due. Then per due game GET /events/{id}/odds on region
  us: the six core markets (at most 6 credits: cost = unique markets
  RETURNED x regions; an empty response costs nothing), plus the five
  secondary markets only with slack.
* Credit ledger. Every response's x-requests-remaining / -used / -last is
  recorded. A call that would leave fewer credits than the rest of the
  month's core snapshots need (6 per game until the quota resets) is
  refused. The reset day is UNVERIFIED (the provider does not publish it):
  the 1st of the month UTC until a reset is observed in the headers. With
  no reading of the current quota period (a lost ledger, a new month, an
  event list without the headers) one core call per run reads them, at
  most 6 credits unguarded; refusing instead would never learn the count.
* The key (rule #9) comes from `gridiron.config` (GRIDIRON_ODDS_API_KEY)
  and is never printed: no URL is ever printed, an exception's text never
  reaches the log, and every message passes `scrub` (the apiKey parameter
  and the key's value removed) before it is shown.
* Storage. Raw responses and the per-book table go to
  data/research/cache/props/raw/ (gitignored; in the cloud carried only
  ENCRYPTED, .github/workflows/props-collect.yml). What `gridiron.props`
  derives (expected stat lines by gsis id, counts) goes to
  data/research/cache/props/derived/season<YYYY>/week<NN>.json.
* Logs print counts and credits only: no player, no name, no line, no price.
* `names` (local only, refused inside GitHub Actions): the provider's player
  names for every game of one week, from one market at 1 credit a game,
  stored under raw/names/ for `scripts/ingest/odds_names.py propose`. Never
  a snapshot; the same ledger and core reserve apply.
"""
from __future__ import annotations

import argparse
import csv
import http.client
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import props as P
from gridiron.league_config import PLAYOFF_START_WEEK, SEASON_YEAR

HOST = "https://api.the-odds-api.com"
SPORT = "americanfootball_nfl"
USER_AGENT = "gridiron/0.1 (python-urllib)"     # a plain script UA, never a browser spoof
#: A game is due this many minutes before its kickoff (and not after it).
DUE_MINUTES = 150
#: The fantasy season's last week: the 6-team playoffs run weeks 15-17.
LAST_WEEK = PLAYOFF_START_WEEK + 2
CORE_COST = len(P.CORE_MARKETS) * len(P.REGIONS)
SECONDARY_COST = len(P.SECONDARY_MARKETS) * len(P.REGIONS)
#: An event-to-game match tolerates this kickoff difference (a flexed game).
MATCH_HOURS = 12
#: Kickoffs further apart than this are counted as a disagreement.
AGREE_MINUTES = 15
PAUSE_SECONDS = 1.0                 # between paid calls (the provider rate-limits bursts)
STOP_CODES = (401, 402, 403)        # a bad key or a spent quota: stop the run
LEDGER_NAME = "credits.json"
LAST_RUN_NAME = "last_run.json"
SCHEDULE_COPY = "schedule.parquet"
_EVENT_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
_KEY_PARAM = re.compile(r"(apiKey=)[^&\s'\"]*", re.IGNORECASE)


# ------------------------------------------------------------------ the key

def scrub(text: object, key: str | None) -> str:
    """`text` with the key's value (raw or URL-encoded) and any apiKey
    parameter removed. Every message this script prints passes through it."""
    s = str(text)
    if key:
        for form in {key, urllib.parse.quote(key, safe=""), urllib.parse.quote_plus(key)}:
            if form:
                s = s.replace(form, "***")
    return _KEY_PARAM.sub(r"\1***", s)


class OddsApiError(RuntimeError):
    """A failed call, described by its status code or error type only (an
    exception from urllib can quote the URL, and the URL holds the key)."""

    def __init__(self, status: int | None, kind: str, headers: Mapping[str, str] | None = None):
        super().__init__(f"HTTP {status}" if status is not None else kind)
        self.status = status
        self.headers = dict(headers or {})


Fetch = Callable[[str], tuple[object, dict[str, str]]]


def _credit_headers(headers) -> dict[str, str]:
    out = {}
    for k, v in (headers.items() if headers is not None else ()):
        if str(k).lower().startswith("x-requests-"):
            out[str(k).lower()] = str(v)
    return out


def http_fetch(url: str, *, timeout: int = 30) -> tuple[object, dict[str, str]]:
    """GET + parse JSON. Raises OddsApiError carrying a status code or an
    error type, raised OUTSIDE the except block so no traceback can chain the
    original exception (whose text may hold the URL)."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/json"})
    failure: OddsApiError | None = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body, _credit_headers(resp.headers)
    except urllib.error.HTTPError as exc:
        failure = OddsApiError(exc.code, "HTTPError", _credit_headers(exc.headers))
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError,
            ValueError) as exc:
        failure = OddsApiError(None, type(exc).__name__)
    raise failure


def events_url(key: str) -> str:
    return f"{HOST}/v4/sports/{SPORT}/events?" + urllib.parse.urlencode({"apiKey": key})


def event_odds_url(key: str, event_id: str, markets: Sequence[str]) -> str:
    if not _EVENT_ID.match(event_id):
        raise OddsApiError(None, "an event id that is not a plain identifier")
    query = urllib.parse.urlencode({"apiKey": key, "regions": ",".join(P.REGIONS),
                                    "markets": ",".join(markets), "oddsFormat": "american"})
    return f"{HOST}/v4/sports/{SPORT}/events/{event_id}/odds?{query}"


# ------------------------------------------------------------------ schedule

@dataclass(frozen=True)
class Game:
    game_id: str
    season: int
    week: int
    home: str
    away: str
    kickoff: datetime        # UTC, from the nflverse schedule


def season_games(schedule, season: int, *, last_week: int = LAST_WEEK) -> tuple[list[Game], int]:
    """Every timed regular-season game of weeks 1..last_week, and how many
    game rows had no usable kickoff (never due: no time is invented)."""
    from gridiron.lineup import kickoff_index
    if schedule is None or len(schedule) == 0 or "week" not in schedule.columns:
        return [], 0
    games, untimed = [], 0
    weeks = sorted({int(w) for w in schedule["week"].dropna().unique() if 1 <= int(w) <= last_week})
    for week in weeks:
        ix = kickoff_index(schedule, week)
        rows = schedule.loc[schedule["week"] == week]
        for row in rows.itertuples():
            if str(getattr(row, "game_type", "REG") or "REG").upper() != "REG":
                continue
            home = str(getattr(row, "home_team", "") or "").strip().upper()
            away = str(getattr(row, "away_team", "") or "").strip().upper()
            kh = ix.kickoffs.get(home) if ix is not None else None
            ka = ix.kickoffs.get(away) if ix is not None else None
            if not home or not away or kh is None or kh != ka:
                untimed += 1
                continue
            gid = str(getattr(row, "game_id", "") or f"{season}_{week:02d}_{away}_{home}")
            games.append(Game(gid, int(season), int(week), home, away, kh))
    games.sort(key=lambda g: (g.kickoff, g.game_id))
    return games, untimed


#: A carried schedule copy is re-fetched from nflverse when older than this
#: (a flexed kickoff is announced days ahead), and not used past the second.
SCHEDULE_REFRESH_HOURS, SCHEDULE_MAX_HOURS = 6, 48


def _copy_age(derived_root: Path, now: datetime) -> timedelta | None:
    try:
        meta = json.loads((Path(derived_root) / (SCHEDULE_COPY + ".json")).read_text("utf-8"))
        at = datetime.fromisoformat(str(meta["fetched_at"]))
    except (OSError, ValueError, KeyError, TypeError):
        return None
    if not (Path(derived_root) / SCHEDULE_COPY).is_file():
        return None
    return now - (at if at.tzinfo else at.replace(tzinfo=timezone.utc))


def load_schedule(season: int, now: datetime, *, cache_root: Path | None = None,
                  derived_root: Path = P.DERIVED_ROOT,
                  fetch_nflverse=None) -> tuple[object, str, bool]:
    """(schedule, where it came from, whether the carried copy was rewritten):
    the season cache when its pull is under 24 h old (a local machine), else
    the carried copy when under 6 h old, else nflverse (one small download,
    kept as the copy), else the copy when under 48 h old."""
    import pandas as pd
    from gridiron import ingest as ing
    try:
        manifest = ing.Manifest.load(ing.season_cache(season, cache_root), season)
        entry = manifest.get("schedules")
        at = entry.as_of_dt if entry is not None else None
        if at is not None and now - at < timedelta(hours=24):
            frame = manifest.read_frame("schedules")
            if frame is not None and len(frame):
                return frame, "season cache", False
    except (OSError, ValueError):
        pass
    copy = Path(derived_root) / SCHEDULE_COPY
    age = _copy_age(derived_root, now)
    if age is not None and timedelta(0) <= age < timedelta(hours=SCHEDULE_REFRESH_HOURS):
        try:
            return pd.read_parquet(copy), "carried copy", False
        except (OSError, ValueError):
            pass
    why = "returned no rows"
    try:
        if fetch_nflverse is None:
            import nflreadpy as nfl
            frame = nfl.load_schedules([season]).to_pandas()
        else:
            frame = fetch_nflverse(season)
        if frame is not None and len(frame):
            copy.parent.mkdir(parents=True, exist_ok=True)
            frame.to_parquet(copy, index=False)
            _atomic_json(Path(str(copy) + ".json"), {"fetched_at": now.isoformat(),
                                                     "source": "nflreadpy.load_schedules"})
            return frame, "nflverse", True
    except Exception as exc:                                     # noqa: BLE001
        why = type(exc).__name__                 # the type only (rule: counts, not text)
    if age is not None and timedelta(0) <= age < timedelta(hours=SCHEDULE_MAX_HOURS):
        try:
            return pd.read_parquet(copy), f"carried copy (nflverse: {why})", False
        except (OSError, ValueError):
            pass
    return None, f"none (nflverse: {why})", False


def due_games(games: Iterable[Game], now: datetime, snapped: set[str]) -> list[Game]:
    window = timedelta(minutes=DUE_MINUTES)
    return [g for g in games
            if timedelta(0) < g.kickoff - now <= window and g.game_id not in snapped]


# ------------------------------------------------------------------ credits

def _int(value: object) -> int | None:
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return None


@dataclass
class Credits:
    """The credit ledger, from the provider's own headers. Counts only."""

    remaining: int | None = None
    used: int | None = None
    last: int | None = None
    at: str = ""
    reset_day: int = 1
    reset_observed: str = ""

    @classmethod
    def load(cls, path: Path) -> "Credits":
        try:
            blob = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        if not isinstance(blob, dict):
            return cls()
        day = _int(blob.get("reset_day")) or 1
        return cls(_int(blob.get("remaining")), _int(blob.get("used")), _int(blob.get("last")),
                   str(blob.get("at") or ""), min(max(day, 1), 28),
                   str(blob.get("reset_observed") or ""))

    def save(self, path: Path) -> None:
        _atomic_json(path, {"remaining": self.remaining, "used": self.used, "last": self.last,
                            "at": self.at, "reset_day": self.reset_day,
                            "reset_observed": self.reset_observed,
                            "note": "from the provider's x-requests-* headers; the reset "
                                    "day is assumed (the 1st) until a reset is observed"})

    def record(self, headers: Mapping[str, str], now: datetime) -> None:
        remaining = _int(headers.get("x-requests-remaining"))
        used = _int(headers.get("x-requests-used"))
        if remaining is None and used is None:
            return
        if used is not None and self.used is not None and used < self.used:
            # The quota reset since the last reading. The true day lies between
            # that reading and now; taking now's day (the latest possible) puts
            # the next reset no earlier than the real one, so the reserve can
            # only be too large, never too small. An owner-set day still wins
            # on the next run (`collect(reset_day=...)`).
            self.reset_observed = now.date().isoformat()
            self.reset_day = min(now.day, 28)
        self.remaining = remaining if remaining is not None else self.remaining
        self.used = used if used is not None else self.used
        self.last = _int(headers.get("x-requests-last"))
        self.at = now.isoformat(timespec="seconds")

    def next_reset(self, now: datetime) -> datetime:
        start = now.replace(day=self.reset_day, hour=0, minute=0, second=0, microsecond=0)
        if start > now:
            return start
        month, year = (now.month % 12) + 1, now.year + (now.month == 12)
        return start.replace(year=year, month=month)

    def last_reset(self, now: datetime) -> datetime:
        """The latest assumed reset at or before `now`."""
        start = now.replace(day=self.reset_day, hour=0, minute=0, second=0, microsecond=0)
        if start <= now:
            return start
        month, year = (12, now.year - 1) if now.month == 1 else (now.month - 1, now.year)
        return start.replace(year=year, month=month)

    def known(self, now: datetime) -> bool:
        """Whether `remaining` is a reading of THIS quota period. A reading
        from before the assumed reset is stale: the quota may have refilled."""
        if self.remaining is None:
            return False
        try:
            at = datetime.fromisoformat(self.at)
        except ValueError:
            return False
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        return at >= self.last_reset(now)


def core_reserve(games: Sequence[Game], after: Game, until: datetime,
                 snapped: set[str]) -> int:
    """Credits the rest of the month's core snapshots need after `after`."""
    later = [g for g in games if (g.kickoff, g.game_id) > (after.kickoff, after.game_id)
             and g.kickoff < until and g.game_id not in snapped]
    return CORE_COST * len(later)


UNKNOWN_CREDITS = "credits unknown (no provider headers this quota period)"


def plan_markets(credits: Credits, reserve: int) -> tuple[tuple[str, ...] | None, str]:
    """The markets this call may buy, or (None, why it is refused). The
    caller decides first whether the reading is of this quota period
    (`Credits.known`); this judges the number it is given."""
    if credits.remaining is None:
        return None, UNKNOWN_CREDITS
    if credits.remaining - CORE_COST < reserve:
        return None, "would leave less than the month's core reserve"
    if credits.remaining - CORE_COST - reserve >= SECONDARY_COST:
        return P.CORE_MARKETS + P.SECONDARY_MARKETS, ""
    return P.CORE_MARKETS, ""


# ------------------------------------------------------------------ storage

def _atomic_json(path: Path, blob: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps(blob, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _stamp(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def raw_dir(raw_root: Path, season: int, week: int) -> Path:
    return Path(raw_root) / f"season{int(season)}" / f"week{int(week):02d}"


def write_raw(raw_root: Path, game: Game, taken_at: datetime, payload: object,
              meta: Mapping[str, object]) -> Path:
    path = raw_dir(raw_root, game.season, game.week) / f"{game.game_id}__{_stamp(taken_at)}.json"
    _atomic_json(path, {"meta": dict(meta), "payload": payload})
    return path


_has_odds = P.has_odds
raw_snapshots = P.read_raw_snapshots


def snapped_games(derived_root: Path, raw_root: Path, season: int,
                  weeks: Iterable[int]) -> set[str]:
    """Games that already have a snapshot: in a derived week file (kept even
    when nothing raw is) or in the raw archive."""
    out = {gid for (_w, gid) in raw_snapshots(raw_root, season)}
    for w in set(weeks):
        blob = P.read_derived(derived_root, season, w)
        if blob:
            out.update(blob["games"])
    return out


BOOK_COLUMNS = ("game_id", "book", "market", "odds_name", "gsis_id", "point", "p_over",
                "overround", "method")


def write_book_table(raw_root: Path, season: int, week: int,
                     snaps: Mapping[tuple[int, str], tuple[Path, dict]],
                     table: P.IdentityTable) -> Path | None:
    """The per-book de-vigged lines of one week (PRIVATE: it lives in raw/)."""
    rows = []
    for (w, gid), (_path, blob) in sorted(snaps.items()):
        if w != week:
            continue
        payload = blob.get("payload")
        teams = tuple(t for t in P.event_teams(payload if isinstance(payload, Mapping) else {})
                      if t)
        lines, _ = P.devig(P.parse_event_odds(payload)[0])
        for b in lines:
            res = table.resolve(b.player, teams)
            rows.append({"game_id": gid, "book": b.book, "market": b.market,
                         "odds_name": b.player, "gsis_id": res.gsis_id or "",
                         "point": "" if b.point is None else b.point,
                         "p_over": round(b.p_over, 6),
                         "overround": "" if b.overround is None else round(b.overround, 6),
                         "method": b.method})
    if not rows:
        return None
    path = raw_dir(raw_root, season, week) / "book_lines.csv"
    tmp = path.with_name(path.name + ".part")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=BOOK_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    os.replace(tmp, path)
    return path


def merge_derived(derived_root: Path, season: int, week: int,
                  entries: Mapping[str, dict]) -> bool:
    """Put game entries into the week's derived file; True when it changed."""
    path = P.derived_path(derived_root, season, week)
    blob = P.read_derived(derived_root, season, week) or P.empty_week(season, week)
    before = json.dumps(blob, sort_keys=True)
    blob["games"].update(entries)
    if json.dumps(blob, sort_keys=True) == before and path.is_file():
        return False
    _atomic_json(path, blob)
    return True


def _meta_time(meta: Mapping[str, object], key: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(meta.get(key)).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def derive_or_none(payload: object, *, table: P.IdentityTable, params: P.PropParams,
                   taken_at: datetime, kickoff: datetime | None,
                   errors: Counter | None = None) -> dict | None:
    """`gridiron.props.derive_game`, or None (its error TYPE counted) when it
    fails: one odd payload in the archive must not stop every later run,
    which re-derives the whole archive."""
    try:
        return P.derive_game(payload, table=table, params=params, taken_at=taken_at,
                             kickoff=kickoff)
    except Exception as exc:                                     # noqa: BLE001
        if errors is not None:
            errors[f"derive: {type(exc).__name__}"] += 1
        return None


def failed_entry(taken_at: datetime, kickoff: datetime | None) -> dict:
    """The derived entry of a snapshot that could not be derived: it still
    marks the game as snapped (so it is not bought again) and projects no one."""
    return {"taken_at": taken_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "kickoff": None if kickoff is None
            else kickoff.astimezone(timezone.utc).isoformat(timespec="seconds"),
            "lead_minutes": None if kickoff is None
            else round((kickoff - taken_at).total_seconds() / 60.0, 1),
            "teams": [], "books": 0, "markets_returned": [], "players": {}, "unmatched": 0,
            "ambiguous": 0, "abstained": {}, "skipped": {}, "params_fitted": "",
            "derive_failed": True}


def rederive(raw_root: Path, derived_root: Path, season: int, table: P.IdentityTable,
             params: P.PropParams, errors: Counter | None = None) -> tuple[int, bool]:
    """Rebuild every week's derived entries from the raw archive with the
    CURRENT name table (a row added later resolves earlier snapshots too).
    Games the archive no longer holds, and games whose re-derivation fails,
    keep their derived entry. Returns (weeks touched, whether any derived
    file changed)."""
    snaps = raw_snapshots(raw_root, season)
    weeks = sorted({w for (w, _g) in snaps})
    changed = False
    for week in weeks:
        entries = {}
        for (w, gid), (_path, blob) in snaps.items():
            if w != week:
                continue
            meta = blob["meta"]
            taken = _meta_time(meta, "taken_at")
            if taken is None:
                continue
            entry = derive_or_none(blob.get("payload"), table=table, params=params,
                                   taken_at=taken, kickoff=_meta_time(meta, "kickoff"),
                                   errors=errors)
            if entry is not None:
                entries[gid] = entry
        changed |= merge_derived(derived_root, season, week, entries)
        try:
            write_book_table(raw_root, season, week, snaps, table)
        except Exception as exc:                                 # noqa: BLE001
            if errors is not None:
                errors[f"book table: {type(exc).__name__}"] += 1
    return len(weeks), changed


# ------------------------------------------------------------------ the run

@dataclass
class RunReport:
    """Counts and credits only: this is what reaches a public log."""

    at: str
    key_present: bool
    schedule: str = ""
    games_timed: int = 0
    games_untimed: int = 0
    due: int = 0
    snapshots: int = 0
    with_secondary: int = 0
    empty: int = 0
    #: core calls made only to read the credit headers (at most one a run)
    first_readings: int = 0
    refused: Counter = None          # type: ignore[assignment]
    errors: Counter = None           # type: ignore[assignment]
    players: int = 0
    unmatched: int = 0
    ambiguous: int = 0
    kickoff_disagreements: int = 0
    unmatched_events: int = 0
    rederived_weeks: int = 0
    raw_changed: bool = False
    derived_changed: bool = False
    #: the credit ledger or the carried schedule copy was rewritten
    ledger_changed: bool = False
    credits: Credits | None = None
    reserve: int | None = None
    #: the week the names mode asked about
    week: int | None = None

    def __post_init__(self):
        self.refused = self.refused or Counter()
        self.errors = self.errors or Counter()

    def record(self) -> dict:
        c = self.credits or Credits()
        return {"at": self.at, "key_present": self.key_present, "schedule": self.schedule,
                "games_timed": self.games_timed, "games_untimed": self.games_untimed,
                "due": self.due, "snapshots": self.snapshots,
                "with_secondary": self.with_secondary, "empty_responses": self.empty,
                "first_readings": self.first_readings,
                "refused": dict(self.refused), "errors": dict(self.errors),
                "players_resolved": self.players, "names_unmatched": self.unmatched,
                "names_ambiguous": self.ambiguous,
                "kickoff_disagreements": self.kickoff_disagreements,
                "due_without_event": self.unmatched_events,
                "rederived_weeks": self.rederived_weeks,
                "raw_changed": self.raw_changed, "derived_changed": self.derived_changed,
                "credits": {"remaining": c.remaining, "used": c.used, "last": c.last,
                            "reset_day": c.reset_day, "reset_observed": c.reset_observed},
                "core_reserve": self.reserve}

    def lines(self) -> list[str]:
        c = self.credits or Credits()
        out = [f"[props] schedule: {self.schedule}; {self.games_timed} timed game(s), "
               f"{self.games_untimed} untimed (never due)",
               f"[props] {self.due} game(s) due; {self.snapshots} snapshot(s) taken "
               f"({self.with_secondary} with secondary markets), {self.empty} empty "
               f"response(s)"]
        if self.first_readings:
            out.append("[props] no credit reading of this quota period: one core call "
                       "read the provider's headers")
        if self.refused:
            out.append("[props] refused: " + ", ".join(f"{k} x{v}" for k, v in
                                                       sorted(self.refused.items())))
        if self.errors:
            out.append("[props] errors: " + ", ".join(f"{k} x{v}" for k, v in
                                                      sorted(self.errors.items())))
        if self.unmatched_events or self.kickoff_disagreements:
            out.append(f"[props] {self.unmatched_events} due game(s) with no provider event; "
                       f"{self.kickoff_disagreements} kickoff disagreement(s) over "
                       f"{AGREE_MINUTES} min")
        out.append(f"[props] players resolved {self.players}; names unmatched "
                   f"{self.unmatched}, ambiguous {self.ambiguous} (counts only; "
                   f"scripts/ingest/odds_names.py lists them locally)")
        out.append(f"[props] credits remaining {c.remaining}, used {c.used}, last call "
                   f"{c.last}; core reserve {self.reserve}")
        return out


def match_events(events: object, due: Sequence[Game]) -> tuple[dict[str, str], int]:
    """game_id -> provider event id, by the unordered team pair and a kickoff
    within MATCH_HOURS; and how many matches disagree on the kickoff by more
    than AGREE_MINUTES (the due rule keeps the nflverse time)."""
    out, disagree = {}, 0
    rows = events if isinstance(events, list) else []
    for g in due:
        best = None
        for e in rows:
            if not isinstance(e, Mapping):
                continue
            home, away = P.event_teams(e)
            if {home, away} != {g.home, g.away}:
                continue
            t = _meta_time(e, "commence_time")
            if t is None or abs(t - g.kickoff) > timedelta(hours=MATCH_HOURS):
                continue
            gap = abs(t - g.kickoff)
            if best is None or gap < best[0]:
                best = (gap, str(e.get("id") or ""))
        if best is not None and _EVENT_ID.match(best[1]):
            out[g.game_id] = best[1]
            disagree += int(best[0] > timedelta(minutes=AGREE_MINUTES))
    return out, disagree


def _wall_clock() -> datetime:
    return datetime.now(timezone.utc)


RESET_DAY_ENV = "GRIDIRON_PROPS_RESET_DAY"


def reset_day_override(raw: str | None) -> tuple[int | None, str]:
    """The quota's reset day the owner read off the provider's account page
    (a repository variable, so untrusted text): a bare day 1-28, or None with
    the reason it was ignored. A reset on the 29th-31st is entered as 28."""
    text = (raw or "").strip()
    if not text:
        return None, ""
    if not re.fullmatch(r"[0-9]{1,2}", text) or not 1 <= int(text) <= 28:
        return None, f"{RESET_DAY_ENV} ignored: not a day 1-28"
    return int(text), ""


def collect(*, key: str, season: int, now: datetime, schedule, schedule_source: str,
            raw_root: Path = P.RAW_ROOT, derived_root: Path = P.DERIVED_ROOT,
            table: P.IdentityTable | None = None, params: P.PropParams | None = None,
            fetch: Fetch | None = None, sleep: Callable[[float], None] = time.sleep,
            clock: Callable[[], datetime] = _wall_clock,
            max_games: int | None = None, dry_run: bool = False,
            reset_day: int | None = None) -> RunReport:
    """One collection run. `now` decides what is due; `clock` stamps each
    snapshot when it is actually taken (a run of nine 1 pm games takes a
    while, and the board trusts a snapshot only if it preceded the kickoff).
    `reset_day` is the owner's reading of the quota's reset day, which wins
    over the assumed 1st and over one inferred from the headers."""
    fetch = fetch or http_fetch
    report = RunReport(now.isoformat(timespec="seconds"), True, schedule_source)
    table_ok = True
    if table is None:
        try:
            table = P.IdentityTable.read()
        except (OSError, ValueError) as exc:
            # Collecting is the time-critical part; names can be fixed later.
            # Every name abstains, and nothing archived is re-derived with an
            # empty table (that would erase the derived values it holds).
            report.errors[f"name table unreadable ({type(exc).__name__}): every name "
                          f"unmatched, nothing re-derived"] += 1
            table, table_ok = P.IdentityTable(), False
    params = params if params is not None else P.PropParams.load()
    ledger_path = Path(derived_root) / LEDGER_NAME
    credits = Credits.load(ledger_path)
    if reset_day is not None:
        credits.reset_day = int(reset_day)
    report.credits = credits
    games, report.games_untimed = season_games(schedule, season)
    report.games_timed = len(games)
    snapped = snapped_games(derived_root, raw_root, season, {g.week for g in games
                                                             if abs(g.kickoff - now) < timedelta(days=8)})
    due = due_games(games, now, snapped)
    if max_games is not None:
        due = due[:max(0, int(max_games))]
    report.due = len(due)
    if due and not dry_run:
        report.ledger_changed = True
        try:
            events, headers = fetch(events_url(key))
            credits.record(headers, now)
        except OddsApiError as exc:
            report.errors[f"events: {scrub(exc, key)}"] += 1
            events = None
        ids, report.kickoff_disagreements = match_events(events, due)
        stop = False
        touched: dict[int, dict[str, dict]] = {}
        for i, game in enumerate(due):
            if stop:
                report.refused["run stopped after an authorisation error"] += 1
                continue
            event_id = ids.get(game.game_id)
            if event_id is None:
                report.unmatched_events += 1
                continue
            reserve = core_reserve(games, game, credits.next_reset(now), snapped)
            report.reserve = reserve
            if credits.known(now):
                markets, why = plan_markets(credits, reserve)
            elif not report.first_readings:
                # No reading of this quota period (a lost ledger, a new month,
                # an event list that sent no headers): ONE core call per run
                # reads the provider's headers, at most CORE_COST credits
                # unguarded. Refusing instead would never learn the count.
                markets, why = P.CORE_MARKETS, ""
                report.first_readings += 1
            else:
                markets, why = None, UNKNOWN_CREDITS
            if markets is None:
                report.refused[why] += 1
                continue
            if i:
                sleep(PAUSE_SECONDS)
            if not clock() < game.kickoff:
                report.refused["kicked off before its turn in this run"] += 1
                continue
            payload, headers = None, {}
            for attempt in range(3):
                try:
                    payload, headers = fetch(event_odds_url(key, event_id, markets))
                    break
                except OddsApiError as exc:
                    credits.record(exc.headers, clock())
                    if exc.status == 429 and attempt < 2:
                        sleep(5.0 * (attempt + 1))
                        continue
                    report.errors[f"event odds: {scrub(exc, key)}"] += 1
                    stop = exc.status in STOP_CODES
                    break
            if payload is None:
                continue
            taken = clock()
            if "x-requests-remaining" in headers:
                credits.record(headers, taken)
            elif credits.remaining is not None:
                # No headers came back: assume the call cost what it could.
                credits.remaining -= len(markets) * len(P.REGIONS)
                credits.last = None
            meta = {"game_id": game.game_id, "season": game.season, "week": game.week,
                    "kickoff": game.kickoff.isoformat(), "taken_at": taken.isoformat(),
                    "lead_minutes": round((game.kickoff - taken).total_seconds() / 60, 1),
                    "event_id": event_id, "markets_requested": list(markets),
                    "regions": list(P.REGIONS),
                    "credits": {"remaining": credits.remaining, "used": credits.used,
                                "last": credits.last}}
            write_raw(raw_root, game, taken, payload, meta)
            report.raw_changed = True
            if not _has_odds(payload):
                report.empty += 1          # costs nothing, not a snapshot: retried next run
                continue
            report.snapshots += 1
            report.with_secondary += int(len(markets) > len(P.CORE_MARKETS))
            snapped.add(game.game_id)
            entry = derive_or_none(payload, table=table, params=params, taken_at=taken,
                                   kickoff=game.kickoff, errors=report.errors)
            if entry is None:
                entry = failed_entry(taken, game.kickoff)
            touched.setdefault(game.week, {})[game.game_id] = entry
            report.players += len(entry["players"])
            report.unmatched += entry["unmatched"]
            report.ambiguous += entry["ambiguous"]
        for week, entries in touched.items():
            report.derived_changed |= merge_derived(derived_root, season, week, entries)
        credits.save(ledger_path)
    if table_ok and not dry_run and any(Path(raw_root).glob(f"season{season}/week*/*__*.json")):
        report.rederived_weeks, changed = rederive(raw_root, derived_root, season, table, params,
                                                   errors=report.errors)
        report.derived_changed |= changed
    if report.reserve is None and games:
        upcoming = [g for g in games if g.kickoff > now]
        if upcoming:
            report.reserve = CORE_COST * len([g for g in upcoming
                                              if g.kickoff < credits.next_reset(now)
                                              and g.game_id not in snapped])
    return report


#: The names mode's one market: the anytime-TD market lists nearly every
#: quarterback, back, receiver and tight end a book prices, at 1 credit a game.
NAME_MARKETS: tuple[str, ...] = ("player_anytime_td",)
#: `--kickers` adds the kickers' market (one more credit a game).
KICKER_NAME_MARKETS: tuple[str, ...] = NAME_MARKETS + ("player_kicking_points",)
LOCAL_ONLY = "[props] names: local only (it stores player names); nothing asked"


def names_dir(raw_root: Path, season: int, week: int) -> Path:
    return Path(raw_root) / P.NAMES_DIR / f"season{int(season)}" / f"week{int(week):02d}"


def collect_names(*, key: str, season: int, now: datetime, schedule, schedule_source: str,
                  raw_root: Path = P.RAW_ROOT, derived_root: Path = P.DERIVED_ROOT,
                  week: int | None = None, fetch: Fetch | None = None,
                  sleep: Callable[[float], None] = time.sleep,
                  clock: Callable[[], datetime] = _wall_clock,
                  reset_day: int | None = None,
                  markets: Sequence[str] = NAME_MARKETS) -> RunReport:
    """LOCAL seeding of the odds-name table: the provider's spellings for
    every game of one week (default: the next week with a game to come), one
    cheap market per game, stored under raw/names/ for `odds_names.py
    propose`. Never a snapshot: nothing is derived from it and the collector
    does not count it. A game that already has a name list is not asked
    again. The same credit ledger and core reserve apply."""
    fetch = fetch or http_fetch
    markets = tuple(markets)
    cost = len(markets) * len(P.REGIONS)
    report = RunReport(now.isoformat(timespec="seconds"), True, schedule_source)
    ledger_path = Path(derived_root) / LEDGER_NAME
    credits = Credits.load(ledger_path)
    if reset_day is not None:
        credits.reset_day = int(reset_day)
    report.credits = credits
    games, report.games_untimed = season_games(schedule, season)
    report.games_timed = len(games)
    upcoming = [g for g in games if g.kickoff > now]
    if week is None and upcoming:
        week = upcoming[0].week
    have = {gid for (_w, gid) in P.read_name_files(raw_root, season)}
    targets = [g for g in upcoming if g.week == week and g.game_id not in have]
    report.week, report.due = week, len(targets)
    if not targets:
        return report
    report.ledger_changed = True
    try:
        events, headers = fetch(events_url(key))
        credits.record(headers, now)
    except OddsApiError as exc:
        report.errors[f"events: {scrub(exc, key)}"] += 1
        events = None
    ids, report.kickoff_disagreements = match_events(events, targets)
    snapped = snapped_games(derived_root, raw_root, season, {week})
    for i, game in enumerate(targets):
        event_id = ids.get(game.game_id)
        if event_id is None:
            report.unmatched_events += 1
            continue
        # every core snapshot the month still needs (the cloud takes them)
        report.reserve = reserve = CORE_COST * len(
            [g for g in games if now < g.kickoff < credits.next_reset(now)
             and g.game_id not in snapped])
        if credits.known(now):
            if credits.remaining - cost < reserve:
                report.refused["would leave less than the month's core reserve"] += 1
                continue
        elif report.first_readings:
            report.refused[UNKNOWN_CREDITS] += 1
            continue
        else:
            report.first_readings += 1
        if i:
            sleep(PAUSE_SECONDS)
        try:
            payload, headers = fetch(event_odds_url(key, event_id, markets))
        except OddsApiError as exc:
            credits.record(exc.headers, clock())
            report.errors[f"event odds: {scrub(exc, key)}"] += 1
            if exc.status in STOP_CODES:
                break
            continue
        taken = clock()
        if "x-requests-remaining" in headers:
            credits.record(headers, taken)
        elif credits.remaining is not None:
            credits.remaining -= cost
            credits.last = None
        if not _has_odds(payload):
            report.empty += 1                 # free; asked again on the next run
            continue
        _atomic_json(names_dir(raw_root, season, game.week)
                     / f"{game.game_id}__{_stamp(taken)}.json",
                     {"meta": {"game_id": game.game_id, "season": game.season,
                               "week": game.week, "kickoff": game.kickoff.isoformat(),
                               "taken_at": taken.isoformat(), "event_id": event_id,
                               "markets_requested": list(markets),
                               "regions": list(P.REGIONS), "purpose": "names only"},
                      "payload": payload})
        report.snapshots += 1
        report.raw_changed = True
    credits.save(ledger_path)
    return report


def names_lines(report: RunReport) -> list[str]:
    """The names mode's counts (no name: they go to raw/names/ only)."""
    c = report.credits or Credits()
    week = report.week
    out = [f"[props names] week {week if week is not None else '?'}: {report.due} game(s) "
           f"to ask; {report.snapshots} name list(s) stored, {report.empty} empty "
           f"response(s), {report.unmatched_events} game(s) with no provider event"]
    for label, counts in (("refused", report.refused), ("errors", report.errors)):
        if counts:
            out.append(f"[props names] {label}: " + ", ".join(
                f"{k} x{v}" for k, v in sorted(counts.items())))
    out.append(f"[props names] credits remaining {c.remaining}, used {c.used}; core "
               f"reserve {report.reserve}. Next: scripts/ingest/odds_names.py propose")
    return out


def write_outputs(report: RunReport, derived_root: Path) -> None:
    """The run's counts beside the derived files, and the two change flags
    for the workflow (GITHUB_OUTPUT), so an unchanged run saves no cache
    (last_run.json alone is not a change: the summary reads it in-run)."""
    _atomic_json(Path(derived_root) / LAST_RUN_NAME, report.record())
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        derived = report.derived_changed or report.ledger_changed
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(f"raw_changed={'true' if report.raw_changed else 'false'}\n")
            fh.write(f"derived_changed={'true' if derived else 'false'}\n")


def summary_lines(derived_root: Path) -> list[str]:
    """The last run, for the workflow's step summary: counts and credits only."""
    try:
        blob = json.loads((Path(derived_root) / LAST_RUN_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["- no collector run recorded on this runner"]
    if not blob.get("key_present"):
        return ["- no GRIDIRON_ODDS_API_KEY: nothing collected"]
    c = blob.get("credits") or {}
    out = [f"- schedule: {blob.get('schedule')}; games due {blob.get('due')}; snapshots "
           f"{blob.get('snapshots')} ({blob.get('with_secondary')} with secondary markets); "
           f"empty responses {blob.get('empty_responses')}",
           f"- credits remaining **{c.get('remaining')}**, used {c.get('used')}, last call "
           f"{c.get('last')}; core reserve to the next reset {blob.get('core_reserve')} "
           f"(reset day {c.get('reset_day')}"
           + (f", observed {c.get('reset_observed')}" if c.get("reset_observed") else ", assumed")
           + ")",
           f"- players resolved {blob.get('players_resolved')}; names unmatched "
           f"{blob.get('names_unmatched')}, ambiguous {blob.get('names_ambiguous')}"]
    if blob.get("first_readings"):
        out.append("- no credit reading of this quota period: one core call read the "
                   "provider's headers")
    for key in ("refused", "errors"):
        if blob.get(key):
            out.append(f"- {key}: " + ", ".join(f"{k} x{v}" for k, v in blob[key].items()))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="collect",
                    choices=("collect", "rederive", "summary", "names"))
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--now", default=None, help="ISO-8601 instant to run AS OF (tests, replays)")
    ap.add_argument("--dry-run", action="store_true", help="say what is due; make no call")
    ap.add_argument("--max-games", type=int, default=None,
                    help="snapshot at most this many due games (a local seeding run)")
    ap.add_argument("--week", type=int, default=None,
                    help="names: the week to ask about (default: the next week with a game)")
    ap.add_argument("--kickers", action="store_true",
                    help="names: also ask the kicking-points market (2 credits a game)")
    ap.add_argument("--raw-root", type=Path, default=P.RAW_ROOT)
    ap.add_argument("--derived-root", type=Path, default=P.DERIVED_ROOT)
    ap.add_argument("--cache-root", type=Path, default=None)
    ap.add_argument("--reset-day", default=None,
                    help=f"the quota's reset day of month (1-28) as the provider's account "
                         f"page shows it; default ${RESET_DAY_ENV}, else the ledger's")
    ap.add_argument("--debug", action="store_true",
                    help="re-raise a failure (local use only; the cloud never passes it)")
    args = ap.parse_args(argv)

    if args.command == "summary":
        print("\n".join(summary_lines(args.derived_root)))
        return 0
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(timezone.utc)
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    if args.command == "rederive":
        weeks, changed = rederive(args.raw_root, args.derived_root, args.season,
                                  P.IdentityTable.read(), P.PropParams.load())
        print(f"[props] re-derived {weeks} week(s) from the raw archive; "
              f"{'changed' if changed else 'unchanged'}")
        return 0
    if args.command == "names" and os.environ.get("GITHUB_ACTIONS") == "true":
        print(LOCAL_ONLY)
        return 2

    try:
        from gridiron.config import get_settings
        key = (get_settings().odds_api_key or "").strip() or None
    except Exception as exc:                                     # noqa: BLE001
        if args.debug:
            raise
        # The type only: a settings error can quote the value it refused.
        print(f"[props] failed: settings unreadable ({type(exc).__name__})")
        return 1
    if key is None:
        print("no GRIDIRON_ODDS_API_KEY: nothing collected")
        write_outputs(RunReport(now.isoformat(timespec="seconds"), False), args.derived_root)
        return 0
    reset_day, why = reset_day_override(args.reset_day if args.reset_day is not None
                                        else os.environ.get(RESET_DAY_ENV))
    if why:
        print(f"[props] {why}")
    if args.command == "names":
        try:
            schedule, source, _ = load_schedule(args.season, now, cache_root=args.cache_root,
                                                derived_root=args.derived_root)
            report = collect_names(key=key, season=args.season, now=now, schedule=schedule,
                                   schedule_source=source, raw_root=args.raw_root,
                                   derived_root=args.derived_root, week=args.week,
                                   reset_day=reset_day,
                                   markets=KICKER_NAME_MARKETS if args.kickers else NAME_MARKETS)
        except Exception as exc:                                 # noqa: BLE001
            if args.debug:
                raise
            print(f"[props] names failed: {type(exc).__name__} (rerun with --debug)")
            return 1
        for line in names_lines(report):
            print(scrub(line, key))
        return 0
    before = set(Path(args.raw_root).rglob("*.json"))
    try:
        schedule, source, rewrote = load_schedule(args.season, now, cache_root=args.cache_root,
                                                  derived_root=args.derived_root)
        clock = (lambda: now) if args.now else _wall_clock
        report = collect(key=key, season=args.season, now=now, schedule=schedule,
                         schedule_source=source, raw_root=args.raw_root,
                         derived_root=args.derived_root, max_games=args.max_games,
                         dry_run=args.dry_run, clock=clock, reset_day=reset_day)
        report.ledger_changed |= rewrote
    except Exception as exc:                                     # noqa: BLE001
        if args.debug:
            raise
        # The type only: an exception's text can quote a URL or a name.
        print(f"[props] failed: {type(exc).__name__} (rerun locally with --debug)")
        # A snapshot already paid for must still be kept: flag what changed.
        failed = RunReport(now.isoformat(timespec="seconds"), True, "failed run")
        failed.raw_changed = set(Path(args.raw_root).rglob("*.json")) != before
        failed.derived_changed = failed.raw_changed
        failed.errors[type(exc).__name__] += 1
        write_outputs(failed, args.derived_root)
        return 1
    for line in report.lines():
        print(scrub(line, key))
    if not args.dry_run:
        write_outputs(report, args.derived_root)
    return 0


if __name__ == "__main__":
    sys.exit(main())
