"""verify_faab.py -- adversarial numeric verification of the FAAB research.

Offline, deterministic (every RNG seeded), numpy only (no scipy: the normal
CDF is math.erf). Run from the repo root:

    python scripts/research/verify_faab.py        (~20 s)

What it recomputes and checks, section by section:

  A. Sealed-bid first-price auction, n in {3,5,8} iid U[0,1] private values.
     Rivals bid v(n-1)/n; a deviating bidder grid-searches its own
     multiplier (common random numbers; exact sorted-threshold evaluation
     of the deviant's expected profit for every multiplier at once).
     Checks that (n-1)/n is the empirical best response overall AND inside
     the value deciles where wins occur; checks the general-F formula
     beta(v) = v - int_0^v F^(n-1) / F(v)^(n-1) on F(x) = x^2 (-> 0.8 v).
  B. Common-value direction. Values V_i = w*C + (1-w)*s_i with C common
     (unobserved) and s_i = C + e_i the private signal (w=0 pure private,
     w=1 pure common). Symmetric fixed point of best-response multipliers
     b = m * E[V_i | s_i] in the proportional-strategy class. Checks whether
     the equilibrium shade grows with w (winner's curse -> shade MORE, as
     claimed) and how it moves with n (the claimed 'fewer bidders -> bid
     down' holds for private values; the sim shows what common values do).
  C. Dollars -> points -> Delta-P(win) chain: phi(0)/s leverage at s in
     {29,32,34}, leverage at |d|=10, the sigma cross-checks, the four dWins
     examples, and the first-order error at X=5.
  D. Backward-induction FAAB budget model (14 runs, budget 100, lognormal
     clearing price with median 20 and sigma 0.6, arrival profile 0.5 for
     t<=8 declining linearly to 0.14 at t=14, V_t = 3*0.85*L*R_t with
     R_t = weeks left + 0.5*2*3) as specified in the findings: shadow prices
     J_t(100)-J_t(90), optimal bid path at full budget, J_1(100), four
     robustness scenarios, concavity of J in B, break-even bids (linear
     lambda vs exact concave cost), and the fair-$-per-(point/week) table
     for weeks remaining in {13,10,7,4,2}.
  E. Pacing: exact policy evaluation (Bellman operator applied to a fixed
     policy) plus a seeded Monte Carlo cross-check of DP-optimal vs
     spend-early vs smooth vs hoard vs fixed-fraction policies on total
     accumulated Delta-P(win).
  F. Sourced-number arithmetic (percent-of-budget conversions, hit rates,
     lognormal quantiles, n_eff shade, the handcuff pre/post ratio).

Prints PASS/FAIL per check and a summary; exits non-zero on any FAIL so the
research verdicts can be re-derived mechanically.
"""
from __future__ import annotations

import sys
import time
from math import erf, exp, log, pi, sqrt

import numpy as np

SEED = 20260904

# --------------------------------------------------------------------------
# Constants under test (inline on purpose; they come from the findings)
# --------------------------------------------------------------------------
S_MARGIN = 32.0            # H2H weekly margin SD (winprob domain)
S_RANGE = (29.0, 34.0)
T_RUNS = 14                # waiver runs in the regular season
BMAX = 100                 # FAAB budget, integer dollars
PRICE_MEDIAN = 20.0        # lognormal clearing-price median (% of budget)
PRICE_SIGMA = 0.6
X_IMPACT = 3.0             # pts/week over the displaced player
S_START = 0.85             # P(started)
P_PLAYOFF = 0.5
K_PLAYOFF = 2.0
PLAYOFF_WEEKS = 3


def Phi(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def phi(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


L32 = phi(0.0) / S_MARGIN

CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    tag = "PASS" if ok else "FAIL"
    print(f"  [{tag}] {name}" + (f" -- {detail}" if detail else ""))


def hdr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# --------------------------------------------------------------------------
# Exact deviant-profit evaluator (common random numbers)
# --------------------------------------------------------------------------
class DeviantEval:
    """Deviant bids m*base_i and wins iff m > thr_i; profit_i = value_i - m*base_i.

    Sorting thresholds once lets the expected profit be evaluated for every
    multiplier on a grid via cumulative sums -- exact for the given draws.
    """

    def __init__(self, base: np.ndarray, value: np.ndarray, thr: np.ndarray):
        order = np.argsort(thr)
        self.thr = thr[order]
        self.cum_val = np.concatenate(([0.0], np.cumsum(value[order])))
        self.cum_base = np.concatenate(([0.0], np.cumsum(base[order])))
        self.n = len(thr)

    def profit(self, grid: np.ndarray, scale: float = 1.0) -> np.ndarray:
        idx = np.searchsorted(self.thr * scale, grid, side="left")
        return (self.cum_val[idx] - grid * self.cum_base[idx]) / self.n


# --------------------------------------------------------------------------
# A. First-price auction: is (n-1)/n the best response?
# --------------------------------------------------------------------------
def section_a(rng: np.random.Generator) -> None:
    hdr("A. First-price sealed-bid auction, iid U[0,1] private values")
    N = 600_000
    grid = np.round(np.arange(0.30, 1.0001, 0.005), 4)
    print(f"  N={N} auctions per n; deviant multiplier grid 0.30..1.00 step 0.005")
    for n in (3, 5, 8):
        c = (n - 1) / n
        v = rng.random((N, n))
        v0 = v[:, 0]
        rival_max = v[:, 1:].max(axis=1)
        thr = c * rival_max / v0          # deviant wins iff m*v0 > c*max rival
        ev = DeviantEval(v0, v0, thr)
        profit = ev.profit(grid)
        m_best = grid[int(np.argmax(profit))]
        analytic_at_c = (1.0 / n) / (n + 1)    # E[(1-c) v * v^(n-1)]
        at_c = profit[int(np.argmin(np.abs(grid - c)))]
        print(
            f"  n={n}: theory (n-1)/n = {c:.4f}; empirical argmax m = {m_best:.3f}; "
            f"profit@argmax {profit.max():.5f}, @m=c {at_c:.5f} "
            f"(analytic {analytic_at_c:.5f}), @m=1 {profit[-1]:.5f}"
        )
        check(
            f"A n={n}: best-response multiplier within 0.01 of (n-1)/n",
            abs(m_best - c) <= 0.0101,
            f"{m_best:.3f} vs {c:.4f}",
        )
        check(
            f"A n={n}: profit at m=c matches analytic 1/(n(n+1))",
            abs(at_c - analytic_at_c) < 3e-4,
            f"{at_c:.5f} vs {analytic_at_c:.5f}",
        )
        # per-decile best response (the BR is exactly linear in v, so every
        # decile should return the same multiplier; low deciles almost never
        # win at n=8 and are reported but not checked).
        edges = np.quantile(v0, np.linspace(0, 1, 11))
        row = []
        for k in range(10):
            hi = edges[k + 1] if k < 9 else 1.01
            sel = (v0 >= edges[k]) & (v0 < hi)
            p = DeviantEval(v0[sel], v0[sel], thr[sel]).profit(grid)
            row.append(grid[int(np.argmax(p))])
        print("      per-decile BR multiplier: " + " ".join(f"{x:.3f}" for x in row))
        worst = max(abs(x - c) for x in row[5:])
        check(
            f"A n={n}: deciles 6-10 BR within 0.02 of (n-1)/n",
            worst <= 0.0201,
            f"max dev {worst:.3f}",
        )
    # General-F formula on F(x) = x^2: beta(v) = v - v/(2n-1) = v(2n-2)/(2n-1)
    n = 3
    c = (2 * n - 2) / (2 * n - 1)
    v = np.sqrt(rng.random((N, n)))
    thr = c * v[:, 1:].max(axis=1) / v[:, 0]
    profit = DeviantEval(v[:, 0], v[:, 0], thr).profit(grid)
    m_best = grid[int(np.argmax(profit))]
    print(f"  F(x)=x^2, n=3: general formula predicts {c:.3f}; empirical argmax {m_best:.3f}")
    check("A general-F formula on F=x^2, n=3 -> 0.800", abs(m_best - c) <= 0.0101, f"{m_best:.3f}")
    # E[max of n-1 uniforms | max < v] = v(n-1)/n
    n = 5
    u = rng.random((N, n - 1))
    mx = u.max(axis=1)
    v_ref = 0.6
    cond = mx[mx < v_ref].mean()
    check(
        "A E[max_{j!=i} v_j | max < v] = v(n-1)/n (n=5, v=0.6)",
        abs(cond - v_ref * (n - 1) / n) < 2e-3,
        f"{cond:.4f} vs {v_ref * (n - 1) / n:.4f}",
    )
    shades = {k: round(1 - (k - 1) / k, 4) for k in (2, 3, 4, 5, 8, 12)}
    print(f"  shade factors 1-(n-1)/n: {shades}")
    check(
        "A shade factors 0.500/0.667/0.750/0.800/0.875/0.917 for n=2/3/4/5/8/12",
        all(abs((k - 1) / k - x) < 5e-4 for k, x in
            zip((2, 3, 4, 5, 8, 12), (0.5, 0.667, 0.75, 0.8, 0.875, 0.917))),
    )
    check("A n_eff shade 33.3% (n=3) vs 8.3% (n=12)",
          abs(shades[3] - 0.3333) < 1e-3 and abs(shades[12] - 0.0833) < 1e-3)


# --------------------------------------------------------------------------
# B. Common vs private values: direction of shading
# --------------------------------------------------------------------------
def solve_bid_function(PM: np.ndarray, V0: np.ndarray, n_bins: int = 25,
                       iters: int = 80, damp: float = 0.5):
    """Symmetric monotone equilibrium bid function beta(x), x = own value estimate.

    Damped best-response iteration on a binned grid: rivals bid beta(PM_j)
    (linear interpolation); in each own-estimate bin the deviant's expected
    profit E[(V0 - b) 1{b > max rival bid}] is evaluated exactly on a $0.25
    bid grid via the sorted-threshold trick. Returns bin centres, beta,
    the final max |BR - beta| residual, and the iteration count.
    """
    N, n = PM.shape
    x0 = PM[:, 0]
    edges = np.quantile(x0, np.linspace(0, 1, n_bins + 1))
    centers = 0.5 * (edges[:-1] + edges[1:])
    bin_idx = np.clip(np.searchsorted(edges, x0, side="right") - 1, 0, n_bins - 1)
    members = [np.where(bin_idx == k)[0] for k in range(n_bins)]
    bgrid = np.arange(0.0, 200.01, 0.25)
    beta = 0.8 * centers
    resid = float("inf")
    for it in range(iters):
        rmax = np.interp(PM[:, 1:], centers, beta).max(axis=1)
        br = np.empty(n_bins)
        for k in range(n_bins):
            idx = members[k]
            prof = DeviantEval(np.ones(len(idx)), V0[idx], rmax[idx]).profit(bgrid)
            br[k] = bgrid[int(np.argmax(prof))]
        br = np.maximum.accumulate(br)
        resid = float(np.max(np.abs(br - beta)))
        beta = (1 - damp) * beta + damp * br
        if resid < 0.3:
            break
    return centers, beta, resid, it + 1


def permute_columns(rng: np.random.Generator, M: np.ndarray) -> np.ndarray:
    """Independently permute each column -> same marginals, zero dependence."""
    out = np.empty_like(M)
    for j in range(M.shape[1]):
        out[:, j] = M[rng.permutation(M.shape[0]), j]
    return out


def section_b(rng: np.random.Generator) -> None:
    hdr("B. Common-value auction: does the winner's curse shade bids MORE?")
    N = 250_000
    mu, sc, se, sz = 100.0, 20.0, 20.0, 10.0
    kgain = sc ** 2 / (sc ** 2 + se ** 2)
    print(f"  C ~ N({mu:.0f},{sc:.0f}^2) unobserved common value; signal s_i = C + N(0,{se:.0f}^2);")
    print("  estimate x_i = E[V_i | info_i]. Models share the SAME marginal law of x_i:")
    print("    iid-PV   : values = column-permuted estimates (independent), known exactly")
    print("    aff-PV   : values = the correlated estimates themselves, known exactly (affiliation, no curse)")
    print("    common   : V_i = C, bidder knows only x_i = E[C|s_i]      (affiliation + winner's curse)")
    print(f"    mixed    : V_i = C + z_i, z_i ~ N(0,{sz:.0f}^2) private, x_i = E[C|s_i] + z_i")
    print("  Nonparametric symmetric equilibrium beta(x) by damped best-response iteration;")
    print("  bids compared at the SAME estimate x (bin quantiles 50/75/95%).")
    summary = {}
    for n in (3, 5, 8):
        C = mu + sc * rng.standard_normal(N)
        S = C[:, None] + se * rng.standard_normal((N, n))
        PM = mu + kgain * (S - mu)
        Z = sz * rng.standard_normal((N, n))
        PMm = PM + Z
        models = {
            "iid-PV": (permute_columns(rng, PM), None),
            "aff-PV": (PM, PM[:, 0]),
            "common": (PM, C),
            "mixed-iid-PV": (permute_columns(rng, PMm), None),
            "mixed": (PMm, C + Z[:, 0]),
        }
        res = {}
        for name, (est, V0) in models.items():
            if V0 is None:
                V0 = est[:, 0]
            res[name] = solve_bid_function(est, V0)
        curse = float((C - PM[:, 0])[PM[:, 0] > PM[:, 1:].max(axis=1)].mean())
        print(f"\n  n={n}: winner's curse E[C - x | own x is max] = {curse:+.2f} (x sd {PM[:, 0].std():.1f})")
        print(f"      {'model':14s} {'bid/x@50%':>10s} {'bid/x@75%':>10s} {'bid/x@95%':>10s} "
              f"{'$shade@50%':>11s} {'$shade@95%':>11s}  resid  its")
        i50, i75, i95 = 12, 18, 23
        for name, (centers, beta, resid, its) in res.items():
            print(f"      {name:14s} {beta[i50] / centers[i50]:10.3f} {beta[i75] / centers[i75]:10.3f} "
                  f"{beta[i95] / centers[i95]:10.3f} {centers[i50] - beta[i50]:11.2f} "
                  f"{centers[i95] - beta[i95]:11.2f}  {resid:5.2f}  {its:3d}")
        upper = slice(12, 25)
        d_common = res["common"][1][upper] - res["iid-PV"][1][upper]
        d_aff = res["aff-PV"][1][upper] - res["iid-PV"][1][upper]
        d_mixed = res["mixed"][1][upper] - res["mixed-iid-PV"][1][upper]
        print(f"      common - iidPV over upper-half bins: mean {d_common.mean():+.2f}$, "
              f"max {d_common.max():+.2f}$;  aff-PV - iidPV: mean {d_aff.mean():+.2f}$;  "
              f"mixed - its iidPV: mean {d_mixed.mean():+.2f}$")
        summary[n] = dict(res=res, d_common=d_common, d_aff=d_aff, d_mixed=d_mixed, curse=curse)
        check(f"B n={n}: common-value equilibrium bids BELOW iid private-value bids at the same estimate",
              bool((d_common < 0).all()), f"mean {d_common.mean():+.2f}$, max {d_common.max():+.2f}$")
        check(f"B n={n}: mixed (common + private) bids below its iid-PV benchmark",
              bool((d_mixed < 0).all()), f"mean {d_mixed.mean():+.2f}$, max {d_mixed.max():+.2f}$")
        check(f"B n={n}: winner's curse E[C - x | max signal] < 0", curse < -1.0, f"{curse:+.2f}")
        check(f"B n={n}: all five bid functions converged (resid < 1$)",
              all(r[2] < 1.0 for r in res.values()))
    for name in ("iid-PV", "common"):
        row = [summary[n]["res"][name][1][12] / summary[n]["res"][name][0][12] for n in (3, 5, 8)]
        print(f"  bid/x at the median estimate vs n=3/5/8, {name}: " + " / ".join(f"{r:.3f}" for r in row))
    pv = [summary[n]["res"]["iid-PV"][1][12] for n in (3, 5, 8)]
    check("B private values: fewer bidders -> lower bid (beta(median) increasing in n)", pv[0] < pv[1] < pv[2])
    cv = [summary[n]["res"]["common"][1][12] for n in (3, 5, 8)]
    print(f"  common-value beta(median x) vs n=3/5/8: {cv[0]:.2f} / {cv[1]:.2f} / {cv[2]:.2f} "
          f"(reported, not checked: the curse deepens with n so d(beta)/dn is model-dependent)")
    print("  NOTE a naive test comparing bid MULTIPLIERS across models with different estimate"
          " dispersion flips the sign (common-value posterior means are compressed, so the $ shade"
          " from competition alone is smaller); the claim only holds against a matched benchmark.")


# --------------------------------------------------------------------------
# C. Dollars -> points -> Delta-P(win)
# --------------------------------------------------------------------------
def section_c() -> None:
    hdr("C. Leverage chain: expected points -> Delta-P(win)")
    check("C phi(0) = 0.39894", abs(phi(0.0) - 0.39894) < 1e-5, f"{phi(0.0):.5f}")
    for s in (29.0, 32.0, 34.0):
        print(f"  s={s:.0f}: L = phi(0)/s = {phi(0.0) / s:.5f} wins/pt/wk")
    check("C L(s=32) = 0.01247", abs(L32 - 0.01247) < 5e-6, f"{L32:.5f}")
    check("C L range 0.01173 (s=34) .. 0.01376 (s=29)",
          abs(phi(0.0) / 34 - 0.01173) < 5e-6 and abs(phi(0.0) / 29 - 0.01376) < 5e-6,
          f"{phi(0.0) / 34:.5f} .. {phi(0.0) / 29:.5f}")
    l10 = phi(10 / S_MARGIN) / S_MARGIN
    check("C L at |d|=10, s=32 = 0.01187", abs(l10 - 0.01187) < 5e-6, f"{l10:.5f}")
    check("C s = sqrt(2)*22.6 = 32.0", abs(sqrt(2) * 22.6 - 32.0) < 0.05, f"{sqrt(2) * 22.6:.2f}")
    for sig, lab, claim in ((21.6, "full PPR", 0.0130), (17.4, "half PPR", 0.0162)):
        lx = phi(0.0) / (sqrt(2) * sig)
        check(f"C cross-check sigma_team {sig} ({lab}) -> L {claim}", abs(lx - claim) < 6e-5,
              f"s={sqrt(2) * sig:.2f}, L={lx:.5f}")
    for X, S, W, claim in ((1, 0.8, 10, 0.100), (3, 0.8, 10, 0.299),
                           (5, 0.9, 12, 0.673), (3, 0.85, 17, 0.540)):
        d = X * S * W * L32
        check(f"C dWins X={X} S={S} W={W} -> {claim}", abs(d - claim) < 6e-4, f"{d:.4f}")
    exact = Phi(5 / S_MARGIN) - 0.5
    lin = 5 * L32
    print(f"  first-order error at X=5: linear {lin:.5f} vs exact Phi(5/32)-0.5 = {exact:.5f} "
          f"({(lin / exact - 1) * 100:+.2f}%)")
    exact15 = Phi(15 / S_MARGIN) - 0.5
    print(f"  first-order error at X=15: linear {15 * L32:.4f} vs exact {exact15:.4f} "
          f"({(15 * L32 / exact15 - 1) * 100:+.1f}%)")
    print("  NOTE W=17 'remaining weeks' counts the 3 playoff weeks as ordinary weeks with S=0.85,"
          " which is inconsistent with the model's own P(playoffs)=0.5 * k=2 weighting"
          " (0.5*2 = 1.0 so the number coincides, but only because k=2 exactly offsets P=0.5).")


# --------------------------------------------------------------------------
# D. Backward-induction budget model
# --------------------------------------------------------------------------
def arrival_profile(flat: float | None = None) -> np.ndarray:
    p = np.zeros(T_RUNS + 2)
    for t in range(1, T_RUNS + 1):
        if flat is not None:
            p[t] = flat
        else:
            p[t] = 0.5 if t <= 8 else 0.5 - (0.5 - 0.14) * (t - 8) / 6.0
    return p


def value_profile(k: float = K_PLAYOFF) -> np.ndarray:
    V = np.zeros(T_RUNS + 2)
    for t in range(1, T_RUNS + 1):
        R = (T_RUNS - t + 1) + P_PLAYOFF * k * PLAYOFF_WEEKS
        V[t] = X_IMPACT * S_START * L32 * R
    return V


def price_cdf(median_by_t: np.ndarray, sigma: float = PRICE_SIGMA) -> np.ndarray:
    F = np.zeros((T_RUNS + 2, BMAX + 1))
    for t in range(1, T_RUNS + 1):
        for b in range(1, BMAX + 1):
            F[t, b] = Phi((log(b) - log(median_by_t[t])) / sigma)
    return F


def solve_dp(p: np.ndarray, V: np.ndarray, F: np.ndarray):
    J = np.zeros((T_RUNS + 2, BMAX + 1))
    bid = np.zeros((T_RUNS + 2, BMAX + 1), dtype=int)
    for t in range(T_RUNS, 0, -1):
        Jn = J[t + 1]
        for B in range(BMAX + 1):
            b = np.arange(B + 1)
            vals = F[t, b] * (V[t] + Jn[B - b]) + (1 - F[t, b]) * Jn[B]
            k = int(np.argmax(vals))
            bid[t, B] = k
            J[t, B] = p[t] * vals[k] + (1 - p[t]) * Jn[B]
    return J, bid


def eval_policy(p: np.ndarray, V: np.ndarray, F: np.ndarray, table: np.ndarray) -> np.ndarray:
    J = np.zeros((T_RUNS + 2, BMAX + 1))
    for t in range(T_RUNS, 0, -1):
        for B in range(BMAX + 1):
            b = int(min(max(table[t, B], 0), B))
            val = F[t, b] * (V[t] + J[t + 1, B - b]) + (1 - F[t, b]) * J[t + 1, B]
            J[t, B] = p[t] * val + (1 - p[t]) * J[t + 1, B]
    return J


def max_bid_exact(Jnext: np.ndarray, value: float, B: int = BMAX) -> int:
    """Largest b with continuation cost J_{t+1}(B) - J_{t+1}(B-b) <= value."""
    best = 0
    for b in range(0, B + 1):
        if Jnext[B] - Jnext[B - b] <= value + 1e-12:
            best = b
    return best


def section_d():
    hdr("D. Backward-induction FAAB budget model (F13-F16)")
    scenarios = {}
    base_med = np.full(T_RUNS + 2, PRICE_MEDIAN)
    dep_med = np.array([PRICE_MEDIAN * (1 - 0.5 * (t - 1) / (T_RUNS - 1)) for t in range(T_RUNS + 2)])
    specs = {
        "A base (median 20, p 0.5->0.14, k=2)": (arrival_profile(), value_profile(2.0), base_med),
        "B rivals deplete (median 20 -> 10)": (arrival_profile(), value_profile(2.0), dep_med),
        "C flat arrivals p=0.35": (arrival_profile(0.35), value_profile(2.0), base_med),
        "D no playoff premium k=1": (arrival_profile(), value_profile(1.0), base_med),
        "E playoff premium k=3": (arrival_profile(), value_profile(3.0), base_med),
    }
    lam_weeks = (1, 5, 8, 10, 13, 14)
    bid_weeks = (1, 4, 7, 8, 10, 12, 14)
    for name, (p, V, med) in specs.items():
        F = price_cdf(med)
        J, bid = solve_dp(p, V, F)
        lam = {t: J[t, 100] - J[t, 90] for t in lam_weeks}
        lam_next = {t: J[t + 1, 100] - J[t + 1, 90] for t in lam_weeks}
        bids = {t: int(bid[t, 100]) for t in bid_weeks}
        scenarios[name] = dict(p=p, V=V, F=F, J=J, bid=bid, med=med, lam=lam, lam_next=lam_next, bids=bids)
        print(f"\n  scenario {name}: J_1(100) = {J[1, 100]:.4f} wins")
        print("    lambda_t = J_t(100)-J_t(90):      " +
              " ".join(f"wk{t}={lam[t]:.4f}" for t in lam_weeks))
        print("    lambda'_t = J_t+1(100)-J_t+1(90): " +
              " ".join(f"wk{t}={lam_next[t]:.4f}" for t in lam_weeks))
        print("    bid*_t(B=100):                    " +
              " ".join(f"wk{t}={bids[t]}" for t in bid_weeks))
        print("    V_t:                              " +
              " ".join(f"wk{t}={V[t]:.3f}" for t in (1, 4, 8, 10, 14)))
    A = scenarios["A base (median 20, p 0.5->0.14, k=2)"]
    J, bid, p, V, F = A["J"], A["bid"], A["p"], A["V"], A["F"]
    # --- checks against the claimed numbers (model A) ---
    check("D V_1 = 0.540, V_14 = 0.127 (4.3x)",
          abs(V[1] - 0.540) < 2e-3 and abs(V[14] - 0.127) < 2e-3 and abs(V[1] / V[14] - 4.25) < 0.1,
          f"{V[1]:.3f}, {V[14]:.3f}, ratio {V[1] / V[14]:.2f}")
    claimed_lam = {1: 0.074, 5: 0.034, 8: 0.018, 10: 0.005, 13: 0.0006, 14: 0.0}
    for t, c in claimed_lam.items():
        check(f"D lambda_wk{t} = {c}", abs(A["lam"][t] - c) <= 0.0031 + 0.1 * c, f"{A['lam'][t]:.4f}")
    claimed_bids = {1: 30, 4: 32, 7: 39, 8: 41, 10: 49, 12: 60, 14: 100}
    for t, c in claimed_bids.items():
        check(f"D bid*_wk{t}(100) = {c}", abs(A["bids"][t] - c) <= 3, f"{A['bids'][t]}")
    check("D J_1(100) = 1.30", abs(J[1, 100] - 1.30) < 0.03, f"{J[1, 100]:.4f}")
    path = [int(bid[t, 100]) for t in range(1, T_RUNS + 1)]
    print("  full bid*_t(100) path: " + " ".join(str(x) for x in path))
    check("D bid*_t(100) is non-decreasing in t", all(path[i] <= path[i + 1] for i in range(len(path) - 1)))
    check("D bid rises while V_t falls (wk1 -> wk12)", path[11] > path[0] and V[12] < V[1])
    # Robustness ranges claimed: wk1 29-32, wk10 43-56 across A-E
    w1 = [s["bids"][1] for s in scenarios.values()]
    w10 = [s["bids"][10] for s in scenarios.values()]
    j1 = [float(s["J"][1, 100]) for s in scenarios.values()]
    print(f"  across A-E: wk1 bids {w1}, wk10 bids {w10}, J_1(100) {[round(x, 3) for x in j1]}")
    check("D wk-1 bids across A-E within 29-32", min(w1) >= 28 and max(w1) <= 33, f"{w1}")
    check("D wk-10 bids across A-E within 43-56", min(w10) >= 42 and max(w10) <= 57, f"{w10}")
    check("D J_1(100) across A-E within 1.08-1.45", min(j1) >= 1.05 and max(j1) <= 1.48,
          f"{[round(x, 3) for x in j1]}")
    check("D ordering holds in every scenario (wk10 bid > wk1 bid)",
          all(s["bids"][10] > s["bids"][1] for s in scenarios.values()))
    # --- bid as % of REMAINING budget at partial budgets ---
    print("\n  bid*_t(B) as % of remaining budget B (model A):")
    print("      t   B=100  B=60  B=30  B=15")
    for t in (1, 4, 7, 8, 10, 12, 14):
        print(f"     {t:2d}   " + "  ".join(f"{100 * bid[t, B] / B:5.0f}" for B in (100, 60, 30, 15)))
    # --- concavity of J in B ---
    concave_viol, worst_kink = 0, 0.0
    for t in range(1, T_RUNS + 1):
        dd = np.diff(np.diff(J[t]))
        concave_viol += int(np.sum(dd > 1e-9))
        worst_kink = max(worst_kink, float(dd.max()))
    print(f"  concavity: {concave_viol} of {T_RUNS * (BMAX - 1)} increments have a convex kink "
          f"(largest {worst_kink:.5f} wins) -- J is only approximately concave, so a constant"
          f" lambda_t is an approximation")
    # --- break-even bids: linear lambda vs exact concave cost ---
    print("\n  break-even max bid for an add worth V wins (model A):")
    print("      linear = V / (lambda_t/10)  |  exact = max b: J_t+1(100)-J_t+1(100-b) <= V")
    print("      V     wk1 lin/exact   wk5 lin/exact   wk8 lin/exact   wk10 lin/exact   wk12 lin/exact")
    be = {}
    for Vadd in (0.05, 0.10, 0.20, 0.30):
        cells = []
        for t in (1, 5, 8, 10, 12):
            lam = J[t, 100] - J[t, 90]
            lin = Vadd / (lam / 10) if lam > 0 else float("inf")
            ex = max_bid_exact(J[t + 1], Vadd)
            be[(Vadd, t)] = (lin, ex)
            cells.append(f"{min(lin, 999):5.1f}/{ex:3d}")
        print(f"     {Vadd:.2f}   " + "   ".join(cells))
    check("D V=0.10 wk1 linear ~13.5", abs(be[(0.10, 1)][0] - 13.5) < 1.5, f"{be[(0.10, 1)][0]:.1f}")
    check("D V=0.10 wk8 linear ~55", abs(be[(0.10, 8)][0] - 55) < 6, f"{be[(0.10, 8)][0]:.1f}")
    print(f"  exact concave-cost max bids for V=0.10: wk1 ${be[(0.10, 1)][1]}, wk8 ${be[(0.10, 8)][1]} "
          f"(linear said {be[(0.10, 1)][0]:.0f} / {be[(0.10, 8)][0]:.0f})")
    # --- fair $ per (point/week) table ---
    print("\n  fair $ (of 100) per +1 pt/wk and per +3 pt/wk, S=0.85, even matchups, model A prices:")
    print("      weeks_left  t   value(1pt, reg only)  value(1pt, +playoff prem)   fair$ 1pt lin/exact   fair$ 3pt lin/exact")
    fair = {}
    for W in (13, 10, 7, 4, 2):
        t = T_RUNS + 1 - W
        v1_reg = S_START * L32 * W
        v1_po = S_START * L32 * (W + P_PLAYOFF * K_PLAYOFF * PLAYOFF_WEEKS)
        lam = J[t, 100] - J[t, 90]
        lin1 = v1_po / (lam / 10) if lam > 0 else float("inf")
        ex1 = max_bid_exact(J[t + 1], v1_po)
        lin3 = 3 * v1_po / (lam / 10) if lam > 0 else float("inf")
        ex3 = max_bid_exact(J[t + 1], 3 * v1_po)
        fair[W] = (v1_reg, v1_po, lin1, ex1, lin3, ex3)
        print(f"        {W:2d}      {t:2d}       {v1_reg:.4f}               {v1_po:.4f}"
              f"                 {min(lin1, 999):5.1f}/{ex1:3d}            {min(lin3, 999):5.1f}/{ex3:3d}")
    return scenarios, fair


# --------------------------------------------------------------------------
# E. Pacing policies
# --------------------------------------------------------------------------
def mc_policy(rng: np.random.Generator, p, V, med, table, N=200_000):
    B = np.full(N, BMAX, dtype=int)
    total = np.zeros(N)
    for t in range(1, T_RUNS + 1):
        arr = rng.random(N) < p[t]
        price = med[t] * np.exp(PRICE_SIGMA * rng.standard_normal(N))
        b = table[t, B]
        win = arr & (b >= 1) & (b > price)
        total += win * V[t]
        B = B - win * b
    return total.mean(), total.std() / sqrt(N)


def section_e(rng: np.random.Generator, scenarios: dict) -> None:
    hdr("E. Pacing: DP-optimal vs spend-early vs smooth vs hoard (model A, exact evaluation)")
    A = scenarios["A base (median 20, p 0.5->0.14, k=2)"]
    p, V, F, bid, med = A["p"], A["V"], A["F"], A["bid"], A["med"]
    Bs = np.arange(BMAX + 1)
    tables = {}
    tables["DP-optimal"] = bid.copy()
    for cut in (7, 9, 11):
        tb = bid.copy()
        tb[1:cut, :] = 0
        tables[f"hoard until wk{cut} then optimal"] = tb
    tb = np.zeros_like(bid)
    for t in range(1, T_RUNS + 1):
        tb[t] = Bs
    tables["all-in on first arrival"] = tb
    tb = bid.copy()
    for t in range(1, 5):
        tb[t] = np.minimum(Bs, 50)
    tables["spend-early: $50 wks1-4 then optimal"] = tb
    for r in (0.10, 0.20, 0.30, 0.40, 0.50, 0.60):
        tb = np.zeros_like(bid)
        for t in range(1, T_RUNS + 1):
            tb[t] = np.rint(r * Bs).astype(int)
        tables[f"fixed {int(r * 100)}% of remaining"] = tb
    tb = np.zeros_like(bid)
    for t in range(1, T_RUNS + 1):
        tb[t] = np.minimum(Bs, 20)
    tables["fixed $20 (median price)"] = tb
    tb = np.zeros_like(bid)
    for t in range(1, T_RUNS + 1):
        tb[t] = np.minimum(Bs, bid[t, 100])
    tables["full-budget schedule 30->100% (cap at remaining)"] = tb
    tb = np.zeros_like(bid)
    for t in range(1, T_RUNS + 1):
        exp_arrivals = p[t:T_RUNS + 1].sum()
        tb[t] = np.rint(Bs / exp_arrivals).astype(int)
    tables["smooth: remaining / expected remaining arrivals"] = tb
    tb = np.zeros_like(bid)
    for t in range(1, T_RUNS + 1):
        tb[t] = np.rint(Bs * min(1.0, 0.30 + 0.05 * (t - 1))).astype(int)
    tables["ramp 30%->95% of remaining"] = tb
    results = {}
    for name, tb in tables.items():
        results[name] = eval_policy(p, V, F, tb)[1, 100]
    opt = results["DP-optimal"]
    print(f"  {'policy':52s} J_1(100)   % of optimal")
    for name, val in sorted(results.items(), key=lambda kv: -kv[1]):
        print(f"  {name:52s} {val:.4f}     {100 * val / opt:5.1f}%")
    check("E DP-optimal beats every other policy", all(v <= opt + 1e-9 for v in results.values()))
    check("E hoarding until wk9 loses to DP-optimal",
          results["hoard until wk9 then optimal"] < 0.9 * opt,
          f"{results['hoard until wk9 then optimal']:.3f} vs {opt:.3f}")
    check("E all-in on first arrival loses to DP-optimal",
          results["all-in on first arrival"] < 0.9 * opt,
          f"{results['all-in on first arrival']:.3f} vs {opt:.3f}")
    fixed = {r: results[f"fixed {r}% of remaining"] for r in (10, 20, 30, 40, 50, 60)}
    best_r = max(fixed, key=fixed.get)
    print(f"  best fixed-fraction policy: {best_r}% of remaining ({100 * fixed[best_r] / opt:.1f}% of optimal)")
    # Monte Carlo cross-check of the exact evaluation
    for name in ("DP-optimal", "fixed 30% of remaining", "hoard until wk9 then optimal"):
        m, se = mc_policy(rng, p, V, med, tables[name])
        check(f"E MC cross-check {name}: exact {results[name]:.4f} vs MC {m:.4f} +/- {se:.4f}",
              abs(m - results[name]) < 4 * se + 1e-3)


# --------------------------------------------------------------------------
# F. Sourced-number arithmetic
# --------------------------------------------------------------------------
def section_f() -> None:
    hdr("F. Sourced-number arithmetic")
    check("F FFPC median $14/$1000 = 1.4%; wk1 $11 = 1.1%", 14 / 1000 == 0.014 and 11 / 1000 == 0.011)
    pos = {"QB": 21, "RB": 21, "WR": 29, "TE": 20, "K": 3, "DST": 10}
    claimed = {"QB": 2.1, "RB": 2.1, "WR": 2.9, "TE": 2.0, "K": 0.3, "DST": 1.0}
    check("F positional medians -> % of budget", all(abs(v / 10 - claimed[k]) < 1e-9 for k, v in pos.items()))
    check("F max $766/$1000 = 76.6%", abs(766 / 1000 * 100 - 76.6) < 1e-9)
    check("F Wilson $300/$1000 = 30%", 300 / 1000 == 0.30)
    check("F handcuffs 8/71 = 11.3%", abs(8 / 71 - 0.113) < 5e-4, f"{8 / 71:.4f}")
    check("F ambiguous backfields 13/20 = 65%; lead outscored 15/20 = 75%", 13 / 20 == 0.65 and 15 / 20 == 0.75)
    check("F 36/105 = 34%", abs(36 / 105 - 0.34) < 4e-3, f"{36 / 105:.4f}")
    check("F speculative handcuff 0.15*0.34 = 0.05, 0.30*0.34 = 0.10",
          abs(0.15 * 0.34 - 0.051) < 1e-3 and abs(0.30 * 0.34 - 0.102) < 1e-3)
    print("  NOTE F12 double-counts the 0.34: if the post-injury value already carries the 34%"
          " weekly top-24 hit rate (it does -- that rate is measured AFTER the starter is out),"
          " the pre/post ratio is P(absence) alone = 15-30%, not 5-10%. 5-10% only holds if"
          " 'post-injury value' means a back already known to be startable (X at S=1).")
    q25 = PRICE_MEDIAN * exp(-0.6745 * PRICE_SIGMA)
    q75 = PRICE_MEDIAN * exp(0.6745 * PRICE_SIGMA)
    q90 = PRICE_MEDIAN * exp(1.2816 * PRICE_SIGMA)
    check("F lognormal(20, 0.6) IQR 13-30, p90 43",
          abs(q25 - 13.3) < 0.3 and abs(q75 - 30.0) < 0.3 and abs(q90 - 43.1) < 0.4,
          f"{q25:.1f}-{q75:.1f}, p90 {q90:.1f}")
    frac_dead = Phi((log(19) - log(20)) / 0.6) - Phi((log(10) - log(20)) / 0.6)
    frac_splash = Phi((log(60) - log(20)) / 0.6) - Phi((log(30) - log(20)) / 0.6)
    print(f"  under the model price law, P(price in dead zone 10-19) = {frac_dead:.2f}, "
          f"P(price in splash 30-60) = {frac_splash:.2f}")
    print("  NOTE the modelled price law puts ~40% of impact-add prices INSIDE the 10-19% 'dead"
          " zone' that the policy says to avoid -- the price prior and the bid bands were not"
          " built from the same population (FFPC dead zone is about bids on ALL adds, most of"
          " which are not impact adds).")


# --------------------------------------------------------------------------
def main() -> int:
    t0 = time.time()
    rng = np.random.default_rng(SEED)
    section_a(rng)
    section_b(rng)
    section_c()
    scenarios, _fair = section_d()
    section_e(rng, scenarios)
    section_f()
    hdr("SUMMARY")
    n_fail = sum(1 for _, ok, _ in CHECKS if not ok)
    print(f"  {len(CHECKS) - n_fail} PASS / {n_fail} FAIL   ({time.time() - t0:.1f}s)")
    for name, ok, detail in CHECKS:
        if not ok:
            print(f"    FAIL: {name} -- {detail}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
