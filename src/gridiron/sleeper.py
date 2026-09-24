"""Read-only Sleeper adapter — the ONE place the league is talked to.

`espn.py` says a Sleeper league gets a sibling module; this is it. The league
lives on Sleeper (league_config.PLATFORM) and Sleeper's read API needs no
auth, so nothing here reads a credential.

READ-ONLY BY CONSTRUCTION. This module issues GET requests and nothing else.
It has no method that submits a lineup, a waiver claim, a trade, an add/drop
or a message, and `tests/test_sleeper.py::test_module_is_read_only` fails the
build if one appears. Anything that changes the league is done by a human in
the Sleeper app.

Every call is injectable (`fetch=`), so the whole adapter is exercised
offline against committed fixtures.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, time as clock, timedelta, timezone
from pathlib import Path
from typing import Any

from gridiron.league_config import SEASON_YEAR, SLEEPER_LEAGUE_ID

BASE = "https://api.sleeper.app/v1"
#: The per-game status feed Sleeper's own app reads. It is NOT in Sleeper's
#: published API documentation (docs.sleeper.com lists no schedule or score
#: endpoint), so it is used as what it is: an undocumented read-only feed that
#: may change shape without notice. One real response was inspected on
#: 2026-09-20 before anything relied on it — a list of games carrying
#: `week`, `date`, `home`, `away`, `game_id` and `status`, with the statuses
#: pre_game / in_game / complete / suspended / canceled observed. Anything the
#: reader has not seen is passed through as UNKNOWN, never guessed at.
SCHEDULE_BASE = "https://api.sleeper.app/schedule"
USER_AGENT = "gridiron/0.1 (python-urllib)"  # a plain script UA, never a browser spoof

Fetch = Callable[[str], Any]


def http_fetch(url: str, *, timeout: int = 60, retries: int = 3) -> Any:
    """GET + parse JSON, with bounded exponential backoff on transient errors.

    A 404 is returned as None rather than raised: Sleeper answers 404 for a
    week that has not been created yet, which is missing data, not a fault.
    """
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            last = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last = exc
        if attempt < retries - 1:
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"Sleeper GET failed after {retries} tries: {url}: {last}")


@dataclass(frozen=True)
class NflState:
    """Sleeper's view of where the NFL season is. The one authority for
    'which week is it' — never infer the week from the wall clock."""

    season: int
    week: int
    display_week: int
    season_type: str
    season_start_date: str

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "NflState":
        return cls(
            season=int(payload["season"]),
            week=int(payload["week"]),
            display_week=int(payload.get("display_week") or payload["week"]),
            season_type=str(payload.get("season_type") or "regular"),
            season_start_date=str(payload.get("season_start_date") or ""),
        )


class SleeperReadOnly:
    """GET-only client for one league."""

    def __init__(self, league_id: str | None = None, *,
                 fetch: Fetch | None = None) -> None:
        self.league_id = str(league_id or resolve_league_id())
        self._fetch: Fetch = fetch or http_fetch
        self._default_fetch = fetch is None

    # --- endpoints (all GET) --------------------------------------------
    def state(self) -> NflState:
        return NflState.from_payload(self._fetch(f"{BASE}/state/nfl") or {})

    def league(self) -> dict:
        return self._fetch(f"{BASE}/league/{self.league_id}") or {}

    def users(self) -> list[dict]:
        return self._fetch(f"{BASE}/league/{self.league_id}/users") or []

    def rosters(self) -> list[dict]:
        return self._fetch(f"{BASE}/league/{self.league_id}/rosters") or []

    def matchups(self, week: int) -> list[dict]:
        return self._fetch(f"{BASE}/league/{self.league_id}/matchups/{int(week)}") or []

    def players(self) -> dict:
        """The full NFL player map (5-16 MB). Bulk — cache it, never commit
        it (rule #10). ONE attempt: Sleeper asks for this call at most once a
        day (see PLAYER_MAP_LEDGER), so a failure waits for the next budgeted
        request rather than being retried on the spot. Callers go through
        `player_map_budget` first; this method does not check it."""
        if self._default_fetch:
            return http_fetch(f"{BASE}/players/nfl", timeout=120, retries=1) or {}
        return self._fetch(f"{BASE}/players/nfl") or {}

    def schedule(self, season: int, season_type: str = "regular") -> list:
        """Per-game status for a season (see SCHEDULE_BASE). Read-only, one
        GET, a few tens of KB, cached upstream for ten minutes. A 404 is an
        empty list: a season the feed does not know is missing data."""
        return self._fetch(f"{SCHEDULE_BASE}/nfl/{str(season_type)}/{int(season)}") or []

    # --- assembled reads -------------------------------------------------
    def snapshot(self, week: int | None = None) -> dict:
        """One coherent read of the league. `as_of` is stamped here so every
        downstream freshness check refers to when the data was actually
        pulled, not when a file happened to be written."""
        state = self.state()
        wk = int(week or state.week)
        return {
            "as_of": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "league_id": self.league_id,
            "week": wk,
            "state": state.__dict__,
            "league": self.league(),
            "users": self.users(),
            "rosters": self.rosters(),
            "matchups": self.matchups(wk),
        }


def resolve_league_id() -> str:
    """Env override first (`GRIDIRON_SLEEPER_LEAGUE_ID`), else the verified
    constant. A Sleeper league id is a public identifier, not a credential —
    it is already in league_config's provenance docstring."""
    try:
        from gridiron.config import get_settings

        configured = get_settings().sleeper_league_id
    except Exception:  # pydantic-settings absent or .env unreadable
        configured = None
    return str(configured or SLEEPER_LEAGUE_ID)


def owner_roster(rosters: list[dict], owner_id: str) -> dict | None:
    """The roster owned by `owner_id`, including co-owned teams."""
    owner_id = str(owner_id)
    for r in rosters:
        if str(r.get("owner_id")) == owner_id:
            return r
        if owner_id in {str(c) for c in (r.get("co_owners") or [])}:
            return r
    return None


def display_names(users: list[dict]) -> dict[str, str]:
    """user_id -> display name. Team names are league-private; callers keep
    them out of shared reports."""
    return {str(u.get("user_id")): str(u.get("display_name") or "") for u in users}


def season_matches(state: NflState) -> bool:
    """Guard against running last season's code against this season's data."""
    return state.season == SEASON_YEAR


# ---------------------------------------------------------------------------
# The full player map's request budget.
#
# docs.sleeper.com, "Fetch all players" (read 2026-09-24): "Please use this
# call sparingly, as it is intended only to be used once per day at most to
# keep your player IDs updated." So the map is an IDENTITY source with a
# request budget. It still carries `injury_status`, and that field is still
# judged on the unchanged designation cadence (freshness.CADENCES), which
# means on a game day the designations it carries go STALE six hours after
# the day's one request and the moves resting on them are withheld with a
# check-in-Sleeper instruction. A filtered query (`?position=`) hits the same
# endpoint and is not a way around the budget; nothing here uses one.
#
# The budget is counted in REQUESTS, not successes: a failed or interrupted
# GET still asked Sleeper for the body. The ledger line is written BEFORE the
# GET so a crash mid-download counts too.
#
# Three bounds, so no failure mode turns into a request every 15 minutes:
#   * >= 24 h between requests, from the ledger (or, for a cache written
#     before the ledger existed, from the map's own pull time);
#   * requests only inside PLAYER_MAP_WINDOW (league time), so a ledger that
#     stops being saved between cloud runs costs at most the runs inside one
#     window a day;
#   * with no history at all (a lost cache) only inside PLAYER_MAP_COLD_SLOT,
#     about one scheduled run a day, unless a person asks for a cold start.
# ---------------------------------------------------------------------------

PLAYER_MAP_LEDGER = "player_map_requests.json"
PLAYER_MAP_MIN_INTERVAL = timedelta(hours=24)
#: League-time window for the day's one request. 10:00-13:00 ET lands after
#: nflverse's 07:00 UTC injury update and before the Sunday early kickoffs,
#: so the day's designations are six hours old at most through the early
#: slate. Scheduling, not a promise: a skipped run just means a later one.
PLAYER_MAP_WINDOW = (clock(10, 0), clock(13, 0))
PLAYER_MAP_COLD_SLOT = (clock(10, 0), clock(10, 20))
PLAYER_MAP_LEDGER_KEEP = 14
_FUTURE_SLACK = timedelta(minutes=5)


def _league_tz():
    from gridiron.freshness import LEAGUE_TZ
    return LEAGUE_TZ


def _stamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        t = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo is not None else None


def valid_player_map_ledger(blob: object) -> tuple[list[dict], str]:
    """The request lines of a ledger blob, or ([], why) when it is not one.

    Total: nothing here raises. A line that does not carry a readable,
    timezone-aware `at` and a known outcome rejects the whole ledger rather
    than being skipped — a ledger with a hole in it is not a count."""
    if not isinstance(blob, dict) or not isinstance(blob.get("requests"), list):
        return [], "not a player-map ledger"
    out: list[dict] = []
    for line in blob["requests"]:
        if not isinstance(line, dict):
            return [], "a ledger line is not an object"
        at = _stamp(line.get("at"))
        outcome = line.get("outcome")
        if at is None or outcome not in ("requested", "ok", "failed"):
            return [], "a ledger line has no readable time or outcome"
        out.append({"at": at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    "outcome": outcome, "note": str(line.get("note") or "")[:200]})
    return out, ""


def read_player_map_ledger(directory: Path) -> list[dict]:
    """Request lines oldest first; [] when absent or unreadable. An unreadable
    ledger is not permission: `player_map_budget` then falls back to the map's
    own pull time and, with none, to the narrow cold slot."""
    try:
        blob = json.loads((Path(directory) / PLAYER_MAP_LEDGER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    lines, _ = valid_player_map_ledger(blob)
    return lines


def note_player_map_request(directory: Path, now: datetime, *, outcome: str,
                            note: str = "") -> None:
    """Append a request line, or settle the last `requested` line to its
    outcome. Written through a temp file so a crash leaves the old ledger."""
    lines = read_player_map_ledger(directory)
    iso = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    if outcome != "requested" and lines and lines[-1]["outcome"] == "requested":
        lines[-1] = {**lines[-1], "outcome": outcome, "note": note[:200]}
    else:
        lines.append({"at": iso, "outcome": outcome, "note": note[:200]})
    path = Path(directory) / PLAYER_MAP_LEDGER
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"endpoint": "players/nfl",
                               "requests": lines[-PLAYER_MAP_LEDGER_KEEP:]}, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


@dataclass(frozen=True)
class PlayerMapBudget:
    due: bool
    reason: str
    last_request: datetime | None = None
    #: True/False for the last logged request's outcome; None when unknown.
    last_ok: bool | None = None
    next_allowed: datetime | None = None


def _in(window: tuple[clock, clock], now: datetime) -> bool:
    local = now.astimezone(_league_tz()).time()
    return window[0] <= local < window[1]


def player_map_budget(ledger: Sequence[Mapping[str, Any]], now: datetime, *,
                      fallback_last: datetime | None = None,
                      cold_start: bool = False) -> PlayerMapBudget:
    """May the full player map be requested now? Never bypassed by --force."""
    last: datetime | None = None
    last_ok: bool | None = None
    for line in ledger:
        t = _stamp(line.get("at"))
        if t is not None and (last is None or t >= last):
            last, last_ok = t, {"ok": True, "failed": False}.get(str(line.get("outcome")))
    if last is None and fallback_last is not None:
        last, last_ok = fallback_last, None
    window = f"{PLAYER_MAP_WINDOW[0]:%H:%M}-{PLAYER_MAP_WINDOW[1]:%H:%M} ET"
    if last is None:
        if cold_start:
            return PlayerMapBudget(True, "no earlier request on record; manual cold start")
        if _in(PLAYER_MAP_COLD_SLOT, now):
            return PlayerMapBudget(True, "no earlier request on record; daily cold-start slot")
        return PlayerMapBudget(
            False, f"no earlier request on record, and outside the "
                   f"{PLAYER_MAP_COLD_SLOT[0]:%H:%M}-{PLAYER_MAP_COLD_SLOT[1]:%H:%M} ET "
                   f"cold-start slot (a lost cache must not become a request every run)")
    nxt = last + PLAYER_MAP_MIN_INTERVAL
    when = f"last request {last.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}"
    if last > now + _FUTURE_SLACK:
        return PlayerMapBudget(False, f"{when} is AFTER this machine's clock; not "
                                      f"requesting until that is explained", last, last_ok, nxt)
    if now < nxt:
        return PlayerMapBudget(False, f"{when}; Sleeper asks for this call once a day at "
                                      f"most, next allowed {nxt.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}",
                               last, last_ok, nxt)
    if not _in(PLAYER_MAP_WINDOW, now):
        return PlayerMapBudget(False, f"{when}; a day has passed but requests are made "
                                      f"only in the {window} window", last, last_ok, nxt)
    return PlayerMapBudget(True, f"{when}; a day has passed and this is the {window} window",
                           last, last_ok, nxt)
