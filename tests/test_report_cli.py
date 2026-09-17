"""The weekly report, end to end, with the network unplugged.

`scripts/weekly/report.py` is the thing the owner actually runs. These tests
drive it exactly as a person would — through `main(argv)` — against a cache
built from the committed fixtures, and pin the four ways the run can go
wrong quietly:

  1. rendering at all without the network (it must, from the cache alone),
  2. rendering LAST season's roster under this season's header,
  3. rendering a week the calendar has no business reporting,
  4. reading weeks the report week cannot have seen.

The fixtures are synthetic (invented user ids, an invented league name) so
nothing here depends on, or exposes, the owner's real league.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from gridiron import ingest as ing

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"
UTC = timezone.utc


def _load():
    spec = importlib.util.spec_from_file_location(
        "weekly_report_cli", ROOT / "scripts" / "weekly" / "report.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


R = _load()


class NetworkUsed(AssertionError):
    """Raised if anything in the report path opens a socket."""


@pytest.fixture
def no_network(monkeypatch):
    """The report claims to be offline. Make that claim falsifiable."""
    import socket

    def boom(*a, **k):
        raise NetworkUsed("the report opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    return True


def build_cache(root: Path, *, season: int = 2026, weeks=(1,),
                state: dict | None = None, as_of: datetime | None = None
                ) -> ing.Manifest:
    """Write a season cache from the committed fixtures, the same shape
    `pull_week.py` writes. No network, no parquet the repo doesn't own."""
    stamp = as_of or datetime.now(UTC) - timedelta(hours=1)
    directory = ing.season_cache(season, root)
    directory.mkdir(parents=True, exist_ok=True)
    manifest = ing.Manifest(directory, season=season)

    weekly = pd.concat(
        [pd.read_csv(FIXTURES / "weekly_offense_wk1.csv"),
         pd.read_csv(FIXTURES / "weekly_kickers_wk1.csv")], ignore_index=True)
    weekly = pd.concat(
        [weekly.assign(week=w) for w in weeks], ignore_index=True)
    snaps = pd.read_csv(FIXTURES / "snaps_wk1.csv")
    schedules = pd.read_csv(FIXTURES / "schedules_wk1_2.csv")
    injuries = pd.read_csv(FIXTURES / "injuries_wk1_2.csv")

    for name, frame, src in (
            ("weekly_stats", weekly, "nflreadpy.load_player_stats"),
            ("snap_counts", snaps, "nflreadpy.load_snap_counts"),
            ("schedules", schedules, "nflreadpy.load_schedules"),
            ("injuries", injuries, "nflreadpy.load_injuries")):
        path = directory / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        manifest.record(name, path=path, rows=len(frame), source=src,
                        as_of=stamp,
                        weeks=sorted({int(w) for w in frame["week"].dropna()}))

    snapshot = json.loads((FIXTURES / "sleeper_league.json").read_text("utf-8"))
    if state is not None:
        snapshot["state"] = {**snapshot["state"], **state}
    league_path = directory / "sleeper_league.json"
    league_path.write_text(json.dumps(snapshot), encoding="utf-8")
    manifest.record("sleeper_league", path=league_path,
                    rows=len(snapshot["rosters"]), as_of=stamp,
                    source="api.sleeper.app (read-only)",
                    weeks=[int(snapshot["state"]["week"])])

    players_path = directory / "sleeper_players.json"
    players_path.write_bytes((FIXTURES / "sleeper_players_small.json").read_bytes())
    manifest.record("sleeper_players", path=players_path, rows=14,
                    as_of=stamp, source="api.sleeper.app (read-only)")

    cross_path = directory / "crosswalk.csv"
    cross_path.write_bytes((FIXTURES / "crosswalk_small.csv").read_bytes())
    manifest.record("crosswalk", path=cross_path, rows=18, as_of=stamp,
                    source="dynastyprocess db_playerids.csv")

    manifest.save()
    return manifest


def run(root: Path, *args, owner: str = "fixture_owner") -> int:
    return R.main(["--cache-root", str(root), "--owner", owner, *args])


# ------------------------------------------------------------------- offline

def test_the_report_renders_from_the_cache_with_no_network(
        tmp_path, no_network, capsys):
    build_cache(tmp_path)
    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "# Weekly report" in out
    assert "## Input freshness" in out and "## Roster" in out


def test_the_report_states_the_as_of_of_every_input_it_used(tmp_path, capsys):
    build_cache(tmp_path)
    run(tmp_path)
    out = capsys.readouterr().out
    block = out.split("## Input freshness")[1].split("```")[1]
    for source in R.SOURCES:
        assert source in block, f"{source} has no as-of line"
        line = next(l for l in block.splitlines() if l.startswith(source))
        assert "as-of" in line and "covers" in line


def test_the_report_refuses_to_rank_anything(tmp_path, capsys):
    build_cache(tmp_path)
    run(tmp_path)
    out = capsys.readouterr().out.lower()
    assert "no projection model shipped" in out
    for banned in ("recommend", "start him", "sit ", "waiver claim", "rank #"):
        assert banned not in out.split("## roster")[0]


def test_a_missing_cache_is_an_instruction_not_a_traceback(tmp_path, capsys):
    assert run(tmp_path) == 2
    assert "pull_week.py" in capsys.readouterr().err


# ----------------------------------------------------------- season rollover

def test_a_previous_season_cache_is_refused_not_relabelled(tmp_path, capsys):
    """The cache directory is per-season, so asking for 2027 against a 2026
    tree must not silently render 2026's roster under a 2027 header."""
    build_cache(tmp_path, season=2026)
    directory = ing.season_cache(2027, tmp_path)
    directory.mkdir(parents=True, exist_ok=True)
    blob = json.loads((ing.season_cache(2026, tmp_path) / "manifest.json")
                      .read_text("utf-8"))
    (directory / "manifest.json").write_text(json.dumps(blob), encoding="utf-8")

    assert run(tmp_path, "--season", "2027") == 2
    assert "season 2026, not 2027" in capsys.readouterr().err


def test_a_snapshot_from_another_season_is_refused(tmp_path, capsys):
    """Sleeper's state carries the league's season. If it disagrees with the
    frames beside it, the roster and the box scores are different years."""
    build_cache(tmp_path, state={"season": 2025})
    assert run(tmp_path) == 2
    err = capsys.readouterr().err
    assert "season 2025" in err and "Re-run pull_week.py" in err


def test_the_offseason_has_no_week_to_report(tmp_path, capsys):
    """Between seasons Sleeper reports week 0. That is not week 1."""
    build_cache(tmp_path, state={"week": 0, "season_type": "off"})
    assert run(tmp_path) == 2
    err = capsys.readouterr().err
    assert "No in-season week to report" in err and "'off'" in err


def test_an_explicit_week_still_renders_in_the_offseason(tmp_path, capsys):
    """--week is how you look back at a finished season."""
    build_cache(tmp_path, weeks=(1, 2), state={"week": 0, "season_type": "off"})
    assert run(tmp_path, "--week", "2") == 0
    assert "week 2" in capsys.readouterr().out


# ---------------------------------------------------------- no future leakage

def test_a_historical_week_does_not_read_the_weeks_after_it(tmp_path, capsys):
    """The cache holds weeks 1-4; a week-2 report reads through week 2 and
    says what it withheld."""
    build_cache(tmp_path, weeks=(1, 2, 3, 4))
    assert run(tmp_path, "--week", "2") == 0
    out = capsys.readouterr().out
    assert "WITHHELD" in out
    assert "3, 4" in out.split("## Roster")[0]


def test_a_failed_refresh_shows_the_old_data_labelled_not_nothing(
        tmp_path, capsys):
    """The cache is good; the latest refresh died. The report still renders
    off the cached frame, and calls the source STALE with the reason."""
    manifest = build_cache(tmp_path)
    manifest.record_failure("injuries", source="nflreadpy",
                            error="HTTPError: 503 upstream")
    manifest.save()

    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    line = next(l for l in out.splitlines() if l.startswith("injuries"))
    assert "STALE" in line and "REFRESH FAILED" in line and "503" in line
    assert "## Roster" in out, "a failed refresh must not empty the report"


def test_fail_on_degraded_is_the_cron_switch(tmp_path):
    manifest = build_cache(tmp_path)
    manifest.record_failure("schedules", source="nflreadpy", error="boom")
    manifest.save()
    assert run(tmp_path, "--fail-on-degraded") == 1


# --------------------------------------------------------------- data hygiene

def test_the_report_never_writes_to_the_league():
    """Rule: read-only. The CLI renders from the cache; it does not speak to
    Sleeper at all, so no HTTP verb and no fetch call belongs in it."""
    import re

    source = (ROOT / "scripts" / "weekly" / "report.py").read_text("utf-8")
    for pattern in (r"\bPOST\b", r"\bPUT\b", r"\bPATCH\b", r"\bDELETE\b",
                    r"\burlopen\b", r"\brequests\.", r"\bhttp_fetch\b",
                    r"SleeperReadOnly\("):
        assert not re.search(pattern, source), f"{pattern} in the report CLI"


def test_writing_output_is_opt_in(tmp_path, capsys):
    """No --write, no files. The report is safe to run anywhere."""
    build_cache(tmp_path)
    before = {p for p in (ROOT / "data" / "outputs").glob("*")}
    run(tmp_path)
    assert {p for p in (ROOT / "data" / "outputs").glob("*")} == before


def test_a_missing_week_in_the_cache_reaches_the_report(tmp_path, capsys):
    """Weeks 1 and 3 present, week 2 never landed. A report that totals
    "through week 3" off two weeks of rows must say which week is absent."""
    build_cache(tmp_path, weeks=(1, 3))
    assert run(tmp_path, "--week", "4") == 0
    out = capsys.readouterr().out
    header = out.split("## Roster")[0]
    assert "(no wk2)" in header
    assert "GAP" in header and "short by those weeks" in header
