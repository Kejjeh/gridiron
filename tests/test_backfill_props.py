"""The paid-history backfill (scripts/ingest/backfill_props.py) on synthetic
schedules and a fake provider: no network, no key, no cache."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gridiron import props as P

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
KEY = "SECRET-key_0dds+/=9"
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=UTC)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BF = _load("backfill_props", "scripts/ingest/backfill_props.py")
PP = BF.pp


def schedule(season=2026) -> pd.DataFrame:
    rows = [(f"{season}_08_BUF_KC", 8, "2026-10-25", "13:00", "BUF", "KC"),
            (f"{season}_08_NYJ_MIA", 8, "2026-10-25", "13:00", "NYJ", "MIA"),
            (f"{season}_08_SF_SEA", 8, "2026-10-25", "16:25", "SF", "SEA"),
            (f"{season}_09_KC_BUF", 9, "2026-11-08", "13:00", "KC", "BUF")]   # not played yet
    return pd.DataFrame([{"game_id": g, "season": season, "game_type": "REG", "week": w,
                          "gameday": d, "gametime": t, "away_team": a, "home_team": h}
                         for g, w, d, t, a, h in rows])


NAMES = {"KC": "Kansas City Chiefs", "BUF": "Buffalo Bills", "MIA": "Miami Dolphins",
         "NYJ": "New York Jets", "SEA": "Seattle Seahawks", "SF": "San Francisco 49ers"}


def event(eid, home, away, kick):
    return {"id": eid, "home_team": NAMES[home], "away_team": NAMES[away],
            "commence_time": kick.strftime("%Y-%m-%dT%H:%M:%SZ")}


class FakeApi:
    """History as the v4 guide describes it: each response wrapped in
    {timestamp, data}; event odds cost 10 x markets returned x regions."""

    def __init__(self, games, *, markets_per_game=6, remaining=1000, fail=None):
        self.games = {g.game_id: g for g in games}
        self.markets_per_game = markets_per_game
        self.remaining = remaining
        self.fail = fail
        self.calls = []

    def __call__(self, url):
        assert KEY in url or "apiKey" in url
        self.calls.append(url)
        if self.fail:
            raise PP.OddsApiError(self.fail, "HTTPError", {})
        if "/events?" in url:
            data = [event(f"ev{g.game_id[-6:].replace('_', '')}", g.home, g.away, g.kickoff)
                    for g in self.games.values()]
            cost = 1
            body = {"timestamp": "2026-10-25T15:00:00Z", "data": data}
        else:
            eid = url.split("/events/")[1].split("/odds")[0]
            g = next(g for g in self.games.values() if f"ev{g.game_id[-6:].replace('_', '')}" == eid)
            markets = [{"key": m, "outcomes": [{"name": "Over", "description": "Pat Fixture",
                                                "price": -110, "point": 1.5},
                                               {"name": "Under", "description": "Pat Fixture",
                                                "price": -110, "point": 1.5}]}
                       for m in P.CORE_MARKETS[:self.markets_per_game]]
            books = [{"key": "book_alpha", "title": "Alpha", "markets": markets}] if markets else []
            cost = 10 * len(markets)
            body = {"timestamp": (g.kickoff - timedelta(minutes=122)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "data": {**event(eid, g.home, g.away, g.kickoff), "bookmakers": books}}
        self.remaining -= cost
        return body, {"x-requests-remaining": str(self.remaining), "x-requests-last": str(cost)}


def games():
    return BF.backfill_games({2026: schedule()}, NOW)


def test_only_played_games_after_props_history_starts_are_counted():
    gs = games()
    assert [g.game_id for g in gs] == ["2026_08_BUF_KC", "2026_08_NYJ_MIA", "2026_08_SF_SEA"]
    old = BF.backfill_games({2022: schedule(2022).assign(gameday="2022-10-25")}, NOW)
    assert old == []                       # before 2023-05-03: the provider has no props


def test_the_estimate_is_the_exact_maximum():
    est = BF.estimate(games(), BF.markets_for(False))
    assert est.games == 3 and est.max_odds == 3 * 60 and est.weeks == 1 and est.slots == 2
    assert BF.game_cost(BF.markets_for(True)) == 70
    text = "\n".join(BF.estimate_lines(est))
    assert "TOTAL at most 181 credits (worst case 182)" in text


def test_a_run_stores_raw_snapshots_where_the_collector_reads_them(tmp_path):
    gs = games()
    api = FakeApi(gs)
    rep = BF.run(key=KEY, games=gs, budget=1000, markets=BF.markets_for(False),
                 raw_root=tmp_path, fetch=api, sleep=lambda s: None, now=NOW)
    assert rep.snapshots == 3 and rep.events_calls == 1 and rep.spent == 181
    snaps = P.read_raw_snapshots(tmp_path, 2026)
    assert sorted(g for (_w, g) in snaps) == [g.game_id for g in gs]
    blob = json.loads(next(tmp_path.rglob("*BUF_KC__*.json")).read_text())
    assert blob["meta"]["source"] == "historical" and blob["meta"]["lead_minutes"] == 120
    assert blob["payload"]["bookmakers"]                     # the unwrapped event, as live
    # resumable: a second run buys nothing
    again = BF.run(key=KEY, games=gs, budget=1000, markets=BF.markets_for(False),
                   raw_root=tmp_path, fetch=FakeApi(gs), sleep=lambda s: None, now=NOW)
    assert again.snapshots == 0 and again.skipped_have == 3 and again.spent == 0


def test_the_budget_is_never_passed(tmp_path):
    gs = games()
    rep = BF.run(key=KEY, games=gs, budget=125, markets=BF.markets_for(False),
                 raw_root=tmp_path, fetch=FakeApi(gs), sleep=lambda s: None, now=NOW)
    assert rep.spent <= 125 and rep.snapshots == 2 and "budget" in rep.stopped


def test_missing_markets_cost_less_and_are_counted(tmp_path):
    gs = games()
    rep = BF.run(key=KEY, games=gs, budget=1000, markets=BF.markets_for(False),
                 raw_root=tmp_path, fetch=FakeApi(gs, markets_per_game=4),
                 sleep=lambda s: None, now=NOW)
    assert rep.spent == 1 + 3 * 40 and rep.markets_returned == {4: 3}


def test_a_free_key_stops_at_the_first_refusal_and_never_prints_the_key(tmp_path):
    gs = games()
    rep = BF.run(key=KEY, games=gs, budget=1000, markets=BF.markets_for(False),
                 raw_root=tmp_path, fetch=FakeApi(gs, fail=401), sleep=lambda s: None, now=NOW)
    assert rep.snapshots == 0 and rep.stopped.startswith("HTTP 401")
    assert KEY not in "\n".join(rep.lines())
    assert not any(tmp_path.rglob("*.json"))


def test_urls_carry_the_date_and_the_key_only_as_a_parameter():
    g = games()[0]
    u = BF.hist_event_odds_url(KEY, "abc123", BF.snapshot_time(g), BF.markets_for(False))
    assert "/v4/historical/sports/americanfootball_nfl/events/abc123/odds?" in u
    assert "date=2026-10-25T15%3A00%3A00Z" in u and "regions=us" in u


def test_the_cli_refuses_inside_actions_and_without_a_budget(monkeypatch, capsys):
    monkeypatch.setattr(BF, "load_schedules", lambda seasons: {2026: schedule()})
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert BF.main(["run", "--budget", "10", "--seasons", "2026"]) == 2
    monkeypatch.delenv("GITHUB_ACTIONS")
    assert BF.main(["run", "--seasons", "2026"]) == 2
    out = capsys.readouterr().out
    assert "local only" in out and "needs --budget" in out
