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


def test_a_pull_that_never_once_succeeded_is_missing_with_its_reason(tmp_path):
    m = _manifest(tmp_path)
    m.record_failure("snap_counts", source="nflreadpy",
                     error="ConnectionError: 403")
    f = m.freshness("snap_counts", now=NOW)
    assert f.status is Status.MISSING and "403" in f.reason
    assert m.file("snap_counts") is None
    assert m.read_frame("snap_counts") is None
    # Nothing was ever fetched, so there is no as-of to report. The time of
    # the FAILURE is not the age of data that does not exist.
    assert f.as_of is None


def test_a_failed_refresh_cannot_move_the_as_of_forward(tmp_path):
    """The headline rule: a refresh that fails makes data older, never newer.

    Recording the failed attempt as a new entry would stamp it with `now`,
    and the report would then age four-day-old snap counts from the moment
    the network died — i.e. call them current. The pull time belongs to the
    pull that actually returned rows.
    """
    m = _manifest(tmp_path)
    path = tmp_path / "snap_counts.parquet"
    pd.DataFrame({"week": [1, 2]}).to_parquet(path)
    good = NOW - timedelta(days=5)
    m.record("snap_counts", path=path, rows=2, source="nflreadpy",
             weeks=[1, 2], as_of=good)

    m.record_failure("snap_counts", source="nflreadpy",
                     error="ConnectionError: 503", at=NOW)

    entry = m.get("snap_counts")
    assert entry.as_of_dt == good, "the failure overwrote the good pull time"
    assert entry.last_attempt.startswith("2026-09-17T13:00")
    assert m.freshness("snap_counts", now=NOW).as_of == good


def test_a_failed_refresh_does_not_discard_the_last_good_pull(tmp_path):
    """A transient 503 must not throw away a perfectly good cached frame."""
    m = _manifest(tmp_path)
    path = tmp_path / "weekly_stats.parquet"
    pd.DataFrame({"week": [1, 1, 2]}).to_parquet(path)
    m.record("weekly_stats", path=path, rows=3, source="nflreadpy",
             weeks=[1, 2], as_of=NOW - timedelta(hours=6))

    m.record_failure("weekly_stats", source="nflreadpy",
                     error="HTTPError: 503", at=NOW)

    assert m.file("weekly_stats") == path
    assert len(m.read_frame("weekly_stats")) == 3
    entry = m.get("weekly_stats")
    assert entry.rows == 3 and entry.weeks == [1, 2]


def test_a_failed_refresh_is_never_fresh_and_says_what_broke(tmp_path):
    """Data young enough to read FRESH still can't, once a refresh has failed:
    the newest thing that happened to this source is a failure, and the reader
    is told so rather than shown a clean bill of health."""
    m = _manifest(tmp_path)
    path = tmp_path / "injuries.parquet"
    pd.DataFrame({"week": [2]}).to_parquet(path)
    m.record("injuries", path=path, rows=1, source="nflreadpy", weeks=[2],
             as_of=NOW - timedelta(minutes=30))
    assert m.freshness("injuries", now=NOW).status is Status.FRESH

    m.record_failure("injuries", source="nflreadpy",
                     error="TimeoutError: read timed out", at=NOW)

    f = m.freshness("injuries", now=NOW)
    assert f.status is Status.STALE
    assert "REFRESH FAILED" in f.reason and "read timed out" in f.reason
    assert f.usable, "the cached rows are still shown, just labelled"


def test_a_successful_pull_clears_a_previous_failure(tmp_path):
    m = _manifest(tmp_path)
    path = tmp_path / "schedules.parquet"
    pd.DataFrame({"week": [1, 2]}).to_parquet(path)
    m.record("schedules", path=path, rows=2, source="nflreadpy", weeks=[1, 2],
             as_of=NOW - timedelta(days=9))
    m.record_failure("schedules", source="nflreadpy", error="boom",
                     at=NOW - timedelta(days=1))

    m.record("schedules", path=path, rows=2, source="nflreadpy", weeks=[1, 2],
             as_of=NOW)

    entry = m.get("schedules")
    assert entry.error == ""
    assert m.freshness("schedules", now=NOW).status is Status.FRESH


def test_the_puller_retries_a_source_whose_refresh_failed(tmp_path):
    """age_ok drives the skip decision. A failed refresh must not let the
    next run skip the source because the OLD data is still young."""
    m = _manifest(tmp_path)
    path = tmp_path / "crosswalk.csv"
    path.write_text("gsis_id\n00-0000001\n", encoding="utf-8")
    m.record("crosswalk", path=path, rows=1, source="dynastyprocess",
             as_of=NOW - timedelta(hours=1))
    assert m.age_ok("crosswalk", NOW, 24.0)

    m.record_failure("crosswalk", source="dynastyprocess", error="404", at=NOW)
    assert not m.age_ok("crosswalk", NOW, 24.0)


def test_a_manifest_written_before_last_attempt_existed_still_loads(tmp_path):
    """Forward compatibility: an on-disk manifest from before this field was
    added must keep working rather than crash the report."""
    (tmp_path / ing.MANIFEST_NAME).write_text(json.dumps({
        "season": 2026,
        "entries": {"schedules": {
            "name": "schedules", "path": "schedules.parquet", "rows": 2,
            "as_of": "2026-09-17T12:00:00+00:00", "source": "nflreadpy",
            "weeks": [1, 2], "error": "",
        }},
    }), encoding="utf-8")
    pd.DataFrame({"week": [1, 2]}).to_parquet(tmp_path / "schedules.parquet")

    m = ing.Manifest.load(tmp_path)
    assert m.get("schedules").last_attempt == ""
    assert m.freshness("schedules", now=NOW).status is Status.FRESH


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


# ------------------------------- a pull can succeed and still be unusable

def test_a_schema_defect_outlives_the_run_that_found_it(tmp_path):
    """The pull worked: 200 OK, rows on disk, a readable file. It is still
    unusable, because upstream dropped a column the scoring rules need.

    A warning on stderr dies with the process that printed it. The next
    reader — the offline report, a cron summary, tomorrow's session — has
    only the manifest, so that is where the verdict has to live.
    """
    m = _manifest(tmp_path)
    path = tmp_path / "weekly_stats.parquet"
    path.write_text("x", encoding="utf-8")
    m.record("weekly_stats", path=path, rows=900, source="nflverse", weeks=[1])
    assert m.get("weekly_stats").missing_columns == []

    m.note_missing_columns("weekly_stats", ["passing_interceptions"])
    m.save()

    reloaded = ing.Manifest.load(tmp_path)
    assert reloaded.get("weekly_stats").missing_columns == ["passing_interceptions"]
    # It is NOT an error: the fetch succeeded and the intact columns are
    # still worth reading, so the file stays readable and the as-of stands.
    assert reloaded.get("weekly_stats").error == ""
    assert reloaded.file("weekly_stats") is not None


def test_a_fresh_pull_clears_a_previous_schema_defect(tmp_path):
    """Last week's defect is not evidence about this week's file."""
    m = _manifest(tmp_path)
    path = tmp_path / "weekly_stats.parquet"
    path.write_text("x", encoding="utf-8")
    m.record("weekly_stats", path=path, rows=900, source="nflverse", weeks=[1])
    m.note_missing_columns("weekly_stats", ["passing_interceptions"])

    m.record("weekly_stats", path=path, rows=950, source="nflverse", weeks=[1, 2])
    assert m.get("weekly_stats").missing_columns == []


def test_a_failed_refresh_preserves_the_recorded_schema_defect(tmp_path):
    """A failure says nothing new about the columns of the file we still
    have, so it must not quietly clear the note on it."""
    m = _manifest(tmp_path)
    path = tmp_path / "weekly_stats.parquet"
    path.write_text("x", encoding="utf-8")
    m.record("weekly_stats", path=path, rows=900, source="nflverse", weeks=[1])
    m.note_missing_columns("weekly_stats", ["passing_interceptions"])

    m.record_failure("weekly_stats", source="nflverse", error="503")
    assert m.get("weekly_stats").missing_columns == ["passing_interceptions"]


def test_noting_columns_on_an_unknown_source_is_a_no_op(tmp_path):
    m = _manifest(tmp_path)
    assert m.note_missing_columns("never_pulled", ["x"]) is None
    assert "never_pulled" not in m.entries


def test_an_older_manifest_without_the_field_still_loads(tmp_path):
    """Manifests written before this field existed must keep working — the
    cache is not versioned and re-pulling everything to read it would be a
    silly tax on a season's worth of data."""
    (tmp_path / ing.MANIFEST_NAME).write_text(json.dumps({
        "season": 2026,
        "entries": {"weekly_stats": {
            "name": "weekly_stats", "path": "weekly_stats.parquet", "rows": 900,
            "as_of": "2026-09-10T12:00:00+00:00", "source": "nflverse",
            "weeks": [1]}}}), encoding="utf-8")
    entry = ing.Manifest.load(tmp_path).get("weekly_stats")
    assert entry.missing_columns == [] and entry.last_attempt == ""
