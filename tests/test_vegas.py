"""Pin the implied-total identity."""
from __future__ import annotations

import pytest

from gridiron.vegas import (
    implied_team_total,
    implied_totals,
    implied_totals_from_nflverse,
)


def test_worked_example():
    assert implied_team_total(47, -3) == pytest.approx(25.0)
    assert implied_team_total(47, +3) == pytest.approx(22.0)


def test_pair_sums_to_total_and_differs_by_spread():
    fav, dog = implied_totals(51.5, -6.5)
    assert fav + dog == pytest.approx(51.5)
    assert fav - dog == pytest.approx(6.5)


def test_pickem_splits_evenly():
    assert implied_totals(44, 0) == (pytest.approx(22.0), pytest.approx(22.0))


def test_favorite_spread_must_be_nonpositive():
    with pytest.raises(ValueError):
        implied_totals(47, 3)


def test_total_must_be_positive():
    with pytest.raises(ValueError):
        implied_team_total(0, -3)


def test_nflverse_sign_convention_home_positive_means_home_favored():
    # nflverse: spread_line +3.5 => home favored by 3.5 => home gets MORE.
    home, away = implied_totals_from_nflverse(47, 3.5)
    assert home == pytest.approx(25.25)
    assert away == pytest.approx(21.75)
    assert home + away == pytest.approx(47)


def test_nflverse_negative_spread_means_away_favored():
    # 2024 Super Bowl row shape: spread_line -1.5 with the away team favored.
    home, away = implied_totals_from_nflverse(48.5, -1.5)
    assert away > home
    assert away - home == pytest.approx(1.5)


# --- verified coefficients: pinned to verify_vegas.py's exact recomputation
# of the worked example (favorite -3 / total 47 -> I = 25.0) ---------------

from gridiron.league_config import ScoringRules  # noqa: E402
from gridiron.vegas import (  # noqa: E402
    I_REF,
    expected_fg_made,
    expected_offensive_tds,
    expected_pass_tds,
    expected_rush_tds,
    expected_team_points,
    league_mean_pass_attempts,
    league_mean_rush_attempts,
    league_mean_targets,
    points_per_carry,
    points_per_target,
    receiving_efficiency_multiplier,
    rushing_efficiency_multiplier,
)


def test_worked_example_scoring_at_I_25():
    assert expected_pass_tds(25.0) == pytest.approx(1.719, abs=0.002)
    assert expected_rush_tds(25.0) == pytest.approx(1.095, abs=0.002)
    assert expected_offensive_tds(25.0) == pytest.approx(2.817, abs=0.002)
    # 1.3965 + 0.0139*25 = 1.744 (the verifier's 1.747 used the rounded
    # intercept 1.40; the refit intercept is what ships)
    assert expected_fg_made(25.0) == pytest.approx(1.744, abs=0.002)
    assert expected_team_points(25.0) == pytest.approx(25.87, abs=0.01)


def test_scoring_identity_ties_the_fits_together():
    # d(pts)/dI ~= 7.0 * d(offTD)/dI + 3 * d(FGM)/dI  (1.112 vs 1.096)
    d_pts = expected_team_points(26) - expected_team_points(25)
    d_td = expected_offensive_tds(26) - expected_offensive_tds(25)
    d_fg = expected_fg_made(26) - expected_fg_made(25)
    assert d_pts == pytest.approx(7.0 * d_td + 3.0 * d_fg, abs=0.02)


def test_worked_example_volume():
    assert league_mean_pass_attempts(-3, 47) == pytest.approx(33.14, abs=0.01)
    assert league_mean_targets(-3, 47) == pytest.approx(31.62, abs=0.02)
    assert league_mean_rush_attempts(-3, 47) == pytest.approx(26.62, abs=0.02)


def test_volume_function_reproduces_league_mean_at_pickem():
    # spread 0 at the mean closing total 44.06 -> the 2023-25 league mean
    assert league_mean_pass_attempts(0, 44.06) == pytest.approx(32.8, abs=0.1)


def test_positional_points_per_target_full_and_half_ppr():
    assert points_per_target("WR") == pytest.approx(1.719, abs=0.002)
    assert points_per_target("TE") == pytest.approx(1.753, abs=0.002)
    assert points_per_target("RB") == pytest.approx(1.545, abs=0.002)
    half = ScoringRules(reception=0.5)
    assert points_per_target("WR", half) == pytest.approx(1.404, abs=0.002)
    assert points_per_target("RB", half) == pytest.approx(1.153, abs=0.002)


def test_points_per_carry():
    assert points_per_carry() == pytest.approx(0.614, abs=0.001)


def test_worked_example_wr_24pct_share():
    targets = 0.24 * league_mean_targets(-3, 47)
    assert targets == pytest.approx(7.59, abs=0.01)
    flat = targets * points_per_target("WR")
    assert flat == pytest.approx(13.04, abs=0.02)
    scaled = flat * receiving_efficiency_multiplier(25.0)
    assert scaled == pytest.approx(13.99, abs=0.03)


def test_efficiency_multipliers_normalised_at_reference():
    assert receiving_efficiency_multiplier(I_REF) == pytest.approx(1.0)
    assert rushing_efficiency_multiplier(I_REF) == pytest.approx(1.0)
    assert receiving_efficiency_multiplier(25.0) == pytest.approx(1.073, abs=0.002)
    assert receiving_efficiency_multiplier(19.0) == pytest.approx(0.926, abs=0.002)
    assert rushing_efficiency_multiplier(25.0) > 1.0 > rushing_efficiency_multiplier(19.0)
