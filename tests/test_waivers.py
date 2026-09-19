"""Upgrades name the drop, are measured against the legal lineup, and
abstain when they cannot be verified."""
from __future__ import annotations

import pytest

from gridiron.lineup import Player, slot_order
from gridiron.projection import Projection, abstain
from gridiron.waivers import available_ids, build_board, droppable_players

SLOTS = slot_order(None)


def P(sid, pos, mean, lineup="BENCH", *, locked=False, unprojected=False):
    proj = abstain("no admissible box scores") if unprojected else Projection(mean, max(mean * 0.5, 1.5))
    return Player(sid, f"p{sid}", pos, "T", proj, lineup, locked, "", "", (), "g" + sid)


def roster():
    return [
        P("q1", "QB", 20, "START"), P("r1", "RB", 15, "START"), P("r2", "RB", 10, "START"),
        P("w1", "WR", 14, "START"), P("w2", "WR", 9, "START"), P("t1", "TE", 7, "START"),
        P("f1", "WR", 8, "START"), P("f2", "RB", 6, "START"), P("k1", "K", 8, "START"),
        P("d1", "DST", 0, "START", unprojected=True),
        P("w3", "WR", 3), P("t2", "TE", 4), P("r3", "RB", 5),
    ]


STARTERS = ["q1", "r1", "r2", "w1", "w2", "t1", "f1", "f2", "k1", "d1"]


def test_available_ids_are_the_unheld_active_teamed_projectable_players():
    players = {
        "1": {"position": "WR", "team": "A", "status": "Active"},
        "2": {"position": "WR", "team": "A", "status": "Active"},     # held
        "3": {"position": "WR", "team": None, "status": "Active"},    # no team
        "4": {"position": "WR", "team": "A", "status": "Inactive"},
        "5": {"position": "OL", "team": "A", "status": "Active"},
        "SEA": {"position": "DEF", "team": "SEA", "status": "Active"},
        "6": {"position": "K", "team": "B", "status": "Active"},
    }
    rosters = [{"players": ["2"]}]
    assert available_ids(players, rosters) == ("1", "6")


def test_droppable_excludes_locked_starters_and_unprojected_players():
    r = roster()
    r[0] = P("q1", "QB", 20, "START", locked=True)
    r[10] = P("w3", "WR", 0, unprojected=True)
    ids = [p.sleeper_id for p in droppable_players(r)]
    assert "q1" not in ids and "w3" not in ids and "d1" not in ids
    assert ids[0] == "t2"                                  # cheapest to lose first


def test_a_lineup_upgrade_names_the_drop_and_the_slot_it_enters():
    pool = [P("fa_rb", "RB", 13), P("fa_te", "TE", 5), P("fa_qb", "QB", 1)]
    board = build_board(roster(), pool, STARTERS, SLOTS, locks_known=True)
    assert not board.abstained
    by_add = {u.add.sleeper_id: u for u in board.upgrades}
    up = by_add["fa_rb"]
    assert up.kind == "lineup" and up.slot == "FLEX"
    assert up.lineup_gain == pytest.approx(13 - 6)        # replaces f2 (6) in FLEX
    assert up.drop.sleeper_id == "w3"                     # the cheapest drop
    assert up.displaces is not None and up.displaces.sleeper_id == "f2"
    # a free agent that beats nothing this week but beats the drop is DEPTH
    te = by_add["fa_te"]
    assert te.kind == "depth" and te.lineup_gain == 0 and te.depth_gain == pytest.approx(2.0)
    assert "fa_qb" not in by_add                           # worse than every drop
    assert board.upgrades[0].add.sleeper_id == "fa_rb"    # lineup gains rank first


def test_the_drop_is_never_a_locked_starter_even_when_he_is_the_cheapest():
    r = roster()
    r[7] = P("f2", "RB", 1, "START", locked=True)         # 1-pt locked starter
    pool = [P("fa_rb", "RB", 13)]
    board = build_board(r, pool, STARTERS, SLOTS, locks_known=True)
    assert all(u.drop.sleeper_id != "f2" for u in board.upgrades)


def test_unprojected_free_agents_are_counted_not_ranked():
    pool = [P("fa1", "RB", 0, unprojected=True), P("fa2", "RB", 13)]
    board = build_board(roster(), pool, STARTERS, SLOTS, locks_known=True)
    assert board.pool_size == 2 and board.unprojected == 1
    assert [u.add.sleeper_id for u in board.upgrades] == ["fa2"]


def test_unknown_locks_abstain_the_whole_board():
    board = build_board(roster(), [P("fa", "RB", 30)], STARTERS, SLOTS, locks_known=False)
    assert board.abstained and board.upgrades == ()


def test_no_droppable_player_abstains_with_a_reason():
    r = [P("q1", "QB", 20, "START", locked=True), P("d1", "DST", 0, "START", unprojected=True)]
    board = build_board(r, [P("fa", "QB", 30)], ["q1", "d1"], ("QB", "DST"), locks_known=True)
    assert "no droppable" in board.abstained
