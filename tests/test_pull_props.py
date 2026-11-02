"""The prop collector (`scripts/ingest/pull_props.py`), offline.

Pins: the key never reaches a log, a file, an exception or a URL that is
printed; the due rule; the credit ledger and its reserve; one snapshot per
game; raw responses stay under raw/ and only derived values under derived/.
A fake transport stands in for the provider; nothing here touches the
network. Fictional players, fictional books, fictional ids.
"""
from __future__ import annotations

import email.message
import http.client
import importlib.util
import io
import json
import sys
import urllib.error
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from gridiron import props as P

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
KEY = "SECRET-key_0dds+/=9"
NOW = datetime(2026, 11, 8, 16, 30, tzinfo=UTC)        # 11:30 ET, week 9 Sunday


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PP = _load("pull_props_cli", "scripts/ingest/pull_props.py")

TABLE = P.IdentityTable([P.IdentityRow("Pat Fixture", "KC", "00-0099001", "exact", "2026-11-02"),
                         P.IdentityRow("Wes Fixture", "BUF", "00-0099002", "exact", "2026-11-02")])


def schedule() -> pd.DataFrame:
    rows = [("2026_09_BUF_KC", 9, "2026-11-08", "13:00", "BUF", "KC"),
            ("2026_09_NYJ_MIA", 9, "2026-11-08", "13:00", "NYJ", "MIA"),
            ("2026_09_SF_SEA", 9, "2026-11-08", "16:25", "SF", "SEA"),
            ("2026_09_DAL_NYG", 9, "2026-11-08", None, "DAL", "NYG"),       # untimed
            ("2026_10_KC_BUF", 10, "2026-11-15", "13:00", "KC", "BUF"),
            ("2026_18_KC_LV", 18, "2027-01-10", "13:00", "KC", "LV")]       # past the fantasy season
    return pd.DataFrame([{"game_id": g, "season": 2026, "game_type": "REG", "week": w,
                          "gameday": d, "gametime": t, "away_team": a, "home_team": h}
                         for g, w, d, t, a, h in rows])


def ou(player, over, under, point):
    return [{"name": "Over", "description": player, "price": over, "point": point},
            {"name": "Under", "description": player, "price": under, "point": point}]


def odds_payload(event_id, home, away, *, empty=False):
    books = [] if empty else [{"key": "book_alpha", "title": "Alpha", "markets": [
        {"key": "player_pass_yds", "outcomes": ou("Pat Fixture", -110, -110, 245.5)},
        {"key": "player_reception_yds", "outcomes": ou("Wes Fixture", -110, -110, 61.5)},
        {"key": "player_receptions", "outcomes": ou("Wes Fixture", -110, -110, 4.5)
         + ou("Nobody Fixture", -110, -110, 2.5)},
        {"key": "player_kicking_points", "outcomes": ou("Kip Fixture", -110, -110, 7.5)}]}]
    return {"id": event_id, "commence_time": "2026-11-08T18:00:00Z",
            "home_team": home, "away_team": away, "bookmakers": books}


EVENTS = [{"id": "evtA", "home_team": "Kansas City Chiefs", "away_team": "Buffalo Bills",
           "commence_time": "2026-11-08T18:00:00Z"},
          {"id": "evtB", "home_team": "Miami Dolphins", "away_team": "New York Jets",
           "commence_time": "2026-11-08T18:05:00Z"},
          {"id": "evtC", "home_team": "Seattle Seahawks", "away_team": "San Francisco 49ers",
           "commence_time": "2026-11-08T21:25:00Z"}]


class FakeApi:
    """The provider's two endpoints, charging what the real one charges:
    unique markets RETURNED x regions; the event list is free."""

    def __init__(self, remaining=400, used=100, odds=None, fail=None):
        self.remaining, self.used = remaining, used
        self.calls: list[str] = []
        self.odds = odds or {"evtA": odds_payload("evtA", "Kansas City Chiefs", "Buffalo Bills"),
                             "evtB": odds_payload("evtB", "Miami Dolphins", "New York Jets")}
        self.fail = fail or {}

    def headers(self, last):
        return {"x-requests-remaining": str(self.remaining), "x-requests-used": str(self.used),
                "x-requests-last": str(last)}

    def __call__(self, url):
        self.calls.append(url)
        parts = urllib.parse.urlparse(url)
        q = urllib.parse.parse_qs(parts.query)
        assert q["apiKey"] == [KEY]
        if parts.path.endswith("/events"):
            return EVENTS, self.headers(0)
        event_id = parts.path.split("/")[-2]
        planned = self.fail.get(event_id)
        if isinstance(planned, list) and planned:      # fail these calls, then answer
            raise planned.pop(0)
        if planned is not None and not isinstance(planned, list):
            raise planned
        assert q["regions"] == ["us"] and q["oddsFormat"] == ["american"]
        wanted = set(q["markets"][0].split(","))
        payload = self.odds[event_id]
        cost = len({m["key"] for b in payload["bookmakers"] for m in b["markets"]} & wanted)
        self.remaining -= cost
        self.used += cost
        return payload, self.headers(cost)


def run(tmp_path, api, now=NOW, **kw):
    return PP.collect(key=KEY, season=2026, now=now, schedule=schedule(),
                      schedule_source="test", raw_root=tmp_path / "raw",
                      derived_root=tmp_path / "derived", table=TABLE, fetch=api,
                      sleep=lambda s: None, clock=lambda: now, **kw)


def _all_text(folder: Path) -> str:
    return "\n".join(p.read_text("utf-8", errors="replace") for p in folder.rglob("*")
                     if p.is_file() and p.suffix in (".json", ".csv"))


# ------------------------------------------------------------------ the key

def test_scrub_removes_the_key_in_every_spelling():
    url = PP.event_odds_url(KEY, "evtA", P.CORE_MARKETS)
    assert KEY not in PP.scrub(url, KEY) and "apiKey=***" in PP.scrub(url, KEY)
    quoted = urllib.parse.quote(KEY, safe="")
    assert quoted in url or KEY in url
    assert quoted not in PP.scrub(f"boom {url}", KEY)
    assert PP.scrub("?apiKey=whatever&x=1", None) == "?apiKey=***&x=1"


@pytest.mark.parametrize("failure", ["http", "url", "os", "timeout", "incomplete"])
def test_no_transport_failure_can_carry_the_key(monkeypatch, failure):
    url = PP.events_url(KEY)

    def boom(req, timeout):
        assert KEY in req.full_url or urllib.parse.quote_plus(KEY) in req.full_url
        hdrs = email.message.Message()
        hdrs["x-requests-remaining"] = "12"
        if failure == "http":
            raise urllib.error.HTTPError(req.full_url, 500, f"server error at {url}", hdrs,
                                         io.BytesIO(url.encode()))
        if failure == "url":
            raise urllib.error.URLError(f"cannot reach {url}")
        if failure == "os":
            raise OSError(f"socket closed for {url}")
        if failure == "incomplete":                 # a body cut off mid-read
            raise http.client.IncompleteRead(url.encode())
        raise TimeoutError(url)

    monkeypatch.setattr(PP.urllib.request, "urlopen", boom)
    with pytest.raises(PP.OddsApiError) as info:
        PP.http_fetch(url)
    exc = info.value
    for text in (str(exc), repr(exc), repr(exc.args), json.dumps(exc.headers)):
        assert KEY not in text and "apiKey" not in text
    assert exc.__context__ is None and exc.__cause__ is None
    if failure == "http":
        assert exc.status == 500 and exc.headers == {"x-requests-remaining": "12"}


def test_the_cli_says_no_key_and_collects_nothing(tmp_path, monkeypatch, capsys):
    import gridiron.config as cfg

    class Empty:
        odds_api_key = None
    monkeypatch.setattr(cfg, "get_settings", lambda: Empty())
    assert PP.main(["--derived-root", str(tmp_path / "d"), "--raw-root", str(tmp_path / "r")]) == 0
    assert capsys.readouterr().out.strip() == "no GRIDIRON_ODDS_API_KEY: nothing collected"
    assert not (tmp_path / "r").exists()
    assert PP.summary_lines(tmp_path / "d") == ["- no GRIDIRON_ODDS_API_KEY: nothing collected"]


def test_a_full_run_logs_counts_only_and_never_the_key(tmp_path, monkeypatch, capsys):
    import gridiron.config as cfg

    class Settings:
        odds_api_key = KEY
    monkeypatch.setattr(cfg, "get_settings", lambda: Settings())
    api = FakeApi(fail={"evtB": urllib.error.HTTPError(
        "https://x/?apiKey=" + KEY, 500, f"bad {KEY}", email.message.Message(), None)})

    def fake_fetch(url):
        try:
            return api(url)
        except urllib.error.HTTPError as exc:     # what http_fetch turns it into
            raise PP.OddsApiError(exc.code, "HTTPError") from None
    monkeypatch.setattr(PP, "http_fetch", fake_fetch)
    monkeypatch.setattr(PP, "load_schedule", lambda *a, **k: (schedule(), "test", False))
    monkeypatch.setattr(PP.P.IdentityTable, "read", classmethod(lambda cls, path=None: TABLE))
    out_file = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out_file))
    rc = PP.main(["--now", NOW.isoformat(), "--raw-root", str(tmp_path / "raw"),
                  "--derived-root", str(tmp_path / "derived")])
    assert rc == 0
    printed = capsys.readouterr()
    log = printed.out + printed.err
    for leak in (KEY, "apiKey", "Fixture", "evtA", "book_alpha", "245.5", "-110"):
        assert leak not in log, f"the log leaked {leak!r}"
    assert "1 snapshot(s) taken" in log and "event odds: HTTP 500 x1" in log
    for leak in (KEY, "apiKey"):
        assert leak not in _all_text(tmp_path / "raw") + _all_text(tmp_path / "derived")
    assert "raw_changed=true" in out_file.read_text()
    summary = "\n".join(PP.summary_lines(tmp_path / "derived"))
    assert "snapshots 1" in summary and "Fixture" not in summary and KEY not in summary


def test_unreadable_settings_print_their_type_only(tmp_path, monkeypatch, capsys):
    import gridiron.config as cfg

    def broken():
        raise ValueError(f"input_value={KEY!r}")
    monkeypatch.setattr(cfg, "get_settings", broken)
    assert PP.main(["--derived-root", str(tmp_path / "d")]) == 1
    out = capsys.readouterr()
    assert out.out.strip() == "[props] failed: settings unreadable (ValueError)"
    assert KEY not in out.out + out.err


def test_an_unexpected_failure_prints_its_type_only(tmp_path, monkeypatch, capsys):
    import gridiron.config as cfg

    class Settings:
        odds_api_key = KEY
    monkeypatch.setattr(cfg, "get_settings", lambda: Settings())

    def explode(*a, **k):
        raise RuntimeError(f"Pat Fixture at {PP.events_url(KEY)}")
    monkeypatch.setattr(PP, "load_schedule", explode)
    assert PP.main(["--derived-root", str(tmp_path / "d")]) == 1
    out = capsys.readouterr().out
    assert out.strip() == "[props] failed: RuntimeError (rerun locally with --debug)"


# ------------------------------------------------------------------ schedule and due rule

def test_only_timed_regular_season_games_through_week_17_exist():
    games, untimed = PP.season_games(schedule(), 2026)
    assert untimed == 1
    assert {g.game_id for g in games} == {"2026_09_BUF_KC", "2026_09_NYJ_MIA",
                                          "2026_09_SF_SEA", "2026_10_KC_BUF"}
    kc = next(g for g in games if g.game_id == "2026_09_BUF_KC")
    assert kc.kickoff == datetime(2026, 11, 8, 18, 0, tzinfo=UTC)      # 13:00 ET


def test_a_game_is_due_inside_150_minutes_before_kickoff_and_only_once():
    games, _ = PP.season_games(schedule(), 2026)
    kick = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
    ids = lambda now, snapped=set(): {g.game_id for g in PP.due_games(games, now, snapped)}
    assert ids(kick - timedelta(minutes=150)) == {"2026_09_BUF_KC", "2026_09_NYJ_MIA"}
    assert ids(kick - timedelta(minutes=151)) == set()
    assert ids(kick - timedelta(seconds=1)) == {"2026_09_BUF_KC", "2026_09_NYJ_MIA"}
    assert ids(kick) == set()                                           # 0 < lead
    assert ids(kick - timedelta(minutes=60), {"2026_09_BUF_KC"}) == {"2026_09_NYJ_MIA"}


# ------------------------------------------------------------------ credits

def test_the_ledger_reads_the_headers_and_learns_the_reset_day():
    c = PP.Credits()
    c.record({"x-requests-remaining": "450", "x-requests-used": "50", "x-requests-last": "6"},
             datetime(2026, 11, 20, tzinfo=UTC))
    assert (c.remaining, c.used, c.last, c.reset_day) == (450, 50, 6, 1)
    assert c.next_reset(datetime(2026, 11, 20, tzinfo=UTC)) == datetime(2026, 12, 1, tzinfo=UTC)
    c.record({"x-requests-remaining": "494", "x-requests-used": "6"},
             datetime(2026, 12, 14, 9, tzinfo=UTC))                     # used fell: a reset
    assert c.reset_day == 14 and c.reset_observed == "2026-12-14"
    assert c.next_reset(datetime(2026, 12, 20, tzinfo=UTC)) == datetime(2027, 1, 14, tzinfo=UTC)
    c.record({}, datetime(2026, 12, 21, tzinfo=UTC))                    # no headers: unchanged
    assert c.remaining == 494


@pytest.mark.parametrize("raw,want", [("", None), (None, None), ("15", 15), (" 1 ", 1),
                                      ("28", 28)])
def test_the_owner_set_reset_day_is_a_bare_day(raw, want):
    assert PP.reset_day_override(raw) == (want, "")


@pytest.mark.parametrize("raw", ["0", "29", "31", "15; rm -rf /", "$(id)", "1e1", "fifteen"])
def test_any_other_reset_day_text_is_ignored_and_said(raw):
    day, why = PP.reset_day_override(raw)
    assert day is None and why == "GRIDIRON_PROPS_RESET_DAY ignored: not a day 1-28"
    assert raw not in why


def test_an_owner_set_reset_day_moves_the_reserve_horizon(tmp_path):
    rep = run(tmp_path, FakeApi(), dry_run=True, reset_day=10)
    assert rep.credits.reset_day == 10
    assert rep.credits.next_reset(NOW) == datetime(2026, 11, 10, tzinfo=UTC)
    # only the week-9 games before Nov 10 count: 3 after now (the other two
    # 1 pm games and the late game); week 10's game is past the reset
    assert rep.reserve == 3 * PP.CORE_COST


def test_a_reading_from_before_the_assumed_reset_is_not_trusted():
    c = PP.Credits(remaining=8, used=492, at="2026-11-30T23:00:00+00:00")
    assert c.known(datetime(2026, 11, 30, 23, 30, tzinfo=UTC))
    assert not c.known(datetime(2026, 12, 3, 1, 0, tzinfo=UTC))       # the 1st came between
    assert c.last_reset(datetime(2027, 1, 1, 1, 15, tzinfo=UTC)) == datetime(2027, 1, 1, tzinfo=UTC)
    assert PP.Credits(remaining=8, at="2026-12-20T00:00:00+00:00", reset_day=25).last_reset(
        datetime(2027, 1, 3, tzinfo=UTC)) == datetime(2026, 12, 25, tzinfo=UTC)
    assert not PP.Credits(remaining=8, at="").known(datetime(2026, 12, 3, tzinfo=UTC))
    assert not PP.Credits().known(datetime(2026, 12, 3, tzinfo=UTC))


class SilentEvents(FakeApi):
    """An event list that sends no credit headers (the free endpoint may not)."""

    def __call__(self, url):
        payload, headers = super().__call__(url)
        return (payload, {}) if urllib.parse.urlparse(url).path.endswith("/events") else \
            (payload, headers)


def test_with_no_reading_of_the_period_one_core_call_reads_the_headers(tmp_path):
    # A ledger from last month (8 left) would refuse every call against
    # December's reserve and, with a silent event list, never learn the refill.
    derived = tmp_path / "derived"
    PP.Credits(remaining=8, used=492, at="2026-10-31T23:00:00+00:00").save(
        derived / PP.LEDGER_NAME)
    api = SilentEvents(remaining=400, used=0)
    rep = run(tmp_path, api)
    assert rep.first_readings == 1 and rep.snapshots == 2
    odds = [u for u in api.calls if "/odds?" in u]
    q = [urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["markets"][0] for u in odds]
    assert q[0] == ",".join(P.CORE_MARKETS)                 # the reading call: core only
    assert q[1] == ",".join(P.CORE_MARKETS + P.SECONDARY_MARKETS)   # then the ledger rules
    assert rep.credits.reset_observed == NOW.date().isoformat()      # used fell: a reset
    assert "one core call read the provider's headers" in "\n".join(rep.lines())


def test_a_reading_call_without_headers_buys_nothing_more_in_that_run(tmp_path):
    class NoHeaders(FakeApi):
        def __call__(self, url):
            payload, _ = super().__call__(url)
            return payload, {}
    api = NoHeaders()
    rep = run(tmp_path, api)
    assert rep.first_readings == 1 and rep.snapshots == 1
    assert rep.refused == {PP.UNKNOWN_CREDITS: 1}
    assert sum("/odds?" in u for u in api.calls) == 1


def test_a_call_that_would_eat_the_core_reserve_is_refused():
    assert PP.plan_markets(PP.Credits(), 0) == (None, PP.UNKNOWN_CREDITS)
    assert PP.plan_markets(PP.Credits(remaining=60), 60)[0] is None    # 60 - 6 < 60
    assert PP.plan_markets(PP.Credits(remaining=66), 60)[0] == P.CORE_MARKETS
    assert PP.plan_markets(PP.Credits(remaining=70), 60)[0] == P.CORE_MARKETS   # slack 4 < 5
    both = PP.plan_markets(PP.Credits(remaining=71), 60)[0]
    assert both == P.CORE_MARKETS + P.SECONDARY_MARKETS


def test_the_reserve_counts_the_months_later_core_snapshots():
    games, _ = PP.season_games(schedule(), 2026)
    first = games[0]
    reset = datetime(2026, 12, 1, tzinfo=UTC)
    # three later games before the reset (one 1 pm game sorts after the other)
    assert PP.core_reserve(games, first, reset, set()) == 3 * PP.CORE_COST
    assert PP.core_reserve(games, first, reset, {"2026_10_KC_BUF"}) == 2 * PP.CORE_COST
    assert PP.core_reserve(games, first, datetime(2026, 11, 9, tzinfo=UTC), set()) == 2 * PP.CORE_COST


# ------------------------------------------------------------------ collection

def test_a_run_snapshots_each_due_game_once_and_derives_only_values(tmp_path):
    api = FakeApi(remaining=400)
    rep = run(tmp_path, api)
    assert (rep.due, rep.snapshots) == (2, 2)
    assert sum("/events?" in u for u in api.calls) == 1               # free, once per run
    assert sum("/odds?" in u for u in api.calls) == 2
    assert rep.with_secondary == 2                                    # 400 credits: slack
    raw = sorted((tmp_path / "raw" / "season2026" / "week09").glob("*__*.json"))
    assert [p.name.split("__")[0] for p in raw] == ["2026_09_BUF_KC", "2026_09_NYJ_MIA"]
    meta = json.loads(raw[0].read_text("utf-8"))["meta"]
    assert meta["lead_minutes"] == 90.0 and meta["markets_requested"][:6] == list(P.CORE_MARKETS)
    assert (tmp_path / "raw" / "season2026" / "week09" / "book_lines.csv").is_file()
    derived = P.read_derived(tmp_path / "derived", 2026, 9)
    game = derived["games"]["2026_09_BUF_KC"]
    assert set(game["players"]) == {"00-0099001", "00-0099002"}
    assert game["unmatched"] == 2                     # Nobody and Kip: counted, not named
    pub = _all_text(tmp_path / "derived")
    for leak in ("Fixture", "book_alpha", "Alpha", "245.5", "-110", "evtA"):
        assert leak not in pub, f"the derived store leaked {leak!r}"
    ledger = json.loads((tmp_path / "derived" / PP.LEDGER_NAME).read_text("utf-8"))
    assert ledger["remaining"] == api.remaining and ledger["last"] == 4

    again = run(tmp_path, api)                         # same window: nothing is due twice
    assert (again.due, again.snapshots) == (0, 0) and len(api.calls) == 3


def test_tight_credits_buy_core_markets_only_then_refuse(tmp_path):
    # 20 credits. The first 1 pm game would leave 14, short of the 18 its three
    # later games need before the reset: refused. The second leaves 14 of the
    # 12 its two later games need: bought, core only (slack 2 < 5).
    api = FakeApi(remaining=20)
    rep = run(tmp_path, api)
    assert rep.snapshots == 1 and rep.with_secondary == 0
    assert rep.refused == {"would leave less than the month's core reserve": 1}
    assert P.read_derived(tmp_path / "derived", 2026, 9)["games"].keys() == {"2026_09_NYJ_MIA"}
    odds = [u for u in api.calls if "/odds?" in u]
    assert urllib.parse.parse_qs(urllib.parse.urlparse(odds[0]).query)["markets"] == \
        [",".join(P.CORE_MARKETS)]


def test_an_empty_response_is_not_a_snapshot_and_is_tried_again(tmp_path):
    api = FakeApi(odds={"evtA": odds_payload("evtA", "Kansas City Chiefs", "Buffalo Bills",
                                             empty=True),
                        "evtB": odds_payload("evtB", "Miami Dolphins", "New York Jets")})
    rep = run(tmp_path, api)
    assert (rep.snapshots, rep.empty) == (1, 1)
    api.odds["evtA"] = odds_payload("evtA", "Kansas City Chiefs", "Buffalo Bills")
    later = run(tmp_path, api, now=NOW + timedelta(minutes=60))
    assert (later.due, later.snapshots) == (1, 1)


def test_an_authorisation_error_stops_the_run_and_a_rate_limit_is_retried(tmp_path):
    api = FakeApi(fail={"evtA": PP.OddsApiError(401, "HTTPError")})
    rep = run(tmp_path, api)
    assert rep.snapshots == 0 and rep.errors == {"event odds: HTTP 401": 1}
    assert rep.refused == {"run stopped after an authorisation error": 1}
    api2 = FakeApi(fail={"evtA": [PP.OddsApiError(429, "HTTPError")]})
    slept = []
    rep2 = PP.collect(key=KEY, season=2026, now=NOW, schedule=schedule(), schedule_source="t",
                      raw_root=tmp_path / "raw2", derived_root=tmp_path / "der2", table=TABLE,
                      fetch=api2, sleep=slept.append, clock=lambda: NOW)
    assert rep2.snapshots == 2 and 5.0 in slept


def test_a_game_that_kicks_off_before_its_turn_is_skipped(tmp_path):
    api = FakeApi()
    late = iter([NOW, datetime(2026, 11, 8, 18, 0, 1, tzinfo=UTC)])
    rep = PP.collect(key=KEY, season=2026, now=NOW, schedule=schedule(), schedule_source="t",
                     raw_root=tmp_path / "raw", derived_root=tmp_path / "der", table=TABLE,
                     fetch=api, sleep=lambda s: None,
                     clock=lambda: next(late, datetime(2026, 11, 8, 18, 0, 1, tzinfo=UTC)))
    assert rep.refused.get("kicked off before its turn in this run") == 1


def test_a_dry_run_calls_nothing(tmp_path):
    api = FakeApi()
    rep = run(tmp_path, api, dry_run=True)
    assert rep.due == 2 and api.calls == [] and not (tmp_path / "raw").exists()


def test_a_name_added_to_the_table_later_resolves_earlier_snapshots(tmp_path):
    run(tmp_path, FakeApi())
    before = P.read_derived(tmp_path / "derived", 2026, 9)["games"]["2026_09_BUF_KC"]
    assert before["unmatched"] == 2
    grown = P.IdentityTable([*TABLE.rows(),
                             P.IdentityRow("Kip Fixture", "KC", "00-0099003", "exact",
                                           "2026-11-09")])
    weeks, changed = PP.rederive(tmp_path / "raw", tmp_path / "derived", 2026, grown,
                                 P.PropParams.load())
    after = P.read_derived(tmp_path / "derived", 2026, 9)["games"]["2026_09_BUF_KC"]
    assert (weeks, changed) == (1, True)
    assert after["unmatched"] == 1 and "00-0099003" in after["players"]
    assert after["taken_at"] == before["taken_at"]                  # the snapshot time stands


# ------------------------------------------------------------------ the local names mode

FRIDAY = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)


def td_payload(event_id, home, away, players):
    return {"id": event_id, "commence_time": "2026-11-08T18:00:00Z", "home_team": home,
            "away_team": away, "bookmakers": [{"key": "book_alpha", "markets": [
                {"key": "player_anytime_td", "outcomes": [
                    {"name": "Yes", "description": p, "price": 150} for p in players]}]}]}


def names_api(**kw):
    return FakeApi(odds={
        "evtA": td_payload("evtA", "Kansas City Chiefs", "Buffalo Bills",
                           ["Pat Fixture", "Wes Fixture"]),
        "evtB": td_payload("evtB", "Miami Dolphins", "New York Jets", ["Ray Fixture"]),
        "evtC": td_payload("evtC", "Seattle Seahawks", "San Francisco 49ers", ["Lee Fixture"])},
        **kw)


def run_names(tmp_path, api, now=FRIDAY, **kw):
    return PP.collect_names(key=KEY, season=2026, now=now, schedule=schedule(),
                            schedule_source="test", raw_root=tmp_path / "raw",
                            derived_root=tmp_path / "derived", fetch=api,
                            sleep=lambda s: None, clock=lambda: now, **kw)


def test_the_names_mode_asks_one_cheap_market_per_game_of_the_next_week(tmp_path):
    api = names_api(remaining=400)
    rep = run_names(tmp_path, api)
    assert rep.week == 9 and (rep.due, rep.snapshots) == (3, 3)     # the untimed game is never asked
    odds = [u for u in api.calls if "/odds?" in u]
    assert sum("/events?" in u for u in api.calls) == 1 and len(odds) == 3
    for u in odds:
        assert urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["markets"] == \
            ["player_anytime_td"]
    assert api.remaining == 397                                      # 1 credit a game
    stored = sorted((tmp_path / "raw" / "names" / "season2026" / "week09").glob("*__*.json"))
    assert len(stored) == 3
    # never a snapshot: nothing derived, nothing counted as snapped, nothing in raw/season2026
    assert not (tmp_path / "raw" / "season2026").exists()
    assert P.read_derived(tmp_path / "derived", 2026, 9) is None
    assert PP.snapped_games(tmp_path / "derived", tmp_path / "raw", 2026, {9}) == set()
    assert P.read_raw_snapshots(tmp_path / "raw", 2026) == {}
    assert len(P.read_name_files(tmp_path / "raw", 2026)) == 3
    lines = "\n".join(PP.names_lines(rep))
    assert "3 name list(s) stored" in lines
    for leak in ("Fixture", KEY, "book_alpha", "evtA"):
        assert leak not in lines
    again = run_names(tmp_path, api)                     # a game with a list is not asked again
    assert again.due == 0 and len(api.calls) == 4


def test_the_names_mode_keeps_the_months_core_reserve(tmp_path):
    # four core snapshots still to come this month: a reserve of 24 credits
    rep = run_names(tmp_path, names_api(remaining=25))
    assert rep.reserve == 4 * PP.CORE_COST
    assert rep.snapshots == 1
    assert rep.refused == {"would leave less than the month's core reserve": 2}


def test_the_names_mode_refuses_to_run_inside_github_actions(tmp_path, monkeypatch, capsys):
    import gridiron.config as cfg

    class Settings:
        odds_api_key = KEY
    monkeypatch.setattr(cfg, "get_settings", lambda: Settings())
    monkeypatch.setenv("GITHUB_ACTIONS", "true")

    def no_call(url):
        raise AssertionError("the names mode called the provider inside Actions")
    monkeypatch.setattr(PP, "http_fetch", no_call)
    assert PP.main(["names", "--raw-root", str(tmp_path / "r"),
                    "--derived-root", str(tmp_path / "d")]) == 2
    assert capsys.readouterr().out.strip() == PP.LOCAL_ONLY
    assert not (tmp_path / "r").exists()


def test_the_names_mode_asks_the_kickers_market_only_when_told(tmp_path):
    api = names_api(remaining=400)
    rep = run_names(tmp_path, api, markets=PP.KICKER_NAME_MARKETS)
    odds = [u for u in api.calls if "/odds?" in u]
    assert rep.snapshots == 3
    assert {urllib.parse.parse_qs(urllib.parse.urlparse(u).query)["markets"][0] for u in odds} \
        == {"player_anytime_td,player_kicking_points"}
