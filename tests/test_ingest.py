"""The manifest is where 'how fresh is this?' is answered. It must not lie."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pandas as pd

from gridiron import ingest as ing
from gridiron.freshness import Status

UTC = timezone.utc
NOW = datetime(2026, 9, 17, 13, 0, tzinfo=UTC)


def _manifest(tmp_path):
    return ing.Manifest(tmp_path, season=2026)


def test_round_trips_through_disk(tmp_path):
    m = _manifest(tmp_path)
    path = tmp_path / "weekly_stats.parquet"
    pd.DataFrame({"week": [1, 1, 2]}).to_parquet(path)
    m.record("weekly_stats", path=path, rows=3, source="nflreadpy", weeks=[1, 2])
    m.save()

    again = ing.Manifest.load(tmp_path)
    entry = again.get("weekly_stats")
    assert entry.rows == 3 and entry.weeks == [1, 2]
    assert entry.covers_through_week == 2
    assert again.file("weekly_stats") == path
    assert len(again.read_frame("weekly_stats")) == 3


def test_freshness_uses_the_pull_time_not_the_file_mtime(tmp_path):
    """A file rewritten from cache is not fresher data."""
    m = _manifest(tmp_path)
    path = tmp_path / "injuries.parquet"
    pd.DataFrame({"week": [2]}).to_parquet(path)
    m.record("injuries", path=path, rows=1, source="nflreadpy", weeks=[2],
             as_of=NOW - timedelta(days=5))
    path.touch()  # the file is brand new; the DATA is five days old
    assert m.freshness("injuries", now=NOW).status is Status.STALE


def test_a_failed_pull_is_recorded_as_missing_with_its_reason(tmp_path):
    m = _manifest(tmp_path)
    m.record("snap_counts", path="", rows=0, source="nflreadpy",
             error="ConnectionError: 403")
    f = m.freshness("snap_counts", now=NOW)
    assert f.status is Status.MISSING and "403" in f.reason
    assert m.file("snap_counts") is None
    assert m.read_frame("snap_counts") is None


def test_a_source_never_pulled_is_missing(tmp_path):
    f = _manifest(tmp_path).freshness("schedules", now=NOW)
    assert f.status is Status.MISSING and f.reason == "never pulled"


def test_a_manifest_entry_whose_file_vanished_is_missing(tmp_path):
    m = _manifest(tmp_path)
    path = tmp_path / "gone.parquet"
    pd.DataFrame({"week": [1]}).to_parquet(path)
    m.record("weekly_stats", path=path, rows=1, source="x", weeks=[1])
    path.unlink()
    assert m.file("weekly_stats") is None
    assert m.read_frame("weekly_stats") is None


def test_age_ok_drives_the_pullers_skip_decision(tmp_path):
    m = _manifest(tmp_path)
    path = tmp_path / "crosswalk.csv"
    path.write_text("a\n1\n", encoding="utf-8")
    m.record("crosswalk", path=path, rows=1, source="dp",
             as_of=NOW - timedelta(hours=10))
    assert m.age_ok("crosswalk", NOW, 24.0)
    assert not m.age_ok("crosswalk", NOW, 6.0)
    assert not m.age_ok("never_pulled", NOW, 1000.0)


def test_freshness_report_keeps_the_requested_order(tmp_path):
    m = _manifest(tmp_path)
    names = ("sleeper_league", "injuries", "weekly_stats")
    report = m.freshness_report(names, now=NOW, required_week=2)
    assert tuple(s.name for s in report) == names


def test_forward_looking_coverage_is_checked_against_the_report_week(tmp_path):
    m = _manifest(tmp_path)
    path = tmp_path / "injuries.parquet"
    pd.DataFrame({"week": [1]}).to_parquet(path)
    m.record("injuries", path=path, rows=1, source="nflreadpy", weeks=[1],
             as_of=NOW)
    assert m.freshness("injuries", now=NOW, required_week=1).status is Status.FRESH
    assert m.freshness("injuries", now=NOW, required_week=2).status is Status.STALE


def test_read_json_round_trips(tmp_path):
    m = _manifest(tmp_path)
    path = tmp_path / "sleeper_league.json"
    path.write_text(json.dumps({"week": 2}), encoding="utf-8")
    m.record("sleeper_league", path=path, rows=1, source="sleeper", weeks=[2])
    assert m.read_json("sleeper_league") == {"week": 2}


def test_the_cache_lives_under_the_gitignored_research_tree():
    from gridiron.paths import RESEARCH_CACHE

    assert ing.season_cache(2026).parent == RESEARCH_CACHE
    assert ing.season_cache(2026).name == "season2026"
