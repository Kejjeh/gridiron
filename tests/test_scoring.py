"""Pin the canonical scoring formula with hand-computed lines."""
from __future__ import annotations

import pytest

from gridiron.league_config import ScoringRules
from gridiron.scoring import fantasy_points


def test_ppr_receiving_line():
    # 8 rec, 103 yds, 1 TD: 8 + 10.3 + 6 = 24.3
    stats = {"receptions": 8, "receiving_yards": 103, "receiving_tds": 1}
    assert fantasy_points(stats) == pytest.approx(24.3)


def test_qb_line_with_turnovers():
    # 287 pass yds (11.48) + 2 TD (8) - 1 INT (2) + 12 rush yds (1.2) = 18.68
    stats = {
        "passing_yards": 287, "passing_tds": 2, "interceptions": 1,
        "rushing_yards": 12,
    }
    assert fantasy_points(stats) == pytest.approx(18.68)


def test_missing_keys_are_zero_and_extras_ignored():
    assert fantasy_points({}) == 0.0
    assert fantasy_points({"snap_pct": 0.91, "team": 0}) == 0.0


def test_rules_parameterization_half_ppr():
    stats = {"receptions": 10}
    assert fantasy_points(stats, ScoringRules(reception=0.5)) == pytest.approx(5.0)


def test_negative_yardage_scores_negative():
    assert fantasy_points({"rushing_yards": -7}) == pytest.approx(-0.7)
