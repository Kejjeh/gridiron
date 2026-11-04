"""kalshi_props.py — Kalshi NFL player-prop ladders before kickoff, for the
owner's OWN trading on Kalshi. Network. LOCAL ONLY, PRIVATE DATA.

    PYTHONPATH=src python scripts/ingest/kalshi_props.py fetch [--max-games N] [--series KXNFLRSHYDS]
    PYTHONPATH=src python scripts/ingest/kalshi_props.py summary

Why this is allowed (owner, 2026-11-04): the owner is a Kalshi member and
uses this data to decide his own trades on Kalshi. The Kalshi Developer
Agreement v1.1 limits API use to "facilitating a members own trading on the
Exchange" and forbids collecting or storing API data for anything else, or
sharing it "with third parties in any manner" (section 3.1). So:

* Everything is written under data/research/cache/kalshi/ (gitignored). It
  is never committed, never an Actions artifact or cache, never on the public
  board, and no number derived from it goes into a tracked file. Analyses of
  it are local reports for the owner only.
* Refused inside GitHub Actions (public logs and caches).
* Slow on purpose (section 5: Kalshi sets the limits): one call at a time,
  PAUSE_SECONDS apart, backing off on 429. Resumable: a game already on disk
  is not asked again.
* Logs print counts only.

What it stores. For every regular-season game of weeks 1-17 since
2025-09-01 that has finished, and every series in SERIES: each settled
ladder market (one contract per player and threshold: "N+ yards", "N+
receptions", "anytime TD"), with its threshold, player, team, open time and
result, and its price at the SNAPSHOT: LEAD_MINUTES before the nflverse
kickoff, as the Odds API collector times its own. The price is the last
hourly candle ending at or before the snapshot (yes bid, yes ask, last
trade). A market that opened after the snapshot (Kalshi adds ladders during
games) has no pre-game price and is stored without one.

Files: data/research/cache/kalshi/markets/<SERIES>.json (the market list) and
data/research/cache/kalshi/games/season<Y>/week<NN>/<game_id>.json (one
game's markets and snapshot prices across every series).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from gridiron.paths import RESEARCH_CACHE

HOST = "https://api.elections.kalshi.com/trade-api/v2"
USER_AGENT = "gridiron/0.1 (python-urllib)"
ROOT = RESEARCH_CACHE / "kalshi"
SINCE = datetime(2025, 9, 1, tzinfo=timezone.utc)
LEAD_MINUTES = 120
PAUSE_SECONDS = 1.5
TRIES = 6
BATCH = 100                                   # market_tickers per candlestick call (the API's maximum)
CANDLE_MINUTES = 60
LOOKBACK_HOURS = 48                           # candles asked for before the snapshot
LAST_WEEK = 17
SERIES: tuple[str, ...] = ("KXNFLPASSYDS", "KXNFLPASSTDS", "KXNFLRSHYDS", "KXNFLRECYDS",
                           "KXNFLREC", "KXNFLANYTD")
#: Kalshi team codes that differ from nflverse's (nflverse -> extra spellings).
TEAM_ALIASES = {"LA": ("LAR",), "JAX": ("JAC",), "WAS": ("WSH",), "LV": ("OAK",)}
_EVENT = re.compile(r"^[A-Z0-9]+-(\d{2})([A-Z]{3})(\d{2})([A-Z]{4,6})$")
_MONTHS = {m: i for i, m in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}
_JERSEY = re.compile(r"(\d{1,2})$")


class KalshiError(Exception):
    def __init__(self, status: int | None, kind: str):
        super().__init__(f"HTTP {status}" if status else kind)
        self.status = status


def http_get(path: str, *, timeout: int = 60) -> dict:
    req = urllib.request.Request(HOST + path, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "application/json"})
    failure = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        failure = KalshiError(exc.code, "HTTPError")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        failure = KalshiError(None, type(exc).__name__)
    raise failure


@dataclass
class Client:
    """One call at a time, PAUSE_SECONDS apart; 429 and transient errors back
    off (10 s, 20 s, ... ) and retry (TRIES tries); any other error is raised."""
    fetch: Callable[[str], dict] = http_get
    sleep: Callable[[float], None] = time.sleep
    pause: float = PAUSE_SECONDS
    calls: int = 0
    retries: int = 0
    _last: float = 0.0

    def get(self, path: str) -> dict:
        for attempt in range(TRIES):
            wait = self.pause - (time.monotonic() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            try:
                return self.fetch(path)
            except KalshiError as exc:
                if exc.status not in (429, 500, 502, 503, 504, None) or attempt == TRIES - 1:
                    raise
                self.retries += 1
                self.sleep(10.0 * (attempt + 1))
        raise KalshiError(None, "unreachable")


# ------------------------------------------------------------- parsing

def event_date_teams(event_ticker: str) -> tuple[date, str] | None:
    """'KXNFLRSHYDS-26NOV02CHISEA' -> (2026-11-02, 'CHISEA')."""
    m = _EVENT.match(event_ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    yy, mon, dd, teams = m.groups()
    try:
        return date(2000 + int(yy), _MONTHS[mon], int(dd)), teams
    except ValueError:
        return None


def _spellings(team: str) -> tuple[str, ...]:
    return (team, *TEAM_ALIASES.get(team, ()))


def match_game(event_ticker: str, games: Sequence["Game"]) -> "Game | None":
    """The nflverse game on the ticker's date (US Eastern, as Kalshi writes it)
    whose two teams spell the ticker's team string, away then home or the
    reverse. None when no game, or more than one, fits."""
    parsed = event_date_teams(event_ticker)
    if parsed is None:
        return None
    day, teams = parsed
    hits = []
    for g in games:
        if g.gameday != day:
            continue
        pairs = {a + h for a in _spellings(g.away) for h in _spellings(g.home)}
        pairs |= {h + a for a in _spellings(g.away) for h in _spellings(g.home)}
        if teams in pairs:
            hits.append(g)
    return hits[0] if len(hits) == 1 else None


def market_record(m: Mapping) -> dict:
    """The fields kept of one market (no prices: those come from candles)."""
    ticker = str(m.get("ticker") or "")
    tail = ticker.rsplit("-", 2)
    player_part = tail[-2] if len(tail) >= 3 else ""
    jm = _JERSEY.search(player_part)
    title = str(m.get("title") or "")
    return {"ticker": ticker, "event_ticker": m.get("event_ticker"),
            "series": ticker.split("-", 1)[0],
            "player": title.split(":", 1)[0].strip() if ":" in title else None,
            "player_code": player_part or None,
            "team": None,                      # set per game (`player_team`)
            "jersey": int(jm.group(1)) if jm else None,
            "threshold": m.get("floor_strike"), "strike_type": m.get("strike_type"),
            "open_time": m.get("open_time"), "close_time": m.get("close_time"),
            "result": m.get("result"), "volume": _num(m.get("volume_fp", m.get("volume")))}


def _num(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


def _when(s) -> datetime | None:
    if not s:
        return None
    try:
        t = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _side(candle: Mapping, name: str) -> Mapping:
    return candle.get(name) or {}


def _close(part: Mapping) -> float | None:
    return _num(part.get("close_dollars", part.get("close")))


def player_team(player_code: str | None, game: "Game") -> str | None:
    """The game's team (nflverse code) whose Kalshi spelling begins the
    player code; None when neither or both do."""
    if not player_code:
        return None
    hits = {t for t in (game.away, game.home) for sp in _spellings(t) if player_code.startswith(sp)}
    return hits.pop() if len(hits) == 1 else None


def snapshot_price(candles: Sequence[Mapping], at: datetime) -> dict | None:
    """The last candle ending at or before `at`: yes bid, yes ask and last
    trade at its close (dollars, 0-1), and when it ended. Both the live
    (`*_dollars`) and the archived (plain) candle formats are read."""
    best = None
    for c in candles:
        end = c.get("end_period_ts")
        if end is None or int(end) > at.timestamp():
            continue
        if best is None or int(end) > int(best["end_period_ts"]):
            best = c
    if best is None:
        return None
    bid, ask = _close(_side(best, "yes_bid")), _close(_side(best, "yes_ask"))
    return {"bid": bid, "ask": ask, "last": _close(_side(best, "price")),
            "mid": (bid + ask) / 2 if bid is not None and ask is not None else None,
            "candle_end": datetime.fromtimestamp(int(best["end_period_ts"]), timezone.utc).isoformat()}


# ------------------------------------------------------------- the games

@dataclass(frozen=True)
class Game:
    game_id: str
    season: int
    week: int
    away: str
    home: str
    gameday: date
    kickoff: datetime


def schedule_games(schedules: Mapping[int, object], now: datetime) -> list[Game]:
    """Finished, timed regular-season games of weeks 1-17 since SINCE, with
    kickoffs from `gridiron.lineup.kickoff_index` (the board's reader)."""
    from gridiron.lineup import kickoff_index
    out = []
    for season, sch in schedules.items():
        if sch is None or len(sch) == 0:
            continue
        for week in sorted({int(w) for w in sch["week"].dropna().unique() if 1 <= int(w) <= LAST_WEEK}):
            ix = kickoff_index(sch, week)
            for r in sch.loc[sch["week"] == week].itertuples():
                if str(getattr(r, "game_type", "REG") or "REG").upper() != "REG":
                    continue
                home, away = str(r.home_team).upper(), str(r.away_team).upper()
                kh = ix.kickoffs.get(home) if ix is not None else None
                if kh is None or kh != (ix.kickoffs.get(away) if ix else None):
                    continue
                if not (SINCE <= kh < now):
                    continue
                gd = date.fromisoformat(str(r.gameday)[:10])
                out.append(Game(str(r.game_id), int(season), int(week), away, home, gd, kh))
    return sorted(out, key=lambda g: (g.kickoff, g.game_id))


def game_path(root: Path, g: Game) -> Path:
    return Path(root) / "games" / f"season{g.season}" / f"week{g.week:02d}" / f"{g.game_id}.json"


# ------------------------------------------------------------- fetching

def list_markets(client: Client, series: str, since: datetime = SINCE) -> list[dict]:
    """Every settled market of `series` closing since `since`, from the live
    and the historical (archived) tiers, newest first, paged."""
    out, seen = [], set()
    for path in (f"/markets?series_ticker={series}&status=settled&limit=1000",
                 f"/historical/markets?series_ticker={series}&limit=1000"):
        cursor = ""
        while True:
            d = client.get(path + (f"&cursor={urllib.parse.quote(cursor)}" if cursor else ""))
            page = d.get("markets") or []
            for m in page:
                t = m.get("ticker")
                ct = _when(m.get("close_time"))
                if t and t not in seen and ct is not None and ct >= since:
                    seen.add(t)
                    out.append(market_record(m))
            cursor = d.get("cursor") or ""
            oldest = min((_when(m.get("close_time")) for m in page if m.get("close_time")), default=None)
            if not cursor or not page or (oldest is not None and oldest < since):
                break
    return out


def archive_cutoff(client: Client) -> datetime | None:
    """Kalshi moves markets settled before this instant to the historical tier,
    whose candles only the per-market endpoint serves (the batch endpoint
    returns none for them; checked 2026-11-04)."""
    return _when((client.get("/historical/cutoff") or {}).get("market_settled_ts"))


def candles(client: Client, tickers: Sequence[str], at: datetime,
            archived: Iterable[str] = ()) -> dict[str, list]:
    end = int(at.timestamp())
    start = end - LOOKBACK_HOURS * 3600
    out = {}
    archived = set(archived)
    for t in [t for t in tickers if t in archived]:
        q = urllib.parse.urlencode({"start_ts": start, "end_ts": end, "period_interval": CANDLE_MINUTES})
        out[t] = client.get(f"/historical/markets/{urllib.parse.quote(t)}/candlesticks?{q}").get("candlesticks") or []
    tickers = [t for t in tickers if t not in archived]
    for i in range(0, len(tickers), BATCH):
        q = urllib.parse.urlencode({"market_tickers": ",".join(tickers[i:i + BATCH]),
                                    "start_ts": start, "end_ts": end,
                                    "period_interval": CANDLE_MINUTES,
                                    "include_latest_before_start": "true"})
        d = client.get(f"/markets/candlesticks?{q}")
        for m in d.get("markets") or []:
            out[m.get("market_ticker")] = m.get("candlesticks") or []
    return out


@dataclass
class Report:
    markets: Counter = field(default_factory=Counter)
    games_done: int = 0
    games_skipped_have: int = 0
    games_no_markets: int = 0
    events_unmatched: int = 0
    priced: int = 0
    opened_after_snapshot: int = 0
    no_candle: int = 0
    archived_calls: int = 0
    stopped: str = ""

    def lines(self, client: Client) -> list[str]:
        out = ["[kalshi] markets listed: " + ", ".join(f"{s} {n}" for s, n in self.markets.items()),
               f"[kalshi] games written {self.games_done}, already on disk {self.games_skipped_have}, "
               f"with no market {self.games_no_markets}; events matching no game {self.events_unmatched}",
               f"[kalshi] markets priced {self.priced}; opened after the snapshot {self.opened_after_snapshot}; "
               f"no candle {self.no_candle}; per-market archive calls {self.archived_calls}",
               f"[kalshi] calls {client.calls}, retries {client.retries}"]
        if self.stopped:
            out.append(f"[kalshi] stopped: {self.stopped}")
        return out


def _atomic_json(path: Path, blob) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(blob, indent=1), encoding="utf-8")
    tmp.replace(path)


def fetch(*, client: Client, games: Sequence[Game], series: Sequence[str] = SERIES,
          root: Path = ROOT, max_games: int | None = None, lead: int = LEAD_MINUTES,
          refresh_lists: bool = False) -> Report:
    rep = Report()
    by_game: dict[str, list[dict]] = defaultdict(list)
    for s in series:
        cache = Path(root) / "markets" / f"{s}.json"
        if cache.exists() and not refresh_lists:
            ms = json.loads(cache.read_text(encoding="utf-8"))
        else:
            try:
                ms = list_markets(client, s)
            except KalshiError as exc:
                rep.stopped = f"listing {s}: {exc}"
                return rep
            _atomic_json(cache, ms)
        rep.markets[s] = len(ms)
        events = {}
        for m in ms:
            ev = m["event_ticker"]
            if ev not in events:
                events[ev] = match_game(ev, games)
            g = events[ev]
            if g is None:
                continue
            by_game[g.game_id].append(m)
        rep.events_unmatched += sum(1 for g in events.values() if g is None)
    todo = []
    for g in games:
        if game_path(root, g).exists():
            rep.games_skipped_have += 1
        elif not by_game.get(g.game_id):
            rep.games_no_markets += 1
        else:
            todo.append(g)
    if max_games is not None:
        todo = todo[:max(0, int(max_games))]
    if todo:
        try:
            cutoff = archive_cutoff(client)
        except KalshiError as exc:
            rep.stopped = f"archive cutoff: {exc}"
            return rep
    for g in todo:
        at = g.kickoff - timedelta(minutes=lead)
        ms = by_game[g.game_id]
        pre = [m for m in ms if (_when(m["open_time"]) or at) <= at]
        try:
            old = [m["ticker"] for m in pre
                   if cutoff is None or (_when(m["close_time"]) or cutoff) < cutoff]
            rep.archived_calls += len(old)
            cs = candles(client, [m["ticker"] for m in pre], at, old) if pre else {}
        except KalshiError as exc:
            rep.stopped = f"candles for {g.game_id}: {exc}"
            break
        rows = []
        for m in ms:
            row = dict(m)
            row["team"] = player_team(m.get("player_code"), g)
            if m not in pre:
                row["snapshot"] = None
                rep.opened_after_snapshot += 1
            else:
                row["snapshot"] = snapshot_price(cs.get(m["ticker"], []), at)
                if row["snapshot"] is None:
                    rep.no_candle += 1
                else:
                    rep.priced += 1
            rows.append(row)
        _atomic_json(game_path(root, g), {
            "meta": {"game_id": g.game_id, "season": g.season, "week": g.week,
                     "kickoff": g.kickoff.isoformat(), "snapshot_at": at.isoformat(),
                     "lead_minutes": int(lead), "source": "kalshi",
                     "private": "Kalshi Developer Agreement 3.1: owner's own trading only; never share"},
            "markets": rows})
        rep.games_done += 1
    return rep


def summary(root: Path = ROOT) -> list[str]:
    files = sorted((Path(root) / "games").rglob("*.json"))
    seasons, markets, priced = Counter(), 0, 0
    for p in files:
        blob = json.loads(p.read_text(encoding="utf-8"))
        seasons[blob["meta"]["season"]] += 1
        markets += len(blob["markets"])
        priced += sum(1 for m in blob["markets"] if m.get("snapshot"))
    return [f"[kalshi] games on disk: {dict(sorted(seasons.items()))}; markets {markets}, "
            f"priced before kickoff {priced}"]


def load_schedules(seasons: Iterable[int]) -> dict[int, object]:
    import nflreadpy as nfl
    return {int(s): nfl.load_schedules([int(s)]).to_pandas() for s in seasons}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", choices=("fetch", "summary"))
    ap.add_argument("--series", action="append", default=None)
    ap.add_argument("--max-games", type=int, default=None)
    ap.add_argument("--lead", type=int, default=LEAD_MINUTES)
    ap.add_argument("--refresh-lists", action="store_true",
                    help="list markets again (the cached lists otherwise stand)")
    ap.add_argument("--root", type=Path, default=ROOT)
    args = ap.parse_args(argv)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("[kalshi] refused: local only (Kalshi data is private: Developer Agreement 3.1)")
        return 2
    if args.command == "summary":
        for line in summary(args.root):
            print(line)
        return 0
    now = datetime.now(timezone.utc)
    games = schedule_games(load_schedules(range(SINCE.year, now.year + 1)), now)
    client = Client()
    rep = fetch(client=client, games=games, series=tuple(args.series or SERIES), root=args.root,
                max_games=args.max_games, lead=args.lead, refresh_lists=args.refresh_lists)
    for line in rep.lines(client):
        print(line)
    return 1 if rep.stopped else 0


if __name__ == "__main__":
    sys.exit(main())
