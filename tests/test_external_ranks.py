"""Outside rankings resolve to ids strictly, or not at all (rule #3's one
name boundary, `gridiron.external_ranks`)."""
from __future__ import annotations

from gridiron import external_ranks as X


def _row(rank, name, pos, team="", value=None):
    return X.RankRow("chart", rank, name, pos, X.team_key(team), value)


UNIVERSE = [
    ("1", "Alan Arcwright", "WR", "JAX", "mine"),
    ("2", "Bo Brightwell Jr.", "WR", "ARI", "available"),
    ("3", "D.J. Cobble", "WR", "BUF", "available"),
    ("4", "Cal Dunmore", "QB", "BUF", "available"),
    ("5", "Cal Dunmore", "QB", "JAX", "available"),      # two men, one name
    ("6", "Kip Fallow", "RB", "SF", "available"),
    ("7", "Dee Vantor", "WR", "NO", "mine"),
]


def test_spelling_resolves_identity_does_not():
    res = X.resolve([_row(1, "Bo Brightwell", "WR", "ARI"),
                     _row(2, "DJ Cobble", "WR", "BUF")], UNIVERSE)
    assert [r.sleeper_id for r in res] == ["2", "3"]
    assert all(r.status == X.RESOLVED for r in res)


def test_a_chart_team_alias_is_not_a_mismatch_but_a_trade_is():
    [alias] = X.resolve([_row(1, "Alan Arcwright", "WR", "JAC")], UNIVERSE)
    assert alias.status == X.RESOLVED and alias.held_by == "mine"
    [moved] = X.resolve([_row(1, "Kip Fallow", "RB", "LV")], UNIVERSE)
    assert moved.status == X.TEAM_DIFFERS and moved.sleeper_id == "6"


def test_two_candidates_resolve_to_nobody():
    [r] = X.resolve([_row(1, "Cal Dunmore", "QB", "BUF")], UNIVERSE)
    assert r.status == X.AMBIGUOUS and r.sleeper_id == ""


def test_position_is_part_of_the_key_and_nothing_is_fuzzy():
    res = X.resolve([_row(1, "Kip Fallow", "WR"), _row(2, "Kip", "RB"),
                     _row(3, "Fallow", "RB"), _row(4, "Kip Fallows", "RB")], UNIVERSE)
    assert all(r.status == X.NOT_HELD for r in res)


def test_ranked_above_lists_available_players_the_list_puts_higher():
    res = X.resolve([_row(94, "Kip Fallow", "RB", "SF"),
                     _row(102, "Alan Arcwright", "WR", "JAC"),
                     _row(114, "Dee Vantor", "WR", "NO"),
                     _row(113, "Bo Brightwell", "WR", "ARI")], UNIVERSE)
    above = X.ranked_above(res)
    assert [a.sleeper_id for a in above["1"]] == ["6"]
    assert [a.sleeper_id for a in above["7"]] == ["6", "2"]


def test_the_csv_loader_tolerates_headers_and_skips_unreadable_rows(tmp_path):
    p = tmp_path / "trade_chart.csv"
    p.write_text("Rank,Name,Pos,Team,Value\n2,Kip Fallow,RB,SF,4.5\n"
                 "1,Alan Arcwright,WR,JAC,16.5\n,No Rank,WR,SF,\n3,No Position,,SF,\n",
                 encoding="utf-8")
    rows = X.load_ranks(p)
    assert [(r.rank, r.name, r.position, r.team, r.value) for r in rows] == [
        (1, "Alan Arcwright", "WR", "JAX", 16.5), (2, "Kip Fallow", "RB", "SF", 4.5)]
    assert rows[0].source == "trade_chart"


def test_the_universe_is_the_records_roster_and_available_pool():
    record = {"roster": [{"sleeper_id": "1", "name": "A B", "position": "WR",
                          "team": "LA"}],
              "radar": {"pool": [{"id": "9", "name": "C D", "position": "DEF",
                                  "team": "LAR"}]}}
    assert X.universe_from_record(record) == [("1", "A B", "WR", "LA", "mine"),
                                              ("9", "C D", "DEF", "LA", "available")]
