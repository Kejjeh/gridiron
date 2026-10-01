"""Drop legality once games have started (PR #7 release blocker 2).

What Sleeper documents, read 2026-09-24 (support.sleeper.com):
  * "Why was someone able to drop their starter after they have played?"
    (article 3473234): a started STARTER leaves a roster only through a
    waiver claim submitted before his kickoff, and then stays locked in the
    lineup with his points counting. A free-agent move cannot drop him.
  * "Why can't I drop players?" (article 4037431): the commissioner setting
    "Lock Free Agent and Waiver Moves" can block every add AND drop.
  * Nothing official says whether a BENCH player can be dropped after his
    game starts. The league object carries `bench_lock`, but Sleeper's API
    documentation does not define it, so it is recorded, not relied on.

So: a started starter is never a drop; a started (or unknown-kickoff) bench
player is an UNVERIFIED drop that can only make a move conditional on a
check in Sleeper; a verified drop is always preferred; and the alternatives
a move names are counted by their own legality, not borrowed from another
move that wants the same drop.
"""
from __future__ import annotations

from datetime import datetime, timezone

from gridiron.freshness import SourceFreshness, Status
from gridiron.gating import build_gate
from gridiron.lineup import Player, plan_lineup
from gridiron.projection import Projection, abstain
from gridiron import radar as R
from gridiron.waivers import (LINEUP, build_board, drop_rule, droppable_players,
                              protected_players)

UTC = timezone.utc
NOW = datetime(2026, 9, 27, 18, 0, tzinfo=UTC)


def P(sid, pos, mean, lineup="BENCH", *, locked=False, lock_known=True,
      unprojected=False):
    proj = abstain("no admissible box scores") if unprojected \
        else Projection(mean, max(mean * 0.5, 1.5))
    note = "his game has kicked off" if locked else ("" if lock_known else "kickoff unknown")
    return Player(sid, f"p{sid}", pos, "T", proj, lineup, locked, note, "", (),
                  "g" + sid, lock_known)


# --------------------------------------------------------- the per-player rule

def test_a_started_starter_is_never_a_drop_and_the_rule_is_cited():
    started = P("s", "RB", 10, "START", locked=True)
    assert started not in droppable_players([started, P("b", "WR", 2)])
    why = dict((p.sleeper_id, r) for p, r in protected_players([started]))["s"]
    assert "waiver claim submitted before" in why


def test_a_started_bench_player_is_an_unverified_drop():
    rule = drop_rule(P("b", "RB", 1, locked=True))
    assert rule and "Sleeper" in rule and "bench" in rule


def test_an_unknown_kickoff_bench_player_is_an_unverified_drop():
    assert drop_rule(P("b", "RB", 1, lock_known=False))


def test_a_bench_player_whose_game_has_not_started_is_a_verified_drop():
    assert drop_rule(P("b", "RB", 1)) == ""


# --------------------------------------------------- choosing among the drops

def test_a_verified_drop_is_preferred_over_a_cheaper_started_bench_player():
    roster = [P("r1", "RB", 10, "START"), P("w1", "WR", 8, "START"),
              P("lockd", "WR", 1, locked=True), P("ok", "RB", 2)]
    board = build_board(roster, [P("fa", "WR", 15)], ["r1", "w1"], ("RB", "WR"),
                        locks_known=True)
    u = board.upgrades[0]
    assert u.drop.sleeper_id == "ok" and u.drop_check == ""
    alt = {d.sleeper_id for d, _ in u.alternatives}
    assert "lockd" in alt                     # still named, as unverified
    c = next(c for c in board.candidates if c.add.sleeper_id == "fa")
    assert c.verdict == LINEUP and c.drop_check == ""


def _full():
    """Full roster, FLEX slot empty, both starters locked, and the only player
    who could go is a bench player whose game has already started."""
    roster = [P("r1", "RB", 10, "START", locked=True), P("w1", "WR", 8, "START", locked=True),
              P("b1", "RB", 1, locked=True)]
    return roster, ["r1", "w1", "0"]


def test_a_full_roster_whose_only_drops_have_played_makes_a_conditional_move():
    """Full roster: every gain needs a drop, and the only players that can go
    are bench players whose games have started. The move is shown, but only
    as conditional on Sleeper allowing that drop — never as executable."""
    roster, starters = _full()
    board = build_board(roster, [P("fa", "RB", 12)], starters,
                        ("RB", "WR", "FLEX"), locks_known=True)
    u = board.upgrades[0]
    assert u.drop.sleeper_id == "b1" and u.drop_check
    c = next(c for c in board.candidates if c.add.sleeper_id == "fa")
    assert c.verdict == LINEUP and c.drop_check == u.drop_check


def test_the_locked_lineup_is_protected_whatever_the_gain():
    roster = [P("r1", "RB", 1, "START", locked=True), P("w1", "WR", 1, "START", locked=True)]
    board = build_board(roster, [P("fa", "RB", 30)], ["r1", "w1"], ("RB", "WR"),
                        locks_known=True)
    assert board.upgrades == () and "protected" in board.abstained


def _shared():
    roster = [P("r1", "RB", 5, "START"), P("w1", "WR", 5, "START"),
              P("ok", "WR", 1), P("lockd", "RB", 0.5, locked=True)]
    pool = [P("fa1", "RB", 12), P("fa2", "WR", 11)]
    return roster, build_board(roster, pool, ["r1", "w1"], ("RB", "WR"), locks_known=True)


def test_two_moves_sharing_a_drop_each_count_their_own_legal_alternatives():
    _, board = _shared()
    u1, u2 = board.upgrades[:2]
    assert u1.drop.sleeper_id == u2.drop.sleeper_id == "ok"
    for u in (u1, u2):
        legal = [d for d, _ in u.alternatives if not drop_rule(d)]
        unverified = [d for d, _ in u.alternatives if drop_rule(d)]
        assert legal, "each move has its own verified fallback drop"
        assert all(d.sleeper_id != "ok" for d in legal)
        # verified fallbacks come before unverified ones, whatever the gain
        order = [bool(drop_rule(d)) for d, _ in u.alternatives]
        assert order == sorted(order)
        assert {d.sleeper_id for d in unverified} <= {"lockd"}


def test_the_either_or_card_names_the_next_verified_drop_not_a_started_player():
    from gridiron.dashboard import build_actions
    roster, board = _shared()
    plan = plan_lineup(roster, ["r1", "w1"], ("RB", "WR"))
    gate = build_gate([SourceFreshness(n, Status.FRESH, NOW, 1, None, "ok") for n in
                       ("sleeper_league", "sleeper_players", "injuries", "schedules")])
    acts = [a for a in build_actions(plan=plan, board=board, gate=gate, now=NOW,
                                     slots=("RB", "WR"), snapshot_as_of="2026-09-27 17:00 UTC",
                                     roster=roster) if a.kind == "acquire"]
    assert len(acts) == 2
    for a in acts:
        assert a.status == "CONDITIONAL"            # never executable
        assert "plockd" not in a.backup.split("next verified drop")[-1].split(".")[0]
        assert "next verified drop" in a.backup
        assert any("FREE AGENT" in v or "waiver" in v for v in a.verify)


def test_a_conditional_drop_puts_the_exact_check_on_the_card():
    from gridiron.dashboard import build_actions
    roster, starters = _full()
    board = build_board(roster, [P("fa", "RB", 12)], starters,
                        ("RB", "WR", "FLEX"), locks_known=True)
    plan = plan_lineup(roster, starters, ("RB", "WR", "FLEX"))
    gate = build_gate([SourceFreshness(n, Status.FRESH, NOW, 1, None, "ok") for n in
                       ("sleeper_league", "sleeper_players", "injuries", "schedules")])
    a = next(a for a in build_actions(plan=plan, board=board, gate=gate, now=NOW,
                                      slots=("RB", "WR", "FLEX"),
                                      snapshot_as_of="2026-09-27 17:00 UTC", roster=roster)
             if a.kind == "acquire")
    assert a.status == "CONDITIONAL"
    assert any("pb1" in v and "Sleeper" in v for v in a.verify)
    assert "UNVERIFIED" in a.detail


# ------------------------------------------------------------ the target side

def test_an_already_owned_target_is_never_a_candidate():
    roster = [P("r1", "RB", 5, "START"), P("w1", "WR", 5, "START"), P("ok", "WR", 1)]
    board = build_board(roster, [P("r1", "RB", 5), P("fa", "RB", 9)], ["r1", "w1"],
                        ("RB", "WR"), locks_known=True)
    assert all(u.add.sleeper_id != "r1" for u in board.upgrades)
    assert all(c.add.sleeper_id != "r1" or c.verdict != LINEUP for c in board.candidates)


def test_a_player_on_another_roster_is_not_in_the_pool():
    from gridiron.waivers import available_ids
    players = {"101": {"position": "RB", "team": "KC", "status": "Active"},
               "102": {"position": "RB", "team": "KC", "status": "Active"}}
    rosters = [{"players": ["101"]}]           # on another team's roster
    assert available_ids(players, rosters) == ("102",)


def test_the_radar_record_carries_the_drop_check():
    roster, starters = _full()
    board = build_board(roster, [P("fa", "RB", 12)], starters,
                        ("RB", "WR", "FLEX"), locks_known=True)
    c = next(c for c in board.candidates if c.add.sleeper_id == "fa")
    rec = R.candidate_record(c)
    assert rec["drop_check"] == c.drop_check and rec["drop_check"]


def test_game_day_carries_the_drop_check_and_reads_old_records_without_one():
    from gridiron.gameday import summarise_radar
    block = {"counts": {}, "candidates": [
        {"verdict": "LINEUP", "id": "1", "name": "A", "drop": {"name": "B", "id": "2"},
         "drop_check": "B's game has started; open B in Sleeper and see whether it offers a drop"},
        {"verdict": "LINEUP", "id": "3", "name": "C", "drop": {"name": "D", "id": "4"}}]}
    moves = summarise_radar(block)["moves"]
    assert moves[0]["drop_check"] and moves[1]["drop_check"] == ""
