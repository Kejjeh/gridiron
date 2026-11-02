"""DEF-03: points-allowed tiers as a distribution, plus the counting stats.

A distribution block (KIND = "distribution"): scored by
scripts/research/distribution_backtest.py against the page's normal, never
by block_runner.py's mean bar. The pre-registration is
docs/research/blocks/DEF-03.md; the shared code is _p7_distributions.py.

Points = the league's points-allowed tier (DEFENSE_SCORING) of the
opponent's points, which are the opponent's implied total (the frozen DEF
table's `opp_implied`, from the line) plus a normal spread fitted on the
training seasons by minimum CRPS, rounded to whole points; plus Poisson
counts of 1-point (sacks, forced fumbles), 2-point (takeaways, safeties,
blocked kicks) and 6-point events (defensive and return touchdowns). The
whole distribution is moved by one constant so its mean is the page's.
"""
from __future__ import annotations

import numpy as np

import _p7_distributions as D

ID = "DEF-03"
TITLE = "Points-allowed tiers over the opponent's points, plus counting stats"
KIND = "distribution"
POSITION = "DEF"
LATTICE = 1.0
COLUMNS = {POSITION: ["dst_rate_1", "dst_rate_2", "dst_rate_6"]}
GROUP_VALUES = {"dst_rate_1": ("sacks", "ff"), "dst_rate_2": ("takeaways", "minor2"),
                "dst_rate_6": ("tds",)}


def build(ctx):
    return D.defense_inputs(ctx)


def _values():
    vals = D.defense_values()
    out = []
    for c in COLUMNS[POSITION]:
        v = {vals[g] for g in GROUP_VALUES[c]}
        if len(v) != 1:
            raise ValueError(f"{c} mixes point values {v}")
        out.append(v.pop())
    return np.array(out, dtype="float64")


def pmf(rows, params):
    rates = rows[COLUMNS[POSITION]].to_numpy(dtype="float64")
    return D.defense_pmf(rows["mu"].to_numpy(), rows["opp_implied"].to_numpy(dtype="float64"),
                         rates, _values(), params["pa_sd"])


def fit(train, crps):
    y = train["actual"].to_numpy(dtype="float64")

    def score(pa_sd):
        x0, p = pmf(train, {"pa_sd": pa_sd})
        return float(crps(x0, p, y).mean())
    best, scores = D.fit_grid(D.PA_SD_GRID, score)
    return {"pa_sd": best, "train_crps": round(scores[best], 5),
            "grid": {f"{k:.2f}": round(v, 5) for k, v in scores.items()}}
