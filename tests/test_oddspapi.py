"""gridiron.oddspapi: offline. Fake transport, fake clock — no network."""
from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from gridiron import oddspapi as op

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk_live_SECRET123"


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.t += s


def make_client(responses):
    calls: list[str] = []
    it = iter(responses)
    clock = FakeClock()

    def opener(url, timeout):
        calls.append(url)
        return next(it)

    c = op.Client(KEY, opener=opener, sleep=clock.sleep, clock=clock)
    return c, calls, clock


def test_missing_key_is_refused():
    with pytest.raises(op.OddsPapiError):
        op.Client("  ")


def test_errors_never_contain_the_key():
    body = json.dumps({"error": {"code": "BAD", "message": f"bad key {KEY}"}}).encode()
    c, _, _ = make_client([(400, body)])
    with pytest.raises(op.OddsPapiError) as exc:
        c.sports()
    assert KEY not in str(exc.value)
    assert "***" in str(exc.value)


def test_transport_exceptions_are_redacted():
    clock = FakeClock()

    def opener(url, timeout):
        raise OSError(f"connect failed for {url}")

    c = op.Client(KEY, opener=opener, sleep=clock.sleep, clock=clock, max_retries=1)
    with pytest.raises(op.OddsPapiError) as exc:
        c.sports()
    assert KEY not in str(exc.value)


def test_quota_exhaustion_stops_without_retry():
    body = json.dumps({"error": {"code": "REQUEST_LIMIT_EXCEEDED"}}).encode()
    c, calls, _ = make_client([(429, body)])
    with pytest.raises(op.QuotaExhausted):
        c.markets()
    assert len(calls) == 1


def test_transient_429_is_retried():
    c, calls, _ = make_client([(429, b"{}"), (200, b"[]")])
    assert c.sports() == []
    assert len(calls) == 2


def test_historical_calls_respect_the_5s_cooldown():
    c, calls, clock = make_client([(200, b"{}"), (200, b"{}")])
    c.historical_odds("f1", ["pinnacle"])
    c.historical_odds("f2", ["pinnacle"])
    assert sum(clock.sleeps) >= op.COOLDOWN_S["historical-odds"]
    assert "bookmakers=pinnacle" in calls[0]


def test_at_most_three_bookmakers_per_historical_call():
    c, _, _ = make_client([])
    with pytest.raises(ValueError):
        c.historical_odds("f", ["a", "b", "c", "d"])


def test_discovery_helpers():
    assert op.find_american_football(
        [{"sportId": 10, "slug": "soccer"}, {"sportId": 14, "slug": "american-football"}]) == 14
    assert op.find_nfl([{"tournamentId": 1, "tournamentName": "NCAA"},
                        {"tournamentId": 7, "tournamentSlug": "nfl", "tournamentName": "NFL"}]) == 7
    with pytest.raises(op.OddsPapiError):
        op.find_nfl([{"tournamentId": 1, "tournamentName": "CFL"}])


MARKETS = [
    {"marketId": 500, "marketName": "Player Receiving Yards", "playerProp": True,
     "sportId": 14, "handicap": 54.5, "outcomes": [{"outcomeId": 501, "outcomeName": "Over"},
                                                   {"outcomeId": 502, "outcomeName": "Under"}]},
    {"marketId": 101, "marketName": "Moneyline", "playerProp": False, "sportId": 14,
     "handicap": 0, "outcomes": []},
    {"marketId": 900, "marketName": "Player Points", "playerProp": True, "sportId": 11,
     "handicap": 20.5, "outcomes": []},
]

PAYLOAD = {"fixtureId": "id1", "bookmakers": {"pinnacle": {"markets": {
    "500": {"outcomes": {"501": {"players": {"8812": [
        {"createdAt": "2026-10-04T12:00:00+00:00", "price": 1.87, "limit": 500, "active": False},
        {"createdAt": "2026-10-04T16:00:00+00:00", "price": 1.80, "limit": 900, "active": True},
    ]}}}},
    "101": {"outcomes": {"101": {"players": {"0": [{"price": 1.5}]}}}},
}}}}

FIXTURE = {"fixtureId": "id1", "startTime": "2026-10-04T17:00:00Z",
           "participant1Name": "Home", "participant2Name": "Away"}


def test_prop_catalogue_is_sport_scoped():
    props = op.prop_markets(MARKETS, 14)
    assert list(props) == [500]
    assert props[500]["outcomes"] == {501: "Over", 502: "Under"}


def test_flatten_keeps_only_prop_markets_and_provider_ids():
    rows = op.flatten_props(PAYLOAD, FIXTURE, op.prop_markets(MARKETS, 14), {"8812": "A Player"})
    assert len(rows) == 2
    r = rows[0]
    assert list(r) == op.PROP_COLUMNS
    assert r["market_id"] == 500 and r["line"] == 54.5 and r["outcome_name"] == "Over"
    assert r["oddspapi_player_id"] == "8812" and r["player_name"] == "A Player"
    assert r["season"] == 2026


def test_names_only_by_id_never_by_text():
    rows = op.flatten_props(PAYLOAD, FIXTURE, op.prop_markets(MARKETS, 14), {"A Player": "x"})
    assert rows[0]["player_name"] is None


@pytest.mark.parametrize("start,season", [("2026-01-18T20:00:00Z", 2025),
                                          ("2026-02-08T23:30:00Z", 2025),
                                          ("2026-09-10T00:20:00Z", 2026)])
def test_postseason_games_belong_to_the_prior_season(start, season):
    assert op.nfl_season(start) == season


def test_summary_is_counts_only():
    s = op.summarize(PAYLOAD, op.prop_markets(MARKETS, 14))
    assert s == {"bookmakers": {"pinnacle": {"markets": 2, "prop_markets": 1}},
                 "prop_markets_seen": 1, "players": 1, "entries": 2}


# ----------------------------------------------------------------- the script

def _load_script():
    spec = importlib.util.spec_from_file_location(
        "backfill_oddspapi_props", ROOT / "scripts/ingest/backfill_oddspapi_props.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


SCRIPT = _load_script()


@pytest.mark.parametrize("raw", ["pinnacle;rm -rf /", "$(id)", "a b", "", "PIN,"])
def test_bookmaker_input_is_data(raw):
    with pytest.raises(SystemExit):
        SCRIPT.parse_books(raw)


def test_bookmaker_input_dedupes():
    assert SCRIPT.parse_books(" Pinnacle,fanduel,pinnacle ") == ["pinnacle", "fanduel"]


@pytest.mark.parametrize("raw", ["2026-13-01", "2026/01/01", "1; ls"])
def test_date_input_is_data(raw):
    with pytest.raises((SystemExit, ValueError)):
        SCRIPT.parse_day(raw, "2026-01-01")


class _Fixtures:
    def __init__(self, answers):
        self.answers = list(answers)

    def fixtures(self, *_a, **_k):
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return a


def test_an_empty_fixture_window_404_is_skipped_not_fatal():
    nf = op.OddsPapiError("fixtures: HTTP 404", 404, "NO_FIXTURES")
    got = SCRIPT.fetch_fixtures(_Fixtures([nf, [{"fixtureId": "x", "startTime": "2026-09-10"}]]),
                                31, "2026-08-01", "2026-09-15")
    assert [f["fixtureId"] for f in got] == ["x"]


def test_all_windows_empty_is_an_error_not_a_silent_zero():
    nf = op.OddsPapiError("fixtures: HTTP 404", 404, None)
    with pytest.raises(op.OddsPapiError):
        SCRIPT.fetch_fixtures(_Fixtures([nf, nf]), 31, "2026-04-01", "2026-05-15")


def test_other_fixture_errors_still_raise():
    with pytest.raises(op.OddsPapiError):
        SCRIPT.fetch_fixtures(_Fixtures([op.OddsPapiError("x", 500)]), 31,
                              "2026-04-01", "2026-04-10")


def test_build_long_from_cached_raw(tmp_path):
    (tmp_path / "raw").mkdir()
    with gzip.open(tmp_path / "raw" / "id1__pinnacle.json.gz", "wt", encoding="utf-8") as fh:
        json.dump(PAYLOAD, fh)
    (tmp_path / "participants.json").write_text(json.dumps({"8812": "A Player"}), "utf-8")
    n = SCRIPT.build_long(tmp_path, [FIXTURE], op.prop_markets(MARKETS, 14))
    assert n == 2
    summary = json.loads((tmp_path / "summary.json").read_text("utf-8"))
    assert summary["players"] == 1 and summary["named_players"] == 1


def test_workflow_binds_the_secret_through_env_only():
    text = (ROOT / ".github/workflows/oddspapi-backfill.yml").read_text("utf-8")
    assert "GRIDIRON_ODDSPAPI_KEY: ${{ secrets.GRIDIRON_ODDSPAPI_KEY }}" in text
    triggers = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch" in triggers
    assert "pull_request" not in triggers and "schedule" not in triggers
