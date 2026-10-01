"""Rule #3: every join anchors on a stable id, and a miss is reported.

The failure this guards is not "the join errored" — it is "the join quietly
matched the wrong Josh Allen, or dropped a player and left a zero where a
number belonged".
"""
from __future__ import annotations

import csv

import pytest

from gridiron.ids import (Crosswalk, is_dst_id, normalize_id,
                          sleeper_gsis_overlay)


@pytest.mark.parametrize("raw,expected", [
    (" 00-0035057", "00-0035057"),   # Sleeper ships a leading space
    ("1234.0", "1234"),              # a CSV round-trip through float
    ("  4034  ", "4034"),
    (None, ""), ("", ""), ("nan", ""), ("NaN", ""), ("None", ""), ("<NA>", ""),
])
def test_normalize_id(raw, expected):
    assert normalize_id(raw) == expected


def test_dst_ids_are_recognised_not_dropped():
    assert is_dst_id("SEA") and is_dst_id("PIT")
    assert not is_dst_id("4034") and not is_dst_id("") and not is_dst_id(None)


def test_crosswalk_resolves_every_id_on_the_fixture_roster(crosswalk,
                                                           league_snapshot):
    roster = league_snapshot["rosters"][0]
    res = crosswalk.resolve(roster["players"])
    assert res.unresolved == (), f"unresolved roster ids: {res.unresolved}"
    assert res.coverage == 1.0
    assert set(res.dst) == {"SEA"}, "team defenses must be routed, not dropped"
    assert len(res.mapping) == len(roster["players"]) - 1


def test_unresolved_ids_are_reported_not_silently_dropped(crosswalk):
    res = crosswalk.resolve(["11560", "999999999", "SEA"])
    assert res.unresolved == ("999999999",)
    assert "999999999" not in res.mapping
    assert res.coverage == pytest.approx(0.5)


def test_gsis_to_pfr_edge_covers_the_snap_fixture(crosswalk, snaps_wk1,
                                                  weekly_offense):
    """Snap counts are PFR-keyed; the gsis->pfr edge is the only way in."""
    snap_pfr = {normalize_id(p) for p in snaps_wk1["pfr_player_id"]} - {""}
    assert snap_pfr, "snap fixture is empty"
    from_gsis = {crosswalk.pfr(normalize_id(g)) for g in weekly_offense["player_id"]}
    from_gsis.discard(None)
    overlap = snap_pfr & from_gsis
    assert len(overlap) >= 0.8 * len(from_gsis), (
        f"only {len(overlap)}/{len(from_gsis)} offensive players reach their "
        f"snap row through the crosswalk")


def test_overlay_fills_gaps_but_never_overrides_the_source_of_record():
    base = Crosswalk({"1": "00-0000001"}, {})
    merged = base.with_overlay({"1": "00-0009999", "2": " 00-0000002"})
    assert merged.gsis("1") == "00-0000001", "overlay must not win"
    assert merged.gsis("2") == "00-0000002", "overlay fills the gap, normalised"
    assert base.gsis("2") is None, "with_overlay must not mutate the original"


def test_sleeper_overlay_extraction_skips_blanks():
    overlay = sleeper_gsis_overlay({
        "1": {"gsis_id": " 00-0000001"},
        "2": {"gsis_id": None},
        "3": {},
        "SEA": {"gsis_id": ""},
    })
    assert overlay == {"1": "00-0000001"}


def test_names_are_display_only_and_never_a_join_key(crosswalk, fixtures):
    """A name column exists for diagnostics. Resolution must not use it."""
    with open(fixtures / "crosswalk_small.csv", newline="", encoding="utf-8") as fh:
        a_name = next(csv.DictReader(fh))["name"]
    assert a_name
    res = crosswalk.resolve([a_name])
    assert res.mapping == {}, "a name must never resolve to a player"
    assert a_name in res.dst or res.unresolved, "and it must be reported"


def test_empty_crosswalk_degrades_to_all_unresolved():
    res = Crosswalk({}, {}).resolve(["4034", "1234"])
    assert res.mapping == {} and len(res.unresolved) == 2
    assert res.coverage == 0.0
