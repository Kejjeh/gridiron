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
    # the other feasible drops are carried, with the gain each keeps
    assert up.alternatives and all(g > 0 and d.sleeper_id != "w3" for d, g in up.alternatives)
    # a free agent that beats nothing this week is NOT an upgrade: he is
    # research against the same position, with no move attached
    assert "fa_te" not in by_add
    watch = {w.add.sleeper_id: w for w in board.watchlist}
    assert watch["fa_te"].versus.sleeper_id == "t2" and watch["fa_te"].gap == pytest.approx(1.0)
    assert "fa_qb" not in by_add and "fa_qb" not in watch      # worse than every drop
    assert board.upgrades[0].add.sleeper_id == "fa_rb"


# ------------------------------------------------ the cross-position trap

def test_a_backup_qb_over_a_bench_wr_is_not_an_upgrade_of_any_kind():
    """Reproduced on the released code (Astra, 2026-09-21): a backup QB
    projecting 17 against a bench WR projecting 3 was listed as a +14 DEPTH
    upgrade with the lineup unchanged, and the public board told the owner
    to consider claiming two backup QBs for Jakobi Meyers. Raw points across
    positions measure nothing about roster utility."""
    board = build_board(roster(), [P("backup_qb", "QB", 17)], STARTERS, SLOTS, locks_known=True)
    assert board.upgrades == ()
    assert board.watchlist == ()            # the roster QB projects 20; no same-position gap
    assert board.coverage == ()
    assert not board.abstained


def test_with_no_droppable_qb_the_board_abstains_rather_than_pricing_a_backup():
    """Every roster QB protected (the starter is locked): there is no like-
    for-like comparison, and the board says so instead of ranking the QB
    against a WR or turning 'he would be the backup' into a number."""
    r = roster()
    r[0] = P("q1", "QB", 20, "START", locked=True)
    board = build_board(r, [P("backup_qb", "QB", 17)], STARTERS, SLOTS, locks_known=True)
    assert board.upgrades == () and board.watchlist == ()
    assert len(board.coverage) == 1 and board.coverage[0].startswith("QB:")
    assert "not priced" in board.coverage[0] and "another position" in board.coverage[0]


def test_a_same_position_gap_on_the_bench_is_research_not_a_move():
    board = build_board(roster(), [P("fa_te", "TE", 5)], STARTERS, SLOTS, locks_known=True)
    assert board.upgrades == ()
    (w,) = board.watchlist
    assert w.add.sleeper_id == "fa_te" and w.versus.sleeper_id == "t2"
    assert "lineup would not change" in w.describe() and "not priced" in w.describe()


def test_nothing_better_is_a_hold_with_no_upgrade_and_no_watch():
    board = build_board(roster(), [P("fa_wr", "WR", 2), P("fa_rb", "RB", 1)], STARTERS, SLOTS,
                        locks_known=True)
    assert board.upgrades == () and board.watchlist == () and not board.abstained


def test_a_protected_player_is_never_an_alternative_drop_either():
    r = roster()
    r[10] = P("w3", "WR", 0, unprojected=True)             # unknown value, protected
    board = build_board(r, [P("fa_rb", "RB", 13)], STARTERS, SLOTS, locks_known=True)
    (up,) = board.upgrades
    assert up.drop.sleeper_id != "w3"
    assert all(d.sleeper_id != "w3" for d, _ in up.alternatives)


def test_two_pickups_wanting_the_same_drop_each_carry_a_different_fallback():
    pool = [P("fa_rb", "RB", 13), P("fa_wr", "WR", 12)]
    board = build_board(roster(), pool, STARTERS, SLOTS, locks_known=True)
    by_add = {u.add.sleeper_id: u for u in board.upgrades}
    assert by_add["fa_rb"].drop.sleeper_id == by_add["fa_wr"].drop.sleeper_id == "w3"
    for u in by_add.values():
        assert u.alternatives and u.alternatives[0][0].sleeper_id != "w3"
        assert u.alternatives[0][1] > 0


def test_the_module_no_longer_claims_bench_depth_pays_off_in_bye_weeks():
    import gridiron.waivers as w
    assert "bye weeks ahead" not in (w.__doc__ or "")
    assert "does not measure" in (w.__doc__ or "") or "do not measure" in (w.__doc__ or "")


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
