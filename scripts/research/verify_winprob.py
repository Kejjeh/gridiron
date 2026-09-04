"""verify_winprob.py — adversarial numeric verification of the winprob research.

Offline, deterministic (every RNG seeded), numpy only (no scipy: the normal
CDF is math.erf, gamma quantiles come from rank-mapped sorted draws, i.e.
Iman-Conover / empirical-ppf inside a Gaussian copula).

What it recomputes and checks, section by section:

  A. Slot arithmetic — per-slot sigma from the F7 means/CVs, gamma shape k
     and scale theta (shifted gamma for DST), gamma skew 2*CV, lognormal
     alternative, Wilson-Hilferty medians, lineup SD (independent / stacked /
     whole recipe), K+DST share of variance AND of the third cumulant, the
     sum-of-cumulants lineup skew, and the head-to-head margin SD including
     the cross-game QB-vs-opposing-DST term (sign check).
  B. Closed-form derivatives dP/dmu_A and dP/dsigma_A against central finite
     differences of Phi(d/s) (target relative error < 1e-4), plus a
     cross-check against gridiron.winprob when the package is importable.
  C. Gaussian-copula Monte Carlo (18 slots = own 9 + opponent 9) with the F6
     correlation recipe under four scenarios; realised Pearson rho vs copula
     rho; Phi vs MC P(win) across mu_A - mu_B in {-40..40}; margin skew.
  D. Variance-flip table — Delta-P(win) for underdog SD multipliers
     1.1/1.2/1.3 at deficits 0..30, closed form vs MC with common random
     numbers; median drop of a gamma sum at fixed mean; breakeven deficit.
  E. Sample-size claims — binomial SE arithmetic, empirical batch SE, and
     the CRN variance reduction for Delta-P.
  F. Source-table arithmetic — ESPN 2023-25 top-12 K and DST tier means,
     Underdog CVs, Stathole CV, K+DST quadrature.

Run from the repo root:  python scripts/research/verify_winprob.py
(~1 minute, ~600 MB peak RAM at N_SIM = 1e6).
"""
from __future__ import annotations

import sys
import time
from math import erf, exp, log, pi, sqrt
from pathlib import Path

import numpy as np

SEED = 20260904
N_SIM = 1_000_000

# --------------------------------------------------------------------------
# Constants under test (all from the research findings, inline on purpose)
# --------------------------------------------------------------------------
SLOTS = ["QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "K", "DST"]
MEANS = np.array([20.0, 16.5, 13.0, 17.0, 14.0, 11.0, 12.5, 9.0, 7.5])
CVS = np.array([0.42, 0.52, 0.55, 0.55, 0.58, 0.65, 0.58, 0.55, 0.90])
DST_SHIFT = -5.0  # F3: DST modelled as shift + Gamma

# F6 engine recipe. Own-team pairs (apply only when the two slots are the
# same NFL team) and cross-lineup same-game pairs (A slot, B slot).
OWN_PAIRS = {
    ("QB", "WR1"): 0.35, ("QB", "WR2"): 0.25, ("QB", "TE"): 0.30,
    ("QB", "RB1"): 0.07, ("WR1", "WR2"): 0.00,
    ("RB1", "WR1"): -0.07, ("RB1", "WR2"): -0.07,
    ("RB2", "WR1"): -0.07, ("RB2", "WR2"): -0.07,
    ("RB1", "DST"): 0.10, ("K", "DST"): 0.15, ("QB", "K"): 0.10,
}
CROSS_PAIRS = {
    ("QB", "DST"): -0.30, ("QB", "QB"): 0.30, ("QB", "WR1"): 0.20,
    ("WR1", "WR1"): 0.15,
    ("WR1", "DST"): -0.15, ("WR2", "DST"): -0.15, ("TE", "DST"): -0.15,
    ("FLEX", "DST"): -0.15,
}

D_LIST = [-40, -30, -20, -15, -10, -5, 0, 5, 10, 15, 20, 30, 40]
F_LIST = [0.10, 0.20, 0.30]
DEFICITS = [0, 2, 4, 5, 6, 8, 10, 15, 20, 30]

# ESPN consistency ratings 2023-25 as transcribed by the researcher
# (PPG, CR = SD/PPG). Only the arithmetic is verifiable offline.
ESPN_DST_TOP12 = [
    ("Texans", 8.4, .671), ("Broncos", 8.0, .819), ("Bills", 7.8, .906),
    ("Vikings", 7.7, .972), ("Seahawks", 7.6, 1.058), ("Browns", 7.5, .826),
    ("Steelers", 7.4, .855), ("Ravens", 7.0, .892), ("Eagles", 6.8, .824),
    ("Chiefs", 6.4, .841), ("Chargers", 6.3, 1.109), ("Packers", 6.3, .923),
]
ESPN_K_TOP12 = [
    ("Aubrey", 10.8, .499), ("Fairbairn", 10.7, .592), ("Dicker", 9.6, .512),
    ("Myers", 9.5, .513), ("Reichard", 9.4, .558), ("Boswell", 9.1, .594),
    ("Mevis", 9.0, .626), ("Bates", 8.9, .401), ("Sanders", 8.9, .656),
    ("McLaughlin", 8.8, .543), ("Gonzalez", 8.6, .562), ("Borregales", 8.5, .485),
]
# Underdog half-PPR 2015-21 (mean, SD) per tier as transcribed.
UNDERDOG = {"QB": (20.6, 7.4), "RB": (14.1, 7.6), "WR": (12.4, 7.2), "TE": (10.1, 6.4)}


# --------------------------------------------------------------------------
# Normal helpers (pure math)
# --------------------------------------------------------------------------
def Phi(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def phi(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def margin_sd(sa, sb, rho):
    return sqrt(sa * sa + sb * sb - 2.0 * rho * sa * sb)


def pwin(mua, sa, mub, sb, rho=0.0):
    return Phi((mua - mub) / margin_sd(sa, sb, rho))


def dp_dmu(mua, sa, mub, sb, rho=0.0):
    s = margin_sd(sa, sb, rho)
    return phi((mua - mub) / s) / s


def dp_dsigma_a(mua, sa, mub, sb, rho=0.0):
    s = margin_sd(sa, sb, rho)
    d = mua - mub
    return -phi(d / s) * d * (sa - rho * sb) / s ** 3


def skew(x: np.ndarray) -> float:
    c = x - x.mean()
    return float((c ** 3).mean() / (c ** 2).mean() ** 1.5)


def hdr(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# --------------------------------------------------------------------------
# Marginals
# --------------------------------------------------------------------------
def gamma_params(mean: float, cv: float, shift: float = 0.0):
    """Gamma(k, theta) on (x - shift) with the requested mean and CV of x."""
    sd = mean * cv
    m = mean - shift
    k = (m / sd) ** 2
    theta = sd * sd / m
    return k, theta, shift


def slot_params(sd_mult: float = 1.0):
    out = []
    for name, mu, cv in zip(SLOTS, MEANS, CVS):
        shift = DST_SHIFT if name == "DST" else 0.0
        out.append(gamma_params(mu, cv * sd_mult, shift))
    return out


# --------------------------------------------------------------------------
# Correlation matrix for 18 slots (A: 0..8, B: 9..17)
# --------------------------------------------------------------------------
IDX = {s: i for i, s in enumerate(SLOTS)}


def build_R(own_a=(), own_b=(), cross=()):
    """own_*: iterable of (slotA, slotB, rho) within one lineup;
    cross: iterable of (slot_in_A, slot_in_B, rho)."""
    n = 2 * len(SLOTS)
    R = np.eye(n)

    def put(i, j, v):
        R[i, j] = v
        R[j, i] = v

    for s1, s2, v in own_a:
        put(IDX[s1], IDX[s2], v)
    for s1, s2, v in own_b:
        put(9 + IDX[s1], 9 + IDX[s2], v)
    for s1, s2, v in cross:
        put(IDX[s1], 9 + IDX[s2], v)
    return R


def nearest_psd(R: np.ndarray, eps: float = 1e-8):
    w, V = np.linalg.eigh(R)
    if w.min() >= 0:
        return R, float(w.min()), False
    w = np.clip(w, eps, None)
    R2 = (V * w) @ V.T
    d = np.sqrt(np.diag(R2))
    R2 = R2 / np.outer(d, d)
    return R2, float(w.min()), True


def analytic_lineup_stats(R: np.ndarray, sig: np.ndarray):
    """sigma_A, sigma_B, cov(A,B), s_margin from sigma^T R sigma."""
    C = R * np.outer(sig, sig)
    var_a = C[:9, :9].sum()
    var_b = C[9:, 9:].sum()
    cov_ab = C[:9, 9:].sum()
    s = sqrt(var_a + var_b - 2 * cov_ab)
    return sqrt(var_a), sqrt(var_b), cov_ab, s


# --------------------------------------------------------------------------
# Copula sampler (rank-mapped sorted gamma draws => exact marginals)
# --------------------------------------------------------------------------
def copula_orders(rng, R, n):
    L = np.linalg.cholesky(R)
    G = rng.standard_normal((n, R.shape[0]))
    Z = G @ L.T
    del G
    orders = [np.argsort(Z[:, j], kind="stable") for j in range(R.shape[0])]
    del Z
    return orders


def draw_slot(rng, order, k, theta, shift, n):
    x = np.empty(n)
    x[order] = np.sort(rng.gamma(k, theta, n)) + shift
    return x


def sample_lineups(rng, R, n, params_a, params_b, orders=None, want_pairs=()):
    """Return (A_total, B_total, orders, pair_corrs)."""
    if orders is None:
        orders = copula_orders(rng, R, n)
    cols = {}
    A = np.zeros(n)
    B = np.zeros(n)
    for j in range(9):
        x = draw_slot(rng, orders[j], *params_a[j], n)
        A += x
        cols[("A", SLOTS[j])] = x
    for j in range(9):
        x = draw_slot(rng, orders[9 + j], *params_b[j], n)
        B += x
        cols[("B", SLOTS[j])] = x
    pair_corrs = {}
    for (l1, s1), (l2, s2) in want_pairs:
        pair_corrs[((l1, s1), (l2, s2))] = float(
            np.corrcoef(cols[(l1, s1)], cols[(l2, s2)])[0, 1])
    del cols
    return A, B, orders, pair_corrs


# ==========================================================================
def section_A():
    hdr("A. Slot arithmetic, lineup SD, margin SD, skew")
    sig = MEANS * CVS
    params = slot_params()
    print(f"{'slot':5s} {'mean':>6s} {'CV':>5s} {'sigma':>7s} {'k':>7s} "
          f"{'theta':>7s} {'shift':>6s} {'skew=2/sqrt(k)':>15s} {'WH median/mean':>15s} "
          f"{'kappa3':>8s}")
    kappa3 = []
    for name, mu, cv, s, (k, th, sh) in zip(SLOTS, MEANS, CVS, sig, params):
        k3 = 2.0 * k * th ** 3
        kappa3.append(k3)
        wh = (1 - 1 / (9 * k)) ** 3
        med_ratio = (sh + (mu - sh) * wh) / mu
        print(f"{name:5s} {mu:6.1f} {cv:5.2f} {s:7.3f} {k:7.3f} {th:7.3f} {sh:6.1f} "
              f"{2 / sqrt(k):15.3f} {med_ratio:15.3f} {k3:8.1f}")
    kappa3 = np.array(kappa3)
    var_ind = float((sig ** 2).sum())
    sd_ind = sqrt(var_ind)
    mu_l = float(MEANS.sum())
    print(f"\nlineup mean = {mu_l:.1f}; sum sigma^2 = {var_ind:.2f}; "
          f"SD_ind = {sd_ind:.3f}; CV = {sd_ind / mu_l:.4f}")
    print(f"sqrt(2)*SD_ind = {sqrt(2) * sd_ind:.3f}  (claimed sd_diff 32.3)")
    print(f"Stathole anchor: 22.6/122 = CV {22.6 / 122:.4f}; sqrt(2)*22.6 = {sqrt(2) * 22.6:.2f}")
    kd_var = sig[IDX['K']] ** 2 + sig[IDX['DST']] ** 2
    print(f"K+DST quadrature SD = {sqrt(kd_var):.3f} (claimed ~8.4); "
          f"share of variance = {kd_var / var_ind:.3%} (claimed 13%)")
    print(f"7-slot (no K/DST) SD = {sqrt(var_ind - kd_var):.2f} PPR "
          f"(Fantasy Points half-PPR anchor 17.4)")
    sk = kappa3.sum() / var_ind ** 1.5
    print(f"sum-of-cumulants lineup skew = {sk:.4f} (claimed 0.374); "
          f"k_eff = 4/skew^2 = {4 / sk ** 2:.2f} (claimed 28.6)")
    kd_k3 = kappa3[IDX['K']] + kappa3[IDX['DST']]
    print(f"K+DST share of third cumulant = {kd_k3 / kappa3.sum():.3%}  "
          f"<-- 'most of the fat tail' claim")
    order = np.argsort(-kappa3)
    print("kappa3 ranking: " + ", ".join(f"{SLOTS[i]} {kappa3[i]:.0f}" for i in order))

    # Stacks
    sq, sw1, ste = sig[IDX['QB']], sig[IDX['WR1']], sig[IDX['TE']]
    for rho in (0.31, 0.35, 0.53):
        v = var_ind + 2 * rho * sq * sw1
        print(f"QB+WR1 stack rho={rho:.2f}: +{2 * rho * sq * sw1:.1f} var -> SD {sqrt(v):.2f}")
    v3 = var_ind + 2 * 0.35 * sq * sw1 + 2 * 0.30 * sq * ste
    print(f"QB+WR1(.35)+TE(.30): SD {sqrt(v3):.2f} (claimed ~24.8)")

    # Whole-recipe (every own-team pair applied inside one lineup) — upper bound
    own_all = [(a, b, v) for (a, b), v in OWN_PAIRS.items()]
    R_all = build_R(own_a=own_all)
    R_all_psd, mineig, fixed = nearest_psd(R_all)
    sa, sb, cab, s = analytic_lineup_stats(R_all_psd, np.concatenate([sig, sig]))
    print(f"all OWN pairs inside A: min eig {mineig:+.4f} (fixed={fixed}); SD_A = {sa:.2f}")

    # Margin SD with cross-game terms — sign check on F8
    cov_qb_dst = -0.30 * sq * sig[IDX['DST']]
    s_wrong = sqrt(2 * var_ind - 2 * abs(cov_qb_dst))
    s_right = sqrt(2 * var_ind - 2 * cov_qb_dst)
    print(f"\nF8 cross term: Cov(A,B) from QB-oppDST(-0.30) = {cov_qb_dst:+.2f}")
    print(f"  s = sqrt(sA^2+sB^2-2Cov) = {s_right:.2f}   [researcher wrote 31.8 = "
          f"sqrt(1044-34) = {s_wrong:.2f}: SIGN ERROR — negative rho RAISES margin SD]")
    cross_all = [(a, b, v) for (a, b), v in CROSS_PAIRS.items()]
    R_c = build_R(cross=cross_all)
    R_c, mineig, fixed = nearest_psd(R_c)
    sa, sb, cab, s = analytic_lineup_stats(R_c, np.concatenate([sig, sig]))
    print(f"  every CROSS pair (A slots vs B's DST/QB/WR1 in one game): Cov(A,B) = {cab:+.2f}, "
          f"s = {s:.2f} (vs {sqrt(2) * sd_ind:.2f} independent; delta {s - sqrt(2) * sd_ind:+.2f})")

    # Lognormal alternative skews
    print("\nlognormal alternative (sigma_ln = sqrt(ln(1+CV^2)), skew=(e^s2+2)sqrt(e^s2-1)):")
    for name, cv in (("QB", 0.42), ("WR", 0.55), ("TE", 0.65)):
        s2 = log(1 + cv * cv)
        print(f"  {name}: sigma_ln {sqrt(s2):.3f}, skew {(exp(s2) + 2) * sqrt(exp(s2) - 1):.2f}")

    # Team-range sensitivity for leverage
    print("\nleverage phi(0)/s: " + ", ".join(
        f"s={s:.1f}->{100 * phi(0) / s:.2f}pp/pt" for s in (29.0, 32.3, 34.0)))
    s0 = sqrt(2) * sd_ind
    for d in (0, 20, 32):
        print(f"  |d|={d}: {100 * phi(d / s0) / s0:.3f} pp/pt")
    return sig, params, sd_ind, sk


# ==========================================================================
def section_B(sd_ind):
    hdr("B. Derivatives vs central finite differences")
    sa = sb = sd_ind
    h = 1e-4
    worst_mu = worst_sig = 0.0
    print(f"{'d':>6s} {'rho':>5s} {'dP/dmu':>12s} {'FD':>12s} {'relerr':>10s} "
          f"{'dP/dsig':>12s} {'FD':>12s} {'relerr':>10s}")
    for rho in (0.0, 0.3, -0.3):
        for d in (-20.0, -10.0, -5.0, -0.5, 0.5, 5.0, 10.0, 20.0):
            mua, mub = 120.0 + d, 120.0
            a_mu = dp_dmu(mua, sa, mub, sb, rho)
            f_mu = (pwin(mua + h, sa, mub, sb, rho) - pwin(mua - h, sa, mub, sb, rho)) / (2 * h)
            a_sg = dp_dsigma_a(mua, sa, mub, sb, rho)
            f_sg = (pwin(mua, sa + h, mub, sb, rho) - pwin(mua, sa - h, mub, sb, rho)) / (2 * h)
            r_mu = abs(a_mu - f_mu) / abs(f_mu)
            r_sg = abs(a_sg - f_sg) / abs(f_sg)
            worst_mu = max(worst_mu, r_mu)
            worst_sig = max(worst_sig, r_sg)
            print(f"{d:6.1f} {rho:5.2f} {a_mu:12.6e} {f_mu:12.6e} {r_mu:10.2e} "
                  f"{a_sg:12.6e} {f_sg:12.6e} {r_sg:10.2e}")
    print(f"max relative error: dP/dmu {worst_mu:.2e}, dP/dsigma {worst_sig:.2e} "
          f"(target < 1e-4) -> {'PASS' if max(worst_mu, worst_sig) < 1e-4 else 'FAIL'}")
    fd0 = (pwin(120, sa + h, 120, sb) - pwin(120, sa - h, 120, sb)) / (2 * h)
    print(f"d=0: analytic dP/dsigma = {dp_dsigma_a(120, sa, 120, sb):.3e}, FD = {fd0:.3e} (both ~0)")

    # Claimed spot values (F10)
    s0 = sqrt(2) * sd_ind
    for d in (-10.0, -20.0):
        v = dp_dsigma_a(120 + d, sa, 120, sb)
        s1 = margin_sd(1.1 * sa, sb, 0.0)
        exact = Phi(d / s1) - Phi(d / s0)
        print(f"d={d:+.0f}: dP/dsigma = {100 * v:.3f} pp per SD-pt; x10% SD ({0.1 * sa:.2f} pts) = "
              f"{100 * v * 0.1 * sa:.2f} pp; exact Phi diff = {100 * exact:.2f} pp")
    print("rho scaling factor (1 - rho*sB/sA) at rho=0.3:", round(1 - 0.3 * sb / sa, 3))

    # Cross-check against gridiron.winprob if importable (src on path via __file__)
    try:
        src = Path(__file__).resolve().parents[2] / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from gridiron import winprob as wp  # noqa: WPS433
        args = (110.0, 22.86, 120.0, 22.86, 0.3)
        diffs = (abs(wp.matchup_win_prob(*args) - pwin(*args)),
                 abs(wp.leverage_per_point(*args) - dp_dmu(*args)),
                 abs(wp.dpwin_dsigma_a(*args) - dp_dsigma_a(*args)))
        print(f"gridiron.winprob cross-check max abs diff = {max(diffs):.2e}")
    except Exception as e:  # pragma: no cover
        print(f"gridiron.winprob not importable ({e.__class__.__name__}); skipped")


# ==========================================================================
def run_scenario(rng, name, R, sig, params_a, params_b, want_pairs, n):
    R_psd, mineig, fixed = nearest_psd(R)
    sa_an, sb_an, cov_an, s_an = analytic_lineup_stats(R_psd, np.concatenate([sig, sig]))
    t0 = time.time()
    A, B, orders, pcorr = sample_lineups(rng, R_psd, n, params_a, params_b,
                                         want_pairs=want_pairs)
    D = A - B
    sa_mc, sb_mc, s_mc = A.std(), B.std(), D.std()
    rho_ab = float(np.corrcoef(A, B)[0, 1])
    print(f"\n--- scenario {name} (min eig {mineig:+.4f}, PSD-fixed={fixed}, "
          f"{time.time() - t0:.1f}s) ---")
    print(f"analytic: SD_A {sa_an:.2f} SD_B {sb_an:.2f} Cov(A,B) {cov_an:+.2f} s {s_an:.2f}")
    print(f"MC:       SD_A {sa_mc:.2f} SD_B {sb_mc:.2f} rho(A,B) {rho_ab:+.4f} s {s_mc:.2f}; "
          f"mean A {A.mean():.2f} B {B.mean():.2f} D {D.mean():+.3f}")
    print(f"MC skew: A {skew(A):.3f}  B {skew(B):.3f}  D {skew(D):.3f}; "
          f"excess kurt D {((D - D.mean()) ** 4).mean() / D.var() ** 2 - 3:.3f}")
    for (p, v) in pcorr.items():
        (l1, s1), (l2, s2) = p
        i = IDX[s1] + (0 if l1 == "A" else 9)
        j = IDX[s2] + (0 if l2 == "A" else 9)
        print(f"  pair {l1}.{s1}-{l2}.{s2}: copula rho {R_psd[i, j]:+.3f} -> Pearson {v:+.4f}")
    se = sqrt(0.25 / n)
    print(f"\n{'d':>4s} {'P_mc':>8s} {'Phi(s_an)':>10s} {'err_pp':>8s} {'Phi(s_mc)':>10s} "
          f"{'err_pp':>8s} {'Phi(s_ind)':>10s} {'err_pp':>8s}   (MC SE {100 * se:.3f} pp)")
    s_ind = sqrt(2) * sqrt((sig ** 2).sum())
    max_an = max_mc = max_ind = 0.0
    rows = []
    d_mean = D.mean()
    for d in D_LIST:
        p_mc = float((D + d > 0).mean())
        p_an = Phi((d + d_mean) / s_an)  # d_mean is ~0 sampling noise; include for fairness
        p_mc_s = Phi((d + d_mean) / s_mc)
        p_ind = Phi((d + d_mean) / s_ind)
        e_an, e_mc, e_ind = 100 * (p_mc - p_an), 100 * (p_mc - p_mc_s), 100 * (p_mc - p_ind)
        max_an, max_mc, max_ind = max(max_an, abs(e_an)), max(max_mc, abs(e_mc)), max(max_ind, abs(e_ind))
        rows.append((d, p_mc, e_an, e_mc, e_ind))
        print(f"{d:4d} {p_mc:8.4f} {p_an:10.4f} {e_an:+8.3f} {p_mc_s:10.4f} {e_mc:+8.3f} "
              f"{p_ind:10.4f} {e_ind:+8.3f}")
    print(f"max |MC - Phi| pp: analytic s {max_an:.3f}; MC-realised s {max_mc:.3f}; "
          f"independent s {max_ind:.3f}")
    return A, B, orders, rows


def section_C(rng, sig, params, n):
    hdr("C. Gaussian-copula MC: Phi vs MC across d, realised correlations")
    stack_a = [("QB", "WR1", 0.35), ("QB", "TE", 0.30)]
    stack_b = [("QB", "WR1", 0.35)]
    cross = [("QB", "DST", -0.30), ("QB", "QB", 0.30), ("QB", "WR1", 0.20),
             ("WR1", "QB", 0.20), ("WR1", "WR1", 0.15), ("WR1", "DST", -0.15),
             ("TE", "DST", -0.15)]
    own_all = [(a, b, v) for (a, b), v in OWN_PAIRS.items()]
    cross_all = [(a, b, v) for (a, b), v in CROSS_PAIRS.items()]
    scen = {
        "indep": (build_R(), [(("A", "QB"), ("A", "WR1")), (("A", "QB"), ("B", "DST"))]),
        "stackA": (build_R(own_a=stack_a),
                   [(("A", "QB"), ("A", "WR1")), (("A", "QB"), ("A", "TE")),
                    (("A", "WR1"), ("A", "TE"))]),
        "crossgame": (build_R(own_a=stack_a, own_b=stack_b, cross=cross),
                      [(("A", "QB"), ("B", "DST")), (("A", "QB"), ("B", "QB")),
                       (("A", "QB"), ("B", "WR1")), (("A", "WR1"), ("B", "WR1"))]),
        "recipe_max": (build_R(own_a=own_all, own_b=own_all, cross=cross_all),
                       [(("A", "QB"), ("A", "WR1")), (("A", "K"), ("A", "DST")),
                        (("A", "QB"), ("B", "DST"))]),
    }
    keep = {}
    for name, (R, pairs) in scen.items():
        A, B, orders, rows = run_scenario(rng, name, R, sig, params, params, pairs, n)
        if name == "indep":
            keep = dict(A=A, B=B, orders=orders)
        else:
            del A, B, orders

    # Scaled-mean variant (CV held fixed, so SD_A scales with mu_A) on indep draws
    A, B = keep["A"], keep["B"]
    mu_a = MEANS.sum()
    sd_i = sqrt((sig ** 2).sum())
    print("\n--- indep, A's slot means scaled by (mu_A+d)/mu_A with CVs fixed ---")
    print(f"{'d':>4s} {'P_mc':>8s} {'Phi':>8s} {'err_pp':>8s}")
    worst = 0.0
    for d in D_LIST:
        c = (mu_a + d) / mu_a
        p_mc = float((c * A - B > 0).mean())
        p_an = Phi(d / sqrt((c * sd_i) ** 2 + sd_i ** 2))
        worst = max(worst, abs(100 * (p_mc - p_an)))
        print(f"{d:4d} {p_mc:8.4f} {p_an:8.4f} {100 * (p_mc - p_an):+8.3f}")
    print(f"max |MC - Phi| = {worst:.3f} pp")
    return keep


# ==========================================================================
def section_D(rng, keep, sig, sd_ind, mu_l, n, lineup_skew):
    hdr("D. Variance flip: underdog scales SD by (1+f) at fixed mean")
    A0, B, orders = keep["A"], keep["B"], keep["orders"]
    s0 = sqrt(2) * sd_ind
    k_eff = 4 / lineup_skew ** 2
    med0 = float(np.median(A0))
    print(f"baseline: SD_A {A0.std():.2f}, median A {med0:.2f} (mean {A0.mean():.2f}), "
          f"s0 {s0:.2f}")
    A_f = {}
    for f in F_LIST:
        params_f = slot_params(1.0 + f)
        Af = np.zeros(n)
        for j in range(9):
            Af += draw_slot(rng, orders[j], *params_f[j], n)
        A_f[f] = Af
        dmed = float(np.median(Af)) - med0
        pred = -mu_l * ((1 + f) ** 2 - 1) / (3 * k_eff)
        print(f"f={f:.1f}: SD_A {Af.std():.2f} (target {(1 + f) * sd_ind:.2f}), mean {Af.mean():.2f}, "
              f"median shift {dmed:+.3f} pts (k_eff prediction {pred:+.3f}), skew {skew(Af):.3f}")

    print(f"\nDelta-P(win) in pp, rows = deficit, cols = f (analytic | MC-CRN | corrected-approx)")
    print(f"{'def':>4s} " + " ".join(f"{'f=' + str(f):>26s}" for f in F_LIST))
    D0 = A0 - B
    table = {}
    for d in DEFICITS:
        cells = []
        p0 = float((D0 - d > 0).mean())
        for f in F_LIST:
            s1 = sqrt(((1 + f) * sd_ind) ** 2 + sd_ind ** 2)
            an = 100 * (Phi(-d / s1) - Phi(-d / s0))
            mc = 100 * (float((A_f[f] - B - d > 0).mean()) - p0)
            dmed = float(np.median(A_f[f])) - med0
            corr = an + 100 * phi(d / s0) / s0 * dmed
            table[(d, f)] = (an, mc, corr)
            cells.append(f"{an:+7.2f} |{mc:+7.2f} |{corr:+7.2f}")
        print(f"{d:4d} " + " ".join(f"{c:>26s}" for c in cells))

    # Breakeven for f = 0.2 on a fine grid
    fine = np.arange(0.0, 12.01, 0.5)
    vals = []
    for d in fine:
        p0 = float((D0 - d > 0).mean())
        p1 = float((A_f[0.2] - B - d > 0).mean())
        vals.append(100 * (p1 - p0))
    vals = np.array(vals)
    be = None
    for i in range(len(fine) - 1):
        if vals[i] < 0 <= vals[i + 1]:
            be = fine[i] + 0.5 * (-vals[i]) / (vals[i + 1] - vals[i])
            break
    print("\nf=0.2 fine grid (deficit: dP pp): " +
          ", ".join(f"{d:.1f}:{v:+.2f}" for d, v in zip(fine, vals)))
    print(f"breakeven deficit for +20% SD ~ {be if be is None else round(float(be), 2)} pts "
          f"(claimed 5-6); as fraction of s0: {None if be is None else round(float(be) / s0, 3)}")
    for f in (0.1, 0.3):
        vals_f = []
        for d in fine:
            p0 = float((D0 - d > 0).mean())
            p1 = float((A_f[f] - B - d > 0).mean())
            vals_f.append(100 * (p1 - p0))
        vals_f = np.array(vals_f)
        be_f = None
        for i in range(len(fine) - 1):
            if vals_f[i] < 0 <= vals_f[i + 1]:
                be_f = fine[i] + 0.5 * (-vals_f[i]) / (vals_f[i + 1] - vals_f[i])
                break
        print(f"breakeven deficit for f={f:.1f}: {None if be_f is None else round(float(be_f), 2)} pts")

    # Error-ratio check for F13 (variance-change error vs analytic gain, |d| <= 10)
    print("\nF13 ratio |analytic - MC| / analytic at deficits 5,10:")
    for d in (5, 10):
        for f in (0.2, 0.3):
            an, mc, _ = table[(d, f)]
            print(f"  d={d} f={f}: analytic {an:+.2f}, MC {mc:+.2f}, error {an - mc:.2f} pp, "
                  f"ratio {(an - mc) / an:.2f}")
    return A_f, D0


# ==========================================================================
def section_E(keep, A_f, D0, n):
    hdr("E. Sample size / SE claims")
    for z, lab in ((1.0, "1 SE"), (1.96, "95%")):
        N = (z * 0.5 / 0.002) ** 2
        print(f"N for +/-0.2 pp at p=0.5 ({lab}): {N:,.0f}")
    print(f"SE at N=62,500: {100 * sqrt(0.25 / 62500):.3f} pp; N=240,100: "
          f"{100 * sqrt(0.25 / 240100):.3f} pp x1.96 = {196 * sqrt(0.25 / 240100):.3f} pp")
    print(f"independent-draw SE of a difference at N=2e5: {100 * sqrt(2 * 0.25 / 2e5):.3f} pp")
    # Empirical batch SE at N = 62,500
    nb = 62_500
    nbatch = n // nb
    B = keep["B"]
    A1 = A_f[0.2]
    d = 10.0
    p0 = np.array([(D0[i * nb:(i + 1) * nb] - d > 0).mean() for i in range(nbatch)])
    p1 = np.array([(A1[i * nb:(i + 1) * nb] - B[i * nb:(i + 1) * nb] - d > 0).mean()
                   for i in range(nbatch)])
    diff_crn = p1 - p0
    # pseudo-independent: pair batch i of p1 with batch (i+1) of p0
    diff_ind = p1 - np.roll(p0, 1)
    print(f"empirical batch SE (N={nb:,}, {nbatch} batches, d=-10): P(win) {100 * p0.std(ddof=1):.3f} pp "
          f"(binomial {100 * sqrt(p0.mean() * (1 - p0.mean()) / nb):.3f})")
    print(f"Delta-P SE: CRN {100 * diff_crn.std(ddof=1):.4f} pp vs independent draws "
          f"{100 * diff_ind.std(ddof=1):.4f} pp (binomial {100 * sqrt(2 * p0.mean() * (1 - p0.mean()) / nb):.4f}); "
          f"ratio {diff_ind.std(ddof=1) / diff_crn.std(ddof=1):.1f}x")
    print(f"scaled to N=2e5: CRN {100 * diff_crn.std(ddof=1) * sqrt(nb / 2e5):.4f} pp, "
          f"independent {100 * diff_ind.std(ddof=1) * sqrt(nb / 2e5):.4f} pp")
    # Exact-ish CRN SE from the flip fraction: the paired difference of two
    # indicators is +/-1 on discordant sims and 0 otherwise, so
    # Var(diff) = p_flip - (E diff)^2 and SE(Delta-P) = sqrt(Var/N).
    for d in (5.0, 10.0, 20.0):
        w0 = D0 - d > 0
        w1 = A1 - B - d > 0
        p_flip = float((w0 != w1).mean())
        e_diff = float(w1.mean() - w0.mean())
        var = p_flip - e_diff ** 2
        print(f"d=-{d:.0f}, f=0.2: flip fraction {p_flip:.4%}; SE(Delta-P) at N=2e5 = "
              f"{100 * sqrt(var / 2e5):.4f} pp (CRN) vs {100 * sqrt(2 * 0.25 / 2e5):.4f} pp "
              f"(independent) -> {sqrt(2 * 0.25) / sqrt(var):.1f}x")


# ==========================================================================
def section_F():
    hdr("F. Source-table arithmetic (ESPN 2023-25 tiers, Underdog, Stathole)")
    for lab, rows in (("DST", ESPN_DST_TOP12), ("K", ESPN_K_TOP12)):
        ppg = np.array([r[1] for r in rows])
        cr = np.array([r[2] for r in rows])
        sd = ppg * cr
        print(f"top-12 {lab}: n={len(rows)} mean PPG {ppg.mean():.3f} (range {ppg.min()}-{ppg.max()}), "
              f"mean CR {cr.mean():.4f} (range {cr.min():.3f}-{cr.max():.3f}), "
              f"mean SD {sd.mean():.3f}, pooled-ish CV mean(SD)/mean(PPG) {sd.mean() / ppg.mean():.3f}")
        k = 1 / cr.mean() ** 2
        print(f"   gamma k from mean CR = {k:.2f}; skew 2*CV = {2 * cr.mean():.2f}")
    m, s = 12.3, 6.5
    print(f"DST shifted gamma on (x+5): mean {m}, SD {s} -> k {(m / s) ** 2:.2f}, "
          f"theta {s * s / m:.2f}, skew {2 / (m / s):.2f}")
    for pos, (mu, sd) in UNDERDOG.items():
        print(f"Underdog {pos}: SD/mean = {sd}/{mu} = {sd / mu:.3f}; k = {(mu / sd) ** 2:.2f}")
    for pos, cv in (("QB", 0.42), ("RB", 0.52), ("RB", 0.55), ("WR", 0.55), ("WR", 0.58),
                    ("TE", 0.65), ("K", 0.55)):
        print(f"k(1/CV^2) {pos} CV {cv}: {1 / cv ** 2:.2f}; skew {2 * cv:.2f}")
    print(f"DFS->season haircut 0.31/0.53 = {0.31 / 0.53:.3f}; -0.46*0.58 = {-0.46 * 0.58:.3f}; "
          f"0.58*0.55 = {0.58 * 0.55:.3f}; 0.37*0.55 = {0.37 * 0.55:.3f}")


# ==========================================================================
def main() -> int:
    t0 = time.time()
    np.set_printoptions(precision=4, suppress=True)
    print(f"verify_winprob.py  seed={SEED}  N_SIM={N_SIM:,}  numpy {np.__version__}")
    rng = np.random.default_rng(SEED)
    sig, params, sd_ind, lineup_skew = section_A()
    section_B(sd_ind)
    keep = section_C(rng, sig, params, N_SIM)
    A_f, D0 = section_D(rng, keep, sig, sd_ind, float(MEANS.sum()), N_SIM, lineup_skew)
    section_E(keep, A_f, D0, N_SIM)
    section_F()
    print(f"\ndone in {time.time() - t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
