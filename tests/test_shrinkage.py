"""Pin the empirical-Bayes shrinkage math."""
from __future__ import annotations

import pytest

from gridiron.shrinkage import beta_from_moments, eb_shrink, shrink_weight


def test_posterior_mean_hand_computed():
    # 20 targets on 80 routes, prior share .15 with n0=120:
    # (20 + .15*120)/(80+120) = 38/200 = 0.19
    assert eb_shrink(20, 80, 0.15, 120) == pytest.approx(0.19)


def test_shrink_is_a_convex_combination():
    x, n, prior, n0 = 22, 60, 0.18, 90
    w = shrink_weight(n, n0)
    assert eb_shrink(x, n, prior, n0) == pytest.approx(
        w * (x / n) + (1 - w) * prior
    )


def test_no_data_returns_the_prior():
    assert eb_shrink(0, 0, 0.22, 100) == pytest.approx(0.22)
    assert shrink_weight(0, 100) == 0.0


def test_weight_hits_half_at_n0():
    assert shrink_weight(150, 150) == pytest.approx(0.5)


def test_weight_is_monotone_in_n():
    ws = [shrink_weight(n, 120) for n in (0, 30, 120, 480)]
    assert ws == sorted(ws) and ws[-1] > 0.75


def test_beta_from_moments_roundtrip():
    a, b = beta_from_moments(0.2, 0.01)
    mean = a / (a + b)
    var = a * b / ((a + b) ** 2 * (a + b + 1))
    assert mean == pytest.approx(0.2)
    assert var == pytest.approx(0.01)


def test_beta_from_moments_rejects_impossible_variance():
    with pytest.raises(ValueError):
        beta_from_moments(0.2, 0.2)  # var >= mean*(1-mean)


def test_eb_shrink_validates_inputs():
    with pytest.raises(ValueError):
        eb_shrink(5, 10, 1.5, 100)
    with pytest.raises(ValueError):
        eb_shrink(0, 0, 0.2, 0)


# --- empirical priors (QUANT_FOUNDATIONS §5, UNVERIFIED) ------------------

from gridiron.shrinkage import (  # noqa: E402
    CARRY_SHARE_PRIORS,
    N0_IN_SEASON,
    N0_RB_CARRY_BY_WEEK,
    TARGET_SHARE_PRIORS,
    TEAM_TARGETS_PER_GAME,
    YOY_RETENTION,
    reliability_to_n0,
)


def test_reliability_to_n0_matches_the_published_conversions():
    # WR/TE target share: r=0.922 at 222 targets per half -> n0 ~ 19
    assert reliability_to_n0(222, 0.922) == pytest.approx(18.8, abs=0.2)
    # yards/target: r=0.221 at 36 targets -> n0 ~ 127 (efficiency is slow)
    assert reliability_to_n0(36, 0.221) == pytest.approx(126.9, abs=0.5)
    # a metric with r=0.5 stabilizes exactly at n0 = n
    assert reliability_to_n0(100, 0.5) == pytest.approx(100.0)


def test_reliability_to_n0_validates():
    with pytest.raises(ValueError):
        reliability_to_n0(100, 0.0)
    with pytest.raises(ValueError):
        reliability_to_n0(0, 0.5)


def test_usage_stabilizes_faster_than_efficiency():
    """Rule #6 as an executable assertion."""
    assert N0_IN_SEASON["wr_te_target_share"] < N0_IN_SEASON["yards_per_target"]
    assert N0_IN_SEASON["yards_per_target"] < N0_IN_SEASON["td_per_target"]
    assert N0_IN_SEASON["rb_carry_share"] < N0_IN_SEASON["yards_per_carry"]


def test_wr_te_target_share_stabilizes_in_about_three_games():
    games = N0_IN_SEASON["wr_te_target_share"] / TEAM_TARGETS_PER_GAME
    assert 2.5 < games < 3.5


def test_rb_carry_prior_decays_to_nothing():
    weeks = sorted(N0_RB_CARRY_BY_WEEK)
    values = [N0_RB_CARRY_BY_WEEK[w] for w in weeks]
    assert values == sorted(values, reverse=True)
    assert values[-1] == 0.0


def test_role_priors_are_ordered_and_valid_shares():
    for priors in (TARGET_SHARE_PRIORS, CARRY_SHARE_PRIORS):
        for mean, n0 in priors.values():
            assert 0.0 < mean < 1.0 and n0 > 0
    assert TARGET_SHARE_PRIORS["WR1"][0] > TARGET_SHARE_PRIORS["WR2"][0]
    assert TARGET_SHARE_PRIORS["WR2"][0] > TARGET_SHARE_PRIORS["WR3"][0]
    assert CARRY_SHARE_PRIORS["RB1"][0] > CARRY_SHARE_PRIORS["RB2"][0]


def test_role_priors_are_consistent_with_beta_from_moments():
    """n0_pop must round-trip through the Beta parameterisation."""
    mean, n0 = TARGET_SHARE_PRIORS["WR1"]
    var = mean * (1 - mean) / (n0 + 1)
    a, b = beta_from_moments(mean, var)
    assert a + b == pytest.approx(n0)
    assert a / (a + b) == pytest.approx(mean)


def test_td_rate_is_the_least_sticky_thing_measured():
    assert YOY_RETENTION["wr_te_td_per_target"] < YOY_RETENTION["rb_td_per_carry"]
    assert YOY_RETENTION["rb_td_per_carry"] < YOY_RETENTION["rb_carry_share"]
    assert YOY_RETENTION["rb_carry_share"] < YOY_RETENTION["wr_te_target_share"]
