"""Scoring against real nflverse columns, pinned to two external ground truths.

The bug this file exists to prevent: `gridiron.scoring` originally read the
column names `interceptions`, `fumbles_lost` and `two_point_conversions`,
none of which exist in the nflverse weekly frame any more. Missing columns
score as ZERO, so every interception, lost fumble and two-point conversion
silently vanished — a wrong number that looks exactly like a right one.

Ground truth 1 (public, in the fixture): nflverse ships `fantasy_points`
(standard) and `fantasy_points_ppr` beside each row. Only the reception
weight differs between them, so half-PPR is their exact midpoint.

Ground truth 2 (public, in the fixture): Sleeper's own scored points for
each kicker in week 1 of this league's scoring.
"""
from __future__ import annotations

import math

import pytest

from gridiron.league_config import DEFAULT_SCORING, KICKING_SCORING, ScoringRules
from gridiron.scoring import fantasy_points, kicker_points, scoring_inputs

#: nflverse standard scoring uses -2 per interception; the league uses -1.
NFLVERSE_STANDARD = ScoringRules(interception=-2.0, reception=0.5)


def test_half_ppr_is_the_exact_midpoint_of_nflverse_standard_and_ppr(weekly_offense):
    assert len(weekly_offense) >= 10, "fixture shrank — this test would go vacuous"
    for row in weekly_offense.to_dict("records"):
        mid = (row["fantasy_points"] + row["fantasy_points_ppr"]) / 2
        assert fantasy_points(row, NFLVERSE_STANDARD) == pytest.approx(mid, abs=1e-9), (
            f"{row['player_display_name']} scores "
            f"{fantasy_points(row, NFLVERSE_STANDARD)} vs nflverse {mid}"
        )


def test_the_parity_fixture_actually_exercises_the_renamed_columns(weekly_offense):
    """A parity test over rows with no INT, no fumble and no 2pt would pass
    with the old broken key names. Fail loudly if the fixture drifts there."""
    fumbles = (weekly_offense[["sack_fumbles_lost", "rushing_fumbles_lost",
                               "receiving_fumbles_lost"]].fillna(0).sum().sum())
    twopt = (weekly_offense[["passing_2pt_conversions", "rushing_2pt_conversions",
                             "receiving_2pt_conversions"]].fillna(0).sum().sum())
    ints = weekly_offense["passing_interceptions"].fillna(0).sum()
    assert fumbles > 0 and twopt > 0 and ints > 0, (
        f"fixture is vacuous for the renamed columns: "
        f"fumbles={fumbles} twopt={twopt} ints={ints}")


def test_kicker_points_match_sleeper_actuals(weekly_kickers):
    assert len(weekly_kickers) >= 10
    for row in weekly_kickers.to_dict("records"):
        got = kicker_points(row, KICKING_SCORING)
        assert got == pytest.approx(row["sleeper_points"], abs=1e-9), (
            f"{row['player_display_name']}: ours {got} vs Sleeper "
            f"{row['sleeper_points']}")


def test_kicker_fixture_covers_a_miss_and_a_long_make(weekly_kickers):
    assert weekly_kickers["fg_missed"].fillna(0).sum() > 0
    assert weekly_kickers["fg_made_50_59"].fillna(0).sum() > 0


def test_legacy_column_names_still_score(weekly_offense):
    """Pre-rewrite frames (and the 2023-25 cache) keep working."""
    legacy = {"passing_yards": 300, "passing_tds": 2, "interceptions": 1,
              "fumbles_lost": 1, "two_point_conversions": 1}
    assert fantasy_points(legacy, DEFAULT_SCORING) == pytest.approx(
        300 * 0.04 + 2 * 4 - 1 - 2 + 2)


def test_a_frame_carrying_both_spellings_does_not_double_count():
    both = {"passing_interceptions": 2, "interceptions": 2}
    assert fantasy_points(both, DEFAULT_SCORING) == pytest.approx(-2.0)
    both_f = {"sack_fumbles_lost": 1, "rushing_fumbles_lost": 0,
              "receiving_fumbles_lost": 0, "fumbles_lost": 5}
    assert fantasy_points(both_f, DEFAULT_SCORING) == pytest.approx(-2.0)


def test_fumbles_lost_total_is_not_used():
    """It counts return fumbles, which nflverse and Sleeper both exclude from
    the player's offensive score."""
    assert fantasy_points({"fumbles_lost_total": 3}, DEFAULT_SCORING) == 0.0


def test_nan_and_none_score_as_zero_not_as_a_crash():
    assert fantasy_points({"passing_yards": float("nan"), "rushing_tds": None,
                           "receptions": 3}) == pytest.approx(1.5)
    assert kicker_points({"pat_made": float("nan"), "fg_made_40_49": 1}) == 4.0


def test_scoring_inputs_lists_every_column_read(weekly_offense, weekly_kickers):
    cols = scoring_inputs()
    assert "passing_interceptions" in cols and "fg_made_50_59" in cols
    assert "interceptions" not in cols, "legacy aliases are not ingest contracts"
    present = set(weekly_offense.columns) | set(weekly_kickers.columns)
    assert cols <= present, (
        "the fixtures no longer carry every column scoring reads: "
        f"{sorted(cols - present)}")


def test_reception_weight_is_the_verified_league_value():
    assert DEFAULT_SCORING.reception == 0.5      # half-PPR, verified on Sleeper
    assert DEFAULT_SCORING.interception == -1.0  # Sleeper default, not ESPN's -2
    assert not math.isnan(DEFAULT_SCORING.pass_yd)
