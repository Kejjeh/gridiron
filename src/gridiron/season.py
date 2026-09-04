"""Season-level playoff math: Poisson-binomial win distributions + leverage.

Remaining weeks have heterogeneous win probabilities p_1..p_W (from
winprob.matchup_win_prob per future matchup). The number of future wins K
follows a Poisson-binomial distribution, computed exactly by DP — W <= 14,
so exact beats any approximation for free.

The key identity (derive once, test forever): if making the playoffs
requires at least k more wins INCLUDING this week's game, then

    P(playoffs | win this week)  = P(K_rest >= k-1)
    P(playoffs | lose this week) = P(K_rest >= k)
    win_leverage = difference    = P(K_rest = k-1)

i.e. this week's game matters exactly as much as the probability that the
REST of the schedule lands on the knife edge. Leverage is ~0 when nearly
clinched or nearly eliminated, and peaks in tight races — which is why one
expected point is worth different amounts of playoff equity in different
standings (rule #7).

Simplification, stated loudly: a fixed wins-needed threshold k is a
stand-in for the true stochastic cutoff (it depends on the other teams).
The full engine Monte-Carlos the league to get a distribution over k and
mixes these primitives across it; that lands with build step 6. Do not
treat a point-estimate k as exact in tight races.
"""
from __future__ import annotations

from collections.abc import Sequence


def win_distribution(probs: Sequence[float]) -> list[float]:
    """Exact Poisson-binomial pmf: result[k] = P(exactly k wins).

    O(W^2) DP; W is at most ~14 weeks, so this is instant and exact.
    """
    for p in probs:
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"win probability out of [0,1]: {p}")
    pmf = [1.0]
    for p in probs:
        nxt = [0.0] * (len(pmf) + 1)
        for k, mass in enumerate(pmf):
            nxt[k] += mass * (1.0 - p)
            nxt[k + 1] += mass * p
        pmf = nxt
    return pmf


def p_at_least(probs: Sequence[float], k: int) -> float:
    """P(at least k wins) over the given weeks. k <= 0 -> 1.0."""
    if k <= 0:
        return 1.0
    pmf = win_distribution(probs)
    if k > len(probs):
        return 0.0
    return sum(pmf[k:])


def win_leverage(rest_probs: Sequence[float], wins_needed: int) -> float:
    """Playoff-equity swing of THIS week's game.

    wins_needed counts this week: the team needs >= wins_needed more wins
    (this game plus the rest) to make the playoffs. Returns
    P(playoffs | win) - P(playoffs | lose) = P(K_rest = wins_needed - 1).

    0 when already clinched (wins_needed <= 0) or already eliminated
    (wins_needed exceeds games remaining including this one).
    """
    if wins_needed <= 0:
        return 0.0                      # clinched: this game is dead rubber
    if wins_needed > len(rest_probs) + 1:
        return 0.0                      # eliminated: also dead rubber
    pmf = win_distribution(rest_probs)
    return pmf[wins_needed - 1]


def playoff_prob(rest_probs: Sequence[float], this_week_p: float,
                 wins_needed: int) -> float:
    """P(playoffs) before this week's game resolves, fixed-threshold model."""
    if not 0.0 <= this_week_p <= 1.0:
        raise ValueError(f"win probability out of [0,1]: {this_week_p}")
    p_win_branch = p_at_least(rest_probs, wins_needed - 1)
    p_lose_branch = p_at_least(rest_probs, wins_needed)
    return this_week_p * p_win_branch + (1.0 - this_week_p) * p_lose_branch
