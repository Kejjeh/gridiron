"""Offline regression for the five-minute Sleeper sync.

Every test here runs against a synthetic payload and a fake client. Nothing in
this file touches the network, so the failure modes that matter — the ones that
only happen on a bad afternoon — are exercised on every run instead of being
waited for.
"""
from __future__ import annotations

import json
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


def test_an_abandoned_lock_is_taken_over_rather_than_wedging_the_schedule(cache):
    (cache / ls.LOCK_NAME).write_text(json.dumps(
        {"pid": 1, "holder": "dead", "started_at": ls._iso(T0)}), encoding="utf-8")
    late = T0 + timedelta(seconds=ls.LOCK_STALE_SECONDS + 60)
    assert _sync(cache, FakeClient(), now=late).ok


def test_the_lock_is_released_when_a_publish_raises(cache):
    with pytest.raises(ValueError):
        with ls.single_writer(cache, now=T0):
            raise ValueError("boom")
    assert not (cache / ls.LOCK_NAME).exists()
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
    """Astra's gate: a lock only one of two writers takes is not a lock.

    The weekly puller used to overwrite one fixed filename and record the
    manifest separately, which left a window where the bytes were new and the
    as_of beside them was old. It now goes through `publish_snapshot`, so both
    writers take one lock and both commit (path, as_of) together.
    """
    pw = _pull_week_module()
    src = (Path(pw.__file__)).read_text(encoding="utf-8")
    assert "ls.publish_snapshot(" in src
    assert 'atomic(snap_path' not in src        # the old un-locked write is gone

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
