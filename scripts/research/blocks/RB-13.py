"""RB-13: a touchdown-driven outcome distribution for running backs.

A distribution block (KIND = "distribution"): scored by
scripts/research/distribution_backtest.py against the page's normal, never
by block_runner.py's mean bar. The pre-registration is
docs/research/blocks/RB-13.md; the shared code is _p7_distributions.py.

Points = 6 x N + Y around the page's own mean mu: N ~ Poisson(s mu / 6)
touchdowns, s = his share of expected fantasy points that comes from
expected touchdowns (season to date shrunk to his last-season share, which
is shrunk to the position's), and Y ~ gamma with mean (1 - s) mu (yards,
catches, two-point tries, fumbles) and sd a + b (1 - s) mu, with (a, b)
fitted on the training seasons by minimum CRPS.
"""
from __future__ import annotations

import _p7_distributions as D

ID = "RB-13"
TITLE = "Touchdown-driven outcome spread (Poisson TDs + gamma yardage), RBs"
KIND = "distribution"
POSITION = "RB"
LATTICE = D.SKILL_STEP
COLUMNS = {POSITION: ["td_share_std", "td_games_std", "td_share_prior"]}


def build(ctx):
    return D.td_share_inputs(ctx, POSITION)


def pmf(rows, params):
    return D.td_pmf(rows["mu"].to_numpy(), D.td_share(rows), params["a"], params["b"])


def fit(train, crps):
    y = train["actual"].to_numpy(dtype="float64")

    def score(a, b):
        x0, p = pmf(train, {"a": a, "b": b})
        return float(crps(x0, p, y).mean())
    a, b, best = D.fit_spread_ab(score)
    return {"a": round(a, 4), "b": round(b, 4), "train_crps": round(best, 5)}
