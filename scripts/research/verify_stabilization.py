"""verify_stabilization.py — adversarial numeric verification of the stabilization research.

Offline, deterministic (every RNG seeded), numpy + csv only (no scipy, no
pandas; the normal CDF is math.erf). Reads the local nflverse cache
(data/research/cache/nflverse/stats_player_week_{2023,2024,2025}.csv) and
recomputes every data-derived number in the research findings with an
independent implementation, then stress-tests the model behind them in
simulation.

What it verifies, section by section:

  A. Closed-form identities — Beta-binomial posterior mean / variance
     (F1), n0 = n(1-r)/r and the first-k product rule (F2), method-of-moments
     Beta fit and the 65.7 arithmetic (F3), the arithmetic inside sourced
     claims (137/151, 50/66, 25/41, retained-excess fractions, unit
     conversions, the F12 n0_prev conversion, the Intentional Rounding
     route-to-target cross-check).
  B. Beta-binomial league simulation — for three fitted priors (WR1 target
     share, RB1 carry share, pooled WR/TE target share): MSE of the raw
     MLE vs the posterior mean vs prior-only at n = n0/4 .. 4n0 and at
     week-1/4/8 volumes; split-half reliability r(n) against n/(n+n0)
     (crossing 0.5 at n = n0); a misspecified-prior case.
  C. Method-of-moments recovery — simulate N=95 player-seasons from the
     fitted WR1 / RB1 priors with realistic game counts, apply the
     researcher's MoM estimator, and report the sampling distribution of
     the recovered n0_pop (bias and width).
  D. Data recomputation (nflverse 2023-2025) — team opportunities per game,
     role priors (plus an EX-ANTE variant: last season's role -> this
     season's share), odd/even split-half r and n0 with bootstrap CIs (plus
     harmonic-mean n and a pooled-population n0_pop cross-check), first-k
     vs rest-of-season r with drift-inclusive n0, out-of-sample n0 grid
     with a bootstrap of the arg-min, year-over-year TD-rate regression,
     weekly / season PPR R^2 on raw counts (and a gridiron.scoring
     cross-check of nflverse fantasy_points_ppr), the per-game log-variance
     decomposition, the prior-season blend OLS, and team changers.
  E. Shrinkage-weight table at week 1 / 4 / 8 / 17 for every recommended n0
     and the games-to-stabilization implied by each.
  F. Variance-decomposition simulation — points = opportunity x efficiency
     with the fitted log-variance parameters; measured opportunity share
     and R^2 of opportunity alone.
  G. Summary PASS/FAIL table.

Run from the repo root:  python scripts/research/verify_stabilization.py
(~30 s).
"""
from __future__ import annotations

import csv
import math
import sys
import time
from collections import defaultdict
from math import erf, sqrt
from pathlib import Path

import numpy as np

SEED = 20260904
HERE = Path(__file__).resolve()
REPO = HERE.parents[2]
CACHE = REPO / "data" / "research" / "cache" / "nflverse"
SEASONS = (2023, 2024, 2025)
SKILL = {"WR", "TE", "RB", "QB", "FB"}
T0 = time.time()

RESULTS: list[tuple[str, float, float, bool]] = []


def phi(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def check(label: str, claimed: float, got: float, tol: float, rel: bool = False) -> bool:
    """Record a claimed-vs-recomputed comparison. tol is absolute unless rel."""
    diff = abs(got - claimed)
    ok = diff <= (tol * abs(claimed) if rel else tol)
    RESULTS.append((label, claimed, got, ok))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}: claimed {claimed:g}, got {got:.4g}")
    return ok


def banner(title: str) -> None:
    print("\n" + "=" * 96 + f"\n{title}\n" + "=" * 96)


def pearson(a, b) -> float:
    return float(np.corrcoef(np.asarray(a, float), np.asarray(b, float))[0, 1])


# --------------------------------------------------------------------------
# A. Closed-form identities
# --------------------------------------------------------------------------
def section_a(rng: np.random.Generator) -> None:
    banner("A. CLOSED-FORM IDENTITIES (F1, F2, F3) AND SOURCED-CLAIM ARITHMETIC")
    # F1: posterior mean and variance identities, random parameters.
    worst_mean = worst_var = 0.0
    for _ in range(2000):
        a, b = rng.uniform(0.5, 60), rng.uniform(0.5, 120)
        n = int(rng.integers(1, 600))
        x = int(rng.integers(0, n + 1))
        n0, m = a + b, a / (a + b)
        w = n / (n + n0)
        post = (a + x) / (a + b + n)
        alt = w * (x / n) + (1 - w) * m
        worst_mean = max(worst_mean, abs(post - alt))
        a2, b2 = a + x, b + n - x
        var_exact = a2 * b2 / ((a2 + b2) ** 2 * (a2 + b2 + 1))
        m2 = a2 / (a2 + b2)
        var_claim = m2 * (1 - m2) / (n + n0 + 1)
        worst_var = max(worst_var, abs(var_exact - var_claim))
    print(f"  F1 posterior-mean identity: max |(a+x)/(n+a+b) - [w x/n + (1-w) m]| = {worst_mean:.2e}")
    print(f"  F1 posterior-variance identity: max |exact - m'(1-m')/(n+n0+1)| = {worst_var:.2e}")
    check("F1 posterior mean identity (max abs err)", 0.0, worst_mean, 1e-12)
    check("F1 posterior variance identity (max abs err)", 0.0, worst_var, 1e-12)
    print("  F1 equal weight at n = n0: w(n0) =", 1.0 * 19 / (19 + 19))

    # F2: n0 = n(1-r)/r inverts r = n/(n+n0); first-k product rule.
    n0 = 37.0
    for n in (10.0, 37.0, 222.0):
        r = n / (n + n0)
        assert abs(n * (1 - r) / r - n0) < 1e-9
    print("  F2 n0 = n(1-r)/r inverts r = n/(n+n0) exactly (checked at n = 10, 37, 222).")
    ne, nl = 31.0, 443.0
    r2 = (ne / (ne + 28)) * (nl / (nl + 28))
    print(f"  F2 worked example: n0=28, n_e=31, n_l=443 -> r^2 = {r2:.3f}, r = {sqrt(r2):.3f} (claimed 0.493 / 0.702)")
    check("F2 first-k product rule example r", 0.702, sqrt(r2), 0.003)
    check("F5 n0 = 222*(1-0.922)/0.922", 18.8, 222 * (1 - 0.922) / 0.922, 0.1)
    check("F5 n0 = 198*(1-0.952)/0.952", 10.0, 198 * (1 - 0.952) / 0.952, 0.1)
    check("F8 n0 = 36*(1-0.221)/0.221", 127, 36 * (1 - 0.221) / 0.221, 1.0)
    check("F8 n0 = 82*(1-0.309)/0.309", 183, 82 * (1 - 0.309) / 0.309, 1.0)

    # F3: method of moments.
    m, rmse = 0.17, 0.046
    check("F3 n0_prior = 0.17*0.83/0.046^2 - 1", 65.7, m * (1 - m) / rmse**2 - 1, 0.2)
    a, b = 0.249 * 106, 0.751 * 106
    print(f"  F4 WR1 Beta({a:.1f}, {b:.1f}) (claimed Beta(26.4, 79.6)); RB1 Beta({0.535*24.6:.1f}, {0.465*24.6:.1f}) (claimed 13.2, 11.4)")
    check("F4 WR1 alpha", 26.4, a, 0.1)
    check("F4 RB1 alpha", 13.2, 0.535 * 24.6, 0.1)
    # Beta-mean/var consistency of the quoted role priors: sd_true = sqrt(m(1-m)/(n0+1))
    for role, mm, sd, n0p in [("WR1", .249, .042, 106), ("WR2", .179, .045, 73), ("WR3", .123, .036, 81),
                              ("TE1", .166, .044, 71), ("TE2", .073, .027, 93), ("RB1 tgt", .103, .038, 62),
                              ("RB2 tgt", .062, .036, 43), ("RB1 car", .535, .099, 25), ("RB2 car", .264, .104, 17)]:
        implied = sqrt(mm * (1 - mm) / (n0p + 1))
        check(f"F4 {role} sd_true vs sqrt(m(1-m)/(n0+1))", sd, implied, 0.004)

    # Sourced arithmetic.
    check("F10 ESPN xTD 137/151", 0.907, 137 / 151, 0.001)
    check("F10 ESPN FORP 50/66", 0.758, 50 / 66, 0.001)
    check("F10 ESPN FORP 25/41", 0.612, 25 / 41, 0.002)
    print(f"  ESPN 2025: 20/21 = {20/21:.3f}")
    check("F9 retained excess (0.0581-0.0527)/(0.1041-0.0527)", 0.105, (0.0581 - 0.0527) / (0.1041 - 0.0527), 0.003)
    check("F9 RB retained (0.0446-0.0332)/(0.0679-0.0332)", 0.33, (0.0446 - 0.0332) / (0.0679 - 0.0332), 0.01)
    check("F9 share retained (0.2469-0.1181)/(0.2760-0.1181)", 0.82, (0.2469 - 0.1181) / (0.2760 - 0.1181), 0.01)
    # Unit conversions.
    g_t, g_c = 31.3, 26.9
    print(f"  units: 19 team targets = {19/g_t:.2f} games; 90 = {90/g_t:.2f}; 80 = {80/g_t:.2f}; 120 = {120/g_t:.2f}; "
          f"10 team carries = {10/g_c:.2f} games; 28 = {28/g_c:.2f}; 45 = {45/g_c:.2f}")
    check("F5 19 team targets in games", 0.6, 19 / g_t, 0.05)
    check("F7 90 team targets in games", 3.0, 90 / g_t, 0.15)
    check("F12 80 team targets in games (2.5-3)", 2.75, 80 / g_t, 0.25)
    check("F12 28 team carries in games", 1.0, 28 / g_c, 0.1)
    # F12 n0_prev = k g (1-w)/w
    for k, w, claimed in [(1, .20, 124), (3, .53, 83)]:
        got = k * g_t * (1 - w) / w
        check(f"F12 n0_prev at k={k} (WR/TE)", claimed, got, 2.0)
    check("F12 n0_prev at k=1 (RB)", 28, 1 * g_c * (1 - .49) / .49, 1.0)
    # F15 decay arithmetic.
    check("F15 45*0.7^3", 15.0, 45 * 0.7**3, 0.5)
    # Intentional Rounding cross-check: YPRR 350 routes, YPT ~3x -> ~1050 routes * 0.2 tgt/route
    check("F8b IR cross-check 350*3*0.2 targets", 200, 350 * 3 * 0.2, 15)
    # F11 YoY-horizon n0 = 500(1-r)/r for r in 0.70..0.84
    lo, hi = 500 * (1 - .84) / .84, 500 * (1 - .70) / .70
    print(f"  F11 YoY n0 at n=500: r=0.84 -> {lo:.0f}, r=0.70 -> {hi:.0f} (claimed 95-215)")
    check("F11 YoY n0 at r=0.84", 95, lo, 2)
    check("F11 YoY n0 at r=0.70", 215, hi, 2)
    # Efficiency-vs-usage ratio in raw counts and in games (unit caveat).
    wr1_tpg = 0.249 * g_t
    print(f"  F8 'efficiency 5-20x slower': raw counts 127/19 = {127/19:.1f}x, 232/19 = {232/19:.1f}x, 182/10 = {182/10:.1f}x; "
          f"in GAMES for a WR1 ({wr1_tpg:.1f} tgt/g): yds/tgt {127/wr1_tpg:.1f} g vs usage {19/g_t:.2f} g = {127/wr1_tpg/(19/g_t):.0f}x")


# --------------------------------------------------------------------------
# B. Beta-binomial league simulation
# --------------------------------------------------------------------------
def section_b(rng: np.random.Generator) -> None:
    banner("B. BETA-BINOMIAL LEAGUE SIMULATION: MSE (MLE vs posterior mean) and split-half r(n)")
    P = 60_000
    priors = [("WR1 target share Beta(26.4,79.6)", 26.4, 79.6, 31.3),
              ("RB1 carry share Beta(13.2,11.4)", 13.2, 11.4, 26.9),
              ("pooled WR/TE share n0=19, m=0.17 Beta(3.2,15.8)", 0.17 * 19, 0.83 * 19, 31.3)]
    for label, a, b, per_game in priors:
        n0, m = a + b, a / (a + b)
        p = rng.beta(a, b, P)
        print(f"\n  {label}: n0={n0:.1f}, m={m:.3f}, sd_true={sqrt(m*(1-m)/(n0+1)):.4f}")
        print(f"  {'n':>6} {'games':>6} {'MSE mle':>10} {'MSE post':>10} {'MSE prior':>10} {'post/mle':>9} {'r split':>8} {'n/(n+n0)':>9}")
        grid = sorted({max(1, int(round(n0 / 4))), max(1, int(round(n0 / 2))), int(round(n0)), int(round(2 * n0)),
                       int(round(4 * n0)), 31, 125, 250})
        for n in grid:
            x1 = rng.binomial(n, p)
            x2 = rng.binomial(n, p)
            mle = x1 / n
            post = (x1 + a) / (n + n0)
            mse_mle = float(np.mean((mle - p) ** 2))
            mse_post = float(np.mean((post - p) ** 2))
            mse_prior = float(np.mean((m - p) ** 2))
            r = pearson(x1 / n, x2 / n)
            print(f"  {n:6d} {n/per_game:6.2f} {mse_mle:10.6f} {mse_post:10.6f} {mse_prior:10.6f} {mse_post/mse_mle:9.3f} {r:8.3f} {n/(n+n0):9.3f}")
            if abs(n - round(n0)) < 0.5:
                check(f"B r(n0) crosses 0.5 [{label[:12]}]", 0.5, r, 0.01)
                check(f"B post beats MLE at n=n0 [{label[:12]}] (post/mle < 1)", 0.5, mse_post / mse_mle, 0.02)
            assert mse_post < mse_mle, "posterior mean must beat MLE under the correct prior"
            assert abs(r - n / (n + n0)) < 0.012, f"split-half r deviates from n/(n+n0) at n={n}"
        # theoretical: MSE_post/MSE_mle = n/(n+n0) at every n (posterior variance vs sampling variance)
        print("  (theory: MSE_post/MSE_mle = n/(n+n0) exactly, r = n/(n+n0) exactly under the model)")

    # Misspecified prior: WR1 players (true Beta(26.4,79.6)) shrunk toward the WR2 mean 0.179 with n0=106.
    a, b = 26.4, 79.6
    n0, m_wrong = 106.0, 0.179
    p = rng.beta(a, b, P)
    print("\n  Misspecified prior: true WR1 players shrunk toward m=0.179 (WR2 mean) with n0=106:")
    print(f"  {'n':>6} {'MSE mle':>10} {'MSE wrong':>10} {'MSE right':>10} {'wrong/mle':>10}")
    for n in (31, 63, 125, 250, 532):
        x = rng.binomial(n, p)
        mle = x / n
        wrong = (x + n0 * m_wrong) / (n + n0)
        right = (x + a) / (n + n0)
        r_mle, r_w, r_r = (float(np.mean((e - p) ** 2)) for e in (mle, wrong, right))
        print(f"  {n:6d} {r_mle:10.6f} {r_w:10.6f} {r_r:10.6f} {r_w/r_mle:10.3f}")
    print("  -> a wrong prior mean (one role tier off) makes the posterior WORSE than the raw share once n >~ n0;"
          " the n0 recommendation only transfers with the matching role/player prior.")


# --------------------------------------------------------------------------
# C. Method-of-moments recovery
# --------------------------------------------------------------------------
def mom(shares, ns):
    shares = np.asarray(shares, float)
    ns = np.asarray(ns, float)
    m = shares.mean()
    v_obs = shares.var(ddof=1)
    noise = float(np.mean(shares * (1 - shares) / ns))
    v_true = max(v_obs - noise, 1e-6)
    n0 = m * (1 - m) / v_true - 1
    return m, sqrt(v_obs), sqrt(v_true), n0


def section_c(rng: np.random.Generator) -> None:
    banner("C. METHOD-OF-MOMENTS RECOVERY FROM SIMULATED PLAYER-SEASONS (N=95, games 8-17)")
    for label, a, b, per_game, sd_pg in [("WR1 target share", 26.4, 79.6, 31.3, 7.5),
                                         ("RB1 carry share", 13.2, 11.4, 26.9, 7.5),
                                         ("RB2 carry share", 0.264 * 17, 0.736 * 17, 26.9, 7.5)]:
        n0_true, m_true = a + b, a / (a + b)
        reps = 3000
        est = np.empty((reps, 4))
        est_nonoise = np.empty(reps)
        for i in range(reps):
            N = 95
            games = rng.integers(8, 18, N)
            n = np.array([max(20.0, rng.normal(per_game, sd_pg, g).clip(5).sum()) for g in games])
            p = rng.beta(a, b, N)
            x = rng.binomial(n.astype(int), p)
            sh = x / n.astype(int)
            est[i] = mom(sh, n)
            v_obs = sh.var(ddof=1)
            est_nonoise[i] = m_true * (1 - m_true) / v_obs - 1
        q = np.percentile(est[:, 3], [5, 25, 50, 75, 95])
        print(f"  {label}: true n0={n0_true:.1f}, m={m_true:.3f}, sd_true={sqrt(m_true*(1-m_true)/(n0_true+1)):.4f}")
        print(f"    recovered m: mean {est[:,0].mean():.4f};  sd_true: mean {est[:,2].mean():.4f};  "
              f"n0_hat: mean {est[:,3].mean():.1f} median {q[2]:.1f}  5/25/75/95% = {q[0]:.0f}/{q[1]:.0f}/{q[3]:.0f}/{q[4]:.0f}")
        print(f"    without the noise subtraction n0_hat median = {np.median(est_nonoise):.1f} (bias from binomial noise)")
        check(f"C MoM recovers n0 [{label}] (median within 15%)", n0_true, q[2], 0.15, rel=True)
        check(f"C MoM recovers mean [{label}]", m_true, est[:, 0].mean(), 0.003)
        rel_width = (q[4] - q[0]) / n0_true
        print(f"    -> 90% interval width is {rel_width*100:.0f}% of n0: with N~95 the fitted n0_pop carries roughly +/-{(q[4]-q[0])/2/n0_true*100:.0f}% sampling error")


# --------------------------------------------------------------------------
# D. Data recomputation
# --------------------------------------------------------------------------
def fnum(s: str) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return 0.0


COLS = ["targets", "receptions", "receiving_yards", "receiving_tds", "receiving_yards_after_catch",
        "carries", "rushing_yards", "rushing_tds", "fantasy_points_ppr", "fantasy_points",
        "passing_yards", "passing_tds", "passing_interceptions", "sack_fumbles_lost", "rushing_fumbles_lost",
        "receiving_fumbles_lost", "passing_2pt_conversions", "rushing_2pt_conversions",
        "receiving_2pt_conversions", "special_teams_tds", "fumbles_lost_total"]
SHORT = {"targets": "tgt", "receptions": "rec", "receiving_yards": "ryds", "receiving_tds": "rtd",
         "receiving_yards_after_catch": "yac", "carries": "car", "rushing_yards": "rush", "rushing_tds": "rushtd",
         "fantasy_points_ppr": "ppr", "fantasy_points": "std"}


def load_rows():
    rows = []
    for s in SEASONS:
        path = CACHE / f"stats_player_week_{s}.csv"
        with open(path, newline="", encoding="utf-8") as fh:
            for d in csv.DictReader(fh):
                if d["season_type"] != "REG" or d["position"] not in SKILL:
                    continue
                r = dict(pid=d["player_id"], pos=d["position"], season=int(d["season"]), week=int(d["week"]),
                         team=d["team"])
                for c in COLS:
                    r[SHORT.get(c, c)] = fnum(d.get(c))
                rows.append(r)
    return rows


def build(rows):
    teamwk = defaultdict(lambda: [0.0, 0.0])
    for r in rows:
        k = (r["season"], r["week"], r["team"])
        teamwk[k][0] += r["tgt"]
        teamwk[k][1] += r["car"]
    ps = defaultdict(list)
    for r in rows:
        k = (r["season"], r["week"], r["team"])
        r["team_tgt"], r["team_car"] = teamwk[k]
        ps[(r["pid"], r["season"])].append(r)
    SP = {}
    for (pid, season), ws in ps.items():
        ws.sort(key=lambda w: w["week"])
        cnt = defaultdict(int)
        for w in ws:
            cnt[w["team"]] += 1
        team = max(cnt, key=cnt.get)
        ws_t = [w for w in ws if w["team"] == team]
        SP[(pid, season)] = dict(pos=ws[0]["pos"], team=team, ws=ws_t, g=len(ws_t), nteams=len(cnt),
                                 tgt=sum(w["tgt"] for w in ws_t), team_tgt=sum(w["team_tgt"] for w in ws_t),
                                 car=sum(w["car"] for w in ws_t), team_car=sum(w["team_car"] for w in ws_t))
    return teamwk, SP


def agg(ws, key):
    return sum(w[key] for w in ws)


def solve_n0(r, ne, nl):
    lo, hi = 0.0, 5000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if (ne / (ne + mid)) * (nl / (nl + mid)) > r * r:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def roles_from(SP, season_filter=None):
    byteam = defaultdict(list)
    for (pid, season), v in SP.items():
        if season_filter is not None and season != season_filter:
            continue
        byteam[(season, v["team"], v["pos"])].append((pid, v))
    roles = defaultdict(list)
    for (season, team, pos), lst in byteam.items():
        if pos in ("WR", "TE"):
            lst.sort(key=lambda kv: -kv[1]["tgt"])
            for i, (pid, v) in enumerate(lst[:3 if pos == "WR" else 2]):
                if v["g"] >= 8:
                    roles[f"{pos}{i+1}"].append((pid, season, team, v))
        elif pos == "RB":
            lst.sort(key=lambda kv: -kv[1]["car"])
            for i, (pid, v) in enumerate(lst[:2]):
                if v["g"] >= 8:
                    roles[f"RB{i+1}"].append((pid, season, team, v))
    return roles


def section_d(rows, teamwk, SP, rng: np.random.Generator) -> None:
    banner("D. DATA RECOMPUTATION FROM nflverse 2023-2025 (independent implementation)")
    # ---- D1 team opportunities per game ----
    tt = np.array([v[0] for v in teamwk.values()])
    tc = np.array([v[1] for v in teamwk.values()])
    print(f"  D1 team-games {len(tt)}; targets/game {tt.mean():.2f} (sd {tt.std():.2f}); carries/game {tc.mean():.2f} (sd {tc.std():.2f})")
    check("D1 team-games", 1632, len(tt), 0)
    check("D1 team targets/game", 31.3, tt.mean(), 0.1)
    check("D1 team carries/game", 26.9, tc.mean(), 0.1)
    check("D1 team targets sd", 7.5, tt.std(), 0.15)

    # ---- D2 role priors ----
    print("\n  D2 role priors (ex post: role = season-end rank within team; >= 8 games)")
    roles = roles_from(SP)
    claimed = {("WR1", "tgt"): (0.249, 0.042, 106, 95), ("WR2", "tgt"): (0.179, 0.045, 73, 94), ("WR3", "tgt"): (0.123, 0.036, 81, 90),
               ("TE1", "tgt"): (0.166, 0.044, 71, 96), ("TE2", "tgt"): (0.073, 0.027, 93, 87), ("RB1", "tgt"): (0.103, 0.038, 62, 96),
               ("RB2", "tgt"): (0.062, 0.036, 43, 94), ("RB1", "car"): (0.535, 0.099, 25, 96), ("RB2", "car"): (0.264, 0.104, 17, 94)}
    print(f"  {'role':5} {'met':4} {'N':>4} {'mean':>6} {'sd_obs':>7} {'sd_true':>8} {'n0_pop':>7} {'p10':>6} {'p50':>6} {'p90':>6}")
    for (role, met), (cm, csd, cn0, cN) in claimed.items():
        lst = roles[role]
        den = "team_tgt" if met == "tgt" else "team_car"
        sh = [v[met] / v[den] for (_, _, _, v) in lst]
        ns = [v[den] for (_, _, _, v) in lst]
        m, so, st, n0 = mom(sh, ns)
        q = np.percentile(sh, [10, 50, 90])
        print(f"  {role:5} {met:4} {len(lst):4d} {m:6.3f} {so:7.3f} {st:8.3f} {n0:7.1f} {q[0]:6.3f} {q[1]:6.3f} {q[2]:6.3f}")
        check(f"D2 {role} {met} N", cN, len(lst), 0)
        check(f"D2 {role} {met} mean", cm, m, 0.002)
        check(f"D2 {role} {met} sd_true", csd, st, 0.002)
        check(f"D2 {role} {met} n0_pop", cn0, n0, 0.06, rel=True)
        if role == "WR1" and met == "tgt":
            check("D2 WR1 p10", 0.185, q[0], 0.003)
            check("D2 WR1 p90", 0.304, q[2], 0.003)
        if role == "RB1" and met == "car":
            check("D2 RB1 carry p10", 0.405, q[0], 0.003)
            check("D2 RB1 carry p90", 0.654, q[2], 0.003)

    # D2b EX-ANTE role prior: last season's role (same team) -> this season's share, >= 8 g both.
    print("\n  D2b EX-ANTE role priors: player who held the role LAST season, same team, this season's share (>= 8 g both)")
    print(f"  {'role':5} {'met':4} {'N':>4} {'mean':>6} {'sd_obs':>7} {'sd_true':>8} {'n0_pop':>7} {'p10':>6} {'p50':>6} {'p90':>6}   ex-post mean / sd_true")
    for (role, met), (cm, csd, cn0, cN) in claimed.items():
        den = "team_tgt" if met == "tgt" else "team_car"
        sh, ns = [], []
        for (pid, season, team, v) in roles[role]:
            nxt = SP.get((pid, season + 1))
            if nxt is None or nxt["team"] != team or nxt["g"] < 8 or nxt[den] <= 0:
                continue
            sh.append(nxt[met] / nxt[den])
            ns.append(nxt[den])
        if len(sh) < 10:
            continue
        m, so, st, n0 = mom(sh, ns)
        q = np.percentile(sh, [10, 50, 90])
        print(f"  {role:5} {met:4} {len(sh):4d} {m:6.3f} {so:7.3f} {st:8.3f} {n0:7.1f} {q[0]:6.3f} {q[1]:6.3f} {q[2]:6.3f}   {cm:.3f} / {csd:.3f}")

    # ---- D3 split-half ----
    print("\n  D3 odd/even split-half (player-seasons >= 10 g): r, n_half (arith / harmonic), n0, bootstrap CI")

    def split_pairs(pos_set, num, den, min_den):
        a, b, n1, n2 = [], [], [], []
        for v in SP.values():
            if v["pos"] not in pos_set or v["g"] < 10:
                continue
            odd = [w for w in v["ws"] if w["week"] % 2 == 1]
            even = [w for w in v["ws"] if w["week"] % 2 == 0]
            d1, d2 = agg(odd, den), agg(even, den)
            if d1 < min_den or d2 < min_den:
                continue
            a.append(agg(odd, num) / d1)
            b.append(agg(even, num) / d2)
            n1.append(d1)
            n2.append(d2)
        return np.array(a), np.array(b), np.array(n1), np.array(n2)

    specs = [("WR/TE target share", {"WR", "TE"}, "tgt", "team_tgt", 1, 0.922, 19, 621, (16, 22)),
             ("WR target share", {"WR"}, "tgt", "team_tgt", 1, 0.927, 18, 409, None),
             ("TE target share", {"TE"}, "tgt", "team_tgt", 1, 0.897, 25, 212, None),
             ("RB target share", {"RB"}, "tgt", "team_tgt", 1, 0.850, 40, 256, None),
             ("RB carry share", {"RB"}, "car", "team_car", 1, 0.952, 10, 256, (8, 12)),
             ("WR/TE catch rate", {"WR", "TE"}, "rec", "tgt", 10, 0.405, 53, 486, None),
             ("WR/TE yards/target", {"WR", "TE"}, "ryds", "tgt", 10, 0.221, 127, 486, (78, 256)),
             ("WR/TE rec TD/target", {"WR", "TE"}, "rtd", "tgt", 10, 0.135, 232, 486, None),
             ("WR/TE YAC/rec", {"WR", "TE"}, "yac", "rec", 8, 0.390, 40, 445, None),
             ("WR/TE PPR/target", {"WR", "TE"}, "ppr", "tgt", 10, 0.184, 161, 486, (88, 443)),
             ("RB yards/carry", {"RB"}, "rush", "car", 20, 0.309, 182, 187, (98, 448)),
             ("RB rush TD/carry", {"RB"}, "rushtd", "car", 20, 0.248, 247, 187, None),
             ("RB PPR/carry(!)", {"RB"}, "ppr", "car", 20, 0.493, 84, 187, None)]
    print(f"  {'metric':22} {'N':>4} {'r':>6} {'n_arith':>8} {'n_harm':>7} {'n0':>6} {'n0_harm':>8} {'claimed':>8}  boot95")
    split_store = {}
    for label, pos_set, num, den, md, cr, cn0, cN, cci in specs:
        a, b, n1, n2 = split_pairs(pos_set, num, den, md)
        r = pearson(a, b)
        n_ar = float((n1.mean() + n2.mean()) / 2)
        n_h = float(1.0 / np.mean(1.0 / np.concatenate([n1, n2])))
        n0 = n_ar * (1 - r) / r
        n0h = n_h * (1 - r) / r
        ci = ""
        if cci is not None:
            nb = (n1 + n2) / 2
            n0s = []
            for _ in range(2000):
                idx = rng.integers(0, len(a), len(a))
                rr = pearson(a[idx], b[idx])
                n0s.append(nb.mean() * (1 - rr) / rr if rr > 0.02 else 5000)
            lo, hi = np.percentile(n0s, [2.5, 97.5])
            ci = f"[{lo:.0f}, {hi:.0f}] (claimed {cci})"
        print(f"  {label:22} {len(a):4d} {r:6.3f} {n_ar:8.1f} {n_h:7.1f} {n0:6.1f} {n0h:8.1f} {cn0:8d}  {ci}")
        check(f"D3 {label} r", cr, r, 0.005)
        check(f"D3 {label} N", cN, len(a), 0)
        check(f"D3 {label} n0", cn0, n0, 0.03, rel=True)
        split_store[label] = (a, b, n1, n2, r, n0)
    print("  (!) 'RB PPR/touch' in the findings is actually PPR per CARRY: the denominator is carries only, not carries+targets.")

    # D3b pooled population n0_pop for the same WR/TE split-half population (full-season MoM).
    for label, pos_set in [("WR/TE target share", {"WR", "TE"}), ("RB carry share", {"RB"})]:
        num, den = ("tgt", "team_tgt") if "target" in label else ("car", "team_car")
        sh, ns = [], []
        for v in SP.values():
            if v["pos"] in pos_set and v["g"] >= 10 and v[den] > 0:
                sh.append(v[num] / v[den])
                ns.append(v[den])
        m, so, st, n0p = mom(sh, ns)
        print(f"  D3b pooled {label} population (>=10 g, N={len(sh)}): mean {m:.3f} sd_true {st:.3f} -> MoM n0_pop = {n0p:.1f} "
              f"vs split-half n0 = {split_store[label][5]:.1f}")
        print("       -> split-half n0 ~ pooled n0_pop: the 'sampling-noise n0' is the pooled population's spread, not a metric constant.")

    # D3c early/late split (weeks <= 9 vs > 9): drift-inclusive at half-season scale.
    for label, pos_set, num, den in [("WR/TE target share", {"WR", "TE"}, "tgt", "team_tgt"), ("RB carry share", {"RB"}, "car", "team_car")]:
        a, b, n1, n2 = [], [], [], []
        for v in SP.values():
            if v["pos"] not in pos_set or v["g"] < 10:
                continue
            e = [w for w in v["ws"] if w["week"] <= 9]
            l = [w for w in v["ws"] if w["week"] > 9]
            d1, d2 = agg(e, den), agg(l, den)
            if d1 < 1 or d2 < 1 or len(e) < 3 or len(l) < 3:
                continue
            a.append(agg(e, num) / d1)
            b.append(agg(l, num) / d2)
            n1.append(d1)
            n2.append(d2)
        r = pearson(a, b)
        n_ar = (np.mean(n1) + np.mean(n2)) / 2
        print(f"  D3c {label} first-half vs second-half (wk<=9 / >9): r={r:.3f} n_half={n_ar:.0f} -> n0={n_ar*(1-r)/r:.1f} "
              f"(odd/even gave {split_store[label][5]:.1f}; the gap is within-season drift)")

    # ---- D4 first-k vs ROS ----
    print("\n  D4 first-k weeks vs rest of season (>= 12 g, >= k early weeks, >= 6 ROS games)")

    def first_k(pos_set, num, den, k):
        x, y, ne, nl = [], [], [], []
        for v in SP.values():
            if v["pos"] not in pos_set or v["g"] < 12:
                continue
            early = [w for w in v["ws"] if w["week"] <= k]
            late = [w for w in v["ws"] if w["week"] > k]
            de, dl = agg(early, den), agg(late, den)
            if len(early) < k or de < 1 or dl < 1 or len(late) < 6:
                continue
            x.append(agg(early, num) / de)
            y.append(agg(late, num) / dl)
            ne.append(de)
            nl.append(dl)
        return np.array(x), np.array(y), float(np.mean(ne)), float(np.mean(nl))

    claimed_fk = {"WR/TE target share": {1: (0.702, 444), 2: (0.816, 410), 3: (0.828, 376), 4: (0.839, 342), 6: (0.842, 224), 8: (0.853, 133)},
                  "RB carry share": {1: (0.758, 198), 2: (0.786, 186), 3: (0.805, 174), 4: (0.806, 169), 6: (0.849, 116), 8: (0.841, 74)}}
    claimed_drift = {"WR/TE target share": {1: 28, 2: 26, 3: 33, 4: 36, 6: 44}, "RB carry share": {1: 18, 2: 27, 3: 33, 4: 39, 6: 35},
                     "RB target share": {1: 40, 2: 41, 3: 57, 4: 64, 6: 63}}
    fk_store = {}
    for label, pos_set, num, den in [("WR/TE target share", {"WR", "TE"}, "tgt", "team_tgt"),
                                     ("RB carry share", {"RB"}, "car", "team_car"),
                                     ("RB target share", {"RB"}, "tgt", "team_tgt"),
                                     ("WR/TE yards/target", {"WR", "TE"}, "ryds", "tgt")]:
        parts = []
        for k in (1, 2, 3, 4, 5, 6, 8):
            x, y, ne, nl = first_k(pos_set, num, den, k)
            r = pearson(x, y)
            n0d = solve_n0(r, ne, nl)
            fk_store[(label, k)] = (r, len(x), ne, nl, n0d)
            parts.append(f"k={k}: r={r:.3f} N={len(x)} n0={n0d:.0f}")
            if label in claimed_fk and k in claimed_fk[label]:
                cr, cN = claimed_fk[label][k]
                check(f"D4 {label} k={k} r", cr, r, 0.004)
                check(f"D4 {label} k={k} N", cN, len(x), 0)
            if label in claimed_drift and k in claimed_drift[label]:
                check(f"D4 {label} k={k} drift n0", claimed_drift[label][k], n0d, 2.0)
        print(f"  {label}: " + " | ".join(parts))
    r3, r8 = fk_store[("WR/TE target share", 3)][0], fk_store[("WR/TE target share", 8)][0]
    print(f"  plateau check: r(k=3)/r(k=8) = {r3/r8:.3f} (claimed ~0.97); plateau r^2 = {r8**2:.2f}")
    check("D4 usage k=3 at ~97% of plateau", 0.97, r3 / r8, 0.015)
    ymax = max(fk_store[("WR/TE yards/target", k)][0] for k in (1, 2, 3, 4, 5, 6, 8))
    check("D4 WR/TE yards/target first-k r never exceeds 0.274", 0.274, ymax, 0.005)
    # what share of the unexplained ROS variance is ROS sampling noise rather than drift?
    nl8 = fk_store[("WR/TE target share", 8)][3]
    print(f"  ROS reliability at n_late={nl8:.0f} with sampling n0=19: {nl8/(nl8+19):.3f} -> of the {1-r8**2:.2f} unexplained, "
          f"~{1-nl8/(nl8+19):.2f} is ROS sampling noise, so drift ~{(1-r8**2)-(1-nl8/(nl8+19)):.2f}")

    # ---- D5 OOS n0 grid with bootstrap of the arg-min ----
    print("\n  D5 out-of-sample n0 grid: prior m = prev-season same-team share (>= 10 g), posterior (x + n0 m)/(n + n0) vs ROS share")

    def eb_arrays(pos_set, num, den, k):
        X, DE, M, ROS = [], [], [], []
        for (pid, season), v in SP.items():
            if v["pos"] not in pos_set or v["g"] < 12:
                continue
            early = [w for w in v["ws"] if w["week"] <= k]
            late = [w for w in v["ws"] if w["week"] > k]
            de, dl = agg(early, den), agg(late, den)
            if len(early) < k or de < 1 or dl < 1 or len(late) < 6:
                continue
            prev = SP.get((pid, season - 1))
            if prev is None or prev["g"] < 10 or prev["team"] != v["team"] or prev[den] <= 0:
                continue
            X.append(agg(early, num))
            DE.append(de)
            M.append(prev[num] / prev[den])
            ROS.append(agg(late, num) / dl)
        return (np.array(X), np.array(DE), np.array(M), np.array(ROS))

    claimed_oos = {"WR/TE target share": {1: (120, 186, 0.0797, 0.0459, 0.0435), 2: (90, 170, 0.0543, 0.0481, 0.0408),
                                          3: (90, 159, 0.0487, 0.0503, 0.0412), 4: (60, 144, 0.0442, 0.0514, 0.0401),
                                          6: (90, 98, 0.0458, 0.0546, 0.0436)},
                   "RB carry share": {1: (45, 76, 0.1680, 0.1508, 0.1342), 2: (45, 70, 0.1541, 0.1577, 0.1371),
                                      3: (30, 67, 0.1374, 0.1631, 0.1312), 4: (20, 63, 0.1297, 0.1675, 0.1282),
                                      6: (0, 44, 0.1213, 0.1813, 0.1213)},
                   "RB target share": {1: (300, 76, 0.0570, 0.0301, 0.0299), 2: (180, 70, 0.0407, 0.0309, 0.0290),
                                       3: (180, 67, 0.0364, 0.0315, 0.0284), 4: (180, 63, 0.0357, 0.0330, 0.0300)}}
    grids = {"WR/TE target share": np.array([0, 10, 20, 30, 45, 60, 90, 120, 180, 300], float),
             "RB carry share": np.array([0, 5, 10, 15, 20, 30, 45, 60, 90, 150], float),
             "RB target share": np.array([0, 10, 20, 30, 45, 60, 90, 120, 180, 300], float)}
    for label, pos_set, num, den in [("WR/TE target share", {"WR", "TE"}, "tgt", "team_tgt"),
                                     ("RB carry share", {"RB"}, "car", "team_car"),
                                     ("RB target share", {"RB"}, "tgt", "team_tgt")]:
        grid = grids[label]
        print(f"  -- {label} --")
        for k in sorted(claimed_oos[label]):
            X, DE, M, ROS = eb_arrays(pos_set, num, den, k)
            N = len(X)
            err = np.array([((X + n0 * M) / (DE + n0) - ROS) ** 2 for n0 in grid])  # G x N
            rmse = np.sqrt(err.mean(axis=1))
            best = int(np.argmin(rmse))
            naive = float(np.sqrt(np.mean((X / DE - ROS) ** 2)))
            prev_only = float(np.sqrt(np.mean((M - ROS) ** 2)))
            # bootstrap the arg-min
            bests = []
            for _ in range(1000):
                idx = rng.integers(0, N, N)
                bests.append(grid[int(np.argmin(err[:, idx].mean(axis=1)))])
            bests = np.array(bests)
            lo, hi = np.percentile(bests, [10, 90])
            # flat region: grid points whose RMSE is within 1% of the best
            flat = grid[rmse <= rmse[best] * 1.01]
            cb, cN, cnaive, cprev, cbest_rmse = claimed_oos[label][k]
            print(f"    k={k} N={N} naive={naive:.4f} prev-only={prev_only:.4f} best n0={grid[best]:.0f} (rmse {rmse[best]:.4f}); "
                  f"within-1%: {flat.min():.0f}-{flat.max():.0f}; bootstrap 10-90% of best n0: {lo:.0f}-{hi:.0f}; "
                  f"P(best=0)={np.mean(bests==0):.2f}")
            check(f"D5 {label} k={k} N", cN, N, 0)
            check(f"D5 {label} k={k} naive RMSE", cnaive, naive, 0.0005)
            check(f"D5 {label} k={k} prev-only RMSE", cprev, prev_only, 0.0005)
            check(f"D5 {label} k={k} best n0", cb, grid[best], 0)
            check(f"D5 {label} k={k} best RMSE", cbest_rmse, rmse[best], 0.0005)

    # ---- D6 YoY regression ----
    print("\n  D6 year-over-year (consecutive seasons by player_id, any team)")

    def yoy(pos_set, num, den, minden):
        x, y = [], []
        for (pid, season), v in SP.items():
            if v["pos"] not in pos_set or season == SEASONS[0]:
                continue
            prev = SP.get((pid, season - 1))
            if prev is None:
                continue
            d0, d1 = agg(prev["ws"], den), agg(v["ws"], den)
            if d0 < minden or d1 < minden:
                continue
            x.append(agg(prev["ws"], num) / d0)
            y.append(agg(v["ws"], num) / d1)
        x, y = np.array(x), np.array(y)
        r = pearson(x, y)
        slope = float(np.polyfit(x, y, 1)[0])
        hi = x >= np.percentile(x, 90)
        lo = x <= np.percentile(x, 10)
        return len(x), x.mean(), r, slope, x[hi].mean(), y[hi].mean(), x[lo].mean(), y[lo].mean()

    for label, pset, num, den, md, cN, cmean, cr, cslope, cxh, cyh, cret in [
            ("WR/TE recTD/tgt", {"WR", "TE"}, "rtd", "tgt", 50, 153, 0.0527, 0.173, 0.178, 0.1041, 0.0581, 0.11),
            ("RB rushTD/carry", {"RB"}, "rushtd", "car", 100, 67, 0.0332, 0.246, 0.227, 0.0679, 0.0446, 0.33),
            ("WR/TE yds/tgt", {"WR", "TE"}, "ryds", "tgt", 50, 153, 8.20, 0.383, 0.420, None, None, None),
            ("RB yds/carry", {"RB"}, "rush", "car", 100, 67, 4.37, 0.177, 0.146, None, None, None),
            ("WR/TE target share", {"WR", "TE"}, "tgt", "team_tgt", 50, 509, 0.1181, 0.837, 0.840, 0.2760, 0.2469, 0.82),
            ("RB carry share", {"RB"}, "car", "team_car", 100, 169, 0.3023, 0.808, 0.820, 0.6536, 0.5588, 0.73)]:
        n, m, r, sl, xh, yh, xl, yl = yoy(pset, num, den, md)
        ret = (yh - m) / (xh - m)
        print(f"    {label:20} N={n:3d} mean={m:.4f} r={r:.3f} slope={sl:.3f} top-decile {xh:.4f}->{yh:.4f} (retains {ret*100:.0f}%) bottom {xl:.4f}->{yl:.4f}")
        check(f"D6 {label} N", cN, n, 0)
        check(f"D6 {label} r", cr, r, 0.004)
        check(f"D6 {label} slope", cslope, sl, 0.004)
        if cret is not None:
            check(f"D6 {label} retained excess", cret, ret, 0.015)
    # YoY-vs-split-half consistency: with a season of ~n_year opportunities and pure noise n0, YoY r = n/(n+n0)
    print("  consistency: split-half n0 -> predicted YoY r if NO between-season drift:")
    for label, n0, nyear, robs in [("WR/TE target share", 19, 500, 0.837), ("RB carry share", 10, 430, 0.808),
                                   ("WR/TE recTD/tgt", 232, 95, 0.173), ("WR/TE yds/tgt", 127, 95, 0.383)]:
        pred = nyear / (nyear + n0)
        print(f"    {label:20} n_year~{nyear}: predicted r={pred:.3f} vs observed {robs:.3f} -> drift-inclusive n0 = {nyear*(1-robs)/robs:.0f}")

    # ---- D7 weekly / season R^2 on raw counts, and scoring cross-check ----
    print("\n  D7 PPR ~ a*targets + b*carries (no intercept); R^2 = 1 - SSE/SST about the mean")

    def r2_fit(R):
        X = np.column_stack([[r["tgt"] for r in R], [r["car"] for r in R]])
        y = np.array([r["ppr"] for r in R])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        pred = X @ beta
        r2 = 1 - ((y - pred) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        X1 = np.column_stack([np.ones(len(y)), X])
        b1, *_ = np.linalg.lstsq(X1, y, rcond=None)
        r2i = 1 - ((y - X1 @ b1) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        return beta, r2, r2i, len(y)

    for label, pset, cN, ca, cb, cr2w, cNs, cr2s in [("WR", {"WR"}, 6472, 1.73, 0.89, 0.601, 346, 0.892),
                                                     ("TE", {"TE"}, 3276, 1.74, 0.83, 0.632, 139, 0.867),
                                                     ("RB", {"RB"}, 4067, 1.31, 0.69, 0.655, 276, 0.929),
                                                     ("pooled", {"WR", "TE", "RB"}, 13815, 1.71, 0.61, 0.626, 761, 0.905)]:
        R = [r for r in rows if r["pos"] in pset and (r["tgt"] + r["car"]) > 0]
        beta, r2, r2i, n = r2_fit(R)
        seas = defaultdict(lambda: [0.0, 0.0, 0.0])
        for r in R:
            s = seas[(r["pid"], r["season"])]
            s[0] += r["tgt"]
            s[1] += r["car"]
            s[2] += r["ppr"]
        S = np.array([v for v in seas.values() if v[0] + v[1] >= 30])
        bs, *_ = np.linalg.lstsq(S[:, :2], S[:, 2], rcond=None)
        r2s = 1 - ((S[:, 2] - S[:, :2] @ bs) ** 2).sum() / ((S[:, 2] - S[:, 2].mean()) ** 2).sum()
        print(f"    {label:6} weekly N={n:5d}: {beta[0]:.2f}/tgt {beta[1]:.2f}/carry R2={r2:.3f} (with intercept {r2i:.3f}) | "
              f"season N={len(S)} R2={r2s:.3f} ({bs[0]:.2f}/tgt {bs[1]:.2f}/carry)")
        check(f"D7 {label} weekly N", cN, n, 0)
        check(f"D7 {label} pts/target", ca, beta[0], 0.01)
        check(f"D7 {label} pts/carry", cb, beta[1], 0.01)
        check(f"D7 {label} weekly R2", cr2w, r2, 0.003)
        check(f"D7 {label} season N", cNs, len(S), 0)
        check(f"D7 {label} season R2", cr2s, r2s, 0.003)
    R_tr = [r for r in rows if r["pos"] in {"WR", "TE", "RB"} and r["tgt"] + r["car"] > 0 and r["season"] < 2025]
    R_te = [r for r in rows if r["pos"] in {"WR", "TE", "RB"} and r["tgt"] + r["car"] > 0 and r["season"] == 2025]
    Xtr = np.column_stack([[r["tgt"] for r in R_tr], [r["car"] for r in R_tr]])
    ytr = np.array([r["ppr"] for r in R_tr])
    Xte = np.column_stack([[r["tgt"] for r in R_te], [r["car"] for r in R_te]])
    yte = np.array([r["ppr"] for r in R_te])
    b, *_ = np.linalg.lstsq(Xtr, ytr, rcond=None)
    r2oos = 1 - ((yte - Xte @ b) ** 2).sum() / ((yte - yte.mean()) ** 2).sum()
    print(f"    OOS 2025 weekly (fit 2023-24): R2={r2oos:.3f} rates {b[0]:.2f}/tgt {b[1]:.2f}/carry")
    check("D7 OOS 2025 weekly R2", 0.613, r2oos, 0.003)
    # half-PPR sensitivity (open question): rebuild points with 0.5/reception
    R = [r for r in rows if r["pos"] in {"WR", "TE", "RB"} and (r["tgt"] + r["car"]) > 0]
    X = np.column_stack([[r["tgt"] for r in R], [r["car"] for r in R]])
    for lab, w_rec in [("PPR", 1.0), ("half-PPR", 0.5), ("standard", 0.0)]:
        y = np.array([r["ppr"] - (1 - w_rec) * r["rec"] for r in R])
        bb, *_ = np.linalg.lstsq(X, y, rcond=None)
        r2 = 1 - ((y - X @ bb) ** 2).sum() / ((y - y.mean()) ** 2).sum()
        print(f"    scoring sensitivity {lab:9}: {bb[0]:.2f}/tgt {bb[1]:.2f}/carry weekly R2={r2:.3f}")

    # scoring cross-check against gridiron.scoring
    try:
        src = REPO / "src"
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))
        from gridiron.scoring import fantasy_points  # noqa: WPS433
        diff_raw, diff_mapped, n_st = [], [], 0
        for r in rows:
            raw = fantasy_points(r) if False else None  # rows use short keys; build explicit dicts below
            raw_keys = {"passing_yards": r["passing_yards"], "passing_tds": r["passing_tds"],
                        "passing_interceptions": r["passing_interceptions"], "rushing_yards": r["rush"],
                        "rushing_tds": r["rushtd"], "receptions": r["rec"], "receiving_yards": r["ryds"],
                        "receiving_tds": r["rtd"], "fumbles_lost_total": r["fumbles_lost_total"],
                        "passing_2pt_conversions": r["passing_2pt_conversions"],
                        "rushing_2pt_conversions": r["rushing_2pt_conversions"],
                        "receiving_2pt_conversions": r["receiving_2pt_conversions"]}
            mapped = dict(raw_keys)
            mapped["interceptions"] = r["passing_interceptions"]
            mapped["fumbles_lost"] = r["sack_fumbles_lost"] + r["rushing_fumbles_lost"] + r["receiving_fumbles_lost"]
            mapped["two_point_conversions"] = (r["passing_2pt_conversions"] + r["rushing_2pt_conversions"]
                                               + r["receiving_2pt_conversions"])
            target = r["ppr"] - 6.0 * r["special_teams_tds"]
            diff_raw.append(fantasy_points(raw_keys) - target)
            diff_mapped.append(fantasy_points(mapped) - target)
            n_st += r["special_teams_tds"] > 0
        diff_raw, diff_mapped = np.array(diff_raw), np.array(diff_mapped)
        print(f"    gridiron.scoring vs nflverse fantasy_points_ppr (minus 6*special_teams_tds; {n_st} rows had ST TDs):")
        print(f"      raw nflverse column names -> max |diff| {np.abs(diff_raw).max():.2f}, rows off by > 0.01: {np.mean(np.abs(diff_raw) > 0.01)*100:.1f}%"
              f"  (keys 'interceptions'/'fumbles_lost'/'two_point_conversions' are NOT nflverse columns)")
        print(f"      with mapped keys            -> max |diff| {np.abs(diff_mapped).max():.2f}, rows off by > 0.01: {np.mean(np.abs(diff_mapped) > 0.01)*100:.2f}%")
        check("D7 gridiron.scoring == nflverse PPR with mapped keys (max |diff|)", 0.0, float(np.abs(diff_mapped).max()), 0.011)
    except Exception as exc:  # pragma: no cover
        print(f"    gridiron.scoring cross-check skipped: {exc!r}")

    # ---- D8 log-variance decomposition ----
    print("\n  D8 per-game log decomposition log(FP/G) = log(opps/G) + log(FP/opp); >= 8 g, >= 3 opps/g")
    for label, pset, cN, cVt, cVo, cVe, c2c, cshare, cr2 in [("WR", {"WR"}, 292, 0.1939, 0.1231, 0.0380, 0.0329, 0.72, 0.82),
                                                              ("TE", {"TE"}, 115, 0.1135, 0.0859, 0.0366, -0.0092, 0.72, 0.68),
                                                              ("RB", {"RB"}, 239, 0.4255, 0.3417, 0.0590, 0.0250, 0.83, 0.86)]:
        Lo, Le = [], []
        for v in SP.values():
            if v["pos"] not in pset or v["g"] < 8:
                continue
            opps = (v["tgt"] + v["car"]) / v["g"]
            fpg = agg(v["ws"], "ppr") / v["g"]
            if opps < 3 or fpg <= 0:
                continue
            Lo.append(math.log(opps))
            Le.append(math.log(fpg / opps))
        Lo, Le = np.array(Lo), np.array(Le)
        L = Lo + Le
        vo, ve, cov = Lo.var(), Le.var(), np.cov(Lo, Le, ddof=0)[0, 1]
        share = (vo + cov) / L.var()
        r2 = pearson(Lo, L) ** 2
        print(f"    {label}: N={len(L)} Var={L.var():.4f} = {vo:.4f} + {ve:.4f} + 2cov {2*cov:.4f}; opp share {share:.3f}; R2 {r2:.3f}")
        check(f"D8 {label} N", cN, len(L), 0)
        check(f"D8 {label} Var(log FPG)", cVt, L.var(), 0.001)
        check(f"D8 {label} Var(log opps)", cVo, vo, 0.001)
        check(f"D8 {label} Var(log eff)", cVe, ve, 0.001)
        check(f"D8 {label} 2cov", c2c, 2 * cov, 0.001)
        check(f"D8 {label} opp share", cshare, share, 0.01)
        check(f"D8 {label} R2", cr2, r2, 0.01)

    # ---- D9 blend OLS ----
    print("\n  D9 ROS share ~ 1 + prev-season share + first-k share (same team, prev >= 10 g, cur >= 12 g)")
    claimed_blend = {"WR/TE target share": {1: (186, 0.829, 0.672, 0.168, 0.20), 2: (170, 0.813, 0.476, 0.380, 0.44),
                                            3: (159, 0.804, 0.432, 0.489, 0.53), 4: (144, 0.789, 0.334, 0.576, 0.63),
                                            5: (121, 0.791, 0.299, 0.622, 0.68), 6: (98, 0.768, 0.294, 0.643, 0.69)},
                     "RB carry share": {1: (76, 0.730, 0.395, 0.378, 0.49), 2: (70, 0.679, 0.266, 0.478, 0.64),
                                        3: (67, 0.658, 0.106, 0.633, 0.86), 4: (63, 0.642, 0.019, 0.735, 0.97),
                                        5: (51, 0.614, 0.008, 0.791, 0.99)}}
    for label, pos_set, num, den, gpg in [("WR/TE target share", {"WR", "TE"}, "tgt", "team_tgt", 31.3),
                                          ("RB carry share", {"RB"}, "car", "team_car", 26.9)]:
        print(f"  -- {label} --")
        for k in sorted(claimed_blend[label]):
            X, DE, M, ROS = eb_arrays(pos_set, num, den, k)
            E = X / DE
            A = np.column_stack([np.ones(len(M)), M, E])
            beta, *_ = np.linalg.lstsq(A, ROS, rcond=None)
            rP = pearson(M, ROS)
            wshare = beta[2] / (beta[1] + beta[2])
            n0prev = k * gpg * (1 - wshare) / wshare if 0 < wshare < 1 else float("nan")
            # bootstrap the weight share
            ws = []
            for _ in range(1000):
                idx = rng.integers(0, len(M), len(M))
                bb, *_ = np.linalg.lstsq(A[idx], ROS[idx], rcond=None)
                ws.append(bb[2] / (bb[1] + bb[2]))
            lo, hi = np.percentile(ws, [5, 95])
            cN, crP, cbP, cbE, cw = claimed_blend[label][k]
            print(f"    k={k} N={len(M)} r(prev,ROS)={rP:.3f} coef prev/cur = {beta[1]:.3f}/{beta[2]:.3f} sum {beta[1]+beta[2]:.2f} "
                  f"cur weight share {wshare:.2f} [boot 5-95%: {lo:.2f}-{hi:.2f}] -> n0_prev {n0prev:.0f}")
            check(f"D9 {label} k={k} N", cN, len(M), 0)
            check(f"D9 {label} k={k} r(prev,ROS)", crP, rP, 0.004)
            check(f"D9 {label} k={k} coef prev", cbP, beta[1], 0.004)
            check(f"D9 {label} k={k} coef cur", cbE, beta[2], 0.004)
            check(f"D9 {label} k={k} cur weight share", cw, wshare, 0.015)

    # ---- D10 team changers ----
    print("\n  D10 team changers: r(prev-season share, ROS share after week 3)")
    for label, pos_set, num, den, cs, cc in [("WR/TE target share", {"WR", "TE"}, "tgt", "team_tgt", (201, 0.822, 0.84), (44, 0.757, 0.71)),
                                             ("RB carry share", {"RB"}, "car", "team_car", (80, 0.727, 0.73), (25, 0.702, 0.74))]:
        same, chg = ([], []), ([], [])
        for (pid, season), v in SP.items():
            prev = SP.get((pid, season - 1))
            if v["pos"] not in pos_set or v["g"] < 12 or prev is None or prev["g"] < 10 or prev[den] == 0:
                continue
            late = [w for w in v["ws"] if w["week"] > 3]
            if agg(late, den) < 1:
                continue
            dest = same if prev["team"] == v["team"] else chg
            dest[0].append(prev[num] / prev[den])
            dest[1].append(agg(late, num) / agg(late, den))
        rs, rc = pearson(*same), pearson(*chg)
        bs, bc = np.polyfit(same[0], same[1], 1)[0], np.polyfit(chg[0], chg[1], 1)[0]
        # bootstrap CI for the changer r
        a, b = np.array(chg[0]), np.array(chg[1])
        rr = [pearson(a[i], b[i]) for i in (rng.integers(0, len(a), len(a)) for _ in range(2000))]
        lo, hi = np.percentile(rr, [2.5, 97.5])
        print(f"    {label}: same-team N={len(same[0])} r={rs:.3f} slope={bs:.2f} | changed N={len(chg[0])} r={rc:.3f} "
              f"[95% {lo:.2f}-{hi:.2f}] slope={bc:.2f}")
        check(f"D10 {label} same N", cs[0], len(same[0]), 0)
        check(f"D10 {label} same r", cs[1], rs, 0.004)
        check(f"D10 {label} changed N", cc[0], len(chg[0]), 0)
        check(f"D10 {label} changed r", cc[1], rc, 0.004)
        check(f"D10 {label} changed slope", cc[2], bc, 0.01)


# --------------------------------------------------------------------------
# E. Shrinkage-weight table
# --------------------------------------------------------------------------
def section_e() -> None:
    banner("E. SHRINKAGE WEIGHT w = n/(n+n0) AT WEEK 1 / 4 / 8 / 17 FOR EACH RECOMMENDED n0")
    g_t, g_c = 31.3, 26.9
    wr1_tpg, rb1_cpg, wr1_rpg = 0.249 * g_t, 0.535 * g_c, 0.249 * g_t * 0.65
    table = [
        ("WR/TE tgt share, sampling (pooled) n0", 19, g_t, "team tgt"),
        ("WR/TE tgt share, drift-incl n0 (k=1..6)", 35, g_t, "team tgt"),
        ("WR/TE tgt share, OOS w/ prev-season prior", 90, g_t, "team tgt"),
        ("WR1 tgt share vs WR1 role prior (n0_pop)", 106, g_t, "team tgt"),
        ("RB carry share, sampling n0", 10, g_c, "team car"),
        ("RB1 carry share vs RB1 role prior", 25, g_c, "team car"),
        ("RB carry share, OOS week-1 n0", 45, g_c, "team car"),
        ("RB tgt share, sampling n0", 40, g_t, "team tgt"),
        ("RB tgt share, OOS w/ prev prior", 180, g_t, "team tgt"),
        ("catch rate (WR1 volume)", 53, wr1_tpg, "player tgt"),
        ("yards/target (WR1 volume)", 127, wr1_tpg, "player tgt"),
        ("PPR/target (WR1 volume)", 161, wr1_tpg, "player tgt"),
        ("rec TD/target (WR1 volume)", 232, wr1_tpg, "player tgt"),
        ("YAC/rec (WR1 volume)", 40, wr1_rpg, "player rec"),
        ("yards/carry (RB1 volume)", 182, rb1_cpg, "player car"),
        ("rush TD/carry (RB1 volume)", 247, rb1_cpg, "player car"),
        ("PPR/carry (RB1 volume)", 84, rb1_cpg, "player car"),
    ]
    print(f"  {'metric / n0 source':44} {'n0':>5} {'per g':>6} {'w wk1':>6} {'w wk4':>6} {'w wk8':>6} {'w wk17':>7} {'games@w=.5':>11}")
    for label, n0, per_g, unit in table:
        ws = [k * per_g / (k * per_g + n0) for k in (1, 4, 8, 17)]
        print(f"  {label:44} {n0:5d} {per_g:6.1f} {ws[0]:6.2f} {ws[1]:6.2f} {ws[2]:6.2f} {ws[3]:7.2f} {n0/per_g:11.1f}  ({unit})")
    print("  claims: usage 'stabilizes' (w=0.5) in 0.6 g (pooled), ~1 g (drift), ~3 g (prev-season prior), 3.4 g (WR1 role prior);")
    print("          a WR1's yards/target reaches w=0.5 only after ~16 games; TD/target never within a season (w=0.36 at wk 17).")
    # OOS n0 vs the F12 OLS weight share at k=1: 31/(31+90) vs 0.20
    print(f"  cross-check: OOS n0=90 gives w(wk1)={g_t/(g_t+90):.2f}, w(wk3)={3*g_t/(3*g_t+90):.2f}; F12 OLS weight shares were 0.20 / 0.53")
    # RB decay schedule check
    sched = {1: 45, 2: 45, 3: 30, 4: 20, 6: 0}
    print("  RB carry-share n0 schedule (OOS) vs 45*0.7^(k-1): " + ", ".join(f"k={k}: {v} vs {45*0.7**(k-1):.0f}" for k, v in sched.items()))


# --------------------------------------------------------------------------
# F. Variance-decomposition simulation
# --------------------------------------------------------------------------
def section_f(rng: np.random.Generator) -> None:
    banner("F. VARIANCE-DECOMPOSITION SIMULATION: points = opportunity x efficiency (fitted log-variance parameters)")
    N = 400_000
    for label, vo, ve, cov, mu_o, mu_e, cshare, cr2 in [("WR", 0.1231, 0.0380, 0.01645, math.log(6.5), math.log(1.75), 0.72, 0.82),
                                                       ("TE", 0.0859, 0.0366, -0.0046, math.log(5.0), math.log(1.7), 0.72, 0.68),
                                                       ("RB", 0.3417, 0.0590, 0.0125, math.log(12.0), math.log(1.0), 0.83, 0.86)]:
        Z = rng.multivariate_normal([mu_o, mu_e], [[vo, cov], [cov, ve]], N)
        lo, le = Z[:, 0], Z[:, 1]
        lp = lo + le
        share = (lo.var() + np.cov(lo, le, ddof=0)[0, 1]) / lp.var()
        r2_log = pearson(lo, lp) ** 2
        opps, pts = np.exp(lo), np.exp(lp)
        r2_lin = pearson(opps, pts) ** 2
        r2_theory = (vo + cov) ** 2 / ((vo + ve + 2 * cov) * vo)
        print(f"  {label}: Var(log pts)={lp.var():.4f}; opp share={share:.3f} (claimed {cshare}); R2 log={r2_log:.3f} (claimed {cr2}, "
              f"theory {r2_theory:.3f}); R2 linear pts~opps={r2_lin:.3f}")
        check(f"F sim {label} opp share", cshare, share, 0.01)
        check(f"F sim {label} R2(log)", cr2, r2_log, 0.01)
    print("  note: 'opportunity share incl. half the covariance' is a bookkeeping convention; the R2 of opportunity alone is the")
    print("        defensible number, and both agree with the data recomputation in D8.")


# --------------------------------------------------------------------------
def main() -> None:
    rng = np.random.default_rng(SEED)
    section_a(rng)
    section_b(rng)
    section_c(rng)
    rows = load_rows()
    print(f"\n  loaded {len(rows)} REG-season skill-position rows in {time.time()-T0:.1f}s")
    teamwk, SP = build(rows)
    section_d(rows, teamwk, SP, rng)
    section_e()
    section_f(rng)

    banner("G. SUMMARY")
    fails = [r for r in RESULTS if not r[3]]
    print(f"  checks: {len(RESULTS)} total, {len(RESULTS)-len(fails)} pass, {len(fails)} fail")
    for label, claimed, got, _ in fails:
        print(f"    FAIL {label}: claimed {claimed:g}, got {got:.4g}")
    print(f"  elapsed {time.time()-T0:.1f}s")


if __name__ == "__main__":
    main()
