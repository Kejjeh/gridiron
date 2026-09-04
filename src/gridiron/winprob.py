"""Closed-form head-to-head win probability and its decision derivatives.

The decision layer denominates every move in ΔP(win) (rule #7). Under a
normal approximation of the weekly margin, everything reduces to:

    D = X_a - X_b ~ Normal(mu_a - mu_b, s^2),
    s^2 = sigma_a^2 + sigma_b^2 - 2*rho*sigma_a*sigma_b,
    P(win) = Phi((mu_a - mu_b) / s).

Two derivatives drive strategy:

  dP/dmu_a  = phi(z)/s                      (the "leverage per point" — how
                                             much one expected point is worth
                                             THIS week, peaks in even matchups)
  dP/dsigma_a = -phi(z) * d * (sigma_a - rho*sigma_b) / s^3, d = mu_a - mu_b
                                            (the variance-sign-flip: positive
                                             when trailing — underdogs chase
                                             ceiling; negative when favored —
                                             favorites chase floor)

Verified numbers (docs/research/QUANT_FOUNDATIONS.md §2, reproduced by
scripts/research/verify_winprob.py, N=1e6 Monte Carlo):
  - 12-team full-PPR 9-slot lineup SD ~22.9 -> margin SD s ~32.3 (29-34)
  - leverage at an even matchup = 1.23 pp per projected point
  - Phi vs MC error for P(win): <= 0.25 pp (similar lineups), <= 0.5 pp
    (stacked vs unstacked with the correlated SD)
  - the variance derivative OVERSTATES the underdog's gain under
    right-skewed (gamma) marginals: raising SD at fixed mean lowers the
    lineup median, so the practical breakeven for chasing variance is a
    deficit of ~6 pts (~0.19 s), not 0. Use full MC for variance/skew
    trades at |d| < 15; use these closed forms for mean-leverage.
"""
from __future__ import annotations

from math import erf, exp, pi, sqrt


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def _margin_sd(sigma_a: float, sigma_b: float, rho: float) -> float:
    var = sigma_a * sigma_a + sigma_b * sigma_b - 2.0 * rho * sigma_a * sigma_b
    # Numerical guard: rho near +/-1 with equal sigmas can round negative.
    return sqrt(max(var, 0.0))


def matchup_win_prob(
    mu_a: float, sigma_a: float, mu_b: float, sigma_b: float, rho: float = 0.0
) -> float:
    """P(team A outscores team B) under the normal margin approximation.

    rho is the correlation between the two LINEUP totals (nonzero when the
    lineups share game environments, e.g. opposing stacks in the same NFL
    game); it is not a player-level correlation.
    """
    s = _margin_sd(sigma_a, sigma_b, rho)
    d = mu_a - mu_b
    if s == 0.0:
        return 0.5 if d == 0.0 else (1.0 if d > 0.0 else 0.0)
    return norm_cdf(d / s)


def leverage_per_point(
    mu_a: float, sigma_a: float, mu_b: float, sigma_b: float, rho: float = 0.0
) -> float:
    """dP(win)/dmu_a: win-probability gained per expected point added to A.

    This is the exchange rate that turns projected points into ΔP(win) —
    the denominator of every start/sit and waiver decision (rule #7).
    """
    s = _margin_sd(sigma_a, sigma_b, rho)
    if s == 0.0:
        return 0.0
    return norm_pdf((mu_a - mu_b) / s) / s


def dpwin_dsigma_a(
    mu_a: float, sigma_a: float, mu_b: float, sigma_b: float, rho: float = 0.0
) -> float:
    """dP(win)/dsigma_a: the variance-sign-flip derivative.

    Positive when A is the underdog (mu_a < mu_b): more lineup variance
    HELPS. Negative when A is favored. Zero in a dead-even matchup.
    """
    s = _margin_sd(sigma_a, sigma_b, rho)
    if s == 0.0:
        return 0.0
    d = mu_a - mu_b
    return -norm_pdf(d / s) * d * (sigma_a - rho * sigma_b) / (s * s * s)
