"""Weekly grading without a person (`gridiron.grading`, `grade_week.py --finished`).

Pins: the board a week is graded on is the last one built before its Sunday
slate, kept aside so the cloud's 40-record retention cannot lose it; a week
is graded only once it is final, and never twice; the cloud mode prints no
player name (its log is public); the carried ledger survives a fresh runner;
the report card counts each week once. Synthetic schedules and archives only.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from gridiron import grading
from gridiron.decisions import archive_path, write_archive

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GW = _load("grade_week_cli_finished", "scripts/weekly/grade_week.py")


def _game(week, day, time, home, away, hs=None, as_=None, gt="REG"):
    return {"week": week, "game_type": gt, "gameday": day, "gametime": time,
            "home_team": home, "away_team": away, "home_score": hs, "away_score": as_}


def _schedule(final3=True):
    s = 21.0 if final3 else None
    return pd.DataFrame([
        # week 3: Thursday night, a Sunday early pair, a Sunday late game, Monday night
        _game(3, "2026-09-24", "20:15", "NYG", "DAL", 20.0, 17.0),
        _game(3, "2026-09-27", "13:00", "BUF", "MIA", 24.0, 10.0),
        _game(3, "2026-09-27", "13:00", "KC", "LV", 30.0, 3.0),
        _game(3, "2026-09-27", "16:25", "SF", "SEA", 14.0, 13.0),
        _game(3, "2026-09-28", "20:15", "GB", "CHI", s, s),
        # week 4: nothing played yet
        _game(4, "2026-10-04", "13:00", "BUF", "NYJ"),
    ])


def _p(sid, gid, proj, name):
    return {"sleeper_id": sid, "gsis_id": gid, "projected": proj, "name": name,
            "position": "WR", "withheld": False}


def _archive(when, phase="pregame", bench_proj=11.0, week=3):
    return {"generated": when.isoformat(timespec="seconds"), "season": 2026, "week": week,
            "phase": phase,
            "roster": [_p("1", "g1", 8.0, "Starter Name"), _p("2", "g2", bench_proj, "Bench Name")],
            "alternatives": [{"slot": "WR", "bench_id": "2", "starter_id": "1",
                              "delta_points": bench_proj - 8.0}],
            "upgrades": [], "gate": {"lineup": {"allowed": True}, "waiver": {"allowed": True}}}


def _write(root, when, **kw):
    blob = _archive(when, **kw)
    return write_archive(blob, archive_path(2026, blob["week"], when, root=root, record=blob))


# ------------------------------------------------------------- schedule
def test_the_cutoff_is_the_sunday_early_kickoff_not_thursday_night():
    cut = grading.main_slate_kickoff(_schedule(), 3)
    assert cut == datetime(2026, 9, 27, 17, 0, tzinfo=UTC)      # 13:00 EDT
    assert grading.main_slate_kickoff(None, 3) is None


def test_a_week_is_final_only_when_every_game_has_a_score():
    assert grading.week_is_final(_schedule(final3=True), 3)
    assert not grading.week_is_final(_schedule(final3=False), 3)   # Monday night pending
    assert not grading.week_is_final(_schedule(), 4)
    assert not grading.week_is_final(_schedule(), 9)                # no rows is not final


# ------------------------------------------------------------------ pins
def test_the_pin_is_the_last_board_before_the_slate_and_only_moves_forward(tmp_path):
    ledger, pins = tmp_path / "decisions", tmp_path / "pins"
    cut = {3: grading.main_slate_kickoff(_schedule(), 3)}
    _write(ledger, datetime(2026, 9, 24, 12, tzinfo=UTC))                       # Thu, pregame
    sat = _write(ledger, datetime(2026, 9, 26, 15, tzinfo=UTC), phase="in_progress")
    _write(ledger, datetime(2026, 9, 27, 18, tzinfo=UTC), phase="in_progress")  # after kickoff
    rep = grading.pin_weeks(ledger, pins, season=2026, cutoffs=cut)
    assert rep.pinned == (f"week 03: {sat.name}",)
    assert grading.pinned(pins, 2026)[3].read_bytes() == sat.read_bytes()

    # the cloud prunes the ledger; the pin survives and is not replaced by nothing
    for p in (ledger / "season2026").glob("*.json"):
        p.unlink()
    assert grading.pin_weeks(ledger, pins, season=2026, cutoffs=cut).pinned == ()
    assert grading.pinned(pins, 2026)[3].name == sat.name

    # an OLDER qualifying board never displaces a newer pin
    _write(ledger, datetime(2026, 9, 25, 9, tzinfo=UTC))
    rep = grading.pin_weeks(ledger, pins, season=2026, cutoffs=cut)
    assert rep.kept == (3,) and grading.pinned(pins, 2026)[3].name == sat.name
    assert len(list((pins / "season2026").glob("*.json"))) == 1


def test_without_a_schedule_the_pin_falls_back_to_the_pregame_phase(tmp_path):
    ledger, pins = tmp_path / "decisions", tmp_path / "pins"
    pre = _write(ledger, datetime(2026, 9, 24, 12, tzinfo=UTC))
    _write(ledger, datetime(2026, 9, 26, 15, tzinfo=UTC), phase="in_progress")
    grading.pin_weeks(ledger, pins, season=2026, cutoffs={3: None})
    assert grading.pinned(pins, 2026)[3].name == pre.name


# ----------------------------------------------------------- report card
def _row(week, archive, graded_at, **kw):
    base = {"season": "2026", "week": str(week), "archive": archive, "archive_built": "",
            "graded": "4", "ungradeable": "0", "scorable": "2", "agree": "1",
            "direction_n": "4", "direction_agree": "3", "projection_mae": "5.0",
            "projection_n": "10", "shootout_pairs": "", "graded_at": graded_at}
    return {**base, **kw}


def test_the_card_counts_each_week_once_and_weights_the_shootout_by_pairs():
    rows = [_row(3, "a.json", "2026-09-30T00:00:00+00:00"),
            _row(3, "b.json", "2026-10-01T00:00:00+00:00", direction_agree="4"),
            _row(4, "c.json", "2026-10-06T00:00:00+00:00", shootout_pairs="30",
                 page_pairwise="0.6", sleeper_pairwise="0.7"),
            _row(5, "d.json", "2026-10-13T00:00:00+00:00", shootout_pairs="10",
                 page_pairwise="1.0", sleeper_pairwise="0.5")]
    card = grading.report_card(rows, 2026)
    assert [w.week for w in card.weeks] == [3, 4, 5]
    assert card.weeks[0].direction_agree == 4                 # the later grade of week 3
    t = card.totals()
    assert (t["weeks"], t["direction_agree"], t["direction_n"]) == (3, 10, 12)
    page, n = card.shootout["page"]
    assert n == 40 and abs(page - (0.6 * 30 + 1.0 * 10) / 40) < 1e-9
    assert grading.report_card([], 2026).any is False


def test_a_carried_ledger_with_junk_rows_is_read_without_them(tmp_path):
    p = tmp_path / "season2026.csv"
    p.write_text("season,week,archive,graded\n2026,3,a.json,4\nx,3,b.json,1\n2026,4,,2\n",
                 encoding="utf-8")
    assert [r["archive"] for r in grading.read_ledger(p)] == ["a.json"]
    assert grading.read_ledger(tmp_path / "missing.csv") == []


# ------------------------------------------------------------ cloud mode
class _Manifest:
    def __init__(self, schedule):
        self.schedule = schedule

    def read_frame(self, name):
        return self.schedule if name == "schedules" else None


def _cloud(tmp_path, monkeypatch, schedule):
    import gridiron.ingest as ing
    monkeypatch.setattr(ing.Manifest, "load", classmethod(lambda cls, d, s: _Manifest(schedule)))
    monkeypatch.setattr(GW, "actuals_from_cache",
                        lambda d, s, w: {"g1": 5.0, "g2": 15.0} if w == 3 else {})
    monkeypatch.setattr(GW, "defense_actuals_from_cache", lambda d, s, w: {})
    ledger = tmp_path / "decisions"
    _write(ledger, datetime(2026, 9, 26, 15, tzinfo=UTC))
    _write(ledger, datetime(2026, 10, 2, 15, tzinfo=UTC), week=4)
    store = tmp_path / "carry"
    args = ["--finished", "--season", "2026", "--archives", str(ledger),
            "--pins", str(store / "pins"), "--actuals-cache", str(tmp_path / "cache"),
            "--grades-dir", str(tmp_path / "grades"),
            "--carry-grades", str(store / "grades")]
    return args, store


def test_the_cloud_grades_a_final_week_once_and_names_nobody(tmp_path, monkeypatch, capsys):
    args, store = _cloud(tmp_path, monkeypatch, _schedule())
    assert GW.main(args) == 0
    out = capsys.readouterr().out
    assert "week 3: graded the board built 2026-09-26T15:00:00+00:00" in out
    assert "week 4: not final yet; waits" in out
    for leak in ("Name", "g1", "g2", "start_sit"):
        assert leak not in out, f"the public log leaked {leak!r}"
    rows = list(csv.DictReader((tmp_path / "grades" / "season2026.csv").open(encoding="utf-8")))
    assert [(r["week"], r["direction_agree"], r["direction_n"]) for r in rows] == [("3", "1", "1")]
    assert (store / "grades" / "season2026.csv").is_file()

    # a second run grades nothing new
    assert GW.main(args) == 0
    assert "graded the board" not in capsys.readouterr().out


def test_a_fresh_runner_gets_the_carried_ledger_back(tmp_path, monkeypatch, capsys):
    args, store = _cloud(tmp_path, monkeypatch, _schedule())
    assert GW.main(args) == 0
    (tmp_path / "grades" / "season2026.csv").unlink()          # a new runner's checkout
    capsys.readouterr()
    assert GW.main(args) == 0
    out = capsys.readouterr().out
    assert "carried ledger: 1 row(s) added" in out and "graded the board" not in out


def test_a_week_someone_graded_by_hand_is_never_regraded(tmp_path, monkeypatch, capsys):
    args, _ = _cloud(tmp_path, monkeypatch, _schedule())
    grades = tmp_path / "grades"
    GW.upsert(grades / "season2026.csv",
              {k: "" for k in GW.FIELDS} | {"season": 2026, "week": 3, "archive": "manual.json",
                                           "graded": 9})
    assert GW.main(args) == 0
    assert "graded the board" not in capsys.readouterr().out


def test_a_week_that_is_not_final_waits(tmp_path, monkeypatch, capsys):
    args, _ = _cloud(tmp_path, monkeypatch, _schedule(final3=False))
    assert GW.main(args) == 0
    assert "week 3: not final yet; waits" in capsys.readouterr().out
    assert not (tmp_path / "grades" / "season2026.csv").exists()
