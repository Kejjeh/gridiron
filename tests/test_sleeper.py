"""The Sleeper adapter is read-only by construction, and testable offline."""
from __future__ import annotations

import re
import urllib.error
from pathlib import Path

import pytest

from gridiron import sleeper as S

SOURCE = Path(S.__file__).read_text(encoding="utf-8")

#: Anything that could change the league. The adapter must contain none.
WRITE_MARKERS = (
    r"\bmethod\s*=\s*['\"](POST|PUT|PATCH|DELETE)",
    r"\bdata\s*=",                 # urllib turns a body into a POST
    r"requests\.(post|put|patch|delete)",
    r"def\s+(set_|submit_|add_|drop_|claim_|trade_|send_|update_|post_)",
)


@pytest.mark.parametrize("pattern", WRITE_MARKERS)
def test_module_is_read_only(pattern):
    hit = re.search(pattern, SOURCE)
    assert hit is None, (
        f"gridiron.sleeper must never write to the league; found {hit!r}. "
        f"Lineups, waivers, trades and messages are a human's job.")


def test_every_public_endpoint_hits_a_get_url():
    client = S.SleeperReadOnly("L1", fetch=lambda url: {"url": url})
    assert client.league()["url"].endswith("/league/L1")
    assert client.matchups(3)["url"].endswith("/league/L1/matchups/3")


def test_fetch_is_injectable_so_the_adapter_runs_offline(league_snapshot):
    calls: list[str] = []

    def fake(url: str):
        calls.append(url)
        if url.endswith("/state/nfl"):
            return league_snapshot["state"]
        if url.endswith("/rosters"):
            return league_snapshot["rosters"]
        if url.endswith("/users"):
            return league_snapshot["users"]
        if "/matchups/" in url:
            return league_snapshot["matchups"]
        return league_snapshot["league"]

    snap = S.SleeperReadOnly("L1", fetch=fake).snapshot()
    assert snap["week"] == 2 and snap["league_id"] == "L1"
    assert len(snap["rosters"]) == 2 and snap["as_of"].endswith("+00:00")
    assert all(url.startswith(S.BASE) for url in calls)


def test_a_missing_endpoint_degrades_to_empty_not_a_crash():
    client = S.SleeperReadOnly("L1", fetch=lambda url: None)
    assert client.rosters() == [] and client.league() == {} and client.players() == {}
    assert client.matchups(99) == []


def test_http_fetch_returns_none_on_404_and_does_not_retry():
    calls = {"n": 0}

    def opener(req, timeout=0):
        calls["n"] += 1
        raise urllib.error.HTTPError(req.full_url, 404, "nope", {}, None)

    import urllib.request
    original = urllib.request.urlopen
    urllib.request.urlopen = opener
    try:
        assert S.http_fetch("https://example.invalid/x") is None
    finally:
        urllib.request.urlopen = original
    assert calls["n"] == 1, "a 404 is missing data, not a transient failure"


def test_the_user_agent_is_a_plain_script_never_a_browser_spoof():
    assert "gridiron" in S.USER_AGENT
    assert not re.search(r"Mozilla|Chrome|Safari", S.USER_AGENT)


def test_nfl_state_parses_and_keeps_the_week_authoritative(league_snapshot):
    state = S.NflState.from_payload(league_snapshot["state"])
    assert state.season == 2026 and state.week == 2
    assert state.display_week == 2 and state.season_type == "regular"


def test_state_tolerates_a_missing_display_week():
    state = S.NflState.from_payload({"season": "2026", "week": 5})
    assert state.display_week == 5


def test_owner_roster_matches_owners_and_co_owners(league_snapshot):
    rosters = league_snapshot["rosters"]
    assert S.owner_roster(rosters, "U1")["roster_id"] == 1
    assert S.owner_roster(rosters, "U2")["roster_id"] == 2, "co-owned team counts"
    assert S.owner_roster(rosters, "nobody") is None


def test_display_names_maps_ids(league_snapshot):
    names = S.display_names(league_snapshot["users"])
    assert names["U1"] == "fixture_owner"


def test_season_guard_catches_running_last_years_code():
    from gridiron.league_config import SEASON_YEAR

    ok = S.NflState(SEASON_YEAR, 1, 1, "regular", "")
    stale = S.NflState(SEASON_YEAR - 1, 1, 1, "regular", "")
    assert S.season_matches(ok) and not S.season_matches(stale)


def test_league_id_resolution_prefers_the_environment(monkeypatch):
    from gridiron import config
    from gridiron.league_config import SLEEPER_LEAGUE_ID

    config.get_settings.cache_clear()
    monkeypatch.setenv("GRIDIRON_SLEEPER_LEAGUE_ID", "override-123")
    try:
        assert S.resolve_league_id() == "override-123"
    finally:
        config.get_settings.cache_clear()
    monkeypatch.delenv("GRIDIRON_SLEEPER_LEAGUE_ID", raising=False)
    config.get_settings.cache_clear()
    assert S.resolve_league_id() == SLEEPER_LEAGUE_ID
    config.get_settings.cache_clear()


def test_no_credential_is_read_anywhere_in_the_adapter():
    """Sleeper's read API needs no auth; a cookie here would be a smell."""
    assert not re.search(r"espn_s2|swid|cookie|api_key|token", SOURCE, re.I)
