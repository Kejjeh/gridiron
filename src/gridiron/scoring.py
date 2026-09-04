"""Canonical fantasy-point formula. Import this everywhere; never re-derive.

The plv_clone scar (test_sp_fp_formula_copies / test_no_hardcoded_scoring
_weights): scoring formulas copied into scripts drift silently. There is
exactly one implementation, parameterized by league_config.ScoringRules.
"""
from __future__ import annotations

from collections.abc import Mapping

from gridiron.league_config import DEFAULT_SCORING, ScoringRules

# Stat keys follow nflverse weekly-data column names so scored frames need
# no renaming at the join boundary.
_STAT_WEIGHTS: tuple[tuple[str, str], ...] = (
    ("passing_yards", "pass_yd"),
    ("passing_tds", "pass_td"),
    ("interceptions", "interception"),
    ("rushing_yards", "rush_yd"),
    ("rushing_tds", "rush_td"),
    ("receptions", "reception"),
    ("receiving_yards", "rec_yd"),
    ("receiving_tds", "rec_td"),
    ("fumbles_lost", "fumble_lost"),
    ("two_point_conversions", "two_pt"),
)


def fantasy_points(
    stats: Mapping[str, float],
    rules: ScoringRules = DEFAULT_SCORING,
) -> float:
    """Score one player-week stat line. Missing stat keys count as zero;
    unknown extra keys are ignored (frames carry many non-scoring columns)."""
    return sum(
        float(stats.get(stat_key, 0.0)) * getattr(rules, rule_field)
        for stat_key, rule_field in _STAT_WEIGHTS
    )
