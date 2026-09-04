"""Empirical-Bayes shrinkage for share/rate metrics (rule #6).

Usage shares (target share, carry share, route rate, snap share) are
binomial-ish counts. With a Beta(alpha, beta) prior over true shares, the
posterior mean after observing x successes in n opportunities is

    (x + alpha) / (n + alpha + beta)  =  w*(x/n) + (1-w)*prior_mean,
    w = n / (n + n0),   n0 = alpha + beta.

n0 is THE stabilization constant: the opportunity count at which the data
gets equal weight with the prior. Per-metric n0 values (and the evidence
behind them) live in docs/research/QUANT_FOUNDATIONS.md — pass them in,
never hardcode them at call sites.
"""
from __future__ import annotations


def shrink_weight(n: float, n0: float) -> float:
    """Weight on the OBSERVED rate (vs the prior) after n opportunities."""
    if n < 0 or n0 < 0:
        raise ValueError("n and n0 must be nonnegative")
    if n + n0 == 0:
        return 0.0
    return n / (n + n0)


def eb_shrink(x: float, n: float, prior_mean: float, n0: float) -> float:
    """Posterior-mean estimate of a rate: x successes in n opportunities,
    shrunk toward prior_mean with pseudo-sample size n0."""
    if not 0.0 <= prior_mean <= 1.0:
        raise ValueError("prior_mean must be a rate in [0, 1]")
    if n + n0 <= 0:
        raise ValueError("need n + n0 > 0")
    return (x + prior_mean * n0) / (n + n0)


def reliability_to_n0(n_per_half: float, split_half_r: float) -> float:
    """Convert a published split-half reliability into a pseudo-sample size.

    Spearman-Brown: reliability at sample n is n/(n + n0), so n0 = n(1-r)/r.
    This is how every "metric X stabilizes at N attempts" claim in the
    literature becomes a usable shrinkage constant.
    """
    if not 0.0 < split_half_r < 1.0:
        raise ValueError("split_half_r must be in (0, 1)")
    if n_per_half <= 0:
        raise ValueError("n_per_half must be positive")
    return n_per_half * (1.0 - split_half_r) / split_half_r


def beta_from_moments(mean: float, var: float) -> tuple[float, float]:
    """Method-of-moments Beta(alpha, beta) fit from a population mean and
    variance of true shares. Requires var < mean*(1-mean)."""
    if not 0.0 < mean < 1.0:
        raise ValueError("mean must be strictly inside (0, 1)")
    limit = mean * (1.0 - mean)
    if not 0.0 < var < limit:
        raise ValueError(f"need 0 < var < mean*(1-mean) = {limit:.6f}")
    nu = limit / var - 1.0
    return mean * nu, (1.0 - mean) * nu


# ---------------------------------------------------------------------------
# Empirically-derived priors, docs/research/QUANT_FOUNDATIONS.md §5.
#
# *** UNVERIFIED (rule #5 gate not cleared) ***
# Derived by a single research agent from the cached nflverse 2023-25 weekly
# data; its adversarial verifier was killed by a usage limit before it ran.
# Sibling sections that DID face a verifier had 14 claims corrected, so treat
# these as first-draft. They may be used for exploration; nothing ships to a
# user-facing output until the verification run lands.
# ---------------------------------------------------------------------------

#: Team opportunities per team-game, 2023-25 (1,632 team-games). Converts a
#: pseudo-sample size in opportunities into "games" for human-readable output.
TEAM_TARGETS_PER_GAME = 31.3
TEAM_CARRIES_PER_GAME = 26.9

#: Out-of-sample-optimal n0 (opportunities) for the posterior mean, chosen by
#: RMSE against rest-of-season share with a last-season prior. Usage
#: stabilizes 5-20x faster than efficiency -- this table IS rule #6.
N0_IN_SEASON: dict[str, float] = {
    "wr_te_target_share": 90.0,    # ~3 games; loss flat over 60-120
    "rb_target_share": 180.0,      # weekly RB targets are far noisier
    "rb_carry_share": 45.0,        # week 1; decays to ~0 by week 6, see N0_RB_CARRY_BY_WEEK
    "catch_rate": 50.0,
    "yards_per_target": 130.0,
    "ppr_per_target": 160.0,
    "td_per_target": 230.0,
    "yards_per_carry": 180.0,
    "td_per_carry": 250.0,
    "snap_share": 80.0,            # ESTIMATE by analogy -- no snap data cached yet
    "route_participation": 80.0,   # ESTIMATE
}

#: The RB carry-share prior dies fast: last season tells you almost nothing
#: about a backfield by October. Week (1-indexed) -> n0 in team carries.
N0_RB_CARRY_BY_WEEK: dict[int, float] = {1: 45.0, 2: 45.0, 3: 30.0, 4: 20.0, 5: 10.0, 6: 0.0}

#: Week-1 role priors: (mean share, n0_pop in opportunities). Population-level
#: Beta fits by method of moments over 2023-25 player-seasons with >= 8 games.
#: Roles are ranked WITHIN a team by opportunity count -- derive the rank from
#: usage, never from the roster position tag (rule #4).
TARGET_SHARE_PRIORS: dict[str, tuple[float, float]] = {
    "WR1": (0.249, 106.0),
    "WR2": (0.179, 73.0),
    "WR3": (0.123, 81.0),
    "TE1": (0.166, 71.0),
    "TE2": (0.073, 93.0),
    "RB1": (0.103, 62.0),
    "RB2": (0.062, 43.0),
}

CARRY_SHARE_PRIORS: dict[str, tuple[float, float]] = {
    "RB1": (0.535, 25.0),
    "RB2": (0.264, 17.0),
}

#: Fraction of a top-decile season retained the following year. The headline
#: of §5.5: touchdown rate is the fakest thing in fantasy football, usage is
#: the stickiest. Shrink TD rates ~90% toward the positional mean.
YOY_RETENTION: dict[str, float] = {
    "wr_te_target_share": 0.82,
    "rb_carry_share": 0.73,
    "rb_td_per_carry": 0.33,
    "wr_te_td_per_target": 0.11,
}
