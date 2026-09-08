"""Behaviour tests for gridiron.ledger (written test-first, 2026-09-08).

The ledger is the draft-night decision log (rule #7): every pick with the
board's value at the time, and for MY picks the rejected alternative.
"""
from __future__ import annotations

import pandas as pd

from gridiron import ledger

ME = "1004814445714989056"


def _pick(no, pid, by, first, last, pos, team, rnd=None):
    return {"pick_no": no, "round": rnd or (no - 1) // 12 + 1, "picked_by": by, "player_id": pid,
            "metadata": {"first_name": first, "last_name": last, "position": pos, "team": team}}


def _board():
    return pd.DataFrame([
        dict(sleeper_id="9221", name="Jahmyr Gibbs", pos="RB", team="DET", proj=296.2, vor=157.6, adp=1.4, adp_sd=1.0),
        dict(sleeper_id="9509", name="Bijan Robinson", pos="RB", team="ATL", proj=286.2, vor=147.7, adp=2.4, adp_sd=1.0),
        dict(sleeper_id="7564", name="Ja'Marr Chase", pos="WR", team="CIN", proj=255.2, vor=116.2, adp=3.7, adp_sd=1.0),
    ])


def test_ledger_has_one_row_per_pick_with_board_value_and_marks_mine():
    picks = [_pick(1, "9221", ME, "Jahmyr", "Gibbs", "RB", "DET"),
             _pick(2, "9509", "111", "Bijan", "Robinson", "RB", "ATL")]
    led = ledger.record_draft(picks, _board(), my_user_id=ME)
    assert list(led.pick) == [1, 2]
    assert led.loc[led.pick == 1, "proj"].item() == 296.2
    assert led.loc[led.pick == 2, "vor"].item() == 147.7
    assert list(led.mine) == [True, False]


def test_my_picks_record_the_rejected_alternative():
    # I take Chase at 1 while Gibbs and Bijan are on the board: the rejected
    # side is the best remaining VOR (Gibbs). At pick 3, after Gibbs went 2nd,
    # my alternative would be Bijan.
    picks = [_pick(1, "7564", ME, "Ja'Marr", "Chase", "WR", "CIN"),
             _pick(2, "9221", "111", "Jahmyr", "Gibbs", "RB", "DET"),
             _pick(3, "9509", ME, "Bijan", "Robinson", "RB", "ATL")]
    led = ledger.record_draft(picks, _board(), my_user_id=ME)
    first = led[led.pick == 1].iloc[0]
    assert first.alt_player == "Jahmyr Gibbs" and first.alt_vor == 157.6
    assert first.vor_given_up == 157.6 - 116.2
    third = led[led.pick == 3].iloc[0]
    assert pd.isna(third.alt_player)  # nothing left on the board
    assert pd.isna(led[led.pick == 2].iloc[0].alt_player)  # not my pick, not graded


def test_reach_is_adp_minus_pick_and_unknown_players_still_get_a_row():
    picks = [_pick(1, "9509", "111", "Bijan", "Robinson", "RB", "ATL"),      # ADP 2.4 taken 1st: +1.4
             _pick(2, "999999", ME, "Some", "Flier", "WR", "JAX"),           # not on the board
             _pick(3, "9221", "222", "Jahmyr", "Gibbs", "RB", "DET")]        # ADP 1.4 taken 3rd: -1.6
    led = ledger.record_draft(picks, _board(), my_user_id=ME)
    assert len(led) == 3
    assert led.loc[led.pick == 1, "reach"].item() == 1.4
    assert led.loc[led.pick == 3, "reach"].item() == -1.6
    flier = led[led.pick == 2].iloc[0]
    assert flier.player == "Some Flier" and flier.mine
    assert pd.isna(flier.proj) and pd.isna(flier.reach)
    assert flier.alt_player == "Jahmyr Gibbs"  # the rejected side is still known


def test_grade_availability_scores_predictions_against_who_was_really_there():
    # board predictions for my pick 3: Gibbs surely gone, Bijan surely gone, Chase a coin flip
    board = _board()
    board["p3"] = [0.0, 0.0, 0.5]
    picks = [_pick(1, "9221", "111", "Jahmyr", "Gibbs", "RB", "DET"),
             _pick(2, "9509", "222", "Bijan", "Robinson", "RB", "ATL"),
             _pick(3, "7564", ME, "Ja'Marr", "Chase", "WR", "CIN")]
    led = ledger.record_draft(picks, board, my_user_id=ME)
    g = ledger.grade_availability(led, board, my_picks=[3])
    # Gibbs/Bijan: predicted 0, gone -> error 0; Chase: predicted .5, there -> (1-.5)^2
    assert g["n"] == 3
    assert abs(g["brier"] - (0 + 0 + 0.25) / 3) < 1e-9
    assert g["by_pick"][3]["available"] == 1 and g["by_pick"][3]["expected"] == 0.5
    # a pick that never happened (draft cut short) is skipped, not an error
    assert ledger.grade_availability(led, board, my_picks=[3, 24])["n"] == 3
