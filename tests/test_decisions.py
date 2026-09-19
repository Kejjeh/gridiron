"""The decision archive freezes the page; grading reads only the archive."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from gridiron.decisions import archive_path, grade_archive, read_archive, write_archive

UTC = timezone.utc


def _archive():
    return {
        "week": 3,
        "roster": [
            {"sleeper_id": "s1", "gsis_id": "g1", "projected": 10.0, "withheld": False},
            {"sleeper_id": "b1", "gsis_id": "g2", "projected": 12.0, "withheld": False},
            {"sleeper_id": "s2", "gsis_id": "g3", "projected": 8.0, "withheld": False},
            {"sleeper_id": "b2", "gsis_id": "g4", "projected": 9.0, "withheld": False},
            {"sleeper_id": "bye", "gsis_id": "g5", "projected": 0.0, "withheld": True},
        ],
        "alternatives": [
            {"slot": "WR", "bench_id": "b1", "starter_id": "s1"},
            {"slot": "TE", "bench_id": "b2", "starter_id": "s2"},
        ],
        "upgrades": [
            {"slot": "FLEX", "drop_id": "s2",
             "add": {"sleeper_id": "fa", "gsis_id": "g9", "projected": 11.0}},
        ],
    }


def test_the_archive_round_trips_atomically(tmp_path):
    now = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
    path = archive_path(2026, 3, now, tmp_path)
    assert path.name == "week03_20260926T120000Z.json"
    write_archive(_archive(), path)
    assert not path.with_name(path.name + ".part").exists()
    blob = read_archive(path)
    assert blob["archive_version"] == 1 and blob["week"] == 3


def test_grading_uses_the_archived_projection_and_the_actuals_only():
    actuals = {"g1": 4.0, "g2": 20.0, "g3": 7.0, "g4": 7.5, "g9": 30.0}
    grade = grade_archive(_archive(), actuals)
    assert grade.week == 3 and grade.ungradeable == 0
    wr = next(g for g in grade.graded if g.slot == "WR")
    assert wr.kind == "start_sit"
    assert wr.projected_edge == -2.0            # chose s1 (10) over b1 (12)
    assert wr.realized_edge == -16.0            # s1 4 vs b1 20
    te = next(g for g in grade.graded if g.slot == "TE")
    assert te.projected_edge == -1.0 and te.realized_edge == -0.5
    wv = next(g for g in grade.graded if g.kind == "waiver")
    assert wv.chosen_id == "s2" and wv.rejected_id == "fa"
    assert wv.realized_edge == -23.0
    # projection MAE over the un-withheld roster rows with an actual
    assert grade.projection_n == 4
    assert grade.projection_mae == round((6 + 8 + 1 + 1.5) / 4, 3)
    assert "graded" in grade.summary()


def test_a_missing_actual_is_ungradeable_not_zero():
    actuals = {"g1": 4.0, "g3": 7.0, "g4": 7.5}       # b1 and fa never played
    grade = grade_archive(_archive(), actuals)
    assert grade.ungradeable == 2
    wr = next(g for g in grade.graded if g.slot == "WR")
    assert wr.status == "ungradeable" and wr.realized_edge is None
    assert "rejected side" in wr.reason
    ok = [g for g in grade.graded if g.status == "graded"]
    assert [g.slot for g in ok] == ["TE"]


def test_a_withheld_projection_is_not_scored_as_a_projection():
    grade = grade_archive(_archive(), {"g5": 15.0})
    assert grade.projection_n == 0 and grade.projection_mae is None


def test_grading_never_reads_anything_but_the_archive(tmp_path, monkeypatch):
    """The grader must not import or call the projection: a re-projection
    with later data is exactly the leak the archive exists to prevent."""
    import gridiron.decisions as d
    src = open(d.__file__, encoding="utf-8").read()
    assert "gridiron.projection" not in src and "build_evidence" not in src
