"""Pin the win-probability closed form and verify its derivatives against
finite differences (implementation-independent checks)."""
from __future__ import annotations

import pytest

from gridiron.winprob import (
    dpwin_dsigma_a,
    leverage_per_point,
    matchup_win_prob,
    norm_cdf,
)


def test_even_matchup_is_a_coin_flip():
    assert matchup_win_prob(110, 25, 110, 25) == pytest.approx(0.5)


def test_probabilities_are_complementary():
    p_ab = matchup_win_prob(120, 22, 105, 31)
    p_ba = matchup_win_prob(105, 31, 120, 22)
    assert p_ab + p_ba == pytest.approx(1.0)


def test_known_value():
    # d=15, s=sqrt(2*25^2)=35.3553..., z=0.424264, Phi(z)=0.66431 (tables)
    assert matchup_win_prob(115, 25, 100, 25) == pytest.approx(0.66431, abs=1e-4)


def test_degenerate_zero_variance():
    assert matchup_win_prob(100, 0, 90, 0) == 1.0
    assert matchup_win_prob(90, 0, 100, 0) == 0.0
    assert matchup_win_prob(100, 0, 100, 0) == 0.5


def test_lineup_correlation_shrinks_margin_sd():
    # Positive rho between lineup totals -> smaller margin SD -> the favorite
    # is MORE likely to win (same-game exposure cancels out).
    base = matchup_win_prob(115, 25, 100, 25, rho=0.0)
    corr = matchup_win_prob(115, 25, 100, 25, rho=0.3)
    assert corr > base


@pytest.mark.parametrize("mu_a,sig_a,mu_b,sig_b,rho", [
    (115, 25, 100, 25, 0.0),
    (95, 30, 110, 22, 0.0),
    (100, 25, 100, 25, 0.2),
    (108, 18, 112, 35, 0.15),
])
def test_leverage_matches_finite_difference(mu_a, sig_a, mu_b, sig_b, rho):
    h = 1e-5
    fd = (
        matchup_win_prob(mu_a + h, sig_a, mu_b, sig_b, rho)
        - matchup_win_prob(mu_a - h, sig_a, mu_b, sig_b, rho)
    ) / (2 * h)
    assert leverage_per_point(mu_a, sig_a, mu_b, sig_b, rho) == pytest.approx(
        fd, rel=1e-4
    )


@pytest.mark.parametrize("mu_a,sig_a,mu_b,sig_b,rho", [
    (115, 25, 100, 25, 0.0),
    (95, 30, 110, 22, 0.0),
    (108, 18, 112, 35, 0.15),
])
def test_dsigma_matches_finite_difference(mu_a, sig_a, mu_b, sig_b, rho):
    h = 1e-5
    fd = (
        matchup_win_prob(mu_a, sig_a + h, mu_b, sig_b, rho)
        - matchup_win_prob(mu_a, sig_a - h, mu_b, sig_b, rho)
    ) / (2 * h)
    assert dpwin_dsigma_a(mu_a, sig_a, mu_b, sig_b, rho) == pytest.approx(
        fd, rel=1e-4
    )


def test_variance_sign_flip():
    """Rule #7's strategic core: variance helps underdogs, hurts favorites."""
    assert dpwin_dsigma_a(95, 25, 110, 25) > 0     # underdog: chase ceiling
    assert dpwin_dsigma_a(110, 25, 95, 25) < 0     # favorite: chase floor
    assert dpwin_dsigma_a(100, 25, 100, 25) == pytest.approx(0.0)


def test_leverage_peaks_when_even():
    even = leverage_per_point(100, 25, 100, 25)
    ahead = leverage_per_point(120, 25, 100, 25)
    behind = leverage_per_point(80, 25, 100, 25)
    assert even > ahead and even > behind


def test_norm_cdf_reference_points():
    assert norm_cdf(0.0) == pytest.approx(0.5)
    assert norm_cdf(1.959964) == pytest.approx(0.975, abs=1e-5)
    assert norm_cdf(-1.281552) == pytest.approx(0.10, abs=1e-5)
