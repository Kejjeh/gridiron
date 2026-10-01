"""Offline regression for the five-minute Sleeper sync.

Every test here runs against a synthetic payload and a fake client. Nothing in
this file touches the network, so the failure modes that matter — the ones that
only happen on a bad afternoon — are exercised on every run instead of being
waited for.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gridiron import livesync as ls
from gridiron.ingest import Manifest
from gridiron.league_config import MY_SLEEPER_USERNAME, NUM_TEAMS, SLEEPER_LEAGUE_ID

T0 = datetime(2026, 9, 18, 17, 0, 0, tzinfo=timezone.utc)
OWNER_UID = "uid-owner"


class FakeState:
    def __init__(self, week=2, season=2026, season_type="regular"):
        self.week, self.season, self.season_type = week, season, season_type
        self.display_week = week
        self.season_start_date = "2026-09-03"


def _payload(*, week=2, season=2026, league_id=SLEEPER_LEAGUE_ID,
             rosters=NUM_TEAMS, matchups=True, owner=True, scoring=None,
             season_type="regular"):
    users = [{"user_id": f"uid-{i}", "display_name": f"team{i}"}
             for i in range(NUM_TEAMS)]
    if owner:
        users[0] = {"user_id": OWNER_UID, "display_name": MY_SLEEPER_USERNAME}
    return {
        "as_of": "2026-09-18T17:00:00+00:00",
        "league_id": league_id,
        "week": week,
        "state": {"season": season, "week": week, "display_week": week,
                  "season_type": season_type, "season_start_date": ""},
        "league": {"league_id": league_id, "season": str(season),
                   "scoring_settings": scoring or {"rec": 0.5, "pass_td": 4},
                   "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE"],
                   "settings": {"playoff_teams": 6, "waiver_budget": 100}},
        "users": users,
        "rosters": [{"roster_id": i + 1,
                     "owner_id": OWNER_UID if (i == 0 and owner) else f"uid-{i}",
                     "starters": ["1", "2"], "players": ["1", "2", "3"],
                     "reserve": ["9"]} for i in range(rosters)],
        "matchups": ([{"roster_id": i + 1, "matchup_id": (i // 2) + 1,
                       "points": 0.0} for i in range(rosters)]
                     if matchups else []),
    }


class FakeClient:
    """Sequential GETs, like the real thing. `states` lets a test move the NFL
    week underneath the read, which is the rollover case."""

    def __init__(self, payload=None, states=None, raises=None):
        self.payload = payload if payload is not None else _payload()
        self._states = list(states or [FakeState(), FakeState()])
        self.raises = raises
        self.calls = 0

    def state(self):
        s = self._states[min(self.calls, len(self._states) - 1)]
        self.calls += 1
        return s

    def snapshot(self, week=None):
        if self.raises is not None:
            raise self.raises
        out = dict(self.payload)
        if week is not None:
            out["week"] = int(week)
        return out


@pytest.fixture()
def cache(tmp_path: Path) -> Path:
    d = tmp_path / "season2026"
    d.mkdir(parents=True)
    return d


def _sync(cache, client, *, now=T0, **kw):
    return ls.sync_once(client, cache, now=now,
                        manifest=Manifest.load(cache, 2026), **kw)


# --- the happy path, and what it commits ----------------------------------
def test_a_successful_sync_publishes_a_generation_the_manifest_points_at(cache):
    res = _sync(cache, FakeClient())
    assert res.ok and res.published and res.week == 2

    entry = Manifest.load(cache, 2026).get("sleeper_league")
    assert entry is not None
    # The coherence property: the file the manifest names is the file that was
    # written in the same locked section, and its name is a generation, not a
    # reused fixed filename.
    assert entry.path.startswith(ls.GENERATION_PREFIX)
    assert (cache / entry.path).exists()
    assert entry.as_of_dt == T0
    assert entry.rows == NUM_TEAMS and entry.weeks == [2]

    snap = json.loads((cache / entry.path).read_text(encoding="utf-8"))
    assert snap["week"] == 2
    # Never claims to be atomic.
    assert "not an atomic read" in snap["consistency"]
    assert snap["read_window_seconds"] >= 0.0


def test_the_sync_never_fetches_the_player_dump(cache):
    """The whole reason this command exists. A five-minute job that pulled 16 MB
    would be abusing an API that asks for once a day."""
    class Tripwire(FakeClient):
        def players(self):
            raise AssertionError("the five-minute sync must never pull players")

    assert _sync(cache, Tripwire()).ok
    assert not list(cache.glob("sleeper_players*"))
    assert Manifest.load(cache, 2026).get("sleeper_players") is None
    assert ls.PLAYER_DUMP_MIN_INTERVAL_SECONDS == 86_400


# --- failure preserves the last good snapshot -----------------------------
@pytest.mark.parametrize("label,client", [
    ("timeout", FakeClient(raises=TimeoutError("timed out"))),
    ("rate_limit", FakeClient(raises=RuntimeError("Sleeper GET failed: 429"))),
    ("malformed", FakeClient(payload=["not", "an", "object"])),
    ("empty_rosters", FakeClient(payload=_payload(rosters=0))),
    ("partial_rosters", FakeClient(payload=_payload(rosters=7))),
    ("empty_matchups", FakeClient(payload=_payload(matchups=False))),
    ("wrong_league", FakeClient(payload=_payload(league_id="9999999999"))),
    ("wrong_season", FakeClient(payload=_payload(season=2025))),
    ("owner_absent", FakeClient(payload=_payload(owner=False))),
])
def test_a_failed_sync_preserves_the_last_good_snapshot(cache, label, client):
    good = _sync(cache, FakeClient())
    entry_before = Manifest.load(cache, 2026).get("sleeper_league")
    bytes_before = (cache / entry_before.path).read_bytes()

    bad = _sync(cache, client, now=T0 + timedelta(minutes=5))
    assert not bad.ok, label
    assert bad.code in {"fetch_failed", "malformed", "invalid", "partial"}

    entry_after = Manifest.load(cache, 2026).get("sleeper_league")
    # Same file, same bytes, same as_of. A failure makes data older, never
    # newer, and never makes it disappear.
    assert entry_after.path == entry_before.path
    assert (cache / entry_after.path).read_bytes() == bytes_before
    assert entry_after.as_of_dt == good.state.last_success_dt == T0

    state = ls.SyncState.load(cache)
    assert state.last_success == ls._iso(T0)          # unchanged by the failure
    assert state.last_attempt == ls._iso(T0 + timedelta(minutes=5))
    assert state.last_error and state.consecutive_failures == 1


def test_a_404_shaped_empty_payload_is_refused_not_published(cache):
    """Sleeper answers 404 with None, which the adapter turns into {} / []."""
    _sync(cache, FakeClient())
    res = _sync(cache, FakeClient(payload={}), now=T0 + timedelta(minutes=5))
    assert not res.ok and "league" in res.detail


def test_the_first_ever_sync_failing_publishes_nothing(cache):
    res = _sync(cache, FakeClient(raises=TimeoutError("no network")))
    assert not res.ok
    assert ls.current_snapshot(cache, Manifest.load(cache, 2026)) is None
    assert Manifest.load(cache, 2026).get("sleeper_league") is None


def test_recovery_clears_the_failure_streak(cache):
    _sync(cache, FakeClient(raises=TimeoutError("x")))
    _sync(cache, FakeClient(raises=TimeoutError("x")), now=T0 + timedelta(minutes=5))
    assert ls.SyncState.load(cache).consecutive_failures == 2
    ok = _sync(cache, FakeClient(), now=T0 + timedelta(minutes=10))
    assert ok.ok
    st = ls.SyncState.load(cache)
    assert st.consecutive_failures == 0 and st.last_error == ""


# --- restart --------------------------------------------------------------
def test_state_survives_a_restart_and_a_corrupt_state_file_does_not_block(cache):
    _sync(cache, FakeClient())
    reloaded = ls.SyncState.load(cache)            # fresh process
    assert reloaded.last_success == ls._iso(T0) and reloaded.successes == 1
    assert reloaded.next_due() == T0 + timedelta(seconds=300)

    (cache / ls.STATE_NAME).write_text("{ this is not json", encoding="utf-8")
    assert ls.SyncState.load(cache).last_success == ""   # counters lost
    # ...but the sync still runs, and the snapshot is still there.
    assert _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5)).ok


def test_an_unfinished_generation_is_never_read(cache):
    """A generation file with no manifest entry is an incomplete publish."""
    _sync(cache, FakeClient())
    committed = ls.current_snapshot(cache, Manifest.load(cache, 2026))
    (cache / f"{ls.GENERATION_PREFIX}20991231T235959Z.json").write_text(
        '{"week": 99}', encoding="utf-8")
    assert ls.current_snapshot(cache, Manifest.load(cache, 2026)) == committed


# --- concurrency ----------------------------------------------------------
def test_an_overlapping_run_declines_instead_of_double_writing(cache):
    _sync(cache, FakeClient())
    before = Manifest.load(cache, 2026).get("sleeper_league").path
    with ls.single_writer(cache, now=T0 + timedelta(minutes=5), holder="other"):
        res = _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5))
    assert not res.ok and res.code == "busy"
    assert Manifest.load(cache, 2026).get("sleeper_league").path == before


def test_a_manual_run_and_a_scheduled_run_are_the_same_code_path(cache):
    """`--if-due` is the only difference, and it is a gate, not a variant."""
    assert _sync(cache, FakeClient()).ok
    st = ls.SyncState.load(cache)
    assert not st.is_due(T0 + timedelta(minutes=4))     # scheduled: skips
    assert st.is_due(T0 + timedelta(minutes=5))         # scheduled: runs
    # A manual run ignores the gate entirely and always syncs.
    assert _sync(cache, FakeClient(), now=T0 + timedelta(minutes=1)).ok


def test_the_lock_is_released_when_a_publish_raises(cache):
    with pytest.raises(ValueError):
        with ls.single_writer(cache, now=T0):
            raise ValueError("boom")
    # The lock FILE persists by design; the OS lock on it is what was released.
    assert (cache / ls.LOCK_NAME).exists()
    assert _sync(cache, FakeClient()).ok


def test_a_concurrent_writer_s_unrelated_entries_are_not_overwritten(cache):
    """The sync re-reads the manifest inside the lock and touches ONE entry."""
    m = Manifest.load(cache, 2026)
    m.record("weekly_stats", path=cache / "weekly_stats.parquet", rows=5000,
             source="nflreadpy", weeks=[1, 2])
    m.save()
    stale_in_memory = Manifest.load(cache, 2026)      # what the sync started with
    assert _sync(cache, FakeClient()).ok

    after = Manifest.load(cache, 2026)
    assert after.get("weekly_stats") is not None and after.get("weekly_stats").rows == 5000
    assert after.get("sleeper_league") is not None
    assert stale_in_memory.get("sleeper_league") is None   # proves it was re-read


# --- week rollover --------------------------------------------------------
def test_a_week_rollover_mid_read_discards_the_snapshot(cache):
    _sync(cache, FakeClient())
    before = Manifest.load(cache, 2026).get("sleeper_league").path
    # state() is called before and after the snapshot; move the week between.
    rolling = FakeClient(states=[FakeState(week=2), FakeState(week=3),
                                 FakeState(week=3), FakeState(week=3)])
    res = _sync(cache, rolling, now=T0 + timedelta(minutes=5))
    # The first read rolls over; the retry is stable and publishes week 3.
    assert res.ok and res.week == 3
    assert Manifest.load(cache, 2026).get("sleeper_league").path != before

    # A week that keeps moving is never published at all.
    never = FakeClient(states=[FakeState(week=3), FakeState(week=4),
                               FakeState(week=4), FakeState(week=5)])
    res2 = _sync(cache, never, now=T0 + timedelta(minutes=10))
    assert not res2.ok and res2.code == "rollover"


def test_the_read_window_is_recorded_not_assumed_away(cache):
    _sync(cache, FakeClient())
    entry = Manifest.load(cache, 2026).get("sleeper_league")
    snap = json.loads((cache / entry.path).read_text(encoding="utf-8"))
    assert "read_window_seconds" in snap
    assert ls.SyncState.load(cache).read_window_seconds >= 0.0


# --- validation, in its own right -----------------------------------------
def test_validation_names_every_problem_and_separates_partial_from_wrong():
    assert ls.validate_snapshot(_payload()) == ()
    wrong = ls.validate_snapshot(_payload(league_id="42"))
    assert wrong and wrong[0].code == "wrong_league" and not wrong[0].partial
    short = ls.validate_snapshot(_payload(rosters=7))
    assert short and all(p.partial for p in short)


def test_preseason_absence_of_matchups_is_not_a_defect():
    assert ls.validate_snapshot(
        _payload(week=0, matchups=False, season_type="pre")) == ()


# --- settings drift -------------------------------------------------------
def test_settings_drift_is_reported_and_never_marks_anything_verified(cache):
    import gridiron.league_config as lc

    _sync(cache, FakeClient())
    changed = _payload(scoring={"rec": 1.0, "pass_td": 4})   # half-PPR -> full
    res = _sync(cache, FakeClient(payload=changed), now=T0 + timedelta(minutes=5))
    assert res.ok                      # a real change is still real data
    assert "scoring_settings.rec" in res.drift
    state = ls.SyncState.load(cache)
    assert state.settings_drift == ["scoring_settings.rec"]
    # Rule #1: detection is not verification.
    assert lc.SETTINGS_VERIFIED is True
    assert "UNVERIFIED-BY-THIS-TOOL" in "\n".join(
        ls.status_lines(state, cache, now=T0))


def test_an_unchanged_league_reports_no_drift(cache):
    _sync(cache, FakeClient())
    res = _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5))
    assert res.ok and res.drift == ()


# --- status ---------------------------------------------------------------
def test_status_is_offline_and_names_nobody(cache):
    _sync(cache, FakeClient())
    text = "\n".join(ls.status_lines(ls.SyncState.load(cache), cache,
                                     now=T0 + timedelta(minutes=7)))
    assert "last success" in text and "next due" in text and "OVERDUE" in text
    # No owner handle, no team name, no player id leaks into a pasteable status.
    assert MY_SLEEPER_USERNAME not in text
    assert "team0" not in text


# --- the legacy weekly puller shares the same lock and scheme --------------
def _pull_week_module():
    import importlib.util
    import sys
    from gridiron.paths import REPO_ROOT

    spec = importlib.util.spec_from_file_location(
        "pull_week_under_test", REPO_ROOT / "scripts" / "ingest" / "pull_week.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_weekly_puller_publishes_through_the_same_generation_scheme(cache):
    """Astra's gate: a lock only one of two writers takes is not a lock."""
    path = ls.publish_snapshot(cache, _payload(), now=T0,
                               source="api.sleeper.app (read-only)",
                               rows=NUM_TEAMS, week=2, season=2026,
                               holder="pull_week")
    entry = Manifest.load(cache, 2026).get("sleeper_league")
    assert entry.path == path.name and path.name.startswith(ls.GENERATION_PREFIX)
    assert entry.as_of_dt == T0

    # A sync publishing next reads that one and supersedes it coherently.
    res = _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5))
    assert res.ok
    after = Manifest.load(cache, 2026).get("sleeper_league")
    assert after.path != entry.path
    assert after.as_of_dt == T0 + timedelta(minutes=5)
    assert (cache / after.path).exists()


def test_generations_are_pruned_but_the_referenced_one_always_survives(cache):
    for i in range(6):
        ls.publish_snapshot(cache, _payload(), now=T0 + timedelta(minutes=5 * i),
                            source="test", rows=NUM_TEAMS, week=2, season=2026)
    gens = sorted(cache.glob(f"{ls.GENERATION_PREFIX}*.json"))
    assert len(gens) == ls.KEEP_GENERATIONS
    entry = Manifest.load(cache, 2026).get("sleeper_league")
    assert (cache / entry.path).exists()


def test_a_sync_during_a_slow_player_fetch_is_not_rolled_back_by_the_puller(cache):
    """The real interleaving, executed — not asserted about the source text.

    Timeline the weekly puller actually produces:
      t+0   pull_week publishes its own snapshot and holds it in memory
      t+5   a scheduled sync publishes a NEWER snapshot (the 16 MB player dump
            is easily long enough for one, usually several)
      t+9   pull_week finishes and saves its manifest

    If that last save writes back the entry it published at t+0, every sync in
    between is silently reverted and the report ages fresh data by the weekly
    pull's clock.
    """
    pw = _pull_week_module()

    manifest = Manifest.load(cache, 2026)
    ls.publish_snapshot(cache, _payload(), now=T0, source="api.sleeper.app",
                        rows=NUM_TEAMS, week=2, season=2026, holder="pull_week")
    pw._adopt(manifest, "sleeper_league")
    old_pointer = manifest.get("sleeper_league").path

    # ... the player dump downloads; a scheduled sync lands mid-fetch ...
    assert _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5)).ok
    new_pointer = Manifest.load(cache, 2026).get("sleeper_league").path
    assert new_pointer != old_pointer

    # ... and now the puller finishes and commits.
    manifest.record("sleeper_players", path=cache / "sleeper_players.json",
                    rows=11000, source="api.sleeper.app", as_of=T0 + timedelta(minutes=9))
    pw.commit_manifest(manifest, cache, 2026,
                       set(pw.NFLVERSE_SOURCES) | {"crosswalk", "sleeper_players"})

    final = Manifest.load(cache, 2026)
    assert final.get("sleeper_league").path == new_pointer      # sync survives
    assert final.get("sleeper_league").as_of_dt == T0 + timedelta(minutes=5)
    assert (cache / new_pointer).exists()
    assert final.get("sleeper_players").rows == 11000           # puller's own lands


def test_a_manifest_write_that_dies_leaves_the_previous_one_whole(cache):
    """Injected failure: the manifest must be all-or-nothing.

    A truncated manifest is worse than a stale one. It is the only thing that
    says which generation is published, so losing it orphans every snapshot
    file in the directory.
    """
    assert _sync(cache, FakeClient()).ok
    good_manifest = (cache / "manifest.json").read_bytes()
    entry = Manifest.load(cache, 2026).get("sleeper_league")
    good_snapshot = (cache / entry.path).read_bytes()

    real_replace = ls.os.replace
    def die(src, dst):
        if str(dst).endswith("manifest.json"):
            raise OSError("disk full")
        return real_replace(src, dst)

    import gridiron.ingest as ingest_mod
    ingest_mod.os.replace = die
    try:
        res = _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5))
    finally:
        ingest_mod.os.replace = real_replace

    assert not res.ok and res.code == "write_failed"
    # Byte-identical, and still pointing at a snapshot that exists.
    assert (cache / "manifest.json").read_bytes() == good_manifest
    after = Manifest.load(cache, 2026).get("sleeper_league")
    assert after.path == entry.path
    assert (cache / after.path).read_bytes() == good_snapshot
    assert not list(cache.glob("manifest.json.*.part"))      # no scratch left
    with ls.single_writer(cache):                            # lock released
        pass


def test_state_writes_do_not_share_a_scratch_file(cache):
    seen = []
    real = ls.Path.write_text
    def spy(self, *a, **kw):
        if ".part" in self.name:
            seen.append(self.name)
        return real(self, *a, **kw)
    ls.Path.write_text = spy
    try:
        ls.SyncState(last_result="a").save(cache)
        ls.SyncState(last_result="b").save(cache)
    finally:
        ls.Path.write_text = real
    assert len(seen) == 2 and seen[0] != seen[1]
    assert not list(cache.glob("*.part"))


# --- fail-closed validation ------------------------------------------------
@pytest.mark.parametrize("label,mutate", [
    ("league season missing", lambda p: p["league"].pop("season")),
    ("state season missing", lambda p: p["state"].pop("season")),
    ("no scoring_settings", lambda p: p["league"].update(scoring_settings={})),
    ("no roster_positions", lambda p: p["league"].update(roster_positions=[])),
    ("duplicate roster ids",
     lambda p: p["rosters"][3].update(roster_id=p["rosters"][2]["roster_id"])),
    ("malformed roster member", lambda p: p["rosters"].__setitem__(4, "not-an-object")),
    ("malformed user member", lambda p: p["users"].__setitem__(4, None)),
    ("short matchups", lambda p: p.__setitem__("matchups", p["matchups"][:5])),
])
def test_fail_closed_on_a_defective_payload(cache, label, mutate):
    assert _sync(cache, FakeClient()).ok
    entry = Manifest.load(cache, 2026).get("sleeper_league")
    before = (cache / entry.path).read_bytes()

    broken = _payload()
    mutate(broken)
    res = _sync(cache, FakeClient(payload=broken), now=T0 + timedelta(minutes=5))
    assert not res.ok, label
    after = Manifest.load(cache, 2026).get("sleeper_league")
    assert after.path == entry.path and (cache / after.path).read_bytes() == before


def test_the_weekly_puller_validates_before_publishing(cache, monkeypatch):
    """Same gate, both writers. A weekly pull that publishes a wrong-league
    snapshot is exactly as harmful as a scheduled one that does."""
    pw = _pull_week_module()

    class BadClient:
        def __init__(self, *a, **kw): pass
        def snapshot(self, week=None):
            return _payload(league_id="9999999999")

    monkeypatch.setattr(pw, "SleeperReadOnly", BadClient)
    manifest = Manifest.load(cache, 2026)
    pw.pull_sleeper(manifest, T0, force=True, with_players=False)

    # Nothing published, and the refusal is recorded as a failure.
    assert not list(cache.glob(f"{ls.GENERATION_PREFIX}*.json"))
    entry = manifest.get("sleeper_league")
    assert entry is not None and "wrong_league" in entry.error
    assert entry.path == ""     # never succeeded, so nothing to point at


def test_settings_drift_stays_sticky_across_later_clean_syncs(cache):
    """Drift is a standing flag, not a one-run notice.

    It is cleared by a human deciding, not by the next sync happening to see
    the same (changed) settings twice — which it always will.
    """
    _sync(cache, FakeClient())
    changed = _payload(scoring={"rec": 1.0, "pass_td": 4})
    assert _sync(cache, FakeClient(payload=changed),
                 now=T0 + timedelta(minutes=5)).drift

    for i in range(2, 5):                       # later, unremarkable syncs
        res = _sync(cache, FakeClient(payload=changed),
                    now=T0 + timedelta(minutes=5 * i))
        assert res.ok and res.drift == ()       # nothing NEW changed
        state = ls.SyncState.load(cache)
        assert state.settings_drift == ["scoring_settings.rec"]
        assert state.settings_drift_at == ls._iso(T0 + timedelta(minutes=5))
    assert "SETTINGS DRIFT" in "\n".join(
        ls.status_lines(ls.SyncState.load(cache), cache, now=T0))


# --- the lock, with real processes ------------------------------------------
HOLDER = """
import sys, time
sys.path.insert(0, sys.argv[2])
from gridiron import livesync as ls
with ls.single_writer(sys.argv[1], holder="child"):
    print("held", flush=True)
    time.sleep(60)
"""


def _hold_in_child(cache):
    import subprocess, sys
    from gridiron.paths import REPO_ROOT
    proc = subprocess.Popen([sys.executable, "-c", HOLDER, str(cache),
                             str(REPO_ROOT / "src")],
                            stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def test_two_processes_cannot_both_hold_the_lock(cache):
    _sync(cache, FakeClient())
    child = _hold_in_child(cache)
    try:
        res = _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5))
        assert not res.ok and res.code == "busy"
        with pytest.raises(ls.LockBusy):
            with ls.single_writer(cache):
                pass
        # A busy tick touches nothing: not the pointer, not the counters.
        assert ls.SyncState.load(cache).last_attempt == ls._iso(T0)
    finally:
        child.kill(); child.wait()


def test_a_killed_holder_releases_the_lock_with_no_timeout_and_no_reclaim(cache):
    child = _hold_in_child(cache)
    child.kill(); child.wait()
    # The OS dropped it with the process. No age check, no takeover, and the
    # lock FILE is still there — its presence means nothing.
    assert (cache / ls.LOCK_NAME).exists()
    assert _sync(cache, FakeClient()).ok
    assert (cache / ls.LOCK_NAME).exists()          # never unlinked


def test_the_lock_is_not_reentrant_and_publish_does_not_nest_it(cache):
    """`sync_once` holds the lock across fetch and publish; the publish path
    it uses must not try to take it again."""
    with ls.single_writer(cache, now=T0):
        ls._publish_locked(cache, _payload(), now=T0, source="t",
                           rows=NUM_TEAMS, week=2, season=2026)
    assert Manifest.load(cache, 2026).get("sleeper_league") is not None


def test_state_is_read_inside_the_lock_so_a_slow_run_cannot_overwrite_a_newer_one(cache):
    """Two syncs, the first slow. With the state read outside the lock the
    slow one would finish last and write back a stale pointer and counters."""
    class Slow(FakeClient):
        def snapshot(self, week=None):
            # A newer sync completes while this one is mid-fetch. It must be
            # refused (busy), not interleaved.
            inner = _sync(cache, FakeClient(), now=T0 + timedelta(minutes=5))
            assert not inner.ok and inner.code == "busy"
            return super().snapshot(week)
    res = _sync(cache, Slow(), now=T0)
    assert res.ok
    st = ls.SyncState.load(cache)
    assert st.successes == 1 and st.last_success == ls._iso(T0)


def test_regular_week_matchups_must_cover_the_roster_set_exactly(cache):
    _sync(cache, FakeClient())
    dup = _payload()
    for m in dup["matchups"]:
        m["roster_id"] = 1                           # 12 rows, one roster
    assert not _sync(cache, FakeClient(payload=dup), now=T0 + timedelta(minutes=5)).ok
    unknown = _payload()
    unknown["matchups"][0]["roster_id"] = 99
    res = _sync(cache, FakeClient(payload=unknown), now=T0 + timedelta(minutes=10))
    assert not res.ok and "unknown" in res.detail
