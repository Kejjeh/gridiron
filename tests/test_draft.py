"""Contract tests for gridiron.draft — the pure math behind the draft board.

Written first (TDD, 2026-09-08). Every function here is used by
scripts/research/draft_board_2026.py and mirrored in the war-room page's
JavaScript, so the two must agree on these numbers.
"""
from __future__ import annotations

import math

import pandas as pd
import pytest

from gridiron import draft


# ----------------------------------------------------------------- snake order
def test_snake_picks_slot_1_of_12():
    assert draft.snake_picks(slot=1, teams=12, rounds=15) == [
        1, 24, 25, 48, 49, 72, 73, 96, 97, 120, 121, 144, 145, 168, 169]


def test_snake_picks_slot_12_turns_immediately():
    picks = draft.snake_picks(slot=12, teams=12, rounds=4)
    assert picks == [12, 13, 36, 37]


def test_snake_picks_every_slot_covers_the_board_exactly_once():
    seen = sorted(p for s in range(1, 13) for p in draft.snake_picks(s, 12, 15))
    assert seen == list(range(1, 181))


# ----------------------------------------------------------------- ADP model
def test_adp_sd_is_linear_with_a_floor():
    assert draft.adp_sd(0.0) == 1.0
    assert draft.adp_sd(100.0) == pytest.approx(0.57 + 11.0)
    assert draft.adp_sd(1.5) == pytest.approx(1.0)  # floor binds near the top


def test_p_available_unconditional_is_half_at_adp():
    # continuity-corrected: the pick numbered ADP + 0.5 is the median
    assert draft.p_available(adp=24.5, sd=3.0, pick=25) == pytest.approx(0.5)
    assert draft.p_available(adp=1.4, sd=1.0, pick=24) < 1e-6
    assert draft.p_available(adp=120.0, sd=14.0, pick=24) > 0.999


def test_p_survive_is_a_proper_conditional():
    # still here now -> certain now; decreasing in the next pick; equals the
    # unconditional survival when "now" is pick 1
    assert draft.p_survive(adp=30.0, sd=4.0, now=25, nxt=25) == pytest.approx(1.0)
    a = draft.p_survive(30.0, 4.0, now=25, nxt=30)
    b = draft.p_survive(30.0, 4.0, now=25, nxt=48)
    assert 1.0 > a > b >= 0.0
    assert draft.p_survive(30.0, 4.0, now=1, nxt=48) == pytest.approx(
        draft.p_available(30.0, 4.0, 48), abs=1e-9)


def test_p_survive_handles_a_player_who_fell_far_past_adp():
    # numerically safe (no division blow-up) and near zero: a top player who
    # fell to pick 40 is taken immediately
    p = draft.p_survive(adp=5.0, sd=1.2, now=40, nxt=41)
    assert 0.0 <= p <= 1.0
    assert p < 0.05


# ----------------------------------------------------------------- replacement + lineup
def _pool():
    rows = []
    rows += [("RB", 100 - 5 * i) for i in range(8)]     # 100..65
    rows += [("WR", 90 - 3 * i) for i in range(8)]      # 90..69
    rows += [("TE", 70 - 10 * i) for i in range(4)]     # 70..40
    rows += [("QB", 300 - 20 * i) for i in range(4)]    # 300..240
    return pd.DataFrame(rows, columns=["pos", "proj"])


def test_replacement_levels_fill_flex_by_projection():
    df = _pool()
    starters = {"QB": 2, "RB": 4, "WR": 4, "TE": 2}     # 2 teams
    repl, flex_mix = draft.replacement_levels(df, starters, flex_slots=2)
    # starters take RB 100..85, WR 90..81, TE 70/60, QB 300/280. Leftovers:
    # RB 80,75,70,65 | WR 78,75,72,69 | TE 50,40. Two flex = RB 80 + WR 78,
    # so replacement is the next one down at each position.
    assert flex_mix == {"RB": 1, "WR": 1}
    assert repl == {"QB": 260.0, "RB": 75.0, "WR": 75.0, "TE": 50.0}


def test_replacement_levels_zero_when_a_position_is_exhausted():
    df = pd.DataFrame([("TE", 50.0), ("RB", 80.0)], columns=["pos", "proj"])
    repl, _ = draft.replacement_levels(df, {"TE": 1, "RB": 1}, flex_slots=0)
    assert repl["TE"] == 0.0 and repl["RB"] == 0.0


def test_optimal_lineup_uses_flex_for_the_best_leftover():
    roster = [("QB", 300), ("RB", 100), ("RB", 90), ("RB", 85), ("WR", 95),
              ("WR", 70), ("TE", 60), ("WR", 88), ("K", 100), ("DEF", 90)]
    slots = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1, "DST": 1}
    total, starters = draft.optimal_lineup(roster, slots)
    # RB 100, 90 | WR 95, 88 | TE 60 | FLEX RB 85 + WR 70 | K, DEF, QB
    assert total == pytest.approx(300 + 100 + 90 + 95 + 88 + 60 + 85 + 70 + 100 + 90)
    assert len(starters) == 10


def test_optimal_lineup_tolerates_missing_positions():
    total, starters = draft.optimal_lineup([("RB", 50)], {"QB": 1, "RB": 2, "FLEX": 1})
    assert total == 50 and starters == [0]
