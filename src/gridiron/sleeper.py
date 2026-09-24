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
from datetime import datetime, timedelta, timezone
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
# The rules, and what they can and cannot promise:
#   * >= 24 h between requests, from a TRUSTWORTHY ledger: readable line by
#     line, at least one request on it, none stamped after this machine's
#     clock. Once a day has passed the next run may request at any hour; a
#     run delayed past the morning does not cost the whole day. Because the
#     first run after the 24 h mark makes the request, the day's request
#     drifts later by up to one run interval a day; nothing re-anchors it.
#   * No trustworthy ledger (missing, unreadable, malformed, future-dated,
#     empty) never authorises a request. The state is RECOVERY NEEDED, shown
#     on the page, until a person runs the one-time bootstrap
#     (`pull_week.py --player-map-bootstrap`, or the workflow's bootstrap
#     input), which itself refuses while the cached map shows a pull or a
#     failed attempt less than 24 h old.
#   * In the cloud (`carried=True`) the ledger lives in an Actions cache,
#     which can silently fail to save or be evicted. Every run stamps the
#     ledger with its workflow run number. A run whose carried stamp is not
#     from the IMMEDIATELY previous run (run N-1), or that is a re-run
#     (GITHUB_RUN_ATTEMPT > 1: the same run number again, whose earlier
#     attempt may have requested and lost its save), cannot see what the
#     missing run did. It requests nothing and stamps a GAP MARK at its own
#     time: every missing run started before it, so any request one made is
#     no later than the mark. The mark is carried like the request lines and
#     only ever moves later; the next request waits for 24 h after the LATER
#     of the last logged request and the mark. A later run stamping its
#     number re-proves the chain from here on; it never erases the mark.
#     A lost save after a request therefore costs a day's delay, not a second
#     request, and a cache that keeps losing saves keeps moving the mark and
#     stops requests (the page says PAUSED once the map is 30 h old) until
#     saves work for a whole day.
#   * What the cloud rule rests on: run numbers of this workflow only
#     increase; its runs do not overlap (the workflow's concurrency group);
#     a restore returns the newest saved entry or nothing; runner clocks
#     agree. The cache is not durable storage: if it loses the ledger
#     outright the state is RECOVERY NEEDED, and a person's bootstrap is
#     their word, checked only against what the cache still shows (the map,
#     a failed attempt, a rejected ledger's readable stamps), that no
#     request was made in the last 24 h. A local run keeps its own ledger on its own
#     disk and is not counted with the cloud's.
# ---------------------------------------------------------------------------

PLAYER_MAP_LEDGER = "player_map_requests.json"
#: This run's budget decision, for the page. Not carried: each run writes its own.
PLAYER_MAP_STATUS = "player_map_status.json"
PLAYER_MAP_MIN_INTERVAL = timedelta(hours=24)
PLAYER_MAP_LEDGER_KEEP = 14
_FUTURE_SLACK = timedelta(minutes=5)
_OUTCOMES = ("requested", "ok", "failed")


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
        if at is None or outcome not in _OUTCOMES:
            return [], "a ledger line has no readable time or outcome"
        out.append({"at": at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                    "outcome": outcome, "note": str(line.get("note") or "")[:200]})
    if blob.get("checked") is not None and _stamp(blob.get("checked")) is None:
        return [], "its checked stamp is unreadable"
    if blob.get("gap_seen") is not None and _stamp(blob.get("gap_seen")) is None:
        return [], "its gap mark is unreadable"
    run = blob.get("checked_run")
    if run is not None and (isinstance(run, bool) or not isinstance(run, int) or run < 1):
        return [], "its checked run number is unreadable"
    return out, ""


@dataclass(frozen=True)
class PlayerMapHistory:
    """What the ledger on disk says, and whether it can be trusted at all."""
    lines: tuple[dict, ...] = ()
    #: When a run last read this ledger and wrote it back, and that run's
    #: workflow run number (cloud carry proof; None locally).
    checked: datetime | None = None
    checked_run: int | None = None
    #: "" when the ledger is present and readable; otherwise why it is not.
    problem: str = ""
    #: Latest time a cloud run found the carried chain broken (a run between
    #: may have requested and lost its save); None when no gap was ever seen.
    gap_seen: datetime | None = None


def read_player_map_history(directory: Path) -> PlayerMapHistory:
    path = Path(directory) / PLAYER_MAP_LEDGER
    if not path.exists():
        return PlayerMapHistory(problem="no request ledger")
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return PlayerMapHistory(problem=f"the request ledger is unreadable "
                                        f"({type(exc).__name__})")
    lines, why = valid_player_map_ledger(blob)
    if why:
        return PlayerMapHistory(problem=f"the request ledger is {why}")
    return PlayerMapHistory(tuple(lines), _stamp(blob.get("checked")),
                            blob.get("checked_run"), gap_seen=_stamp(blob.get("gap_seen")))


def read_player_map_ledger(directory: Path) -> list[dict]:
    """Request lines oldest first; [] when absent or unreadable. An unreadable
    ledger is not permission: `player_map_budget` treats it as RECOVERY NEEDED."""
    return list(read_player_map_history(directory).lines)


def _write_ledger(directory: Path, lines: Sequence[dict], checked: datetime,
                  run: int | None, gap_seen: datetime | None = None) -> None:
    path = Path(directory) / PLAYER_MAP_LEDGER
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(json.dumps({"endpoint": "players/nfl",
                               "checked": checked.astimezone(timezone.utc)
                               .isoformat(timespec="seconds"),
                               "checked_run": run,
                               "gap_seen": gap_seen.astimezone(timezone.utc)
                               .isoformat(timespec="seconds") if gap_seen else None,
                               "requests": list(lines)[-PLAYER_MAP_LEDGER_KEEP:]}, indent=1),
                   encoding="utf-8")
    os.replace(tmp, path)


def note_player_map_request(directory: Path, now: datetime, *, outcome: str,
                            note: str = "", run: int | None = None) -> None:
    """Append a request line, or settle the last `requested` line to its
    outcome. Written through a temp file so a crash leaves the old ledger.
    An unreadable ledger is replaced only here, by a request a budget (or a
    person's bootstrap) allowed; request times and the gap mark already on it
    are kept."""
    history = read_player_map_history(directory)
    lines = list(history.lines)
    iso = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    if outcome != "requested" and lines and lines[-1]["outcome"] == "requested":
        lines[-1] = {**lines[-1], "outcome": outcome, "note": note[:200]}
    else:
        lines.append({"at": iso, "outcome": outcome, "note": note[:200]})
    _write_ledger(directory, lines, now, run, history.gap_seen)


def note_player_map_check(directory: Path, now: datetime, run: int | None = None,
                          gap: datetime | None = None) -> bool:
    """Stamp a trustworthy ledger as read by this run (its request lines and
    their times untouched), and record `gap` (PlayerMapBudget.gap_at) when
    this run found the carried chain broken. The gap mark only moves later;
    the run number stamped here never clears it. Never CREATES a ledger: an
    empty one written here would read as trustworthy history and let a lost
    cache request freely."""
    h = read_player_map_history(directory)
    if h.problem or not h.lines:
        return False
    marks = [t for t in (h.gap_seen, gap) if t is not None]
    _write_ledger(directory, h.lines, now, run, max(marks) if marks else None)
    return True


@dataclass(frozen=True)
class PlayerMapBudget:
    due: bool
    reason: str
    last_request: datetime | None = None
    #: True/False for the last logged request's outcome; None when unknown.
    last_ok: bool | None = None
    next_allowed: datetime | None = None
    #: "due", "wait" (inside the 24 h), "unconfirmed" (this cloud run found
    #: the carried chain broken), "held" (inside the 24 h after a gap mark),
    #: "recovery" (no trustworthy history: a person must act) or
    #: "bootstrap-refused".
    state: str = "wait"
    #: Set when this run found the carried chain broken: the gap mark the
    #: run must carry forward (note_player_map_check). None otherwise.
    gap_at: datetime | None = None

    @property
    def needs_person(self) -> bool:
        return self.state in ("recovery", "bootstrap-refused")


def player_map_budget(history: PlayerMapHistory | Sequence[Mapping[str, Any]],
                      now: datetime, *, carried: bool = False, run: int | None = None,
                      attempt: int | None = 1, bootstrap: bool = False,
                      fallback_last: datetime | None = None) -> PlayerMapBudget:
    """May the full player map be requested now? Never bypassed by --force.

    `fallback_last` is the cache's own evidence of the last request (the map's
    pull time, or a later failed attempt); it only ever blocks a bootstrap,
    it never authorises a request. `run` and `attempt` are the workflow's
    run number and attempt (GITHUB_RUN_NUMBER, GITHUB_RUN_ATTEMPT); they
    matter only when `carried`, where an unknown one counts as a gap."""
    if not isinstance(history, PlayerMapHistory):
        lines, why = valid_player_map_ledger({"requests": list(history)})
        history = PlayerMapHistory(tuple(lines), None, why)
    problem = history.problem or ("" if history.lines else "no request on the ledger")
    stamps = [t for t in (_stamp(x.get("at")) for x in history.lines) if t is not None]
    last = max(stamps) if stamps else None
    last_ok = None
    if last is not None:
        final = [x for x in history.lines if _stamp(x.get("at")) == last][-1]
        last_ok = {"ok": True, "failed": False}.get(str(final.get("outcome")))
        if last > now + _FUTURE_SLACK:
            problem = (f"a request is stamped {last.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}, "
                       f"after this machine's clock")
    hold = history.gap_seen
    if not problem and hold is not None and hold > now + _FUTURE_SLACK:
        problem = (f"its gap mark is stamped {hold.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}, "
                   f"after this machine's clock")
    if problem:
        if not bootstrap:
            return PlayerMapBudget(
                False, f"RECOVERY NEEDED — {problem}, so there is no trustworthy "
                       f"record of the last player-map request and none is made "
                       f"automatically; a person runs the one-time bootstrap "
                       f"(HANDOFF.md, 'Player-map bootstrap')", last, last_ok,
                state="recovery")
        if carried and attempt != 1:
            return PlayerMapBudget(
                False, f"bootstrap refused — {problem}, and this is a re-run (attempt "
                       f"{attempt}) of a run whose earlier attempt may already have "
                       f"requested; dispatch a NEW run with the bootstrap input once the "
                       f"earlier attempt's log shows no request in the last 24 h",
                last, last_ok, state="bootstrap-refused")
        # Readable evidence on a rejected ledger (a gap mark, or a request
        # before a future-dated one) blocks a bootstrap exactly as the map does.
        seen = [t for t in (fallback_last, hold, *stamps) if t is not None
                and (t is fallback_last or t <= now + _FUTURE_SLACK)]
        latest = max(seen) if seen else None
        if latest is not None and (latest > now + _FUTURE_SLACK
                                   or now < latest + PLAYER_MAP_MIN_INTERVAL):
            nxt = latest + PLAYER_MAP_MIN_INTERVAL
            return PlayerMapBudget(
                False, f"bootstrap refused — {problem}, and the cache shows a "
                       f"request (or a gap that may hide one) at "
                       f"{latest.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}; "
                       f"retry the bootstrap after {nxt.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}",
                latest, None, nxt, state="bootstrap-refused")
        return PlayerMapBudget(True, f"bootstrap by a person — {problem}", last, last_ok,
                               state="due")
    # A broken carried chain is checked BEFORE the 24 h, not only once the
    # day has passed: a gap seen while waiting still hides a possible request.
    gap = carried and (run is None or attempt != 1 or history.checked_run is None
                       or history.checked_run != run - 1)
    if gap:
        hold = now if hold is None else max(hold, now)
    held = hold is not None and hold > last
    nxt = (hold if held else last) + PLAYER_MAP_MIN_INTERVAL
    when = f"last request {last.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}"
    until = f"{nxt.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}"
    if gap:
        seen = (f"it was last saved by run {history.checked_run}"
                if history.checked_run is not None else "no run has stamped it")
        this = f"this is run {run}" + (f", attempt {attempt}" if attempt != 1 else "")
        return PlayerMapBudget(
            False, f"{when}; the carried ledger is not shown to come from the previous "
                   f"run ({seen}; {this}), so a run in between may have requested and "
                   f"lost its save; no request before {until}, 24 h after this run",
            last, last_ok, nxt, state="unconfirmed", gap_at=now)
    if now < nxt:
        if held:
            return PlayerMapBudget(
                False, f"{when}; a gap in the carried ledger was seen at "
                       f"{hold.astimezone(timezone.utc):%Y-%m-%d %H:%MZ} (a run whose save was "
                       f"lost may have requested), so the next request waits until {until}",
                last, last_ok, nxt, state="held")
        return PlayerMapBudget(False, f"{when}; Sleeper asks for this call once a day at "
                                      f"most, next allowed {until}",
                               last, last_ok, nxt, state="wait")
    return PlayerMapBudget(True, f"{when}; a day has passed" + (
        f" since it and since the gap seen {hold.astimezone(timezone.utc):%Y-%m-%d %H:%MZ}"
        if held else ""), last, last_ok, nxt, state="due")


def write_player_map_status(directory: Path, now: datetime, budget: PlayerMapBudget) -> None:
    path = Path(directory) / PLAYER_MAP_STATUS
    tmp = path.with_name(path.name + ".part")
    last = budget.last_request
    tmp.write_text(json.dumps({"at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
                               "state": budget.state, "reason": budget.reason[:400],
                               "last_request": last.astimezone(timezone.utc)
                               .isoformat(timespec="seconds") if last else None}),
                   encoding="utf-8")
    os.replace(tmp, path)


def player_map_status_note(directory: Path) -> str:
    """The page's line for a budget that needs a person, or ""."""
    try:
        blob = json.loads((Path(directory) / PLAYER_MAP_STATUS).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(blob, dict):
        return ""
    at, last = _stamp(blob.get("at")), _stamp(blob.get("last_request"))
    # A gap ("unconfirmed", then "held") is routine after one lost save; it is
    # said on the page only once the map is six hours past a day old.
    stuck = (blob.get("state") in ("unconfirmed", "held") and at is not None
             and last is not None
             and at - last >= PLAYER_MAP_MIN_INTERVAL + timedelta(hours=6))
    if blob.get("state") not in ("recovery", "bootstrap-refused") and not stuck:
        return ""
    when = f" (pull run {at.astimezone(timezone.utc):%Y-%m-%d %H:%MZ})" if at else ""
    return (f"Player map requests are PAUSED{when}: {str(blob.get('reason') or '')[:300]}. "
            f"Designations age from the last map; moves resting on them are withheld "
            f"once it is stale")
