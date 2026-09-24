"""Sleeper's full player map is fetched at most once a day, and the league
snapshot at most once a run.

docs.sleeper.com, "Fetch all players": "Please use this call sparingly, as it
is intended only to be used once per day at most to keep your player IDs
updated." (read 2026-09-24). The map is therefore an IDENTITY source with a
request budget, not a designation feed that can be refreshed on the
designation clock. These tests pin:

  1. the budget: >= 24 h between REQUESTS (success or failure), counted from
     a ledger that is written before the GET, carried between cloud runs and
     not bypassed by `--force`. Once a day has passed the first run may
     request at ANY hour (a delayed run does not skip the day). History that
     is missing, unreadable, malformed, empty or future-dated never
     authorises a request: RECOVERY NEEDED, shown on the page, until a
     person's one-time bootstrap. In the cloud a request also needs proof the
     carried ledger was saved by the previous run (its run number, first
     attempt); a run that cannot prove it stamps a gap mark, and nothing
     requests for 24 h after the mark, so no pattern of lost saves, re-runs,
     --force or bootstrap inputs yields two requests within a day;
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
#: Sunday 2026-09-27. ET is UTC-4.
IN_WINDOW = datetime(2026, 9, 27, 14, 7, tzinfo=UTC)
LATE_WINDOW = datetime(2026, 9, 27, 16, 22, tzinfo=UTC)
EVENING = datetime(2026, 9, 27, 23, 0, tzinfo=UTC)


def _hist(*lines, checked=None, run=None, problem=""):
    return sleeper.PlayerMapHistory(tuple({"at": t.isoformat(), "outcome": o, "note": ""}
                                          for t, o in lines), checked, run, problem)


def _seed(cache: Path, at: datetime, outcome: str = "ok") -> None:
    """A trustworthy ledger with one request on it (the steady state)."""
    sleeper.note_player_map_request(cache, at, outcome=outcome)


def _load_script(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


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

def test_the_budget_is_a_day_between_requests():
    last = IN_WINDOW - timedelta(hours=23, minutes=50)
    assert not sleeper.player_map_budget(_hist((last, "ok")), IN_WINDOW).due
    last = IN_WINDOW - timedelta(hours=24)
    b = sleeper.player_map_budget(_hist((last, "ok")), IN_WINDOW)
    assert b.due and b.state == "due"


def test_a_delayed_run_catches_up_after_24h_at_any_hour():
    """Astra's reproduction: last success 2026-09-23 16:52Z (12:52 ET). The
    run at 16:52Z the next day was due; runs at 17:02Z and 20:00Z were not,
    only because they fell after 13:00 ET. A day has passed either way."""
    last = datetime(2026, 9, 23, 16, 52, tzinfo=UTC)
    for now in (datetime(2026, 9, 24, 16, 52, tzinfo=UTC),
                datetime(2026, 9, 24, 17, 2, tzinfo=UTC),
                datetime(2026, 9, 24, 20, 0, tzinfo=UTC),
                datetime(2026, 9, 25, 3, 30, tzinfo=UTC)):
        b = sleeper.player_map_budget(_hist((last, "ok")), now)
        assert b.due, now
        # the original timestamp is what the decision cites
        assert b.last_request == last and "2026-09-23 16:52Z" in b.reason
    # ...and still never inside the 24 h
    assert not sleeper.player_map_budget(
        _hist((last, "ok")), datetime(2026, 9, 24, 16, 51, tzinfo=UTC)).due


def test_a_failed_request_counts_against_the_budget():
    last = IN_WINDOW - timedelta(minutes=15)
    b = sleeper.player_map_budget(_hist((last, "failed")), IN_WINDOW)
    assert not b.due and b.last_ok is False
    assert b.next_allowed == last + timedelta(hours=24)


@pytest.mark.parametrize("history", [
    sleeper.PlayerMapHistory(problem="no request ledger"),
    sleeper.PlayerMapHistory(problem="the request ledger is unreadable (JSONDecodeError)"),
    sleeper.PlayerMapHistory(problem="the request ledger is a ledger line has no readable "
                                     "time or outcome"),
    sleeper.PlayerMapHistory(),                                       # readable, empty
], ids=["lost", "unreadable", "malformed", "empty"])
def test_no_trustworthy_history_never_requests_at_any_hour(history):
    """The old cold slot (10:00-10:20 ET) let a lost cache request once a
    day, every day, and a manual cold start could repeat. Now nothing
    automatic requests: RECOVERY NEEDED until a person bootstraps."""
    for now in (IN_WINDOW, LATE_WINDOW, EVENING):
        for carried in (False, True):
            b = sleeper.player_map_budget(history, now, carried=carried)
            assert not b.due and b.state == "recovery" and b.needs_person
            assert "RECOVERY NEEDED" in b.reason and "bootstrap" in b.reason


def test_a_future_dated_ledger_is_recovery_not_a_block_forever():
    b = sleeper.player_map_budget(_hist((IN_WINDOW + timedelta(days=3), "ok")), IN_WINDOW)
    assert not b.due and b.state == "recovery" and "after this machine's clock" in b.reason


def test_the_bootstrap_is_one_request_and_refused_inside_the_maps_own_day():
    lost = sleeper.PlayerMapHistory(problem="no request ledger")
    b = sleeper.player_map_budget(lost, EVENING, bootstrap=True)
    assert b.due and "bootstrap" in b.reason
    # The cached map (or a failed attempt) shows a request 5 h ago: refused.
    b = sleeper.player_map_budget(lost, EVENING, bootstrap=True,
                                  fallback_last=EVENING - timedelta(hours=5))
    assert not b.due and b.state == "bootstrap-refused" and b.needs_person
    assert b.next_allowed == EVENING + timedelta(hours=19)
    # A cached map pulled 25 h ago does not block it.
    assert sleeper.player_map_budget(lost, EVENING, bootstrap=True,
                                     fallback_last=EVENING - timedelta(hours=25)).due
    # With a sound ledger the bootstrap is ignored and the 24 h governs.
    b = sleeper.player_map_budget(_hist((EVENING - timedelta(hours=2), "ok")), EVENING,
                                  bootstrap=True)
    assert not b.due and b.state == "wait"


def test_map_evidence_alone_never_authorises_a_request():
    """A cache written before the ledger existed: the map's pull time is a
    lower bound on the last request, not a count. It can block a bootstrap;
    it cannot open the budget."""
    lost = sleeper.PlayerMapHistory(problem="no request ledger")
    assert not sleeper.player_map_budget(
        lost, IN_WINDOW, fallback_last=IN_WINDOW - timedelta(hours=40)).due


def test_in_the_cloud_a_request_needs_the_ledger_saved_by_the_previous_run():
    last = IN_WINDOW - timedelta(hours=30)
    ok = _hist((last, "ok"), run=41)
    assert sleeper.player_map_budget(ok, IN_WINDOW, carried=True, run=42).due
    # run 42 did not save its ledger: run 43 cannot tell whether it requested
    b = sleeper.player_map_budget(ok, IN_WINDOW, carried=True, run=43)
    assert not b.due and b.state == "unconfirmed" and not b.needs_person
    # a ledger never stamped by a run, a run number unknown, or a stamp from
    # a LATER run (a rolled-back cache) all wait
    for hist, run in ((_hist((last, "ok")), 42), (ok, None), (_hist((last, "ok"), run=50), 42)):
        assert sleeper.player_map_budget(hist, IN_WINDOW, carried=True, run=run).state \
            == "unconfirmed"
    # locally the disk is the ledger's home; no run number needed
    assert sleeper.player_map_budget(_hist((last, "ok")), IN_WINDOW).due


def test_a_malformed_run_stamp_rejects_the_ledger():
    for bad in ("41", True, 0, -3, 4.5):
        lines, why = sleeper.valid_player_map_ledger(
            {"requests": [{"at": IN_WINDOW.isoformat(), "outcome": "ok"}], "checked_run": bad})
        assert lines == [] and why, bad


def test_force_does_not_bypass_the_budget(tmp_path):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    _seed(cache, IN_WINDOW - timedelta(hours=2))
    client = _run(pw, m, IN_WINDOW, FakeClient(), force=True)
    assert client.player_calls == 0
    # ...nor with no ledger at all: --force is not a bootstrap
    cache2, m2 = _cache(tmp_path / "lost")
    assert _run(pw, m2, IN_WINDOW, FakeClient(), force=True).player_calls == 0
    assert json.loads((cache2 / sleeper.PLAYER_MAP_STATUS).read_text())["state"] == "recovery"
    assert not (cache2 / sleeper.PLAYER_MAP_LEDGER).exists()     # nothing invented


def test_the_request_is_ledgered_before_the_get_and_a_failure_does_not_loop(tmp_path):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    _seed(cache, IN_WINDOW - timedelta(hours=25))
    client = FakeClient(fail=True)
    now = IN_WINDOW
    for _ in range(40):                        # ten hours of 15-minute runs
        _run(pw, Manifest.load(cache, 2026), now, client)
        now += timedelta(minutes=15)
    assert client.player_calls == 1
    ledger = sleeper.read_player_map_ledger(cache)
    assert ledger[-1]["outcome"] == "failed"
    assert ledger[-1]["at"] == IN_WINDOW.isoformat(timespec="seconds")   # not restamped
    # And it retries the next day, not before.
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW + timedelta(hours=24), client)
    assert client.player_calls == 2


def test_a_crash_mid_request_still_counts(tmp_path, monkeypatch):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    _seed(cache, IN_WINDOW - timedelta(hours=25))

    class Boom(FakeClient):
        def players(self):
            self.player_calls += 1
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        _run(pw, m, IN_WINDOW, Boom())
    assert sleeper.read_player_map_ledger(cache)[-1]["outcome"] == "requested"
    for later in (timedelta(minutes=15), timedelta(hours=9)):
        assert not sleeper.player_map_budget(sleeper.read_player_map_history(cache),
                                             IN_WINDOW + later).due


def test_a_bootstrap_run_makes_one_request_and_a_repeat_makes_none(tmp_path):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    client = FakeClient()
    _run(pw, Manifest.load(cache, 2026), EVENING, client, bootstrap=True)
    _run(pw, Manifest.load(cache, 2026), EVENING + timedelta(minutes=15), client,
         bootstrap=True, force=True)
    assert client.player_calls == 1
    # the bootstrap left a trustworthy ledger; the budget governs from here
    assert sleeper.read_player_map_history(cache).problem == ""


def test_a_lost_ledger_with_a_fresh_map_refuses_the_bootstrap(tmp_path):
    pw = _pull_week()
    cache, m = _cache(tmp_path)
    good = cache / "sleeper_players.json"
    good.write_text(json.dumps({"1": {}}), encoding="utf-8")
    m.record("sleeper_players", path=good, rows=1, source="t",
             as_of=EVENING - timedelta(hours=3))
    m.save()
    client = _run(pw, Manifest.load(cache, 2026), EVENING, FakeClient(), bootstrap=True)
    assert client.player_calls == 0
    st = json.loads((cache / sleeper.PLAYER_MAP_STATUS).read_text())
    assert st["state"] == "bootstrap-refused"


def test_two_scheduled_runs_and_a_manual_force_make_one_request(tmp_path):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    _seed(cache, IN_WINDOW - timedelta(hours=26))
    client = FakeClient()
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW, client)
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW + timedelta(minutes=15), client)
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW + timedelta(minutes=40), client,
         force=True)
    assert client.player_calls == 1


def test_every_run_stamps_the_ledger_checked_without_touching_request_times(tmp_path):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    _seed(cache, IN_WINDOW - timedelta(hours=3))
    before = sleeper.read_player_map_ledger(cache)
    _run(pw, Manifest.load(cache, 2026), IN_WINDOW, FakeClient())
    h = sleeper.read_player_map_history(cache)
    assert list(h.lines) == before and h.checked == IN_WINDOW


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
    _seed(cache, pulled)
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

    # A ledger stamped in the future is refused as history: laid down only as
    # bootstrap evidence, and the budget reads it as RECOVERY NEEDED.
    stored = store / carryover.INPUTS_DIR / "season2026" / sleeper.PLAYER_MAP_LEDGER
    stored.write_text(json.dumps({"requests": [
        {"at": (IN_WINDOW + timedelta(days=3)).isoformat(), "outcome": "ok"}]}),
        encoding="utf-8")
    other = tmp_path / "c" / "season2026"
    other.mkdir(parents=True)
    Manifest(other, {}, 2026).save()
    report = carryover.restore_inputs(store, other, season=2026, now=IN_WINDOW)
    assert any(sleeper.PLAYER_MAP_LEDGER in v.name and not v.accepted
               and "bootstrap evidence" in v.reason for v in report.verdicts)
    b = sleeper.player_map_budget(sleeper.read_player_map_history(other), IN_WINDOW,
                                  carried=True, run=5)
    assert not b.due and b.state == "recovery"


def test_an_unreadable_ledger_never_opens_the_budget(tmp_path):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    (cache / sleeper.PLAYER_MAP_LEDGER).write_text("{not json", encoding="utf-8")
    h = sleeper.read_player_map_history(cache)
    assert h.lines == () and "unreadable" in h.problem
    client = FakeClient()
    for now in (IN_WINDOW, LATE_WINDOW, EVENING):
        _run(pw, Manifest.load(cache, 2026), now, client)
    assert client.player_calls == 0
    # the unreadable file is left for a person to look at, not overwritten
    assert (cache / sleeper.PLAYER_MAP_LEDGER).read_text(encoding="utf-8") == "{not json"


def test_recovery_needed_is_said_on_the_page(tmp_path):
    cache, _ = _cache(tmp_path)
    b = sleeper.player_map_budget(sleeper.read_player_map_history(cache), EVENING)
    sleeper.write_player_map_status(cache, EVENING, b)
    note = sleeper.player_map_status_note(cache)
    assert "PAUSED" in note and "RECOVERY NEEDED" in note and "bootstrap" in note
    # a routine wait says nothing
    ok = sleeper.player_map_budget(_hist((EVENING - timedelta(hours=2), "ok")), EVENING)
    sleeper.write_player_map_status(cache, EVENING, ok)
    assert sleeper.player_map_status_note(cache) == ""
    # a carry that has not confirmed for 6 h past due is said too
    stuck = sleeper.player_map_budget(_hist((EVENING - timedelta(hours=31), "ok")), EVENING,
                                      carried=True, run=9)
    sleeper.write_player_map_status(cache, EVENING, stuck)
    assert "PAUSED" in sleeper.player_map_status_note(cache)


def test_the_board_shows_the_recovery_note(tmp_path):
    scn = _load_script("budget_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load_script("budget_cli", "scripts/weekly/dashboard.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    directory = root / "season2026"
    b = sleeper.player_map_budget(sleeper.PlayerMapHistory(problem="no request ledger"),
                                  scn.NOW)
    sleeper.write_player_map_status(directory, scn.NOW, b)
    out = tmp_path / "out"
    cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch"),
              "--now", scn.NOW.isoformat()])
    html = (out / "dashboard_latest.html").read_text("utf-8")
    assert "RECOVERY NEEDED" in html and "DEGRADED" in html


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


def _cloud_run(pw, tmp_path, store, i, now, client, *, run, save=True, bootstrap=False,
               attempt=1, force=False):
    """One hosted run: empty runner, restore the carry, pull, publish (unless
    this run's cache save is lost)."""
    cache = tmp_path / f"runner{i}" / "season2026"
    cache.mkdir(parents=True)
    Manifest(cache, {}, 2026).save()
    carryover.restore_inputs(store, cache, season=2026, now=now)
    m = Manifest.load(cache, 2026)
    pw.pull_sleeper(m, now, force, True, client=client, carried=True, run=run,
                    bootstrap=bootstrap, attempt=attempt)
    m.save()
    if save:
        carryover.publish_inputs(cache, store, season=2026, now=now)
    return cache


def test_two_fresh_cloud_runners_share_one_daily_request_through_the_carry(tmp_path):
    """The cloud shape: every run starts on an empty runner, restores the
    carried inputs, pulls, publishes. After the one-time bootstrap, runs 15
    minutes apart make ONE request, and keep the first's pull time."""
    pw = _pull_week()
    store = tmp_path / "store"
    client = FakeClient()
    stamps = []
    for i, now in enumerate((IN_WINDOW, IN_WINDOW + timedelta(minutes=15),
                             IN_WINDOW + timedelta(minutes=30))):
        cache = _cloud_run(pw, tmp_path, store, i, now, client, run=100 + i,
                           bootstrap=(i == 0))
        stamps.append(Manifest.load(cache, 2026).get("sleeper_players").as_of)
        assert Manifest.load(cache, 2026).get("sleeper_players").error == ""
    assert client.player_calls == 1
    assert stamps == [IN_WINDOW.isoformat(timespec="seconds")] * 3


def test_a_fresh_cloud_deployment_without_a_bootstrap_requests_nothing(tmp_path):
    pw = _pull_week()
    store = tmp_path / "store"
    client = FakeClient()
    for i in range(6):
        cache = _cloud_run(pw, tmp_path, store, i, IN_WINDOW + timedelta(minutes=15 * i),
                           client, run=10 + i)
    assert client.player_calls == 0
    assert "RECOVERY NEEDED" in sleeper.player_map_status_note(cache)


def test_a_day_of_cloud_runs_with_a_delay_makes_exactly_one_more_request(tmp_path):
    """Bootstrap at 12:52 ET, then 15-minute runs, with the schedule stalling
    from 12:40 to 16:00 ET the next day: the first run after the 24 h mark
    requests, whatever the hour."""
    pw = _pull_week()
    store = tmp_path / "store"
    client = FakeClient()
    t0 = datetime(2026, 9, 23, 16, 52, tzinfo=UTC)
    _cloud_run(pw, tmp_path, store, 0, t0, client, run=1, bootstrap=True)
    now, run, i = t0, 1, 0
    requested_at = []
    while now < t0 + timedelta(hours=30):
        now += timedelta(minutes=15)
        if datetime(2026, 9, 24, 16, 40, tzinfo=UTC) <= now < datetime(2026, 9, 24, 20, 0,
                                                                       tzinfo=UTC):
            continue                                   # GitHub dropped these
        run, i = run + 1, i + 1
        before = client.player_calls
        _cloud_run(pw, tmp_path, store, i, now, client, run=run)
        if client.player_calls > before:
            requested_at.append(now)
    assert client.player_calls == 2
    assert requested_at == [datetime(2026, 9, 24, 20, 7, tzinfo=UTC)]


def test_a_cache_that_stops_saving_stops_requests(tmp_path):
    """Every save after the first is lost: each runner restores the same old
    ledger. The run numbers never line up again, so no run requests after
    the one whose save was lost — not one per 15 minutes."""
    pw = _pull_week()
    store = tmp_path / "store"
    client = FakeClient()
    t0 = IN_WINDOW - timedelta(hours=24, minutes=5)    # run 2 inside the day, run 3 past it
    _cloud_run(pw, tmp_path, store, 0, t0, client, run=1, bootstrap=True)
    _cloud_run(pw, tmp_path, store, 1, IN_WINDOW - timedelta(minutes=15), client, run=2)
    assert client.player_calls == 1
    for i in range(3, 40):                           # ~9 hours of runs, none saved
        _cloud_run(pw, tmp_path, store, i, IN_WINDOW + timedelta(minutes=15 * (i - 3)),
                   client, run=i, save=False)
    assert client.player_calls == 2                  # run 3 requested; nothing after


# ------------------------------------------- lost saves never buy a request
#
# The acceptance criterion: no two ACTUAL requests within 24 h, whatever the
# pattern of lost saves, re-runs, --force and bootstrap inputs. Runs are
# simulated end to end (empty runner, restore, pull, publish or lose the
# save) with FakeClient counting GETs; nothing touches the network.

T106 = datetime(2026, 9, 24, 17, 2, tzinfo=UTC)       # Astra's timeline


class _Clock:
    """Drives _cloud_run and records when the player map was actually GET."""

    def __init__(self, pw, tmp_path, client=None):
        self.pw, self.tmp, self.store = pw, tmp_path, tmp_path / "store"
        self.client = client or FakeClient()
        self.i = 0
        self.requests: list[datetime] = []
        self.last_cache: Path | None = None

    def run(self, now, run, **kw):
        before = self.client.player_calls
        self.i += 1
        try:
            self.last_cache = _cloud_run(self.pw, self.tmp, self.store, self.i, now,
                                         self.client, run=run, **kw)
        except KeyboardInterrupt:                      # the runner died mid-GET
            self.last_cache = None
        if self.client.player_calls > before:
            self.requests.append(now)
        return self.last_cache

    def status(self):
        return json.loads((self.last_cache / sleeper.PLAYER_MAP_STATUS).read_text())


def _no_two_within_a_day(times):
    for a, b in zip(times, times[1:]):
        assert b - a >= sleeper.PLAYER_MAP_MIN_INTERVAL, (a, b)


class _Crash(FakeClient):
    def players(self):
        self.player_calls += 1
        raise KeyboardInterrupt


@pytest.mark.parametrize("lost", ["ok", "failed", "crash"])
def test_a_lost_save_after_a_request_holds_the_next_one_for_a_day(tmp_path, lost):
    """Astra's reproduction at 7d335ab: run 107 requests and its save is lost;
    run 108 restores 106's ledger, found the chain broken and (then) stamped
    only its run number, so run 109, 15 minutes later, requested again. Now
    run 108 stamps a gap mark the run number cannot clear, and nothing
    requests until 24 h after it — whether 107's GET succeeded, failed, or
    killed the runner."""
    pw = _pull_week()
    c = _Clock(pw, tmp_path)
    c.run(T106, 106, bootstrap=True)
    t107 = datetime(2026, 9, 25, 17, 30, tzinfo=UTC)
    if lost == "crash":
        c.client = _Crash()
        c.run(t107, 107, save=False)                   # counted: the GET began
        c.client = FakeClient()
    else:
        c.client._fail = lost == "failed"
        c.run(t107, 107, save=False)
        c.client._fail = False
    assert len(c.requests) == 2
    t108 = t107 + timedelta(minutes=15)
    cache = c.run(t108, 108)
    h = sleeper.read_player_map_history(cache)
    assert c.status()["state"] == "unconfirmed"
    assert h.gap_seen == t108 and h.checked_run == 108
    assert [x["at"] for x in h.lines][-1] == T106.isoformat(timespec="seconds")  # kept
    now, run = t108, 108
    while now < t108 + timedelta(hours=26):
        now, run = now + timedelta(minutes=15), run + 1
        c.run(now, run)
        if now == t108 + timedelta(minutes=15):
            assert c.status()["state"] == "held"         # run 109: no second request
    _no_two_within_a_day([T106, t107] + c.requests[2:])
    # ...and it resumes on its own once the gap is a day old: run 108 + 24 h.
    assert c.requests[2:] == [t108 + timedelta(hours=24)]
    assert sleeper.read_player_map_history(c.last_cache).lines[-1]["at"] \
        == (t108 + timedelta(hours=24)).isoformat(timespec="seconds")


def test_the_run_number_stamp_never_erases_the_gap_mark(tmp_path):
    cache, _ = _cache(tmp_path)
    _seed(cache, T106)
    gap = T106 + timedelta(hours=3)
    assert sleeper.note_player_map_check(cache, gap, 50, gap=gap)
    for k in range(1, 5):                              # later, contiguous, no gap
        sleeper.note_player_map_check(cache, gap + timedelta(minutes=15 * k), 50 + k)
    h = sleeper.read_player_map_history(cache)
    assert h.gap_seen == gap and h.checked_run == 54
    # An older gap never moves the mark back; a newer one moves it on.
    sleeper.note_player_map_check(cache, gap, 55, gap=gap - timedelta(hours=2))
    assert sleeper.read_player_map_history(cache).gap_seen == gap
    sleeper.note_player_map_check(cache, gap, 56, gap=gap + timedelta(hours=1))
    assert sleeper.read_player_map_history(cache).gap_seen == gap + timedelta(hours=1)
    # A request line added later keeps the mark too.
    sleeper.note_player_map_request(cache, gap + timedelta(days=2), outcome="ok", run=57)
    assert sleeper.read_player_map_history(cache).gap_seen == gap + timedelta(hours=1)


def test_a_gap_seen_while_waiting_is_not_erased_by_the_wait():
    """At 7d335ab the 24 h wait was checked first, so a gap found inside the
    day was never looked at and the run's stamp closed it silently."""
    last = IN_WINDOW - timedelta(hours=10)
    b = sleeper.player_map_budget(_hist((last, "ok"), run=40), IN_WINDOW, carried=True, run=42)
    assert not b.due and b.state == "unconfirmed" and b.gap_at == IN_WINDOW
    assert b.next_allowed == IN_WINDOW + timedelta(hours=24)       # not last + 24 h
    held = replace(_hist((last, "ok"), run=42), gap_seen=IN_WINDOW)
    for later in (timedelta(hours=14, minutes=1), timedelta(hours=23, minutes=59)):
        b = sleeper.player_map_budget(held, IN_WINDOW + later, carried=True, run=43)
        assert not b.due and b.state == "held" and b.gap_at is None
    b = sleeper.player_map_budget(held, IN_WINDOW + timedelta(hours=24), carried=True, run=43)
    assert b.due and b.gap_at is None
    # A gap mark older than the last request adds nothing.
    old = replace(_hist((last, "ok"), run=42), gap_seen=last - timedelta(hours=1))
    assert sleeper.player_map_budget(old, last + timedelta(hours=24), carried=True,
                                     run=43).due


def test_repeated_and_intermittent_lost_saves_never_request_twice_in_a_day(tmp_path):
    """Two days in which saves are lost in bursts and at random (fixed seed),
    then two days in which they all work. No two requests are ever inside a
    day; while saves keep failing the gap mark keeps moving and requests stop
    (the page says so once the map is 30 h old); once saves work, requests
    resume a day after the last gap."""
    import random
    rng = random.Random(20260924)
    pw = _pull_week()
    c = _Clock(pw, tmp_path)
    c.run(T106, 1, bootstrap=True)
    now, run, last_gap, paused = T106, 1, None, False
    while now < T106 + timedelta(days=4):
        now, run = now + timedelta(minutes=15), run + 1
        stormy = now < T106 + timedelta(days=2)
        burst = stormy and (now - T106) % timedelta(hours=9) < timedelta(hours=1)
        save = not (stormy and (burst or rng.random() < 0.15))
        cache = c.run(now, run, save=save)
        if json.loads((cache / sleeper.PLAYER_MAP_STATUS).read_text())["state"] \
                == "unconfirmed":
            last_gap = now
        paused = paused or "PAUSED" in sleeper.player_map_status_note(cache)
    _no_two_within_a_day(c.requests)
    assert paused
    calm = [t for t in c.requests if t >= T106 + timedelta(days=2)]
    assert calm and calm[0] == last_gap + timedelta(hours=24)


def test_a_re_run_of_the_same_run_number_never_requests(tmp_path):
    """GitHub's re-run keeps the run number (GITHUB_RUN_ATTEMPT goes up) and
    the cache key (per run id). If attempt 1 requested and lost its save,
    attempt 2 restores run 106's ledger — contiguous by number — and at
    7d335ab requested again. A re-run is a gap."""
    pw = _pull_week()
    c = _Clock(pw, tmp_path)
    c.run(T106, 106, bootstrap=True)
    t107 = datetime(2026, 9, 25, 17, 30, tzinfo=UTC)
    c.run(t107, 107, save=False)
    for k, attempt in enumerate((2, 3), start=1):
        c.run(t107 + timedelta(minutes=20 * k), 107, attempt=attempt)
        assert c.status()["state"] == "unconfirmed"
    assert c.run(t107 + timedelta(minutes=60), 108) and c.status()["state"] == "held"
    # A re-run whose first attempt DID save restores its own stamp: also a gap.
    c.run(t107 + timedelta(minutes=70), 108, attempt=2)
    assert c.status()["state"] == "unconfirmed"
    # An unknown attempt in the cloud is a gap as well.
    b = sleeper.player_map_budget(_hist((T106, "ok"), run=106), t107, carried=True,
                                  run=107, attempt=None)
    assert not b.due and b.state == "unconfirmed"
    assert len(c.requests) == 2 and c.requests[1] == t107


def test_force_and_bootstrap_do_not_bypass_a_gap_hold(tmp_path):
    pw = _pull_week()
    c = _Clock(pw, tmp_path)
    c.run(T106, 106, bootstrap=True)
    t107 = datetime(2026, 9, 25, 17, 30, tzinfo=UTC)
    c.run(t107, 107, save=False)
    c.run(t107 + timedelta(minutes=15), 108)                       # the gap
    c.run(t107 + timedelta(minutes=30), 109, force=True)
    c.run(t107 + timedelta(minutes=45), 110, bootstrap=True, force=True)
    c.run(t107 + timedelta(hours=20), 111, bootstrap=True, attempt=2)
    assert c.requests == [T106, t107]
    assert c.status()["state"] == "unconfirmed"                    # the re-run's own gap


def test_a_re_run_cannot_bootstrap(tmp_path):
    """A bootstrap dispatch whose save was lost, re-run: attempt 1 may have
    requested and its map went with the save, so the old map cannot refuse
    it. The re-run is refused; a person dispatches a NEW run."""
    lost = sleeper.PlayerMapHistory(problem="no request ledger")
    b = sleeper.player_map_budget(lost, EVENING, carried=True, run=7, attempt=2,
                                  bootstrap=True)
    assert not b.due and b.state == "bootstrap-refused" and "re-run" in b.reason
    assert sleeper.player_map_budget(lost, EVENING, carried=True, run=7, attempt=1,
                                     bootstrap=True).due
    pw = _pull_week()
    c = _Clock(pw, tmp_path)
    c.run(EVENING, 7, bootstrap=True, save=False)
    c.run(EVENING + timedelta(minutes=20), 7, bootstrap=True, attempt=2)
    c.run(EVENING + timedelta(minutes=35), 8)
    assert c.requests == [EVENING]
    assert "RECOVERY NEEDED" in sleeper.player_map_status_note(c.last_cache)


def test_evidence_on_a_rejected_ledger_blocks_a_bootstrap():
    """A future-dated ledger is still recovery (not a block forever), but a
    readable gap mark or an earlier request on it is evidence, like the map."""
    fut = IN_WINDOW + timedelta(days=3)
    b = sleeper.player_map_budget(_hist((IN_WINDOW - timedelta(hours=3), "ok"), (fut, "ok")),
                                  IN_WINDOW, bootstrap=True)
    assert not b.due and b.state == "bootstrap-refused"
    assert b.next_allowed == IN_WINDOW + timedelta(hours=21)
    assert sleeper.player_map_budget(_hist((fut, "ok")), IN_WINDOW, bootstrap=True).due
    # A future gap mark is itself recovery, and does not block a bootstrap.
    h = replace(_hist((IN_WINDOW - timedelta(hours=30), "ok"), run=9), gap_seen=fut)
    assert sleeper.player_map_budget(h, IN_WINDOW, carried=True, run=10).state == "recovery"
    assert sleeper.player_map_budget(h, IN_WINDOW, carried=True, run=10, bootstrap=True).due


def test_the_gap_mark_is_validated():
    base = {"requests": [{"at": IN_WINDOW.isoformat(), "outcome": "ok"}]}
    for bad in ("yesterday", 5, "2026-09-24T10:00:00"):          # no timezone either
        lines, why = sleeper.valid_player_map_ledger({**base, "gap_seen": bad})
        assert lines == [] and "gap mark" in why, bad
    assert sleeper.valid_player_map_ledger({**base, "gap_seen": None})[1] == ""
    assert sleeper.valid_player_map_ledger({**base, "gap_seen": IN_WINDOW.isoformat()})[1] == ""


def test_a_held_budget_that_outlasts_the_map_says_so_on_the_page(tmp_path):
    cache, _ = _cache(tmp_path)
    last = IN_WINDOW - timedelta(hours=31)
    h = replace(_hist((last, "ok"), run=42), gap_seen=IN_WINDOW - timedelta(hours=2))
    b = sleeper.player_map_budget(h, IN_WINDOW, carried=True, run=43)
    assert b.state == "held"
    sleeper.write_player_map_status(cache, IN_WINDOW, b)
    note = sleeper.player_map_status_note(cache)
    assert "PAUSED" in note and "gap" in note
    # ...but not while the map is under 30 h old.
    b = sleeper.player_map_budget(replace(h, lines=_hist((last + timedelta(hours=2), "ok")).lines),
                                  IN_WINDOW, carried=True, run=43)
    sleeper.write_player_map_status(cache, IN_WINDOW, b)
    assert b.state == "held" and sleeper.player_map_status_note(cache) == ""


# --------------------------- a rejected ledger still refuses a bootstrap
#
# Astra's reproduction at 471980c, from a real file: a ledger with a request
# an hour old and a gap mark 30 min old, whose checked_run is "broken", was
# read as NO history at all, so a bootstrap with an old (or no) map
# requested. The malformed field still makes the ledger untrustworthy (no
# automatic request, RECOVERY NEEDED); what still parses now blocks the
# bootstrap, and the file is left exactly as it was.

NOW_E = datetime(2026, 9, 24, 20, 0, tzinfo=UTC)
AN_HOUR_AGO = "2026-09-24T19:00:00+00:00"
HALF_AN_HOUR_AGO = "2026-09-24T19:30:00+00:00"


def _ledger_file(cache: Path, **fields) -> bytes:
    blob = {"endpoint": "players/nfl", "checked": HALF_AN_HOUR_AGO, "checked_run": 9,
            "gap_seen": HALF_AN_HOUR_AGO,
            "requests": [{"at": AN_HOUR_AGO, "outcome": "requested"}]}
    blob.update(fields)
    raw = json.dumps(blob).encode()
    (cache / sleeper.PLAYER_MAP_LEDGER).write_bytes(raw)
    return raw


def _with_map(cache: Path, as_of: datetime | None) -> None:
    m = Manifest.load(cache, 2026)
    if as_of is not None:
        f = cache / "sleeper_players.json"
        f.write_text(json.dumps({"1": {}}), encoding="utf-8")
        m.record("sleeper_players", path=f, rows=1, source="t", as_of=as_of)
    m.save()


def _pull_from_disk(pw, cache: Path, *, bootstrap: bool) -> tuple[int, dict]:
    client = FakeClient()
    pw.pull_player_map(Manifest.load(cache, 2026), client, NOW_E, carried=True, run=10,
                       attempt=1, bootstrap=bootstrap)
    return client.player_calls, json.loads((cache / sleeper.PLAYER_MAP_STATUS).read_text())


@pytest.mark.parametrize("fields", [
    {"checked_run": "broken"},                                          # Astra's case
    {"checked": "not a time"},
    {"requests": [{"at": AN_HOUR_AGO, "outcome": "requested"},
                  {"at": "garbage", "outcome": "ok"}]},                 # another row bad
    {"requests": [{"at": AN_HOUR_AGO, "outcome": "failed"}, "not a row"]},
    {"requests": [{"at": AN_HOUR_AGO, "outcome": "who knows"}], "gap_seen": None},
    {"requests": [{"at": "garbage"}], "checked_run": "broken"},        # only the gap mark
    {"gap_seen": "garbage"},                                            # only the request
], ids=["checked_run", "checked", "other-row", "not-a-row", "unknown-outcome",
        "gap-mark-only", "request-only"])
@pytest.mark.parametrize("map_as_of", [datetime(2026, 9, 22, 20, 0, tzinfo=UTC), None],
                         ids=["old-map", "no-map"])
def test_a_malformed_field_does_not_erase_recent_evidence(tmp_path, fields, map_as_of):
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    _with_map(cache, map_as_of)
    raw = _ledger_file(cache, **fields)
    h = sleeper.read_player_map_history(cache)
    assert h.problem and not h.lines and h.evidence          # rejected, evidence kept
    calls, st = _pull_from_disk(pw, cache, bootstrap=True)
    assert calls == 0 and st["state"] == "bootstrap-refused"
    assert "2026-09-2" in st["reason"] and "retry the bootstrap after" in st["reason"]
    calls, st = _pull_from_disk(pw, cache, bootstrap=False)  # automatic: still recovery
    assert calls == 0 and st["state"] == "recovery"
    # Never sanitised: the rejected file is exactly what was on disk.
    assert (cache / sleeper.PLAYER_MAP_LEDGER).read_bytes() == raw


def test_evidence_only_blocks_it_never_authorises(tmp_path):
    """A rejected ledger whose readable stamps are all over a day old: the
    bootstrap proceeds as before (the existing manual-recovery policy), and
    without one nothing requests however old the evidence."""
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    _with_map(cache, datetime(2026, 9, 22, 20, 0, tzinfo=UTC))
    _ledger_file(cache, checked_run="broken", gap_seen="2026-09-23T18:00:00+00:00",
                 requests=[{"at": "2026-09-23T17:00:00+00:00", "outcome": "ok"}])
    assert _pull_from_disk(pw, cache, bootstrap=False)[0] == 0
    calls, st = _pull_from_disk(pw, cache, bootstrap=True)
    assert calls == 1 and st["state"] == "due"
    assert sleeper.read_player_map_history(cache).problem == ""   # the bootstrap's own ledger


def test_a_ledger_that_is_not_json_shows_nothing(tmp_path):
    """Stated limit: nothing parses, so only the map (and the run logs a
    person reads first) can refuse the bootstrap."""
    pw = _pull_week()
    cache, _ = _cache(tmp_path)
    _with_map(cache, datetime(2026, 9, 22, 20, 0, tzinfo=UTC))
    (cache / sleeper.PLAYER_MAP_LEDGER).write_text('{"requests": [{"at": "2026-09-24T19', "utf-8")
    assert sleeper.read_player_map_history(cache).evidence == ()
    assert _pull_from_disk(pw, cache, bootstrap=True)[0] == 1
    cache2, _ = _cache(tmp_path / "fresh-map")
    _with_map(cache2, NOW_E - timedelta(hours=3))
    (cache2 / sleeper.PLAYER_MAP_LEDGER).write_text("not json", "utf-8")
    assert _pull_from_disk(pw, cache2, bootstrap=True)[0] == 0      # the map still refuses


def test_a_rejected_carried_ledger_still_refuses_a_cloud_bootstrap(tmp_path):
    """The cloud reads the ledger through the carry: a malformed carried
    ledger is laid down (reported refused), so its evidence reaches the
    budget instead of being dropped at restore."""
    pw = _pull_week()
    store = tmp_path / "store"
    seed, _ = _cache(tmp_path / "seed")
    _with_map(seed, datetime(2026, 9, 22, 20, 0, tzinfo=UTC))
    _ledger_file(seed, checked_run="broken")
    carryover.publish_inputs(seed, store, season=2026, now=NOW_E - timedelta(minutes=20))
    c = FakeClient()
    for i, (now, boot) in enumerate(((NOW_E, True), (NOW_E + timedelta(minutes=15), False))):
        cache = _cloud_run(pw, tmp_path, store, i, now, c, run=10 + i, bootstrap=boot)
    assert c.player_calls == 0
    assert "RECOVERY NEEDED" in sleeper.player_map_status_note(cache)


def test_the_sequence_adapter_keeps_the_reason_in_problem():
    b = sleeper.player_map_budget([{"at": "garbage", "outcome": "ok"}], NOW_E)
    assert b.state == "recovery" and "no readable time or outcome" in b.reason
