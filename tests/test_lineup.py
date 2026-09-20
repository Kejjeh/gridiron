"""Legal lineups under kickoff locks.

Every case is built from hand-made Players so the arithmetic is visible.
The invariants: the optimizer never proposes an illegal slot, never moves a
locked player, never promotes off IR, never proposes a swap it cannot
evaluate (an unprojected side), abstains entirely when the lock state is
unknown, and does not report same-value churn as a change.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from gridiron.lineup import (
    LOCKED, OPEN, UNKNOWN, Player, current_lineup, eligible, kickoff_index,
    lock_state, plan_lineup, slot_order,
)
from gridiron.projection import Projection, abstain

UTC = timezone.utc
SLOTS = slot_order(["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF",
                    "BN", "BN", "IR"])


def P(sid, pos, mean, lineup="BENCH", *, locked=False, sd=None, unprojected=False):
    proj = abstain("no admissible box scores") if unprojected else \
        Projection(mean, sd if sd is not None else max(mean * 0.5, 1.5))
    return Player(sid, f"p{sid}", pos, "T" + sid, proj, lineup, locked,
                  "LOCKED — kicked off" if locked else "kicks off Sun")


def roster():
    return [
        P("q1", "QB", 20, "START"), P("r1", "RB", 15, "START"), P("r2", "RB", 10, "START"),
        P("w1", "WR", 14, "START"), P("w2", "WR", 9, "START"), P("t1", "TE", 7, "START"),
        P("f1", "WR", 8, "START"), P("f2", "RB", 6, "START"), P("k1", "K", 8, "START"),
        P("d1", "DST", 0, "START", unprojected=True),
        P("q2", "QB", 22), P("r3", "RB", 12), P("w3", "WR", 3), P("t2", "TE", 4),
        P("ir", "RB", 30, "IR"),
    ]


STARTERS = ["q1", "r1", "r2", "w1", "w2", "t1", "f1", "f2", "k1", "d1"]


def test_slot_order_follows_sleeper_and_drops_bench_and_ir():
    assert SLOTS == ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DST")
    assert slot_order(None) == ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DST")


def test_eligibility_is_position_and_flex_only():
    assert eligible("FLEX", "RB") and eligible("FLEX", "WR") and eligible("FLEX", "TE")
    assert not eligible("FLEX", "QB") and not eligible("FLEX", "K")
    assert eligible("DST", "DEF") and not eligible("RB", "WR")


def test_the_best_lineup_is_legal_and_improves_on_the_current_one():
    plan = plan_lineup(roster(), STARTERS, SLOTS)
    assert not plan.abstained
    for slot, p in zip(SLOTS, plan.best):
        assert p is None or eligible(slot, p.position), (slot, p)
    ids = [p.sleeper_id for p in plan.best if p]
    assert len(ids) == len(set(ids))                      # nobody twice
    assert "ir" not in ids                                # never off IR
    assert plan.best_points > plan.current_points
    # q2 (22) replaces q1 (20); r3 (12) replaces f2 (6) in FLEX
    changed = {(SLOTS[i], c.sleeper_id if c else None, b.sleeper_id if b else None)
               for i, c, b in plan.changes()}
    assert ("QB", "q1", "q2") in changed
    assert ("FLEX", "f2", "r3") in changed


def test_a_locked_starter_is_frozen_and_a_locked_bench_player_is_not_proposed():
    r = roster()
    r[0] = P("q1", "QB", 20, "START", locked=True)      # q1 cannot leave
    r[10] = P("q2", "QB", 22, locked=True)              # q2 cannot enter
    plan = plan_lineup(r, STARTERS, SLOTS)
    assert plan.best[0].sleeper_id == "q1"
    assert all(a.bench.sleeper_id != "q2" for a in plan.alternatives)
    reasons = {p.sleeper_id: why for p, why in plan.frozen}
    assert "q1" in reasons and "LOCKED" in reasons["q1"]
    assert "q2" in reasons


def test_an_unprojected_starter_is_never_swapped_out():
    r = roster()
    r[3] = P("w1", "WR", 0, "START", unprojected=True)  # unknown WR1
    plan = plan_lineup(r, STARTERS, SLOTS)
    assert plan.best[3].sleeper_id == "w1"
    assert all(not (a.starter and a.starter.sleeper_id == "w1") for a in plan.alternatives)
    assert any(p.sleeper_id == "w1" and "no projection" in why for p, why in plan.frozen)


def test_an_unprojected_bench_player_is_never_proposed():
    r = roster()
    r[11] = P("r3", "RB", 0, unprojected=True)
    plan = plan_lineup(r, STARTERS, SLOTS)
    assert all(a.bench.sleeper_id != "r3" for a in plan.alternatives)
    assert "r3" not in [p.sleeper_id for p in plan.best if p]


def test_alternatives_carry_a_z_score_and_a_noise_verdict():
    plan = plan_lineup(roster(), STARTERS, SLOTS)
    by_bench = {a.bench.sleeper_id: a for a in plan.alternatives}
    a = by_bench["q2"]
    assert a.slot == "QB" and a.starter.sleeper_id == "q1"
    assert a.delta_points == pytest.approx(2.0)
    assert a.z == pytest.approx(2.0 / (11 ** 2 + 10 ** 2) ** 0.5, abs=1e-3)
    assert a.within_noise                                 # 2 pts on ~15 SD
    assert "within noise" in a.describe()
    assert by_bench["w3"].delta_points < 0                # a worse bench WR is listed, negative


def test_unknown_lock_state_abstains_from_everything():
    plan = plan_lineup(roster(), STARTERS, SLOTS, locks_known=False)
    assert "UNKNOWN" in plan.abstained
    assert plan.best == plan.current and plan.alternatives == ()


def test_same_value_churn_is_not_reported_as_a_change():
    r = [P("q1", "QB", 20, "START"), P("r1", "RB", 15, "START"), P("r2", "RB", 10, "START"),
         P("w1", "WR", 14, "START"), P("w2", "WR", 9, "START"), P("t1", "TE", 7, "START"),
         P("f1", "RB", 12, "START"), P("f2", "WR", 8, "START"), P("k1", "K", 8, "START"),
         P("d1", "DST", 0, "START", unprojected=True)]
    starters = ["q1", "r1", "r2", "w1", "w2", "t1", "f1", "f2", "k1", "d1"]
    plan = plan_lineup(r, starters, SLOTS)
    # f1 (RB 12) out-projects r2 (RB 10): greedy would put f1 at RB and r2 at
    # FLEX for the same total. That is not a change and must not be listed.
    assert plan.changes() == ()
    assert plan.best_points == pytest.approx(plan.current_points)


def test_an_empty_slot_is_filled_and_reads_as_an_alternative():
    r = roster()
    starters = list(STARTERS)
    starters[2] = "0"                                     # RB2 empty
    r[2] = P("r2", "RB", 10)                              # r2 sits on the bench
    plan = plan_lineup(r, starters, SLOTS)
    assert plan.current[2] is None
    assert plan.best[2] is not None and plan.best[2].position == "RB"
    a = next(a for a in plan.alternatives if a.bench.sleeper_id == "r3")
    assert a.starter is None and a.delta_points == pytest.approx(12.0)


def test_current_lineup_maps_zero_to_an_empty_slot():
    r = roster()
    cur = current_lineup(r, ["q1", "0", "r2"], SLOTS[:3])
    assert cur[0].sleeper_id == "q1" and cur[1] is None and cur[2].sleeper_id == "r2"


# ------------------------------------------------------------------ locks

def test_kickoff_locks_follow_the_schedule_in_eastern_time():
    sched = pd.DataFrame([
        {"week": 2, "gameday": "2026-09-20", "gametime": "13:00", "home_team": "A", "away_team": "B"},
        {"week": 2, "gameday": "2026-09-21", "gametime": "20:15", "home_team": "C", "away_team": "D"},
    ])
    idx = kickoff_index(sched, 2)
    assert idx.rows_intact and idx.games == 2
    assert idx.kickoffs["A"] == idx.kickoffs["B"] == datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    before = datetime(2026, 9, 20, 16, 59, tzinfo=UTC)
    after = datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    assert lock_state("A", idx, before).state == OPEN
    assert lock_state("A", idx, before).note == "kicks off Sun 17:00 UTC"
    assert lock_state("A", idx, before).kickoff == datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
    assert lock_state("A", idx, after).state == LOCKED
    assert lock_state("C", idx, after).state == OPEN     # Monday night not yet


def test_a_bye_needs_a_game_on_both_sides_of_the_gap():
    """Three absences, three different facts, and only one of them is a bye.

    Z is missing from week 2 but plays in weeks 1 and 3, so the gap is
    bracketed by the frame's own rows and is a scheduled bye. Y is missing
    from week 2 and plays only in week 1, so the frame stops before it could
    show a week-3 game: that is the shape of a truncated pull, not evidence
    of a bye. ZZ is a name this schedule has never carried at all.
    """
    sched = pd.DataFrame([
        {"week": 1, "gameday": "2026-09-13", "gametime": "13:00", "home_team": "A", "away_team": "Z"},
        {"week": 1, "gameday": "2026-09-13", "gametime": "13:00", "home_team": "B", "away_team": "Y"},
        {"week": 2, "gameday": "2026-09-20", "gametime": "13:00", "home_team": "A", "away_team": "B"},
        {"week": 3, "gameday": "2026-09-27", "gametime": "13:00", "home_team": "A", "away_team": "Z"},
    ])
    idx = kickoff_index(sched, 2)
    now = datetime(2026, 9, 20, 18, 0, tzinfo=UTC)

    bye = lock_state("Z", idx, now)
    assert bye.state == OPEN and "BYE" in bye.note
    assert idx.proven_bye == frozenset({"Z"})

    unbracketed = lock_state("Y", idx, now)
    assert unbracketed.state == UNKNOWN and not unbracketed.movable
    assert "before and after" in unbracketed.note

    stranger = lock_state("ZZ", idx, now)
    assert stranger.state == UNKNOWN and "not a bye" in stranger.note


def test_no_schedule_means_lock_state_unknown_not_unlocked():
    assert kickoff_index(None, 2) is None
    assert kickoff_index(pd.DataFrame(), 2) is None
    assert kickoff_index(pd.DataFrame([{"week": 1, "gameday": "2026-09-13", "gametime": "13:00",
                                        "home_team": "A", "away_team": "B"}]), 2) is None
    lock = lock_state("A", None, datetime.now(UTC))
    assert lock.state == UNKNOWN and not lock.movable and "UNKNOWN" in lock.note
