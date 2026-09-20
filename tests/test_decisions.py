"""The decision archive freezes the page; grading reads only the archive.

The archive half pins two properties a record has to have to be evidence at
all: it is IMMUTABLE (a second, different page cannot quietly take its place)
and it is ORDERABLE BY TIME (so "the previous snapshot" is the previous one).

The grading half pins the distinction the grader used to collapse: an archive
records what the page SHOWED, never what the owner DID. Nothing in this
project watches the owner, so a comparison stays hypothetical unless an
observation is supplied from outside, and advice the page WITHHELD is not
scored as advice at all.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from gridiron.decisions import (ACTED, ArchiveCollision, DECLINED, ENDORSED,
                                HYPOTHETICAL, OBSERVED, WITHHELD, archive_path,
                                archive_stamp, grade_archive, list_archives,
                                previous_archive, read_archive, write_archive)

UTC = timezone.utc
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)


def _archive(gate_allows: bool = True):
    return {
        "week": 3,
        "season": 2026,
        "generated": NOW.isoformat(timespec="seconds"),
        "gate": {
            "lineup": {"allowed": gate_allows, "blockers": [], "verify": []},
            "waiver": {"allowed": gate_allows, "blockers": [], "verify": []},
            "matchup": {"allowed": gate_allows, "blockers": [], "verify": []},
        },
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


ACTUALS = {"g1": 4.0, "g2": 20.0, "g3": 7.0, "g4": 7.5, "g9": 30.0}


# ------------------------------------------------------------------- storage

def test_the_archive_round_trips_atomically(tmp_path):
    path = archive_path(2026, 3, NOW, tmp_path)
    assert path.name == "week03_20260926T120000Z.json"
    write_archive(_archive(), path)
    assert not path.with_name(path.name + ".part").exists()
    blob = read_archive(path)
    assert blob["archive_version"] == 1 and blob["week"] == 3


def test_a_second_different_page_cannot_replace_the_first(tmp_path):
    """Astra wrote choice A and then choice B one second apart; B replaced A
    and A was gone. Two distinct pages are two distinct records."""
    a = {**_archive(), "current_points": 100.0}
    b = {**_archive(), "current_points": 200.0}
    pa = write_archive(a, archive_path(2026, 3, NOW, tmp_path, a))
    pb = write_archive(b, archive_path(2026, 3, NOW, tmp_path, b))

    assert pa != pb, "same timestamp, different content, must not share a path"
    assert read_archive(pa)["current_points"] == 100.0
    assert read_archive(pb)["current_points"] == 200.0
    assert len(list((tmp_path / "season2026").glob("*.json"))) == 2


def test_rewriting_the_same_page_is_a_no_op_and_a_different_one_raises(tmp_path):
    a = _archive()
    path = write_archive(a, archive_path(2026, 3, NOW, tmp_path, a))
    assert write_archive(a, path) == path          # byte-identical: idempotent
    with pytest.raises(ArchiveCollision):
        write_archive({**a, "current_points": 1.0}, path)
    assert "current_points" not in read_archive(path)


def test_the_previous_archive_is_the_previous_one_in_time_not_by_filename(tmp_path):
    """Week 5 written in September, week 4 backfilled in October. Sorting the
    NAMES puts week05 last and returns it as "newest", which is a record from
    a month earlier."""
    early = datetime(2026, 9, 1, 10, 0, tzinfo=UTC)
    late = datetime(2026, 10, 2, 10, 0, tzinfo=UTC)
    r5 = {**_archive(), "week": 5, "generated": early.isoformat()}
    r4 = {**_archive(), "week": 4, "generated": late.isoformat()}
    write_archive(r5, archive_path(2026, 5, early, tmp_path, r5))
    write_archive(r4, archive_path(2026, 4, late, tmp_path, r4))

    prev = previous_archive(tmp_path, 2026, datetime(2026, 10, 3, tzinfo=UTC))
    assert read_archive(prev)["week"] == 4

    order = [read_archive(p)["week"] for _w, p in list_archives(tmp_path, 2026)]
    assert order == [5, 4], "oldest write first, regardless of week number"


def test_an_unparseable_archive_name_is_skipped_rather_than_ordered_by_guess(tmp_path):
    folder = tmp_path / "season2026"
    folder.mkdir(parents=True)
    (folder / "notes.json").write_text("{}", encoding="utf-8")
    (folder / "week99_garbage.json").write_text("{}", encoding="utf-8")
    assert archive_stamp(folder / "week99_garbage.json") is None
    assert list_archives(tmp_path, 2026) == ()
    assert previous_archive(tmp_path, 2026, NOW) is None


# ------------------------------------------------------------------- grading

def test_grading_uses_the_archived_projection_and_the_actuals_only():
    grade = grade_archive(_archive(), ACTUALS)
    assert grade.week == 3 and grade.ungradeable == 0
    wr = next(g for g in grade.comparisons if g.slot == "WR")
    assert wr.kind == "start_sit"
    assert wr.projected_edge == -2.0            # held s1 (10) against b1 (12)
    assert wr.realized_edge == -16.0            # s1 4 vs b1 20
    te = next(g for g in grade.comparisons if g.slot == "TE")
    assert te.projected_edge == -1.0 and te.realized_edge == -0.5
    wv = next(g for g in grade.comparisons if g.kind == "waiver")
    assert wv.held_id == "s2" and wv.alternative_id == "fa"
    assert wv.realized_edge == -23.0
    assert grade.projection_n == 4
    assert grade.projection_mae == round((6 + 8 + 1 + 1.5) / 4, 3)


def test_a_missing_actual_is_ungradeable_not_zero():
    grade = grade_archive(_archive(), {"g1": 4.0, "g3": 7.0, "g4": 7.5})
    assert grade.ungradeable == 2
    wr = next(g for g in grade.comparisons if g.slot == "WR")
    assert wr.status == "ungradeable" and wr.realized_edge is None
    assert "alternative side" in wr.reason
    ok = [g for g in grade.comparisons if g.status == "graded"]
    assert [g.slot for g in ok] == ["TE"]


def test_a_withheld_projection_is_not_scored_as_a_projection():
    grade = grade_archive(_archive(), {"g5": 15.0})
    assert grade.projection_n == 0 and grade.projection_mae is None


def test_no_comparison_is_a_decision_without_an_observation():
    """The core of the finding: the roster still holding a player is not
    evidence that anyone chose to keep him."""
    grade = grade_archive(_archive(), ACTUALS)
    assert grade.decisions == ()
    assert all(c.basis == HYPOTHETICAL for c in grade.comparisons)
    assert all(c.owner_action == "" for c in grade.comparisons)
    text = grade.summary()
    assert "0 confirmed owner decision(s)" in text
    assert "followed" not in text and "chose" not in text
    assert any("never sees what the owner actually did" in n for n in grade.notes)


def test_an_observation_is_the_only_thing_that_makes_a_decision():
    grade = grade_archive(_archive(), ACTUALS,
                          observed={"start_sit:WR:s1:b1": ACTED,
                                    "waiver:FLEX:s2:fa": DECLINED})
    seen = {c.key: c for c in grade.comparisons}
    assert seen["start_sit:WR:s1:b1"].basis == OBSERVED
    assert seen["start_sit:WR:s1:b1"].owner_action == ACTED
    assert seen["waiver:FLEX:s2:fa"].owner_action == DECLINED
    assert seen["start_sit:TE:s2:b2"].basis == HYPOTHETICAL
    assert {c.key for c in grade.decisions} == {"start_sit:WR:s1:b1",
                                                "waiver:FLEX:s2:fa"}
    assert "2 confirmed owner decision(s)" in grade.summary()


def test_advice_the_page_withheld_is_not_scored_as_advice():
    """On a stale page every action is WITHHELD: the comparison is shown as
    last-known information and the recommendation is explicitly not given.
    Counting those as recommendations grades advice that was never offered."""
    grade = grade_archive(_archive(gate_allows=False), ACTUALS)
    assert all(c.stance == WITHHELD for c in grade.comparisons)
    assert grade.scorable == ()
    assert grade.agreement() == (0, 0)
    assert "no agreement rate is reported" in grade.summary()
    assert any("excluded from the agreement rate" in n for n in grade.notes)


def test_a_waiver_pair_is_never_scored_as_though_the_add_was_available():
    """The page can prove a player was unrostered in a snapshot. It cannot
    prove he was claimable, so a waiver comparison stays out of the rate."""
    grade = grade_archive(_archive(), ACTUALS)
    wv = next(c for c in grade.comparisons if c.kind == "waiver")
    assert wv.eligibility == "UNVERIFIED" and not wv.scorable
    assert "UNVERIFIED" in wv.label()
    assert {c.slot for c in grade.scorable} == {"WR", "TE"}
    assert grade.agreement() == (2, 2)


def test_an_archive_with_no_gate_does_not_pretend_to_know_what_it_endorsed():
    old = {k: v for k, v in _archive().items() if k != "gate"}
    grade = grade_archive(old, ACTUALS)
    assert all(c.stance == "UNKNOWN" for c in grade.comparisons)
    assert grade.scorable == ()
    assert any("carries no gate record" in n for n in grade.notes)


def test_grading_never_reads_anything_but_the_archive():
    """The grader must not import or call the projection: a re-projection
    with later data is exactly the leak the archive exists to prevent."""
    import gridiron.decisions as d
    src = open(d.__file__, encoding="utf-8").read()
    assert "gridiron.projection" not in src and "build_evidence" not in src


def test_the_grade_summary_is_read_only_prose_about_this_archive():
    assert ENDORSED != WITHHELD
    grade = grade_archive(_archive(), ACTUALS)
    assert "nothing re-projected" in " ".join(grade.notes)
