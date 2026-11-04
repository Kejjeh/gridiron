"""OddsPapi (api.oddspapi.io, REST v4) — read-only client for historical props.

What the provider documents (oddspapi.io/en/docs, read 2026-11-03), and what
this module therefore assumes:

* auth is a query parameter, ``apiKey``. The key comes from
  ``GRIDIRON_ODDSPAPI_KEY`` via :mod:`gridiron.config` (rule #9) and is
  REDACTED from every message this module raises or prints: a URL with the
  key in it must never reach a public Actions log.
* ``/v4/historical-odds`` is free (it never increments the monthly count),
  takes at most 3 bookmakers per call, has a 5000 ms cooldown, and covers
  fixtures "since January 2026" only. A backfill therefore reaches the 2025
  postseason and the 2026 season — nothing earlier exists to fetch.
* other endpoints are metered and have their own cooldowns (below).
* historical payloads are ``bookmakers -> markets -> outcomes -> players ->
  [entries]`` keyed by OddsPapi ids; the market catalogue
  (``/v4/markets``) says which market ids are player props.

Nothing here resolves an OddsPapi player id to an nflverse ``gsis_id``.
That is a crosswalk job (rule #3: one cached crosswalk, never a name match)
and the rows keep the provider id so it can be built later.
"""
from __future__ import annotations

import gzip
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

BASE_URL = "https://api.oddspapi.io/v4"

# Documented per-endpoint cooldowns, seconds. A small margin is added on top.
COOLDOWN_S = {
    "sports": 1.0,
    "tournaments": 1.0,
    "bookmakers": 1.0,
    "markets": 1.0,
    "participants": 1.0,
    "fixtures": 2.0,
    "odds": 0.5,
    "historical-odds": 5.0,
    "account": 1.0,
}
COOLDOWN_MARGIN_S = 0.25
MAX_BOOKMAKERS_PER_HISTORICAL_CALL = 3
FIRST_COVERED_DATE = "2026-01-01"  # provider: "since January 2026"

FINISHED = 2


class OddsPapiError(RuntimeError):
    """A request failed. The message never contains the API key."""

    def __init__(self, message: str, status: int | None = None, code: str | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


class QuotaExhausted(OddsPapiError):
    pass


def redact(text: str, secret: str | None) -> str:
    if secret:
        text = text.replace(secret, "***")
        text = text.replace(urllib.parse.quote(secret, safe=""), "***")
    return text


Opener = Callable[[str, float], tuple[int, bytes]]


def _urllib_open(url: str, timeout: float) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "User-Agent": "gridiron-backfill/0.1",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https host
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


@dataclass
class Client:
    api_key: str
    opener: Opener = _urllib_open
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    timeout: float = 60.0
    max_retries: int = 4
    _last_call: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_key.strip():
            raise OddsPapiError("no OddsPapi key: set GRIDIRON_ODDSPAPI_KEY")

    # -- transport ---------------------------------------------------------
    def _wait(self, endpoint: str) -> None:
        gap = COOLDOWN_S.get(endpoint, 1.0) + COOLDOWN_MARGIN_S
        last = self._last_call.get(endpoint)
        if last is not None:
            remaining = gap - (self.clock() - last)
            if remaining > 0:
                self.sleep(remaining)

    def get(self, endpoint: str, **params: Any) -> Any:
        query = {k: v for k, v in params.items() if v is not None}
        query["apiKey"] = self.api_key
        url = f"{BASE_URL}/{endpoint}?{urllib.parse.urlencode(query)}"
        for attempt in range(self.max_retries + 1):
            self._wait(endpoint)
            try:
                status, body = self.opener(url, self.timeout)
            except Exception as exc:  # network: never echo the URL
                self._last_call[endpoint] = self.clock()
                if attempt < self.max_retries:
                    self.sleep(2.0 * (attempt + 1))
                    continue
                raise OddsPapiError(
                    f"{endpoint}: transport failure "
                    f"({redact(type(exc).__name__ + ': ' + str(exc), self.api_key)})") from None
            self._last_call[endpoint] = self.clock()
            if status == 200:
                return json.loads(body.decode("utf-8")) if body else None
            code = _error_code(body)
            detail = redact(body[:300].decode("utf-8", "replace"), self.api_key)
            if status == 429 and code == "REQUEST_LIMIT_EXCEEDED":
                raise QuotaExhausted(f"{endpoint}: monthly quota exhausted", status, code)
            if status in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                self.sleep(COOLDOWN_S.get(endpoint, 1.0) * (attempt + 2))
                continue
            raise OddsPapiError(f"{endpoint}: HTTP {status} {code or ''} {detail}".strip(),
                                status, code)
        raise OddsPapiError(f"{endpoint}: retries exhausted")  # pragma: no cover

    # -- endpoints ---------------------------------------------------------
    def account(self) -> Any:
        return self.get("account")

    def sports(self) -> list[dict]:
        return self.get("sports")

    def tournaments(self, sport_id: int) -> list[dict]:
        return self.get("tournaments", sportId=sport_id)

    def bookmakers(self) -> Any:
        return self.get("bookmakers")

    def markets(self) -> list[dict]:
        return self.get("markets")

    def participants(self, sport_id: int) -> dict:
        return self.get("participants", sportId=sport_id)

    def fixtures(self, tournament_id: int, start: str, end: str,
                 status_id: int | None = FINISHED) -> list[dict]:
        return self.get("fixtures", tournamentId=tournament_id, statusId=status_id,
                        **{"from": start, "to": end})

    def odds(self, fixture_id: str, bookmakers: str | None = None) -> Any:
        return self.get("odds", fixtureId=fixture_id, bookmakers=bookmakers)

    def historical_odds(self, fixture_id: str, bookmakers: Iterable[str]) -> Any:
        books = list(bookmakers)
        if not 1 <= len(books) <= MAX_BOOKMAKERS_PER_HISTORICAL_CALL:
            raise ValueError(f"historical-odds takes 1-3 bookmakers, got {len(books)}")
        return self.get("historical-odds", fixtureId=fixture_id, bookmakers=",".join(books))


def _error_code(body: bytes) -> str | None:
    try:
        data = json.loads(body.decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, dict):
        err = data.get("error", data)
        if isinstance(err, dict):
            return err.get("code")
    return None


# -- discovery (pure) -------------------------------------------------------

def find_american_football(sports: list[dict]) -> int:
    for s in sports:
        slug = str(s.get("slug", "")).lower()
        name = str(s.get("sportName", "")).lower()
        if slug in ("american-football", "americanfootball") or name == "american football":
            return int(s["sportId"])
    raise OddsPapiError("no American football sport in /v4/sports")


def find_nfl(tournaments: list[dict]) -> int:
    hits = [t for t in tournaments
            if str(t.get("tournamentSlug", "")).lower() == "nfl"
            or str(t.get("tournamentName", "")).strip().upper() == "NFL"]
    if len(hits) != 1:
        names = sorted({str(t.get("tournamentName")) for t in tournaments})[:20]
        raise OddsPapiError(f"expected exactly one NFL tournament, found {len(hits)}: {names}")
    return int(hits[0]["tournamentId"])


def prop_markets(markets: list[dict], sport_id: int) -> dict[int, dict]:
    """Player-prop markets of one sport, keyed by marketId."""
    out: dict[int, dict] = {}
    for m in markets:
        if not m.get("playerProp") or int(m.get("sportId", -1)) != sport_id:
            continue
        out[int(m["marketId"])] = {
            "name": m.get("marketName"),
            "handicap": m.get("handicap"),
            "period": m.get("period"),
            "type": m.get("marketType"),
            "outcomes": {int(o["outcomeId"]): o.get("outcomeName")
                         for o in m.get("outcomes", []) or []},
        }
    return out


def nfl_season(start_time: str) -> int:
    """NFL season a fixture belongs to (Jan/Feb games are last season's)."""
    year, month = int(start_time[:4]), int(start_time[5:7])
    return year if month >= 3 else year - 1


# -- flattening (pure) --------------------------------------------------------

PROP_COLUMNS = [
    "season", "fixture_id", "start_time", "home", "away", "bookmaker",
    "market_id", "market_name", "line", "outcome_id", "outcome_name",
    "oddspapi_player_id", "player_name", "created_at", "price_decimal",
    "limit", "active",
]


def flatten_props(payload: dict, fixture: dict, props: dict[int, dict],
                  player_names: dict[str, str] | None = None) -> list[dict]:
    """One row per price observation in a player-prop market.

    ``line`` is the market catalogue's handicap: OddsPapi models each line as
    its own market id. ``player_name`` is filled only from a provider map
    keyed by the same id, never by matching text.
    """
    names = player_names or {}
    rows: list[dict] = []
    start = fixture.get("startTime") or ""
    books = (payload or {}).get("bookmakers") or {}
    for book, bdata in books.items():
        for mid_s, mdata in ((bdata or {}).get("markets") or {}).items():
            try:
                mid = int(mid_s)
            except (TypeError, ValueError):
                continue
            market = props.get(mid)
            if market is None:
                continue
            for oid_s, odata in ((mdata or {}).get("outcomes") or {}).items():
                oid = int(oid_s)
                for pid, entries in ((odata or {}).get("players") or {}).items():
                    for e in entries or []:
                        rows.append({
                            "season": nfl_season(start) if start else None,
                            "fixture_id": fixture.get("fixtureId"),
                            "start_time": start,
                            "home": fixture.get("participant1Name"),
                            "away": fixture.get("participant2Name"),
                            "bookmaker": book,
                            "market_id": mid,
                            "market_name": market["name"],
                            "line": market["handicap"],
                            "outcome_id": oid,
                            "outcome_name": market["outcomes"].get(oid),
                            "oddspapi_player_id": str(pid),
                            "player_name": names.get(str(pid)),
                            "created_at": e.get("createdAt"),
                            "price_decimal": e.get("price"),
                            "limit": e.get("limit"),
                            "active": e.get("active"),
                        })
    return rows


def summarize(payload: dict, props: dict[int, dict]) -> dict:
    """Counts only — safe for a public log."""
    out = {"bookmakers": {}, "prop_markets_seen": set(), "players": set(), "entries": 0}
    for book, bdata in ((payload or {}).get("bookmakers") or {}).items():
        mk = (bdata or {}).get("markets") or {}
        n_prop = 0
        for mid_s, mdata in mk.items():
            if int(mid_s) in props:
                n_prop += 1
                out["prop_markets_seen"].add(int(mid_s))
                for odata in ((mdata or {}).get("outcomes") or {}).values():
                    for pid, entries in ((odata or {}).get("players") or {}).items():
                        out["players"].add(str(pid))
                        out["entries"] += len(entries or [])
        out["bookmakers"][book] = {"markets": len(mk), "prop_markets": n_prop}
    out["prop_markets_seen"] = len(out["prop_markets_seen"])
    out["players"] = len(out["players"])
    return out
