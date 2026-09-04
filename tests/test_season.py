"""Pin the Poisson-binomial season math and the leverage identity."""
from __future__ import annotations

import math

import pytest

from gridiron.season import (
    p_at_least,
    playoff_prob,
    win_distribution,
    win_leverage,
)


def test_pmf_sums_to_one_and_matches_binomial_for_iid():
    probs = [0.6] * 8
    pmf = win_distribution(probs)
    assert sum(pmf) == pytest.approx(1.0)
    for k, mass in enumerate(pmf):
        assert mass == pytest.approx(
            math.comb(8, k) * 0.6**k * 0.4 ** (8 - k)
        )


def test_heterogeneous_hand_computed():
    # [0.6, 0.3]: P(0)=.4*.7=.28, P(1)=.6*.7+.4*.3=.54, P(2)=.18
    pmf = win_distribution([0.6, 0.3])
    assert pmf == [
        pytest.approx(0.28),
        pytest.approx(0.54),
        pytest.approx(0.18),
    ]
    assert p_at_least([0.6, 0.3], 1) == pytest.approx(0.72)
    assert p_at_least([0.6, 0.3], 2) == pytest.approx(0.18)


def test_p_at_least_edges():
    assert p_at_least([0.5, 0.5], 0) == 1.0
    assert p_at_least([0.5, 0.5], 3) == 0.0
    assert p_at_least([], 0) == 1.0


def test_leverage_identity_matches_direct_difference():
    """win_leverage must equal P(playoffs|win) - P(playoffs|lose) computed
    the long way — the identity is the module's core claim."""
    rest = [0.65, 0.5, 0.4, 0.55]
    for k in range(0, 7):
        direct = p_at_least(rest, k - 1) - p_at_least(rest, k)
        assert win_leverage(rest, k) == pytest.approx(
            direct if k >= 1 else 0.0
        ), f"identity failed at wins_needed={k}"


def test_leverage_dead_rubbers_are_zero():
    rest = [0.5, 0.5]
    assert win_leverage(rest, 0) == 0.0        # clinched
    assert win_leverage(rest, -2) == 0.0       # extra clinched
    assert win_leverage(rest, 4) == 0.0        # eliminated (3 games can't give 4)


def test_leverage_peaks_on_the_knife_edge():
    # 4 coin-flip games left; needing 3 (incl. this week) is the tight race.
    rest = [0.5] * 4
    levs = {k: win_leverage(rest, k) for k in range(1, 6)}
    # pmf_rest = [1/16, 4/16, 6/16, 4/16, 1/16] -> peak at k-1=2 -> k=3
    assert max(levs, key=levs.get) == 3
    assert levs[3] == pytest.approx(6 / 16)


def test_playoff_prob_decomposition():
    rest, p_now, k = [0.6, 0.45, 0.7], 0.55, 2
    total = playoff_prob(rest, p_now, k)
    manual = 0.55 * p_at_least(rest, 1) + 0.45 * p_at_least(rest, 2)
    assert total == pytest.approx(manual)
    # and the leverage is exactly d(playoff_prob)/d(p_now)'s coefficient:
    lift = playoff_prob(rest, 1.0, k) - playoff_prob(rest, 0.0, k)
    assert lift == pytest.approx(win_leverage(rest, k))


def test_input_validation():
    with pytest.raises(ValueError):
        win_distribution([0.5, 1.2])
    with pytest.raises(ValueError):
        playoff_prob([0.5], 1.5, 1)
