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
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
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
        """The full NFL player dump (~16 MB). Bulk — cache it, never commit
        it (rule #10). Used for injury_status, depth chart order and as the
        fallback id overlay."""
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
