"""backfill_props.py — buy past player-prop snapshots (PAID plan). Network.

    PYTHONPATH=src python scripts/ingest/backfill_props.py                     # estimate: no key, no call
    PYTHONPATH=src python scripts/ingest/backfill_props.py estimate --kickers
    PYTHONPATH=src python scripts/ingest/backfill_props.py run --budget 120 --max-games 2   # a probe
    PYTHONPATH=src python scripts/ingest/backfill_props.py run --budget 53400

The live collector (`pull_props.py`) learns forward only: the free plan has
no history. The Odds API sells history on PAID plans only ("This endpoint is
only available on paid usage plans", the v4 guide, read 2026-11-04), and
player props exist there from 2023-05-03T05:30:00Z. This script buys, for
every timed regular-season game of weeks 1-17 from 2023 to the last played
2026 week, ONE snapshot of the collector's six core markets (plus the
kicking-points market with --kickers) taken LEAD_MINUTES before the nflverse
kickoff, as the live collector would have taken it, and stores it exactly
where the live collector stores its own: raw/season<Y>/week<NN>/
<game_id>__<stamp>.json, marked `"source": "historical"`. So
`pull_props.py rederive --season Y`, `odds_names.py propose --week N` and the
book ranking read the backfill unchanged.

Costs (the v4 guide, read 2026-11-04):
  * historical event odds: 10 x [unique markets RETURNED] x [regions]; an
    empty response costs nothing. Six core markets on region us: at most 60
    credits a game (70 with --kickers).
  * historical events (to find the provider's event ids): 1 credit a call,
    nothing when no event is found. One call per week (the listing at the
    week's first snapshot holds every game still to come that week), plus
    one per kickoff slot whose games that listing missed.
`estimate` prints the exact maximum from the schedule; the actual bill is
lower by 10 for every market the provider has no line for. `run` never
spends past --budget: before each call it checks that the call's maximum
still fits, and it stops at the provider's 401/402/403 or when
x-requests-remaining would not cover the next call.

Rules: the key comes from `gridiron.config` (GRIDIRON_ODDS_API_KEY, rule #9)
and is never printed (every message passes `pull_props.scrub`). LOCAL only:
refused inside GitHub Actions (public logs, and the raw files must never be
published, owner decision 2). Resumable: a game that already has a raw
snapshot is never bought again. Logs print counts and credits only.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
import urllib.parse
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import props as P

_HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("pull_props", _HERE / "pull_props.py")
pp = sys.modules.get("pull_props")
if pp is None:
    pp = importlib.util.module_from_spec(_spec)
    sys.modules["pull_props"] = pp
    _spec.loader.exec_module(pp)

#: Player props exist in the provider's history from this instant.
PROPS_FROM = datetime(2023, 5, 3, 5, 30, tzinfo=timezone.utc)
FIRST_SEASON = 2023
#: The snapshot is the provider's latest one at or before kickoff minus this.
#: The live collector snaps 0-150 minutes ahead on an hourly cron; 120 is the
#: middle of where its snapshots land.
LEAD_MINUTES = 120
HIST_ODDS_MULTIPLIER = 10
EVENTS_COST = 1
KICKER_MARKETS: tuple[str, ...] = ("player_kicking_points",)


def markets_for(kickers: bool) -> tuple[str, ...]:
    return tuple(P.CORE_MARKETS) + (KICKER_MARKETS if kickers else ())


def game_cost(markets: Sequence[str]) -> int:
    """The most one game's snapshot can cost (every market returned)."""
    return HIST_ODDS_MULTIPLIER * len(markets) * len(P.REGIONS)


def snapshot_time(game: "pp.Game", lead: int = LEAD_MINUTES) -> datetime:
    return game.kickoff - timedelta(minutes=lead)


def hist_events_url(key: str, at: datetime) -> str:
    q = urllib.parse.urlencode({"apiKey": key, "date": _iso(at)})
    return f"{pp.HOST}/v4/historical/sports/{pp.SPORT}/events?{q}"


def hist_event_odds_url(key: str, event_id: str, at: datetime, markets: Sequence[str]) -> str:
    if not pp._EVENT_ID.match(event_id):
        raise pp.OddsApiError(None, "an event id that is not a plain identifier")
    q = urllib.parse.urlencode({"apiKey": key, "date": _iso(at), "regions": ",".join(P.REGIONS),
                                "markets": ",".join(markets), "oddsFormat": "american"})
    return f"{pp.HOST}/v4/historical/sports/{pp.SPORT}/events/{event_id}/odds?{q}"


def _iso(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------- the games

def backfill_games(schedules: Mapping[int, object], now: datetime,
                   lead: int = LEAD_MINUTES) -> list["pp.Game"]:
    """Every timed regular-season game of weeks 1-17, 2023 on, whose snapshot
    instant is after PROPS_FROM and whose kickoff has passed."""
    out = []
    for season in sorted(schedules):
        games, _untimed = pp.season_games(schedules[season], int(season))
        out += [g for g in games if g.kickoff < now and snapshot_time(g, lead) >= PROPS_FROM]
    return sorted(out, key=lambda g: (g.kickoff, g.game_id))


def have_raw(raw_root: Path, game: "pp.Game") -> bool:
    d = pp.raw_dir(raw_root, game.season, game.week)
    return d.is_dir() and any(d.glob(f"{game.game_id}__*.json"))


@dataclass
class Estimate:
    markets: tuple[str, ...]
    by_season: dict[int, dict] = field(default_factory=dict)

    @property
    def games(self) -> int:
        return sum(v["games"] for v in self.by_season.values())

    @property
    def max_odds(self) -> int:
        return self.games * game_cost(self.markets)

    @property
    def weeks(self) -> int:
        return sum(v["weeks"] for v in self.by_season.values())

    @property
    def slots(self) -> int:
        return sum(v["slots"] for v in self.by_season.values())


def estimate(games: Sequence["pp.Game"], markets: Sequence[str],
             raw_root: Path | None = None, lead: int = LEAD_MINUTES) -> Estimate:
    """The exact maximum the backfill can cost, from the schedule alone.
    Games already on disk (raw_root) are not counted."""
    est = Estimate(tuple(markets))
    todo = [g for g in games if raw_root is None or not have_raw(raw_root, g)]
    for season in sorted({g.season for g in todo}):
        gs = [g for g in todo if g.season == season]
        est.by_season[season] = {"games": len(gs),
                                 "weeks": len({g.week for g in gs}),
                                 "slots": len({snapshot_time(g, lead) for g in gs}),
                                 "first_week": min(g.week for g in gs),
                                 "last_week": max(g.week for g in gs)}
    return est


def estimate_lines(est: Estimate) -> list[str]:
    per = game_cost(est.markets)
    out = [f"[backfill] markets: {len(est.markets)} ({', '.join(est.markets)}), "
           f"regions: {len(P.REGIONS)}, snapshot {LEAD_MINUTES} min before kickoff"]
    for s, v in est.by_season.items():
        out.append(f"[backfill] {s}: {v['games']} games, weeks {v['first_week']}-{v['last_week']}, "
                   f"max {v['games'] * per:,} credits for odds + {v['weeks']} for event lists")
    lo = est.max_odds + est.weeks
    hi = est.max_odds + est.slots
    out.append(f"[backfill] {est.games} games x {per} = {est.max_odds:,} credits at most for odds")
    out.append(f"[backfill] event lists: {est.weeks} (one a week) to {est.slots} (one per kickoff slot)")
    out.append(f"[backfill] TOTAL at most {lo:,} credits (worst case {hi:,}); "
               f"10 less for every market a game has no line in")
    return out


# --------------------------------------------------------------- the run

@dataclass
class Report:
    budget: int
    spent: int = 0
    events_calls: int = 0
    snapshots: int = 0
    empty: int = 0
    skipped_have: int = 0
    unmatched: int = 0
    stopped: str = ""
    remaining: int | None = None
    markets_returned: Counter = field(default_factory=Counter)
    errors: Counter = field(default_factory=Counter)

    def lines(self) -> list[str]:
        out = [f"[backfill] spent {self.spent:,} of a {self.budget:,} budget "
               f"({self.events_calls} event list(s), {self.snapshots} snapshot(s), "
               f"{self.empty} empty), provider says {self.remaining} left",
               f"[backfill] skipped (already on disk) {self.skipped_have}; "
               f"no provider event {self.unmatched}"]
        if self.markets_returned:
            mean = sum(k * v for k, v in self.markets_returned.items()) / max(1, self.snapshots)
            out.append(f"[backfill] markets returned per snapshot: mean {mean:.2f} "
                       f"({dict(sorted(self.markets_returned.items()))})")
        if self.errors:
            out.append("[backfill] errors: " + ", ".join(f"{k} x{v}" for k, v in self.errors.items()))
        if self.stopped:
            out.append(f"[backfill] stopped: {self.stopped}")
        return out


def _cost_from_headers(headers: Mapping[str, str], fallback: int) -> int:
    v = pp._int(headers.get("x-requests-last"))
    return fallback if v is None else v


def _data(resp: object):
    return resp.get("data") if isinstance(resp, Mapping) else None


def run(*, key: str, games: Sequence["pp.Game"], budget: int, markets: Sequence[str],
        raw_root: Path = P.RAW_ROOT, fetch: Callable | None = None,
        sleep: Callable[[float], None] = time.sleep, max_games: int | None = None,
        lead: int = LEAD_MINUTES, now: datetime | None = None) -> Report:
    fetch = fetch or pp.http_fetch
    now = now or datetime.now(timezone.utc)
    rep = Report(budget=int(budget))
    per = game_cost(markets)
    todo = []
    for g in games:
        if have_raw(raw_root, g):
            rep.skipped_have += 1
        else:
            todo.append(g)
    if max_games is not None:
        todo = todo[:max(0, int(max_games))]
    ids: dict[str, str] = {}
    listed_at: set[datetime] = set()

    def can_spend(n: int) -> bool:
        if rep.spent + n > rep.budget:
            rep.stopped = rep.stopped or "the next call's maximum would pass --budget"
            return False
        if rep.remaining is not None and rep.remaining < n:
            rep.stopped = rep.stopped or "the provider's remaining credits would not cover the next call"
            return False
        return True

    def listing(at: datetime, want: Sequence["pp.Game"]) -> bool:
        if at in listed_at or not can_spend(EVENTS_COST):
            return at in listed_at
        listed_at.add(at)
        try:
            resp, headers = fetch(hist_events_url(key, at))
        except pp.OddsApiError as exc:
            rep.errors[f"events: {pp.scrub(exc, key)}"] += 1
            if exc.status in pp.STOP_CODES:
                rep.stopped = f"HTTP {exc.status} on the event list (a free key, or no paid plan)"
            return True
        rep.events_calls += 1
        rep.spent += _cost_from_headers(headers, EVENTS_COST if _data(resp) else 0)
        rep.remaining = pp._int(headers.get("x-requests-remaining"))
        found, _disagree = pp.match_events(_data(resp), want)
        ids.update(found)
        return True

    for i, g in enumerate(todo):
        if rep.stopped:
            break
        at = snapshot_time(g, lead)
        if g.game_id not in ids:
            week_games = [x for x in todo if x.season == g.season and x.week == g.week]
            first = min(snapshot_time(x, lead) for x in week_games)
            listing(first, week_games)
            if g.game_id not in ids and not rep.stopped:
                listing(at, [x for x in week_games if snapshot_time(x, lead) == at])
        if rep.stopped:
            break
        event_id = ids.get(g.game_id)
        if event_id is None:
            rep.unmatched += 1
            continue
        if not can_spend(per):
            break
        if i:
            sleep(pp.PAUSE_SECONDS)
        try:
            resp, headers = fetch(hist_event_odds_url(key, event_id, at, markets))
        except pp.OddsApiError as exc:
            rep.errors[f"event odds: {pp.scrub(exc, key)}"] += 1
            if exc.status in pp.STOP_CODES:
                rep.stopped = f"HTTP {exc.status} on event odds"
            continue
        payload = _data(resp)
        returned = len({m.get("key") for b in (payload or {}).get("bookmakers", []) or []
                        for m in (b.get("markets") or []) if isinstance(m, Mapping)})
        rep.spent += _cost_from_headers(headers, HIST_ODDS_MULTIPLIER * returned * len(P.REGIONS))
        rep.remaining = pp._int(headers.get("x-requests-remaining"))
        if not pp._has_odds(payload):
            rep.empty += 1
            continue
        rep.markets_returned[returned] += 1
        taken = pp._meta_time(resp, "timestamp") or at
        pp.write_raw(raw_root, g, taken, payload, {
            "game_id": g.game_id, "season": g.season, "week": g.week,
            "kickoff": g.kickoff.isoformat(), "taken_at": taken.isoformat(),
            "event_id": event_id, "markets_requested": list(markets),
            "regions": list(P.REGIONS), "source": "historical", "lead_minutes": int(lead),
            "requested_at": now.isoformat(timespec="seconds")})
        rep.snapshots += 1
    return rep


# --------------------------------------------------------------- the CLI

def load_schedules(seasons: Sequence[int]) -> dict[int, object]:
    import nflreadpy as nfl
    return {int(s): nfl.load_schedules([int(s)]).to_pandas() for s in seasons}


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("command", nargs="?", default="estimate", choices=("estimate", "run"))
    ap.add_argument("--seasons", default=None, help="e.g. 2024 or 2023-2025 (default: 2023 to now)")
    ap.add_argument("--kickers", action="store_true", help="also buy player_kicking_points (+10 a game)")
    ap.add_argument("--budget", type=int, default=None, help="run: the most credits it may spend")
    ap.add_argument("--max-games", type=int, default=None, help="run: buy at most this many games")
    ap.add_argument("--lead", type=int, default=LEAD_MINUTES, help="minutes before kickoff")
    ap.add_argument("--raw-root", type=Path, default=P.RAW_ROOT)
    args = ap.parse_args(argv)
    now = datetime.now(timezone.utc)
    if args.seasons:
        a, _, b = args.seasons.partition("-")
        seasons = list(range(int(a), int(b or a) + 1))
    else:
        seasons = list(range(FIRST_SEASON, now.year + 1))
    markets = markets_for(args.kickers)
    games = backfill_games(load_schedules(seasons), now, args.lead)
    est = estimate(games, markets, args.raw_root, args.lead)
    for line in estimate_lines(est):
        print(line)
    if args.command == "estimate":
        return 0
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print("[backfill] refused: local only (public logs; raw files are never published)")
        return 2
    if args.budget is None:
        print("[backfill] refused: `run` needs --budget (the most credits it may spend)")
        return 2
    from gridiron.config import get_settings
    key = get_settings().odds_api_key
    if not key:
        print("[backfill] refused: no GRIDIRON_ODDS_API_KEY (rule #9: .env or the environment)")
        return 2
    rep = run(key=key, games=games, budget=args.budget, markets=markets,
              raw_root=args.raw_root, max_games=args.max_games, lead=args.lead, now=now)
    for line in rep.lines():
        print(pp.scrub(line, key))
    print("[backfill] next: pull_props.py rederive --season <Y>; odds_names.py propose --week <N>")
    return 1 if rep.stopped.startswith("HTTP") else 0


if __name__ == "__main__":
    sys.exit(main())
