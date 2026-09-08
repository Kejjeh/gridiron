"""Pin the canonical scoring formula with hand-computed lines.

Numbers use the VERIFIED league scoring (Sleeper, 2026-09-08): half-PPR,
INT -1, fumble lost -2. If these fail after a settings pull, the league
changed its rules — update league_config and these lines in one commit.
"""
from __future__ import annotations

import pytest

from gridiron.league_config import DEFAULT_SCORING, ScoringRules
from gridiron.scoring import fantasy_points


def test_default_rules_are_the_verified_league_rules():
    assert DEFAULT_SCORING.reception == 0.5
    assert DEFAULT_SCORING.interception == -1.0
    assert DEFAULT_SCORING.fumble_lost == -2.0


def test_half_ppr_receiving_line():
    # 8 rec (4.0) + 103 yds (10.3) + 1 TD (6) = 20.3
    stats = {"receptions": 8, "receiving_yards": 103, "receiving_tds": 1}
    assert fantasy_points(stats) == pytest.approx(20.3)


def test_qb_line_with_turnovers():
    # 287 pass yds (11.48) + 2 TD (8) - 1 INT (1) + 12 rush yds (1.2) = 19.68
    stats = {
        "passing_yards": 287, "passing_tds": 2, "interceptions": 1,
        "rushing_yards": 12,
    }
    assert fantasy_points(stats) == pytest.approx(19.68)


def test_missing_keys_are_zero_and_extras_ignored():
    assert fantasy_points({}) == 0.0
    assert fantasy_points({"snap_pct": 0.91, "team": 0}) == 0.0


def test_rules_parameterization_full_ppr():
    stats = {"receptions": 10}
    assert fantasy_points(stats, ScoringRules(reception=1.0)) == pytest.approx(10.0)


def test_negative_yardage_scores_negative():
    assert fantasy_points({"rushing_yards": -7}) == pytest.approx(-0.7)
