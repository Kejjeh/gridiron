"""Kalshi prop backfill (scripts/ingest/kalshi_props.py) on synthetic data and
a fake API: no network. Fictional players only."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _load():
    spec = importlib.util.spec_from_file_location("kalshi_props", ROOT / "scripts/ingest/kalshi_props.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["kalshi_props"] = mod
    spec.loader.exec_module(mod)
    return mod


K = _load()
KICK = datetime(2026, 11, 3, 1, 15, tzinfo=UTC)          # Monday night, 20:15 ET on Nov 2
GAME = K.Game("2026_08_CHI_SEA", 2026, 8, "CHI", "SEA", date(2026, 11, 2), KICK)
OTHER = K.Game("2026_08_LA_JAX", 2026, 8, "LA", "JAX", date(2026, 11, 2), KICK - timedelta(hours=7))


def market(ticker, title, strike, open_time, result="yes"):
    return {"ticker": ticker, "event_ticker": ticker.rsplit("-", 2)[0], "title": title,
            "floor_strike": strike, "strike_type": "greater", "open_time": open_time,
            "close_time": "2026-11-03T04:40:00Z", "result": result, "volume_fp": "100.0"}


MARKETS = [market("KXNFLRSHYDS-26NOV02CHISEA-SEAPFIXTURE20-40", "Pat Fixture: 40+ rushing yards", 39.5,
                  "2026-11-01T12:00:00Z"),
           market("KXNFLRSHYDS-26NOV02CHISEA-SEAPFIXTURE20-60", "Pat Fixture: 60+ rushing yards", 59.5,
                  "2026-11-01T12:00:00Z", "no"),
           market("KXNFLRSHYDS-26NOV02CHISEA-CHIWFIXTURE11-5", "Wes Fixture: 5+ rushing yards", 4.5,
                  "2026-11-03T02:49:00Z")]                     # opened during the game


class FakeKalshi:
    def __init__(self, fail=None):
        self.paths = []
        self.fail = fail
        self.archived = []

    def __call__(self, path):
        self.paths.append(path)
        if self.fail:
            raise K.KalshiError(self.fail, "HTTPError")
        if path.startswith("/markets?"):
            return {"markets": MARKETS, "cursor": ""}
        if path.startswith("/historical/markets?"):
            return {"markets": [], "cursor": ""}
        if path.startswith("/historical/cutoff"):
            return {"market_settled_ts": "2026-09-04T00:00:00Z"}
        if path.startswith("/historical/markets/") and "/candlesticks" in path:
            self.archived.append(path)
            return {"candlesticks": self._candles()}
        if path.startswith("/markets/candlesticks?"):
            return {"markets": [{"market_ticker": t, "candlesticks": self._candles()}
                for t in path.split("market_tickers=")[1].split("&")[0].replace("%2C", ",").split(",")]}
        raise AssertionError(path)

    def _candles(self, kick=None):
        snap = int(((kick or KICK) - timedelta(minutes=120)).timestamp())
        return [
                {"end_period_ts": snap - 3600, "yes_bid": {"close_dollars": "0.40"},
                 "yes_ask": {"close_dollars": "0.44"}, "price": {"close_dollars": "0.42"}},
                {"end_period_ts": snap, "yes_bid": {"close_dollars": "0.50"},
                 "yes_ask": {"close_dollars": "0.54"}, "price": {"close_dollars": "0.52"}},
                {"end_period_ts": snap + 3600, "yes_bid": {"close": "0.90"},       # after: never read
                 "yes_ask": {"close": "0.95"}, "price": {"close": "0.93"}}]


def client(api):
    return K.Client(fetch=api, sleep=lambda s: None, pause=0)


def test_event_tickers_parse_and_match_one_game():
    assert K.event_date_teams("KXNFLRSHYDS-26NOV02CHISEA") == (date(2026, 11, 2), "CHISEA")
    assert K.event_date_teams("garbage") is None
    assert K.match_game("KXNFLREC-26NOV02CHISEA", [GAME, OTHER]) is GAME
    assert K.match_game("KXNFLREC-26NOV02LARJAC", [GAME, OTHER]) is OTHER      # Kalshi spellings
    assert K.match_game("KXNFLREC-26NOV09CHISEA", [GAME]) is None             # another day


def test_the_player_team_comes_from_the_games_two_teams():
    assert K.player_team("SEAPFIXTURE20", GAME) == "SEA"
    assert K.player_team("CHIWFIXTURE11", GAME) == "CHI"
    assert K.player_team("NYJXFIXTURE1", GAME) is None


def test_the_snapshot_price_is_the_last_candle_at_or_before_it():
    at = KICK - timedelta(minutes=120)
    cs = FakeKalshi()(f"/markets/candlesticks?market_tickers=X&start_ts=0")["markets"][0]["candlesticks"]
    p = K.snapshot_price(cs, at)
    assert (p["bid"], p["ask"], p["last"]) == (0.50, 0.54, 0.52) and abs(p["mid"] - 0.52) < 1e-9
    assert K.snapshot_price(cs, at - timedelta(hours=5)) is None


def test_fetch_writes_one_private_file_per_game_and_resumes(tmp_path):
    api = FakeKalshi()
    rep = K.fetch(client=client(api), games=[GAME, OTHER], series=("KXNFLRSHYDS",), root=tmp_path)
    assert rep.games_done == 1 and rep.games_no_markets == 1 and rep.priced == 2
    assert rep.opened_after_snapshot == 1                    # the in-game ladder has no pre-game price
    blob = json.loads(K.game_path(tmp_path, GAME).read_text())
    rows = {r["ticker"].rsplit("-", 1)[1]: r for r in blob["markets"]}
    assert rows["40"]["snapshot"]["bid"] == 0.50 and rows["40"]["team"] == "SEA"
    assert rows["40"]["threshold"] == 39.5 and rows["60"]["result"] == "no"
    assert rows["5"]["snapshot"] is None and rows["5"]["team"] == "CHI"
    assert "never share" in blob["meta"]["private"]
    again = K.fetch(client=client(FakeKalshi()), games=[GAME, OTHER], series=("KXNFLRSHYDS",),
                    root=tmp_path)
    assert again.games_done == 0 and again.games_skipped_have == 1


def test_rate_limits_back_off_then_give_up_cleanly(tmp_path):
    rep = K.fetch(client=client(FakeKalshi(fail=429)), games=[GAME], series=("KXNFLRSHYDS",),
                  root=tmp_path)
    assert rep.stopped.startswith("listing KXNFLRSHYDS") and rep.games_done == 0
    assert not any(tmp_path.rglob("*.json"))


def test_the_cli_refuses_inside_actions(monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert K.main(["fetch"]) == 2
    assert "local only" in capsys.readouterr().out


def test_markets_settled_before_the_archive_cutoff_use_the_per_market_endpoint(tmp_path):
    old = K.Game("2025_06_PHI_NYG", 2025, 6, "PHI", "NYG", date(2025, 10, 9),
                 datetime(2025, 10, 10, 0, 15, tzinfo=UTC))
    global MARKETS
    keep = MARKETS
    try:
        MARKETS = [{**market("KXNFLRSHYDS-25OCT09PHINYG-NYGPFIXTURE20-40", "Pat Fixture: 40+ rushing yards",
                             39.5, "2025-10-08T04:00:00Z"), "close_time": "2025-10-10T03:57:00Z"}]
        api = FakeKalshi()
        rep = K.fetch(client=client(api), games=[old], series=("KXNFLRSHYDS",), root=tmp_path)
    finally:
        MARKETS = keep
    assert rep.games_done == 1 and rep.archived_calls == 1 and len(api.archived) == 1
    assert not any(p.startswith("/markets/candlesticks?") for p in api.paths)
