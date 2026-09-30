"""Actual usage and its trend (`gridiron.trends`). Synthetic frames only.

Pins: the label reads VOLUME only (rule #6); a missing week is absent, never
zero; no week after the boundary is read; fewer than three games claims no
direction; and a later record's block grades a week by gsis id.
"""
from __future__ import annotations

import pandas as pd
import pytest

from gridiron import trends as T


def _frame(rows):
    base = {"position": "WR", "league_points": 10.0, "offense_pct": 0.8,
            "targets": 6.0, "carries": 0.0, "attempts": 0.0, "target_share": 0.2,
            "receptions": 4.0, "receiving_yards": 50.0, "rushing_yards": 0.0,
            "passing_yards": 0.0, "receiving_tds": 0.0, "rushing_tds": 0.0,
            "passing_tds": 0.0}
    return pd.DataFrame([{**base, **r} for r in rows])


def _weeks(gid, *specs, **common):
    return [{"gsis_id": gid, "week": w, **common, **s} for w, s in enumerate(specs, 1)]


def _line(frame, gid="g1", through=None):
    return T.usage_line(frame, gid, through_week=through)


def test_a_growing_role_is_rising_and_a_shrinking_one_falling():
    rising = _frame(_weeks("g1", {"targets": 3, "offense_pct": .60},
                           {"targets": 5, "offense_pct": .85},
                           {"targets": 8, "offense_pct": .80}))
    assert _line(rising)["trend"] == T.RISING
    falling = _frame(_weeks("g1", {"targets": 0, "carries": 18, "offense_pct": .85},
                            {"targets": 0, "carries": 6, "offense_pct": .36},
                            {"targets": 0, "carries": 7, "offense_pct": .52}))
    line = _line(falling)
    assert line["trend"] == T.FALLING
    assert "18.0 -> 6.5" in line["trend_why"]


def test_points_and_efficiency_never_move_the_label():
    """Same volume, wildly different production: STEADY either way (rule #6)."""
    hot = _frame(_weeks("g1", {"league_points": 2, "receiving_yards": 10},
                        {"league_points": 30, "receiving_yards": 180, "receiving_tds": 3},
                        {"league_points": 35, "receiving_yards": 200, "receiving_tds": 3}))
    assert _line(hot)["trend"] == T.STEADY


def test_a_big_snap_swing_alone_moves_the_label():
    up = _frame(_weeks("g1", {"offense_pct": .40}, {"offense_pct": .60},
                       {"offense_pct": .70}))
    assert _line(up)["trend"] == T.RISING


def test_a_quarterbacks_volume_is_attempts_and_carries():
    qb = _frame(_weeks("g1", {"attempts": 40, "carries": 2},
                       {"attempts": 22, "carries": 1}, {"attempts": 20, "carries": 1},
                       position="QB", targets=0))
    line = _line(qb)
    assert line["weeks"][0]["opportunities"] == 42
    assert line["trend"] == T.FALLING


def test_fewer_than_three_games_claims_no_direction():
    two = _frame(_weeks("g1", {"targets": 1}, {"targets": 12}))
    assert _line(two)["trend"] == T.TOO_FEW


def test_a_missing_week_is_absent_not_zero():
    f = _frame([{"gsis_id": "g1", "week": 1}, {"gsis_id": "g1", "week": 3},
                {"gsis_id": "g1", "week": 4}])
    line = _line(f)
    assert [w["week"] for w in line["weeks"]] == [1, 3, 4]
    assert line["games"] == 3 and line["ppg"] == 10.0


def test_no_week_after_the_boundary_is_read():
    f = _frame(_weeks("g1", {}, {}, {}, {"league_points": 99}))
    line = _line(f, through=3)
    assert max(w["week"] for w in line["weeks"]) == 3 and line["points"] == 30.0


def test_a_missing_snap_row_stays_none():
    f = _frame(_weeks("g1", {"offense_pct": float("nan")}, {}, {}))
    assert _line(f)["weeks"][0]["snap_pct"] is None


def test_the_block_is_keyed_by_sleeper_id_and_skips_players_with_no_line():
    f = _frame(_weeks("g1", {}, {}, {}))
    block = T.usage_block(f, [("111", "g1"), ("222", "g2"), ("", "g1")], through_week=3)
    assert set(block["players"]) == {"111"}
    assert block["players"]["111"]["gsis_id"] == "g1"
    assert "volume only" in block["basis"]
    assert T.usage_block(None, [("111", "g1")], through_week=3)["players"] == {}


def test_actuals_come_from_a_later_blocks_week_by_gsis_id():
    f = _frame(_weeks("g1", {"league_points": 4}, {"league_points": 17.5}, {})
               + _weeks("g2", {"league_points": 9}))
    block = T.usage_block(f, [("111", "g1"), ("222", "g2")], through_week=3)
    assert T.actuals_from_usage(block, 2) == {"g1": 17.5}
    assert T.actuals_from_usage(block, 1) == {"g1": 4.0, "g2": 9.0}
    assert T.actuals_from_usage(None, 1) == {}


@pytest.mark.parametrize("specs,label", [
    (({"targets": 4, "offense_pct": .9}, {"targets": 8, "offense_pct": .6},
      {"targets": 8, "offense_pct": .6}), T.MIXED),
])
def test_contradicting_signals_say_mixed(specs, label):
    assert _line(_frame(_weeks("g1", *specs)))["trend"] == label
