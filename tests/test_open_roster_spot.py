"""A pickup never gives up a player it does not have to (Astra review of PR #7).

The defect: with an open ACTIVE roster spot the board still proposed a drop
for every pickup, so the owner was told to surrender a player for nothing.

Pinned here:
  * open active spots are counted from the league's `roster_positions`
    (starters + bench) against the roster's players MINUS its IR (`reserve`)
    and taxi lists — never from a raw player count, and an IR or taxi spot
    is never a free active spot;
  * with a VERIFIED open spot a pickup is an add with no drop, judged by the
    same rule as every pickup (it must improve THIS week's best legal
    lineup; bench-only is research), with the same lock rules;
  * with the count UNKNOWN the drop is still named, and the move carries the
    capacity check explicitly (the cost can only be lower, never hidden);
  * a full roster is unchanged;
  * records with a null drop stay readable, and older records (which always
    named a drop) are never read as "no drop";
  * an unverified drop is checked by LOOKING in Sleeper, never by making it.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gridiron import radar as R
from gridiron.dashboard import action_desk, build_actions
from gridiron.freshness import SourceFreshness, Status
from gridiron.gating import build_gate
from gridiron.lineup import Player, plan_lineup
from gridiron.projection import Projection
from gridiron.waivers import (LINEUP, RESEARCH, RosterCapacity, build_board, drop_rule,
                              roster_capacity)

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
POS = ["QB", "RB", "WR", "FLEX", "BN", "BN", "BN"]          # 7 active spots
SLOTS = ("QB", "RB", "WR", "FLEX")


def P(sid, pos, mean, lineup="BENCH", *, locked=False):
    return Player(sid, f"p{sid}", pos, "T", Projection(mean, max(mean * 0.5, 1.5)), lineup,
                  locked, "his game has kicked off" if locked else "", "", (), "g" + sid, True)


def _gate():
    return build_gate([SourceFreshness(n, Status.FRESH, NOW, 1, None, "ok") for n in
                       ("sleeper_league", "sleeper_players", "injuries", "schedules")])


# ---------------------------------------------------------------- capacity

def _roster(players, reserve=None, taxi=None, **extra):
    return {"players": players, "reserve": reserve, "taxi": taxi, **extra}


def test_open_spots_are_active_spots_minus_active_players():
    c = roster_capacity(POS, _roster(["1", "2", "3", "4", "5"]))
    assert (c.open_spots, c.active_spots, c.active_players) == (2, 7, 5)
    assert c.known and c.check() == ""


def test_ir_and_taxi_players_do_not_use_an_active_spot_and_are_not_one():
    # 8 players on the roster, one on IR and one on taxi: 6 active of 7
    c = roster_capacity(POS, _roster([str(i) for i in range(8)], reserve=["6"], taxi=["7"]))
    assert c.open_spots == 1
    # a raw count (8 > 7) would have called this roster over-full
    # ...and an IR/taxi entry in roster_positions is not an active spot
    c = roster_capacity(POS + ["IR", "TAXI"], _roster(["1", "2", "3", "4", "5", "6", "7"]))
    assert c.open_spots == 0


def test_a_full_roster_has_no_open_spot():
    assert roster_capacity(POS, _roster([str(i) for i in range(7)])).open_spots == 0


@pytest.mark.parametrize("positions,roster,why", [
    (None, _roster(["1"]), "roster_positions"),
    ([], _roster(["1"]), "roster_positions"),
    (POS, None, "lists no players"),
    (POS, {"players": ["1"], "reserve": None}, "`taxi`"),          # field absent
    (POS, {"players": ["1"], "taxi": None}, "`reserve`"),
    (POS, _roster(["1"], reserve="6"), "not a list"),
    (POS, _roster([str(i) for i in range(9)]), "inconsistent"),
])
def test_an_unknown_count_is_said_to_be_unknown(positions, roster, why):
    c = roster_capacity(positions, roster)
    assert c.open_spots is None and why in c.reason
    assert "open active spot could not be established" in c.check()
    assert "no drop" in c.check()


# ------------------------------------------------------------- the board

def _board(capacity, pool=None, roster=None):
    roster = roster or [P("q", "QB", 15, "START"), P("r", "RB", 10, "START"),
                        P("w", "WR", 9, "START"), P("f", "RB", 6, "START"),
                        P("b1", "WR", 5), P("b2", "RB", 2)]
    pool = pool or [P("fa", "WR", 14)]
    return roster, build_board(roster, pool, ["q", "r", "w", "f"], SLOTS,
                               locks_known=True, capacity=capacity)


def test_a_verified_open_spot_makes_an_add_with_no_drop():
    roster, board = _board(roster_capacity(POS, _roster(["q", "r", "w", "f", "b1", "b2"])))
    u = board.upgrades[0]
    assert u.drop is None and u.lineup_gain > 0 and u.alternatives == ()
    c = next(c for c in board.candidates if c.add.sleeper_id == "fa")
    assert c.verdict == LINEUP and c.drop is None and "NO drop" in c.reason
    assert any("an add with NO drop" in n for n in board.notes)
    # nobody on the roster is given up
    assert "b2" not in {u.drop.sleeper_id for u in board.upgrades if u.drop}


def test_the_add_only_gain_is_the_full_gain_and_never_below_the_drop_version():
    _, full = _board(RosterCapacity(0))
    _, open_ = _board(RosterCapacity(1, 7, 6, "6 of 7"))
    assert full.upgrades[0].drop is not None
    assert open_.upgrades[0].lineup_gain >= full.upgrades[0].lineup_gain


def test_the_same_positive_gain_rule_applies_with_an_open_spot():
    """An open spot is not a reason to add a player who would only sit on the
    bench: that is research, exactly as with a full roster."""
    _, board = _board(RosterCapacity(2, 7, 5, "5 of 7"), pool=[P("fa", "WR", 3)])
    assert board.upgrades == ()
    c = next(c for c in board.candidates if c.add.sleeper_id == "fa")
    assert c.verdict != LINEUP


def test_a_started_free_agent_cannot_enter_even_into_an_open_spot():
    _, board = _board(RosterCapacity(2, 7, 5, "5 of 7"), pool=[P("fa", "WR", 30, locked=True)])
    assert board.upgrades == ()


def test_an_open_spot_still_works_when_every_player_is_protected():
    roster = [P("q", "QB", 15, "START", locked=True), P("r", "RB", 10, "START", locked=True),
              P("w", "WR", 9, "START", locked=True)]
    board = build_board(roster, [P("fa", "RB", 12)], ["q", "r", "w", "0"], SLOTS,
                        locks_known=True, capacity=RosterCapacity(4, 7, 3, "3 of 7"))
    assert board.upgrades and board.upgrades[0].drop is None and board.upgrades[0].slot == "FLEX"


def test_an_unknown_count_names_the_drop_and_carries_the_check():
    _, board = _board(roster_capacity(None, _roster(["q"])))
    u = board.upgrades[0]
    assert u.drop is not None and "could not be established" in u.capacity_check
    c = next(c for c in board.candidates if c.add.sleeper_id == "fa")
    assert c.capacity_check == u.capacity_check
    assert any("Roster capacity UNKNOWN" in n for n in board.notes)


def test_a_full_roster_is_unchanged():
    _, a = _board(RosterCapacity(0))
    _, b = _board(RosterCapacity(0, 7, 7, "7 of 7"))
    assert [(u.add.sleeper_id, u.drop.sleeper_id, u.lineup_gain) for u in a.upgrades] == \
           [(u.add.sleeper_id, u.drop.sleeper_id, u.lineup_gain) for u in b.upgrades]
    assert all(u.capacity_check == "" for u in a.upgrades)


# ------------------------------------------------------------- the cards

def _actions(capacity, pool):
    roster, board = _board(capacity, pool=pool)
    plan = plan_lineup(roster, ["q", "r", "w", "f"], SLOTS)
    acts = [a for a in build_actions(plan=plan, board=board, gate=_gate(), now=NOW,
                                     slots=SLOTS, snapshot_as_of="2026-09-26 11:00 UTC",
                                     roster=roster) if a.kind == "acquire"]
    return acts, action_desk(acts, _gate(), valid_until=None, designations_as_of="x")


def test_an_add_only_card_says_no_drop_and_names_nobody_to_drop():
    acts, desk = _actions(RosterCapacity(2, 7, 5, "5 of 7 active spots used"),
                          [P("fa", "WR", 14)])
    a = acts[0]
    assert a.status == "CONDITIONAL" and a.player_ids == ("fa",) and a.names == ("pfa",)
    assert a.cost.startswith("no drop") and "Cost: no drop" in a.detail
    assert "No drop needed" in a.backup and "nobody leaves the roster" in a.backup
    assert "with no drop" in a.neutral_detail and "at the cost of" not in a.neutral_detail
    rows = dict(desk.top[0].rows)
    assert rows["Cost"].startswith("no drop") and "FREE AGENT" in rows["Check"]


def test_two_adds_share_the_last_open_spot_as_one_either_or():
    acts, desk = _actions(RosterCapacity(1, 7, 6, "6 of 7 active spots used"),
                          [P("fa", "WR", 14), P("fb", "RB", 13)])
    assert len(acts) == 2 and all(a.shares == "open-spot" for a in acts)
    assert len(desk.top) == 1 and desk.top[0].title.startswith("Pick one")
    assert "one open spot" in desk.top[0].title
    assert all("ONE open spot" in a.backup for a in acts)


def test_two_adds_with_two_open_spots_are_not_an_either_or():
    acts, desk = _actions(RosterCapacity(2, 7, 5, "5 of 7 active spots used"),
                          [P("fa", "WR", 14), P("fb", "RB", 13)])
    assert all(a.shares == "" for a in acts) and len(desk.top) == 2


def test_an_unknown_count_keeps_the_card_conditional_with_the_check_in_view():
    acts, desk = _actions(roster_capacity(None, _roster(["q"])), [P("fa", "WR", 14)])
    a = acts[0]
    assert a.status == "CONDITIONAL" and a.capacity_check
    assert "capacity UNKNOWN" in a.cost and a.capacity_check in a.verify
    assert "needs no drop at all" in a.backup
    assert a.capacity_check in dict(desk.top[0].rows)["Check"]


def test_a_full_roster_card_still_names_its_verified_fallback_drops():
    acts, _ = _actions(RosterCapacity(0, 7, 7, "7 of 7"), [P("fa", "WR", 14)])
    a = acts[0]
    assert a.cost.startswith("drop ") and "next verified drop" in a.backup
    assert "capacity" not in a.cost


# ------------------------------------------------------- records, old and new

def test_a_null_drop_round_trips_through_the_records():
    _, board = _board(RosterCapacity(2, 7, 5, "5 of 7"))
    c = next(c for c in board.candidates if c.add.sleeper_id == "fa")
    rec = R.candidate_record(c)
    assert rec["drop"] is None and rec["alternatives"] == [] and rec["capacity_check"] == ""
    json.dumps(rec)                                   # archivable as is
    from gridiron.gameday import summarise_radar
    old = {"verdict": "LINEUP", "id": "1", "name": "A", "drop": {"name": "B", "id": "2"}}
    no_key = {"verdict": "LINEUP", "id": "3", "name": "C"}      # damaged: not "no drop"
    moves = summarise_radar({"counts": {}, "candidates": [rec, old, no_key]})["moves"]
    assert [m["no_drop"] for m in moves] == [True, False, False]
    # the like-for-like diff reads a drop that became "none"
    assert R._pid(rec["drop"]) == "" and R._pname(rec["drop"]) == "nobody"


# --------------------------------------------------- never test with a real drop

def test_an_unverified_drop_is_checked_by_looking_not_by_dropping():
    for p in (P("b", "RB", 1, locked=True),
              Player("u", "pu", "RB", "T", Projection(1, 1.5), "BENCH", False, "", "", (),
                     "gu", False)):
        rule = drop_rule(p)
        assert "try the drop" not in rule and "without submitting" in rule
    src = (ROOT / "src/gridiron/waivers.py").read_text("utf-8")
    assert "try the drop" not in src


# ------------------------------------------------------------ on the page

def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


SCN = _load("open_spot_scn", "scripts/weekly/dashboard_scenarios.py")
CLI = _load("open_spot_cli", "scripts/weekly/dashboard.py")


def _render(tmp_path, kind):
    root = tmp_path / kind
    SCN.build_scenario(root, kind, now=SCN.NOW)
    out = tmp_path / "out" / kind
    CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch" / kind),
              "--now", SCN.NOW.isoformat()])
    return ((out / "dashboard_latest.html").read_text("utf-8"),
            json.loads((out / "dashboard_latest.json").read_text("utf-8")))


def test_the_open_spot_scenario_shows_adds_without_drops(tmp_path):
    html, rec = _render(tmp_path, "open_spot")
    ups = rec["upgrades"]
    assert ups and all(u["drop_id"] is None for u in ups)
    desk = html.split('id="desk"', 1)[1].split("</section>", 1)[0]
    assert "no drop" in desk.lower() and "Pick one" not in desk


def test_the_complete_scenario_is_a_full_roster_and_still_names_drops(tmp_path):
    html, rec = _render(tmp_path, "complete")
    assert rec["upgrades"] and all(u["drop_id"] for u in rec["upgrades"])
    assert re.search(r"Pick one", html)
