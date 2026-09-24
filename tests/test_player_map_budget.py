"""Sleeper's full player map is fetched at most once a day, and the league
snapshot at most once a run.

docs.sleeper.com, "Fetch all players": "Please use this call sparingly, as it
is intended only to be used once per day at most to keep your player IDs
updated." (read 2026-09-24). The map is therefore an IDENTITY source with a
request budget, not a designation feed that can be refreshed on the
designation clock. These tests pin:

  1. the budget: >= 24 h between REQUESTS (success or failure), counted from
     a ledger that is written before the GET, carried between cloud runs and
     not bypassed by `--force`; requests only inside a fixed daily window, and
     a narrower slot when no history exists at all, so a lost or unsaved
     cache cannot turn into a request every 15 minutes;
  2. one attempt per request (no 3x retry of a 5-16 MB body), and an empty
     or 404 map is a failure that never overwrites the last good map;
  3. no fake freshness: designation limits are unchanged, a skipped request
     never restamps the map, and a carried map is cleared of its carried mark
     only when the last logged request succeeded;
  4. the league snapshot the sync step published in this run is reused, not
     fetched a second time, and the board reads that same generation.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gridiron import carryover, gating, sleeper
from gridiron import livesync as ls
from gridiron.freshness import CADENCES
from gridiron.ingest import Manifest

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
#: Sunday 2026-09-27. ET is UTC-4, so the 10:00-13:00 ET window is 14-17 UTC.
IN_WINDOW = datetime(2026, 9, 27, 14, 7, tzinfo=UTC)
COLD_SLOT = datetime(2026, 9, 27, 14, 7, tzinfo=UTC)
LATE_WINDOW = datetime(2026, 9, 27, 16, 22, tzinfo=UTC)
EVENING = datetime(2026, 9, 27, 23, 0, tzinfo=UTC)


def _pull_week():
    spec = importlib.util.spec_from_file_location("budget_pull_week",
                                                  ROOT / "scripts/ingest/pull_week.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeClient:
    """Counts every GET the puller would make. No network."""

    league_id = "L1"

    def __init__(self, players=None, fail=False, snapshot=None):
        self.player_calls = 0
        self.snapshot_calls = 0
        self._players = {"1": {"full_name": "A"}} if players is None else players
        self._fail = fail
        self._snapshot = snapshot

    def players(self):
        self.player_calls += 1
        if self._fail:
            raise RuntimeError("Sleeper GET failed after 1 tries")
        return self._players

    def snapshot(self):
        self.snapshot_calls += 1
        if self._snapshot is None:
            raise RuntimeError("offline")
        return self._snapshot


def _cache(tmp_path: Path) -> tuple[Path, Manifest]:
    cache = tmp_path / "season2026"
    cache.mkdir(parents=True, exist_ok=True)
    return cache, Manifest.load(cache, 2026)


def _run(pw, manifest, now, client, **kw):
    kw.setdefault("force", False)
    kw.setdefault("with_players", True)
    pw.pull_sleeper(manifest, now, client=client, **kw)
    return client


# ------------------------------------------------------------ 1. the budget

def test_the_budget_is_a_day_between_requests_inside_the_window():
    last = IN_WINDOW - timedelta(hours=23, minutes=50)
    assert not sleeper.player_map_budget([{"at": last.isoformat(), "outcome": "ok"}],
                                         IN_WINDOW).due
    last = IN_WINDOW - timedelta(hours=24)
    b = sleeper.player_map_budget([{"at": last.isoformat(), "outcome": "ok"}], IN_WINDOW)
    assert b.due
    # Past 24 h but outside the window: waits for the window, never fetches at 7 pm.
    b = sleeper.player_map_budget([{"at": last.isoformat(), "outcome": "ok"}], EVENING)
    assert not b.due and "window" in b.reason


def test_a_failed_request_counts_against_the_budget():
    last = IN_WINDOW - timedelta(minutes=15)
    b = sleeper.player_map_budget([{"at": last.isoformat(), "outcome": "failed"}], IN_WINDOW)
    assert not b.due and b.last_ok is False
    assert b.next_allowed == last + timedelta(hours=24)


def test_no_history_at_all_is_bounded_to_a_narrow_daily_slot():
    assert sleeper.player_map_budget([], COLD_SLOT).due
    assert not sleeper.player_map_budget([], LATE_WINDOW).due
    assert not sleeper.player_map_budget([], EVENING).due
    # An explicit manual cold start is allowed once; the ledger then governs.
    assert sleeper.player_map_budget([], EVENING, cold_start=True).due


def test_manifest_evidence_governs_when_there_is_no_ledger_yet():
    """A cache written before the ledger existed still has a budget: the
    map's own pull time (and a later failed attempt) counts as a request."""
    pulled = IN_WINDOW - timedelta(hours=5)
    assert not sleeper.player_map_budget([], IN_WINDOW, fallback_last=pulled).due
    assert sleeper.player_map_budget([], IN_WINDOW,
                                     fallback_last=IN_WINDOW - timedelta(hours=25)).due


def test_force_does_not_bypass_the_budget(tmp_path):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    sleeper.note_player_map_request(cache, IN_WINDOW - timedelta(hours=2), outcome="ok")
    client = _run(pw, m, IN_WINDOW, FakeClient(), force=True)
    assert client.player_calls == 0


def test_the_request_is_ledgered_before_the_get_and_a_failure_does_not_loop(tmp_path):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    client = FakeClient(fail=True)
    now = IN_WINDOW
    for _ in range(8):                         # two hours of 15-minute runs
        _run(pw, Manifest.load(cache, 2026), now, client)
        now += timedelta(minutes=15)
    assert client.player_calls == 1
    ledger = sleeper.read_player_map_ledger(cache)
    assert ledger[-1]["outcome"] == "failed"
    # And it retries the next day, not before.
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW + timedelta(hours=24), client)
    assert client.player_calls == 2


def test_a_crash_mid_request_still_counts(tmp_path, monkeypatch):
    pw = _pull_week()
    cache, m = _cache(tmp_path)

    class Boom(FakeClient):
        def players(self):
            self.player_calls += 1
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(pw, m, IN_WINDOW, Boom())
    assert sleeper.read_player_map_ledger(cache)[-1]["outcome"] == "requested"
    assert not sleeper.player_map_budget(sleeper.read_player_map_ledger(cache),
                                         IN_WINDOW + timedelta(minutes=15)).due


def test_two_scheduled_runs_and_a_manual_force_make_one_request(tmp_path):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    client = FakeClient()
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW, client)
    m = Manifest.load(cache, 2026)
    m.save()
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW + timedelta(minutes=15), client)
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW + timedelta(minutes=40), client,
         force=True)
    assert client.player_calls == 1


# ------------------------------------------- 2. one attempt, empty = failure

def test_the_player_map_is_one_attempt_not_three(monkeypatch):
    seen: list[dict] = []

    def fake_http(url, **kw):
        seen.append({"url": url, **kw})
        return {"1": {}}

    monkeypatch.setattr(sleeper, "http_fetch", fake_http)
    sleeper.SleeperReadOnly("L1").players()
    assert seen and seen[-1]["url"].endswith("/players/nfl")
    assert seen[-1].get("retries") == 1


@pytest.mark.parametrize("body", [{}, [], "not a map"])
def test_an_empty_map_is_a_failure_and_keeps_the_last_good_one(tmp_path, body):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    good = cache / "sleeper_players.json"
    good.write_text(json.dumps({"1": {"full_name": "A"}}), encoding="utf-8")
    pulled = IN_WINDOW - timedelta(hours=30)
    m.record("sleeper_players", path=good, rows=1, source="t", as_of=pulled)
    m.save()
    m = Manifest.load(cache, 2026)
    _run(pw, m, IN_WINDOW, FakeClient(players=body))
    e = m.get("sleeper_players")
    assert e.error and e.as_of == pulled.isoformat(timespec="seconds")
    assert json.loads(good.read_text(encoding="utf-8")) == {"1": {"full_name": "A"}}
    assert sleeper.read_player_map_ledger(cache)[-1]["outcome"] == "failed"


# ------------------------------------------------------ 3. no fake freshness

def test_designation_limits_are_not_widened():
    c = CADENCES["sleeper_players"]
    assert (c.max_age_hours, c.gameday_max_age_hours) == (24.0, 6.0)
    assert "sleeper_players" in gating.GATED_SOURCES["lineup"]
    assert "sleeper_players" in gating.GATED_SOURCES["waiver"]
    step = gating.VERIFY["sleeper_players"]
    assert "once a day" in step and "Sleeper" in step


def _carried_map(cache: Path, m: Manifest, pulled: datetime) -> None:
    good = cache / "sleeper_players.json"
    good.write_text(json.dumps({"1": {}}), encoding="utf-8")
    m.record("sleeper_players", path=good, rows=1, source="t", as_of=pulled)
    e = m.get("sleeper_players")
    m.entries["sleeper_players"] = replace(e, error=carryover.CARRIED_FORWARD)
    m.save()


def test_a_carried_map_is_kept_unstamped_when_the_last_request_succeeded(tmp_path):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    pulled = IN_WINDOW - timedelta(hours=8)
    _carried_map(cache, m, pulled)
    sleeper.note_player_map_request(cache, pulled, outcome="ok")
    m = Manifest.load(cache, 2026)
    client = _run(pw, m, IN_WINDOW, FakeClient())
    e = m.get("sleeper_players")
    assert client.player_calls == 0
    assert e.error == "" and e.as_of == pulled.isoformat(timespec="seconds")


def test_a_carried_map_keeps_its_mark_when_the_last_request_failed(tmp_path):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    pulled = IN_WINDOW - timedelta(hours=30)
    _carried_map(cache, m, pulled)
    sleeper.note_player_map_request(cache, IN_WINDOW - timedelta(hours=1), outcome="failed")
    m = Manifest.load(cache, 2026)
    _run(pw, m, IN_WINDOW, FakeClient())
    assert m.get("sleeper_players").error == carryover.CARRIED_FORWARD


def test_the_ledger_travels_with_the_carried_inputs_and_is_validated(tmp_path):
    cache, m = _cache(tmp_path / "a")
    pulled = IN_WINDOW - timedelta(hours=3)
    good = cache / "sleeper_players.json"
    good.write_text(json.dumps({"1": {}}), encoding="utf-8")
    m.record("sleeper_players", path=good, rows=1, source="t", as_of=pulled)
    m.save()
    sleeper.note_player_map_request(cache, pulled, outcome="ok")
    store = tmp_path / "store"
    carryover.publish_inputs(cache, store, season=2026, now=IN_WINDOW)
    fresh = tmp_path / "b" / "season2026"
    fresh.mkdir(parents=True)
    Manifest(fresh, {}, 2026).save()
    carryover.restore_inputs(store, fresh, season=2026, now=IN_WINDOW)
    assert sleeper.read_player_map_ledger(fresh) == sleeper.read_player_map_ledger(cache)

    # A ledger stamped in the future is refused rather than laid down.
    stored = store / carryover.INPUTS_DIR / "season2026" / sleeper.PLAYER_MAP_LEDGER
    stored.write_text(json.dumps({"requests": [
        {"at": (IN_WINDOW + timedelta(days=3)).isoformat(), "outcome": "ok"}]}),
        encoding="utf-8")
    other = tmp_path / "c" / "season2026"
    other.mkdir(parents=True)
    Manifest(other, {}, 2026).save()
    report = carryover.restore_inputs(store, other, season=2026, now=IN_WINDOW)
    assert not (other / sleeper.PLAYER_MAP_LEDGER).exists()
    assert any(sleeper.PLAYER_MAP_LEDGER in v.name and not v.accepted
               for v in report.verdicts)


def test_an_unreadable_ledger_never_opens_the_budget(tmp_path):
    cache, _ = _cache(tmp_path)
    (cache / sleeper.PLAYER_MAP_LEDGER).write_text("{not json", encoding="utf-8")
    assert sleeper.read_player_map_ledger(cache) == []
    # With no readable history the narrow slot applies, not "fetch now".
    assert not sleeper.player_map_budget(sleeper.read_player_map_ledger(cache),
                                         LATE_WINDOW).due


# ----------------------------------------- 4. one league snapshot per run

def _snapshot(league="L1") -> dict:
    fixture = json.loads((ROOT / "tests/fixtures/sleeper_league.json")
                         .read_text(encoding="utf-8"))
    return {**fixture, "league_id": league}


def test_the_sync_steps_snapshot_is_reused_not_fetched_again(tmp_path):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    snap = _snapshot()
    synced = IN_WINDOW - timedelta(minutes=3)
    gen = ls.publish_snapshot(cache, snap, now=synced, source="sync", rows=12,
                              week=int(snap.get("week") or 3), season=2026)
    m = Manifest.load(cache, 2026)
    client = _run(pw, m, IN_WINDOW, FakeClient(snapshot=snap), with_players=False,
                  reuse_league_minutes=20)
    assert client.snapshot_calls == 0
    # The board reads the very generation the sync published.
    assert Manifest.load(cache, 2026).file("sleeper_league") == gen


@pytest.mark.parametrize("case", ["old", "failed", "carried", "other_league", "off"])
def test_a_snapshot_that_is_not_this_runs_is_fetched(tmp_path, case):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    snap = _snapshot("L2" if case == "other_league" else "L1")
    when = IN_WINDOW - (timedelta(hours=2) if case == "old" else timedelta(minutes=3))
    ls.publish_snapshot(cache, snap, now=when, source="sync", rows=12,
                        week=int(snap.get("week") or 3), season=2026)
    m = Manifest.load(cache, 2026)
    if case in ("failed", "carried"):
        e = m.get("sleeper_league")
        m.entries["sleeper_league"] = replace(
            e, error="boom" if case == "failed" else carryover.CARRIED_FORWARD)
        m.save()
        m = Manifest.load(cache, 2026)
    client = FakeClient(snapshot=_snapshot())
    _run(pw, m, IN_WINDOW, client, with_players=False,
         reuse_league_minutes=0 if case == "off" else 20)
    assert client.snapshot_calls == 1


def test_two_fresh_cloud_runners_share_one_daily_request_through_the_carry(tmp_path):
    """The cloud shape: every run starts on an empty runner, restores the
    carried inputs, pulls, publishes. Two consecutive runs 15 minutes apart
    make ONE player-map request, and the second keeps the first's pull time."""
    pw = _pull_week()
    store = tmp_path / "store"
    client = FakeClient()
    stamps = []
    for i, now in enumerate((IN_WINDOW, IN_WINDOW + timedelta(minutes=15))):
        cache = tmp_path / f"runner{i}" / "season2026"
        cache.mkdir(parents=True)
        Manifest(cache, {}, 2026).save()
        carryover.restore_inputs(store, cache, season=2026, now=now)
        m = Manifest.load(cache, 2026)
        pw.pull_sleeper(m, now, False, True, client=client)
        m.save()
        stamps.append(Manifest.load(cache, 2026).get("sleeper_players").as_of)
        assert Manifest.load(cache, 2026).get("sleeper_players").error == ""
        carryover.publish_inputs(cache, store, season=2026, now=now)
    assert client.player_calls == 1
    assert stamps[0] == stamps[1] == IN_WINDOW.isoformat(timespec="seconds")
