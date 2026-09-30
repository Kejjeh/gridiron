"""Grading a finished week, every week (`scripts/weekly/grade_week.py`).

Pins: which archive is graded and why; actuals never come from the archive;
an owner decision exists only when the owner said so; the season ledger
holds aggregates only (no names, no ids) and re-grading replaces its row.
Synthetic archives only.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from gridiron.decisions import archive_path, write_archive

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GW = _load("grade_week_cli", "scripts/weekly/grade_week.py")


def _p(sid, gid, proj, name):
    return {"sleeper_id": sid, "gsis_id": gid, "projected": proj, "name": name,
            "position": "WR", "withheld": False}


def _archive(phase, *, bench_proj=11.0, lineup_allowed=True):
    return {"season": 2026, "week": 3, "phase": phase,
            "roster": [_p("1", "g1", 8.0, "Starter Name"), _p("2", "g2", bench_proj, "Bench Name"),
                       _p("3", "g3", 4.0, "Drop Name")],
            "alternatives": [{"slot": "WR", "bench_id": "2", "starter_id": "1",
                              "delta_points": bench_proj - 8.0}],
            "upgrades": [{"slot": "WR", "drop_id": "3",
                          "add": _p("9", "g9", 12.0, "Add Name")}],
            "gate": {"lineup": {"allowed": lineup_allowed}, "waiver": {"allowed": True}}}


def _write(root, when, blob):
    return write_archive(blob, archive_path(2026, 3, when, root=root, record=blob))


def _actuals_record(tmp_path, pts):
    players = {sid: {"gsis_id": gid, "weeks": [{"week": 3, "points": p}]}
               for sid, (gid, p) in pts.items()}
    path = tmp_path / "later.json"
    path.write_text(json.dumps({"usage": {"players": players}}), encoding="utf-8")
    return path


def _setup(tmp_path):
    root = tmp_path / "decisions"
    _write(root, datetime(2026, 9, 24, 23, 0, tzinfo=UTC), _archive("pregame", bench_proj=9.0))
    _write(root, datetime(2026, 9, 25, 12, 0, tzinfo=UTC), _archive("in_progress", bench_proj=11.0))
    _write(root, datetime(2026, 9, 28, 3, 0, tzinfo=UTC), _archive("in_progress", bench_proj=20.0))
    actuals = _actuals_record(tmp_path, {"1": ("g1", 5.0), "2": ("g2", 15.0),
                                         "3": ("g3", 2.0), "9": ("g9", 10.0)})
    return root, actuals


def test_the_default_is_the_last_pregame_board_and_before_moves_it(tmp_path):
    root, _ = _setup(tmp_path)
    arcs = GW.week_archives(root, 2026, 3)
    assert len(arcs) == 3
    chosen, why = GW.pick_archive(arcs, before=None)
    assert chosen[2]["phase"] == "pregame" and "pregame" in why
    chosen, _ = GW.pick_archive(arcs, before=datetime(2026, 9, 27, 17, tzinfo=UTC))
    assert chosen[0] == datetime(2026, 9, 25, 12, tzinfo=UTC)
    assert GW.pick_archive(arcs, before=datetime(2026, 9, 1, tzinfo=UTC))[0] is None


def test_a_week_is_graded_and_the_ledger_holds_aggregates_only(tmp_path, capsys):
    root, actuals = _setup(tmp_path)
    grades = tmp_path / "grades"
    rc = GW.main(["--week", "3", "--season", "2026", "--archives", str(root),
                  "--before", "2026-09-27T17:00:00Z", "--actuals-record", str(actuals),
                  "--grades-dir", str(grades),
                  "--acted", "waiver:WR:3:9"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Starter Name" in out and "`start_sit:WR:1:2`" in out
    assert "owner acted" in out and "projection direction" in out
    text = (grades / "season2026.csv").read_text(encoding="utf-8")
    [row] = list(csv.DictReader(text.splitlines()))
    assert set(row) == set(GW.FIELDS)
    values = " ".join(row.values())
    for leak in ("Name", "g1", "g2", "g9", "start_sit", "waiver:"):
        assert leak not in values, f"the season ledger leaked {leak!r}"
    # bench (11) over starter (8) projected; actual 15 vs 5 -> right way.
    # drop (4) vs add (12) projected; actual 2 vs 10 -> right way.
    assert (row["graded"], row["direction_n"], row["direction_agree"]) == ("2", "2", "2")
    assert row["scorable"] == "1" and row["agree"] == "1"     # the start/sit only
    assert row["decisions"] == "1"


def test_regrading_replaces_its_row_and_the_season_line_adds_up(tmp_path, capsys):
    root, actuals = _setup(tmp_path)
    grades = tmp_path / "grades"
    args = ["--week", "3", "--season", "2026", "--archives", str(root),
            "--actuals-record", str(actuals), "--grades-dir", str(grades)]
    assert GW.main(args) == 0 and GW.main(args) == 0
    rows = list(csv.DictReader((grades / "season2026.csv").open(encoding="utf-8")))
    assert len(rows) == 1
    assert "season to date over 1 graded archive(s)" in capsys.readouterr().out


def test_a_withheld_page_scores_direction_but_not_advice(tmp_path):
    from gridiron.decisions import grade_archive
    g = grade_archive(_archive("pregame", lineup_allowed=False),
                      {"g1": 5.0, "g2": 15.0, "g3": 2.0, "g9": 10.0})
    assert g.agreement() == (0, 0)
    assert GW.direction(g) == (2, 2)


def test_no_actuals_is_an_error_not_a_zero_grade(tmp_path):
    root, _ = _setup(tmp_path)
    empty = _actuals_record(tmp_path, {})
    assert GW.main(["--week", "3", "--season", "2026", "--archives", str(root),
                    "--actuals-record", str(empty), "--no-save"]) == 3
    assert GW.main(["--week", "9", "--season", "2026", "--archives", str(root),
                    "--actuals-record", str(empty), "--no-save"]) == 2
