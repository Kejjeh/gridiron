"""K-03: kicker points from attempts by distance tier, as a distribution.

A distribution block (KIND = "distribution"): scored by
scripts/research/distribution_backtest.py against the page's normal, never
by block_runner.py's mean bar. The pre-registration is
docs/research/blocks/K-03.md; the shared code is _p7_distributions.py.

Points = the league's value (KICKING_SCORING) times the count of each
kicking event: made inside 40 (3), 40-49 (4), 50+ (5), made extra points
(1), misses (-1). Each count's rate is the team's attempts per game in that
tier (season to date shrunk to last season's league rate) times the
kicker's make rate (shrunk heavily to the league's), every rate scaled by
one factor so the mean is the page's. One dispersion kappa (multinomial,
Poisson or gamma-mixed counts) is fitted on the training seasons by minimum
CRPS.
"""
from __future__ import annotations

import numpy as np

import _p7_distributions as D

ID = "K-03"
TITLE = "Kicker points by distance tier (league scoring), counts around the page's mean"
KIND = "distribution"
POSITION = "K"
LATTICE = 1.0
COLUMNS = {POSITION: ["k_made_short", "k_made_40", "k_made_50", "k_xp_made", "k_missed"]}


def build(ctx):
    return D.kicking_inputs(ctx)


def _rates(rows):
    vals = D.kicking_values()
    cols = COLUMNS[POSITION]
    return rows[cols].to_numpy(dtype="float64"), np.array([vals[c] for c in cols], dtype="float64")


def pmf(rows, params):
    rates, values = _rates(rows)
    return D.kicker_pmf(rows["mu"].to_numpy(), rates, values, params["kappa"])


def fit(train, crps):
    y = train["actual"].to_numpy(dtype="float64")

    def score(kappa):
        x0, p = pmf(train, {"kappa": kappa})
        return float(crps(x0, p, y).mean())
    best, scores = D.fit_grid(D.KAPPA_GRID, score)
    return {"kappa": best, "train_crps": round(scores[best], 5),
            "grid": {f"{k:.4f}": round(v, 5) for k, v in scores.items()}}
