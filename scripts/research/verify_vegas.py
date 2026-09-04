"""verify_vegas.py — adversarial numeric verification of the Vegas-domain research.

What this verifies (research draft: docs/research/drafts/vegas.md):

  PART A — pure algebra, always runs (numpy + math only, no data needed)
    A1  implied-total identity: fav + dog == T, fav - dog == |S|; agrees with
        gridiron.vegas; nflverse sign convention.
    A2  scoring identity per season: 6*TD_all + XP + 2*2pt + 3*FGM + 2*safety
        reproduces the claimed points/game; points-per-offTD; points shares.
    A3  regression internal consistency: every claimed OLS line must pass
        through (mean I, mean y); pts slope == 7*offTD slope + 3*FGM slope.
    A4  dimension check of every formula (units in -> units out).
    A5  attempts(S,T)/rushes(S,T)/plays at S=0 vs the league averages, and
        the reconciliation with plays x pass-rate (sack definition matters).
    A6  Monte-Carlo league average of the linear volume functions over a
        spread ~ N(0, 5.5) distribution (seeded); normal-CDF tail checks of
        the implied-total distribution with math.erf.
    A7  worked example (fav -3 / total 47, WR 24% share) recomputed exactly
        from the stated constants; any drift > 2% is flagged.
    A8  kicker arithmetic, second-half dropback slope, (M,T)->(I_own,I_opp)
        coefficient conversion, slope decompositions, half-PPR shifts.

  PART B — data refit, runs when the nflverse files are present in
           data/research/cache/nflverse/ (gitignored, rule #10):
             play_by_play_{2023,2024,2025}.csv.gz
             stats_player_week_{2023,2024,2025}.csv
    B1  game-level: corr(result, spread_line), SD(result - spread), totals.
    B2  team-game anchors per season (pts, TDs, FGM, volume, pass rates).
    B3  pooled OLS on implied total I for every claimed coefficient.
    B4  Rule 5: leave-one-season-out out-of-sample skill of each I-regression
        against a mean-only baseline (RMSE ratio, OOS r).
    B5  within-team (team-season demeaned) slopes, per season and pooled.
    B6  positional efficiency (WR/TE/RB per target, RB per carry, QB per att).
    B7  team-week fantasy PPR (QB+RB+WR+TE) vs I, pooled and LOSO.
    B8  worked-example neighbourhood and the weekly SD of real WRs whose
        season mean sits at the worked-example level (~13.5 PPR).
    B9  dropback rate by score differential / half; second-half slope.

Deterministic: the only RNG is seeded. No scipy: Phi(x) = 0.5*(1+erf(x/sqrt2)).
No absolute paths: everything derives from __file__.

Run from the repo root:
    python scripts/research/verify_vegas.py
"""
from __future__ import annotations

import csv
import gzip
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "research" / "cache" / "nflverse"
sys.path.insert(0, str(REPO / "src"))

from gridiron.vegas import (  # noqa: E402
    implied_team_total,
    implied_totals,
    implied_totals_from_nflverse,
)

SEASONS = (2023, 2024, 2025)
SEED = 20260904

# ---------------------------------------------------------------------------
# Claimed constants (verbatim from the research findings)
# ---------------------------------------------------------------------------
CLAIM = {
    # per-season anchors per team-game
    "pts": {2023: 21.77, 2024: 22.91, 2025: 23.01},
    "off_td": {2023: 2.252, 2024: 2.432, 2025: 2.430},
    "pass_td": {2023: 1.386, 2024: 1.487, 2025: 1.491},
    "rush_td": {2023: 0.864, 2024: 0.939, 2025: 0.938},
    "nonoff_td": {2023: 0.142, 2024: 0.118, 2025: 0.134},
    "fgm": {2023: 1.675, 2024: 1.722, 2025: 1.711},
    "fga": {2023: 1.949, 2024: 2.050, 2025: 2.000},
    "xp": {2023: 2.061, 2024: 2.186, 2025: 2.224},
    "two_pt": {2023: 0.129, 2024: 0.101, 2025: 0.108},
    "safety": {2023: 0.031, 2024: 0.028, 2025: 0.022},
    "plays": {2023: 63.19, 2024: 62.22, 2025: 61.32},
    "pass_att": {2023: 33.67, 2024: 32.74, 2025: 32.05},
    "rush_att_all": {2023: 26.9, 2024: 27.1, 2025: 26.9},
    "rush_att_nokneel": {2023: 26.06, 2024: 26.25, 2025: 26.04},
    "sacks": {2023: 2.6, 2024: 2.4, 2025: 2.4},
    "dropbacks": {2023: 38.18, 2024: 37.16, 2025: 36.46},
    "pass_rate": {2023: 0.556, 2024: 0.547, 2025: 0.544},
    "dropback_rate": {2023: 0.604, 2024: 0.597, 2025: 0.594},
    "neutral_db_rate": {2023: 0.544, 2024: 0.534, 2025: 0.532},
    "targets": {2023: 32.14, 2024: 31.27, 2025: 30.53},
    "tgt_per_att": {2023: 0.9546, 2024: 0.9552, 2025: 0.9525},
    # pooled OLS y = a + b*I (team-game, n=1632)
    "ols_I": {
        "pts": (-1.93, 1.112, 0.415, 9.04),
        "off_td": (-0.945, 0.1505, 0.396, 1.30),
        "pass_td": (-0.511, 0.0892, 0.290, 1.09),
        "rush_td": (-0.430, 0.0610, 0.238, 0.92),
        "fgm": (1.40, 0.0139, 0.041, None),
        "pass_att": (32.14, 0.031, 0.015, None),
        "targets": (29.83, 0.067, 0.033, None),
        "rush_att": (19.94, 0.280, 0.144, None),
        "ppr_per_tgt": (0.791, 0.0420, 0.36, 0.41),
        "rec_ppr": (24.06, 1.319, 0.31, None),
        "rush_pts": (2.13, 0.684, None, None),
        "pts_per_carry": (0.210, 0.0196, None, None),
    },
    # y = a + b*M + c*T (M = expected margin, + favored)
    "ols_MT": {
        "pass_att": (24.70, -0.068, 0.184, 0.013),
        "targets": (22.49, -0.049, 0.200, None),
        "rush_att": (30.31, 0.289, -0.076, 0.057),
        "plays": (59.60, 0.171, 0.060, 0.016),
        "pass_td": (-0.863, 0.0406, 0.0526, None),
        "rush_td": (-0.190, 0.0332, 0.0251, None),
    },
    "within": {"ppr_per_tgt": 0.0298, "pass_td": 0.066, "rec_ppr": 1.11,
               "rush_pts": 0.46, "targets": 0.16, "pts_per_carry": 0.0134},
    "between": {"ppr_per_tgt": 0.0531, "pass_td": 0.110, "rec_ppr": 1.51,
                "rush_pts": 0.89, "targets": -0.02},
    "game": {"corr_result_spread": 0.477, "sd_result_minus_spread": 12.64,
             "corr_total_line": 0.305, "sd_total_minus_line": 12.92,
             "mean_total_gap": 1.07, "mean_abs_spread": 5.1,
             "mean_implied": 22.03, "sd_implied": 3.7},
    "pos_eff": {
        "WR": (0.630, 7.93, 0.0493, 10.8, 1.72),
        "TE": (0.720, 7.30, 0.0505, 6.3, 1.75),
        "RB": (0.784, 5.79, 0.0304, -0.15, 1.55),
    },
    "rb_carry": (4.29, 0.0309, 0.614),
    "qb_pass": (7.06, 0.0441, 0.0223, 0.414),
    "team_ppr": {"ALL": (0.411, 2.62, 83.1, 23.6), "QB": (0.333, 0.70),
                 "RB": (0.268, 0.71), "WR": (0.261, 0.97), "TE": (0.112, 0.24)},
    "h2_dropback": {"<=-17": 0.732, "-16..-9": 0.733, "-8..-1": 0.644, "0": 0.592,
                    "1..8": 0.497, "9..16": 0.444, ">=17": 0.362},
    "h1_dropback": {"<=-17": 0.696, "-16..-9": 0.649, "-8..-1": 0.613, "0": 0.579,
                    "1..8": 0.610, "9..16": 0.625, ">=17": 0.593},
}

FLAGS: list[str] = []
PASSES: list[str] = []


def check(name: str, claimed, computed, tol_rel=0.02, tol_abs=None) -> bool:
    """Record a pass/flag. Tolerance: relative 2% by default, or absolute."""
    if computed is None or (isinstance(computed, float) and math.isnan(computed)):
        FLAGS.append(f"{name}: could not compute (claimed {claimed})")
        return False
    diff = abs(computed - claimed)
    if tol_abs is not None:
        ok = diff <= tol_abs
    else:
        ok = diff <= tol_rel * max(abs(claimed), 1e-12)
    line = f"{name}: claimed {claimed:g} vs computed {computed:.4g} (diff {diff:.3g})"
    (PASSES if ok else FLAGS).append(line)
    print(("  ok   " if ok else "  FLAG ") + line)
    return ok


def phi(x: float) -> float:
    """Standard normal CDF via math.erf (no scipy)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def ols(x, y):
    x = np.asarray(x, float)
    y = np.asarray(y, float)
    m = ~(np.isnan(x) | np.isnan(y))
    x, y = x[m], y[m]
    X = np.column_stack([np.ones_like(x), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    yhat = X @ b
    r = float(np.corrcoef(x, y)[0, 1])
    return dict(n=len(x), a=float(b[0]), b=float(b[1]), r=r,
                sd=float(np.std(y - yhat, ddof=2)), ybar=float(y.mean()),
                xbar=float(x.mean()))


def ols2(x1, x2, y):
    x1 = np.asarray(x1, float)
    x2 = np.asarray(x2, float)
    y = np.asarray(y, float)
    m = ~(np.isnan(x1) | np.isnan(x2) | np.isnan(y))
    X = np.column_stack([np.ones(m.sum()), x1[m], x2[m]])
    b, *_ = np.linalg.lstsq(X, y[m], rcond=None)
    yhat = X @ b
    r2 = 1 - np.var(y[m] - yhat) / np.var(y[m])
    return dict(a=float(b[0]), b=float(b[1]), c=float(b[2]), r2=float(r2),
                sd=float(np.std(y[m] - yhat, ddof=3)))


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


# ===========================================================================
# PART A — algebra
# ===========================================================================
def part_a() -> dict:
    out = {}
    section("A1  Implied-total identity")
    for T, S in [(47.0, -3.0), (51.5, -6.5), (44.0, 0.0), (38.5, -13.5)]:
        fav = (T - S) / 2
        dog = (T + S) / 2
        assert abs(fav + dog - T) < 1e-12 and abs(fav - dog - abs(S)) < 1e-12
        assert abs(implied_team_total(T, S) - fav) < 1e-12
        f2, d2 = implied_totals(T, S)
        assert abs(f2 - fav) < 1e-12 and abs(d2 - dog) < 1e-12
        print(f"  T={T:5.1f} S={S:+5.1f}: fav {fav:5.2f} dog {dog:5.2f} "
              f"sum {fav + dog:5.2f} diff {fav - dog:4.2f}")
    h, a = implied_totals_from_nflverse(47.0, 3.0)
    print(f"  nflverse total 47 / spread_line +3 -> home {h}, away {a} "
          f"(home-positive convention)")
    check("A1 fav-3/47 favorite implied", 25.0, implied_team_total(47, -3), tol_abs=1e-9)
    check("A1 fav-3/47 dog implied", 22.0, implied_team_total(47, 3), tol_abs=1e-9)
    # Sharp Football example: Rams -3, total 50 -> 26.5
    check("A1 Sharp example Rams -3 / 50", 26.5, implied_team_total(50, -3), tol_abs=1e-9)

    section("A2  Scoring identity per season (from the claimed anchors)")
    c = CLAIM
    for s in SEASONS:
        td_all = c["off_td"][s] + c["nonoff_td"][s]
        recon = 6 * td_all + c["xp"][s] + 2 * c["two_pt"][s] + 3 * c["fgm"][s] + 2 * c["safety"][s]
        per_off = (6 * c["off_td"][s] + c["xp"][s] + 2 * c["two_pt"][s]) / c["off_td"][s]
        per_all = (6 * td_all + c["xp"][s] + 2 * c["two_pt"][s]) / td_all
        print(f"  {s}: recon {recon:.3f} vs claimed pts {c['pts'][s]:.2f}; "
              f"pass+rush TD {c['pass_td'][s] + c['rush_td'][s]:.3f} vs offTD {c['off_td'][s]:.3f}; "
              f"pts/offTD {per_off:.3f}; pts/allTD {per_all:.3f}")
        check(f"A2 {s} scoring identity reproduces pts/g", c["pts"][s], recon, tol_abs=0.05)
        check(f"A2 {s} pass+rush TD == offTD", c["off_td"][s],
              c["pass_td"][s] + c["rush_td"][s], tol_abs=0.005)
    pool = {k: np.mean([c[k][s] for s in SEASONS]) for k in
            ("pts", "off_td", "nonoff_td", "fgm", "xp", "two_pt", "safety")}
    share_off = (6 * pool["off_td"] + pool["xp"] + 2 * pool["two_pt"]) / pool["pts"]
    share_fg = 3 * pool["fgm"] / pool["pts"]
    share_non = 6 * pool["nonoff_td"] / pool["pts"]
    share_saf = 2 * pool["safety"] / pool["pts"]
    print(f"  pooled shares: offTD incl conv {share_off:.3%}, FG {share_fg:.3%}, "
          f"def/ST TD {share_non:.3%}, safety {share_saf:.3%}, "
          f"sum {share_off + share_fg + share_non + share_saf:.3%}")
    check("A2 offTD share", 0.736, share_off, tol_abs=0.003)
    check("A2 FG share", 0.226, share_fg, tol_abs=0.003)
    check("A2 def/ST TD share", 0.035, share_non, tol_abs=0.003)
    check("A2 safety share (~0.2%)", 0.002, share_saf, tol_abs=0.001)
    per_off_pool = (6 * pool["off_td"] + pool["xp"] + 2 * pool["two_pt"]) / pool["off_td"]
    check("A2 pooled pts per offTD incl conversions", 7.00, per_off_pool, tol_abs=0.02)
    # NOTE: the offTD basis credits ALL XPs (incl. those after def/ST TDs) to
    # offensive TDs; the all-TD basis is the physically correct one.
    per_all_pool = (6 * (pool["off_td"] + pool["nonoff_td"]) + pool["xp"] + 2 * pool["two_pt"]) / (pool["off_td"] + pool["nonoff_td"])
    print(f"  pooled pts per TD, all-TD basis: {per_all_pool:.3f} (claimed 6.94-6.97)")
    out["pool"] = pool

    section("A3  Regression internal consistency (line passes through the means)")
    I_bar = c["game"]["mean_implied"]
    means = {"pts": 22.56, "off_td": 2.371, "pass_td": 1.455, "rush_td": 0.914,
             "fgm": 1.703, "pass_att": 32.82, "targets": 31.31, "rush_att": 26.12,
             "ppr_per_tgt": 1.716, "rec_ppr": 53.12, "rush_pts": 17.21,
             "pts_per_carry": 0.642}
    for k, (a, b, r, sd) in c["ols_I"].items():
        at_mean = a + b * I_bar
        check(f"A3 {k}: a + b*mean(I) == mean({k})", means[k], at_mean, tol_rel=0.01)
    b_pts = c["ols_I"]["pts"][1]
    b_from_parts = 7.0 * c["ols_I"]["off_td"][1] + 3 * c["ols_I"]["fgm"][1]
    print(f"  d(pts)/dI = {b_pts:.3f} vs 7*offTD' + 3*FGM' = {b_from_parts:.3f} "
          f"(gap {b_pts - b_from_parts:+.3f} = def/ST TD + safety + XP-mix slope)")
    check("A3 pts slope vs 7*offTD + 3*FGM slopes", b_pts, b_from_parts, tol_rel=0.03)
    check("A3 passTD' + rushTD' == offTD'", c["ols_I"]["off_td"][1],
          c["ols_I"]["pass_td"][1] + c["ols_I"]["rush_td"][1], tol_rel=0.01)
    # (M,T) -> (I_own, I_opp) conversion: T = Io + Ip, M = Io - Ip
    for k, claimed_io_ip in [("pass_att", (0.116, 0.253)), ("rush_att", (0.213, -0.365)),
                             ("plays", (0.231, -0.111))]:
        _, bm, ct, _ = c["ols_MT"][k]
        io, ip = bm + ct, ct - bm
        print(f"  {k}: b_M={bm:+.3f} c_T={ct:+.3f} -> b_own={io:+.3f} c_opp={ip:+.3f} "
              f"(claimed {claimed_io_ip[0]:+.3f}, {claimed_io_ip[1]:+.3f})")
        check(f"A3 {k} own-implied coefficient", claimed_io_ip[0], io, tol_abs=0.002)
        check(f"A3 {k} opp-implied coefficient", claimed_io_ip[1], ip, tol_abs=0.002)
    # slope decompositions (delta method)
    eff_mean, tgt_mean = means["ppr_per_tgt"], means["targets"]
    vol_part = eff_mean * c["ols_I"]["targets"][1]
    eff_part = tgt_mean * c["ols_I"]["ppr_per_tgt"][1]
    print(f"  receiving: volume part {vol_part:.3f} + efficiency part {eff_part:.3f} = "
          f"{vol_part + eff_part:.3f} vs direct OLS {c['ols_I']['rec_ppr'][1]:.3f}; "
          f"efficiency share {eff_part / (vol_part + eff_part):.1%}")
    check("A3 receiving efficiency share ~91%", 0.91, eff_part / (vol_part + eff_part), tol_abs=0.02)
    vol_r = means["pts_per_carry"] * c["ols_I"]["rush_att"][1]
    eff_r = means["rush_att"] * c["ols_I"]["pts_per_carry"][1]
    print(f"  rushing: volume {vol_r:.3f} + efficiency {eff_r:.3f} = {vol_r + eff_r:.3f} "
          f"vs direct {c['ols_I']['rush_pts'][1]:.3f}; efficiency share {eff_r / (vol_r + eff_r):.1%}")
    check("A3 rushing efficiency share ~74%", 0.74, eff_r / (vol_r + eff_r), tol_abs=0.02)
    # multipliers
    mult_pooled = (0.791 + 0.0420 * np.array([19.0, 22.0, 25.0])) / 1.716
    print(f"  pooled multiplier at I=19/22/25: {mult_pooled.round(3)} (claimed 0.93/1.00/1.07)")
    check("A3 pooled multiplier intercept 0.461", 0.461, 0.791 / 1.716, tol_abs=0.001)
    check("A3 pooled multiplier slope 0.0245", 0.0245, 0.0420 / 1.716, tol_abs=0.0002)
    check("A3 within multiplier slope 0.0174", 0.0174, 0.0298 / 1.716, tol_abs=0.0002)

    section("A4  Dimension check of every formula")
    dims = [
        ("I = (T - S)/2", "pts = (pts - pts)/1", "ok"),
        ("pts = a + b*I", "pts = pts + (pts/pt)*pts", "b dimensionless (1.112)"),
        ("offTD = a + b*I", "TD = TD + (TD/pt)*pts", "0.1505 TD per implied point"),
        ("FGM = a + b*I", "FG = FG + (FG/pt)*pts", "0.0139 FG per point"),
        ("attempts(S,T) = a + b*S + c*T", "att = att + (att/pt)*pts + (att/pt)*pts", "ok"),
        ("targets = 0.954*attempts", "tgt = (tgt/att)*att", "ok"),
        ("PPR/tgt = catch + 0.1*Y/T + 6*TD/T", "(rec + 0.1*yd + 6*TD)/tgt", "scoring weights carry units"),
        ("PPR/tgt = 0.791 + 0.0420*I", "PPR/tgt + (PPR/tgt/pt)*pts", "ok"),
        ("mult = (0.791 + 0.0420*I)/1.716", "(PPR/tgt)/(PPR/tgt)", "dimensionless"),
        ("mult = 1 + 0.0174*(I - I_base)", "1 + (1/pt)*pts", "dimensionless"),
        ("K pts = 3*FGM + 0.86*TD_all", "pts = (pts/FG)*FG + (XP/TD)*TD*(1 pt/XP)", "assumes 1 pt per XP, no FG distance bonus"),
        ("WR PPR = share * targets * PPR/tgt", "PPR = 1 * tgt * PPR/tgt", "ok"),
        ("SD_eff = sqrt(n_tgt) * SD(PPR/tgt)", "PPR = sqrt(tgt) * PPR/tgt (per target)", "iid targets assumption"),
    ]
    for f, u, note in dims:
        print(f"  {f:40s} {u:52s} {note}")
    PASSES.append("A4 all formulas dimensionally consistent (see table)")

    section("A5  Volume functions at spread 0 vs league averages")
    T_bar = 44.06
    att0 = 24.70 + 0.068 * 0 + 0.184 * T_bar
    rush0 = 30.31 - 0.289 * 0 - 0.076 * T_bar
    plays0 = 59.60 + 0.171 * 0 + 0.060 * T_bar
    pooled_att = np.mean(list(c["pass_att"].values()))
    pooled_rush = np.mean(list(c["rush_att_all"].values()))
    pooled_plays = np.mean(list(c["plays"].values()))
    pooled_sacks = np.mean(list(c["sacks"].values()))
    print(f"  attempts(0, {T_bar}) = {att0:.2f} vs pooled pass_att {pooled_att:.2f}")
    print(f"  rushes(0, {T_bar})   = {rush0:.2f} vs pooled rush_att {pooled_rush:.2f}")
    print(f"  plays(0, {T_bar})    = {plays0:.2f} vs pooled plays {pooled_plays:.2f}")
    check("A5 attempts(0,Tbar) == league pass_att", pooled_att, att0, tol_rel=0.01)
    check("A5 rushes(0,Tbar) == league rush_att", pooled_rush, rush0, tol_rel=0.01)
    check("A5 plays(0,Tbar) == league plays", pooled_plays, plays0, tol_rel=0.01)
    pass_rate = np.mean(list(c["pass_rate"].values()))
    neutral = np.mean(list(c["neutral_db_rate"].values()))
    db_rate = np.mean(list(c["dropback_rate"].values()))
    print(f"  plays x pass_rate (att share {pass_rate:.3f}) = {plays0 * pass_rate:.2f} "
          f"!= attempts {att0:.2f} because 'plays' includes {pooled_sacks:.2f} sacks;")
    print(f"  (plays - sacks) x pass_rate = {(plays0 - pooled_sacks) * pass_rate:.2f}  <- reconciles")
    check("A5 (plays - sacks) x attempt-share == attempts", att0,
          (plays0 - pooled_sacks) * pass_rate, tol_rel=0.01)
    print(f"  plays x NEUTRAL dropback rate {neutral:.3f} = {plays0 * neutral:.2f} dropbacks "
          f"vs actual dropbacks {np.mean(list(c['dropbacks'].values())):.2f}: the neutral rate is "
          f"NOT a whole-game rate (whole-game dropback rate {db_rate:.3f}).")
    check("A5 plays x overall dropback rate == dropbacks",
          np.mean(list(c["dropbacks"].values())), plays0 * db_rate, tol_rel=0.01)
    out["T_bar"] = T_bar

    section("A6  Monte-Carlo league average over spread ~ N(0, 5.5) (seeded)")
    rng = np.random.default_rng(SEED)
    n = 200_000
    S = rng.normal(0.0, 5.5, n)          # betting-convention spread of a random team
    T = rng.normal(T_bar, 3.9, n)        # closing totals; SD ~3.9 (checked in B1)
    att = 24.70 + 0.068 * S + 0.184 * T
    rush = 30.31 - 0.289 * S - 0.076 * T
    plays = 59.60 - 0.171 * S + 0.060 * T
    I_mc = (T - S) / 2
    print(f"  E[attempts] {att.mean():.2f} (SD {att.std():.2f}) | E[rushes] {rush.mean():.2f} "
          f"(SD {rush.std():.2f}) | E[plays] {plays.mean():.2f} | E|S| {np.abs(S).mean():.2f} "
          f"| SD(I) {I_mc.std():.2f}")
    check("A6 MC mean attempts within league range", pooled_att, att.mean(), tol_rel=0.01)
    check("A6 MC mean rushes within league range", pooled_rush, rush.mean(), tol_rel=0.01)
    print("  (linear functions -> the league average equals the value at the mean line "
          "by construction; the MC only confirms the sign convention did not leak.)")
    sd_needed = math.sqrt(4 * 3.7 ** 2 - 5.5 ** 2)
    print(f"  SD(I)=3.7 with SD(S)=5.5 independent implies SD(T) = {sd_needed:.2f}; "
          f"E|S| for N(0,5.5) = {5.5 * math.sqrt(2 / math.pi):.2f} vs claimed mean|spread| 5.1 "
          f"-> data spread SD is nearer {5.1 / math.sqrt(2 / math.pi):.1f} if normal")
    # second scenario: empirical closing-line SDs (B1 measures spread SD 5.86, total SD 4.27)
    S2 = rng.normal(0.0, 5.86, n)
    T2 = rng.normal(T_bar, 4.27, n)
    att2 = 24.70 + 0.068 * S2 + 0.184 * T2
    rush2 = 30.31 - 0.289 * S2 - 0.076 * T2
    print(f"  empirical-SD scenario: E[attempts] {att2.mean():.2f} E[rushes] {rush2.mean():.2f} "
          f"SD(I) {((T2 - S2) / 2).std():.2f} (data 3.71) E|S| {np.abs(S2).mean():.2f} (data 5.08)")
    # implied-total tail probabilities under the claimed N(22.03, 3.7)
    p24 = 1 - phi((24 - 22.03) / 3.7)
    p27 = 1 - phi((27 - 22.03) / 3.7)
    print(f"  N(22.03,3.7): P(I>=24) = {p24:.3f}, P(I>=27) = {p27:.3f} "
          f"(4for4 2013-17: 0.357 / 0.124 — a higher-scoring era, direction only)")
    for q, z in [(5, -1.645), (25, -0.674), (50, 0.0), (75, 0.674), (95, 1.645)]:
        print(f"    normal {q}th pct = {22.03 + z * 3.7:.1f}", end="")
    print("   (claimed 16 / 19.5 / 22 / 24.8 / 28)")
    out["p24_norm"], out["p27_norm"] = p24, p27

    section("A7  Worked example recomputed exactly (fav -3 / total 47, WR 24% share)")
    T, S = 47.0, -3.0
    I = (T - S) / 2
    passTD = -0.511 + 0.0892 * I
    rushTD = -0.430 + 0.0610 * I
    offTD = -0.945 + 0.1505 * I
    fgm = 1.40 + 0.0139 * I
    pts = -1.93 + 1.112 * I
    recon = 7.0 * offTD + 3 * fgm + 0.9
    att = 24.70 + 0.068 * S + 0.184 * T
    tg = 0.954 * att
    tg_direct = 22.49 - 0.049 * (-S) + 0.200 * T
    rush = 30.31 - 0.289 * S - 0.076 * T
    plays = 59.60 + 0.171 * (-S) + 0.060 * T
    wr_t = 0.24 * tg
    rec, yds, td = wr_t * 0.630, wr_t * 7.93, wr_t * 0.0493
    ppr_flat = rec + 0.1 * yds + 6 * td
    m_within = 1 + 0.0174 * (I - 22.0)
    m_pooled = (0.791 + 0.0420 * I) / 1.716
    ppr_within, ppr_pooled = ppr_flat * m_within, ppr_flat * m_pooled
    print(f"  I={I:.1f} passTD {passTD:.3f} rushTD {rushTD:.3f} offTD {offTD:.3f} FGM {fgm:.3f} "
          f"pts {pts:.2f} (recon 7*offTD+3*FGM+0.9 = {recon:.2f})")
    print(f"  pass_att {att:.2f} targets {tg:.2f} (direct {tg_direct:.2f}) rush_att {rush:.2f} plays {plays:.2f}")
    print(f"  WR targets {wr_t:.3f} rec {rec:.3f} yds {yds:.2f} TD {td:.4f} -> PPR flat {ppr_flat:.2f}")
    print(f"  within mult {m_within:.4f} -> {ppr_within:.2f}; pooled mult {m_pooled:.4f} -> {ppr_pooled:.2f}")
    for name, claimed, comp in [("I", 25.0, I), ("passTD", 1.72, passTD), ("rushTD", 1.10, rushTD),
                                ("offTD", 2.82, offTD), ("FGM", 1.75, fgm), ("pts", 25.9, pts),
                                ("pass_att", 33.1, att), ("targets", 31.6, tg), ("rush_att", 27.6, rush),
                                ("plays", 62.9, plays), ("WR targets", 7.58, wr_t), ("rec", 4.78, rec),
                                ("yds", 60.1, yds), ("TD", 0.374, td), ("PPR flat", 13.0, ppr_flat),
                                ("x within", 1.052, m_within), ("PPR within", 13.7, ppr_within),
                                ("x pooled", 1.073, m_pooled), ("PPR pooled", 14.0, ppr_pooled)]:
        check(f"A7 example {name}", claimed, comp, tol_rel=0.02)
    # uncertainty arithmetic as stated, then the missing binomial share term
    rush_nokneel = 29.68 - 0.250 * S - 0.081 * T   # excl-kneel refit (B3) for comparison
    print(f"  rush_att excl kneel-downs at the same line: {rush_nokneel:.2f} vs 27.6 stated "
          f"(neighbourhood empirical 26.5)")
    sd_eff = math.sqrt(wr_t) * 1.09
    sd_vol = 0.24 * 7.4 * 1.72
    sd_share = math.sqrt(tg * 0.24 * 0.76) * 1.72
    sd_stated = math.sqrt(sd_eff ** 2 + sd_vol ** 2)
    sd_full = math.sqrt(sd_eff ** 2 + sd_vol ** 2 + sd_share ** 2)
    print(f"  SD terms: efficiency {sd_eff:.2f}, team-volume {sd_vol:.2f} -> combined {sd_stated:.2f} "
          f"(claimed 4.5-5); adding binomial target-share noise {sd_share:.2f} -> {sd_full:.2f}")
    out["example"] = dict(I=I, ppr_flat=ppr_flat, ppr_within=ppr_within, ppr_pooled=ppr_pooled,
                          sd_stated=sd_stated, sd_full=sd_full, wr_t=wr_t, tg=tg)

    section("A8  Kicker arithmetic, second-half slope, half-PPR shifts")
    xp_per_td = pool["xp"] / (pool["off_td"] + pool["nonoff_td"])
    k25 = 3 * (1.40 + 0.0139 * 25) + xp_per_td * ((-0.945 + 0.1505 * 25) + pool["nonoff_td"])
    k19 = 3 * (1.40 + 0.0139 * 19) + xp_per_td * ((-0.945 + 0.1505 * 19) + pool["nonoff_td"])
    print(f"  XP per TD_all = {xp_per_td:.3f}; K pts at I=25: {k25:.2f}, I=19: {k19:.2f}")
    check("A8 XP/TD ratio 0.86", 0.86, xp_per_td, tol_abs=0.01)
    check("A8 kicker pts at I=25", 7.8, k25, tol_abs=0.1)
    check("A8 kicker pts at I=19", 6.7, k19, tol_abs=0.1)
    x = np.array([-4.5, 0.0, 4.5, 12.5])
    y = np.array([0.644, 0.592, 0.497, 0.444])
    sl = ols(x, y)["b"]
    print(f"  second-half dropback slope over bucket midpoints -8..+16: {sl * 100:.2f} pp/pt "
          f"(claimed ~-0.9 pp/pt; two-point form quoted -1.2)")
    check("A8 H2 slope ~ -0.9 pp/pt (bucket-midpoint OLS)", -0.009, sl, tol_abs=0.0015)
    for pos, (catch, yt, tdt, _, pprt) in c["pos_eff"].items():
        full = catch + 0.1 * yt + 6 * tdt
        half = full - 0.5 * catch
        print(f"  {pos}: full-PPR/tgt {full:.3f} (claimed {pprt}), half-PPR {half:.3f}")
        check(f"A8 {pos} PPR/target from components", pprt, full, tol_abs=0.006)
    y_c, td_c, pts_c = c["rb_carry"]
    check("A8 RB pts/carry from components", pts_c, 0.1 * y_c + 6 * td_c, tol_abs=0.003)
    ya, tda, inta, ppa = c["qb_pass"]
    check("A8 QB pts/att from components", ppa, 0.04 * ya + 4 * tda - 2 * inta, tol_abs=0.003)
    check("A8 half-PPR WR 1.40", 1.40, 1.719 - 0.5 * 0.630, tol_abs=0.01)
    check("A8 half-PPR TE 1.39", 1.39, 1.753 - 0.5 * 0.720, tol_abs=0.01)
    check("A8 half-PPR RB 1.16", 1.16, 1.545 - 0.5 * 0.784, tol_abs=0.01)
    # team-week PPR slopes must add across positions
    pos_slopes = sum(c["team_ppr"][p][1] for p in ("QB", "RB", "WR", "TE"))
    check("A8 positional PPR slopes sum to ALL slope", c["team_ppr"]["ALL"][1], pos_slopes, tol_abs=0.02)
    return out


# ===========================================================================
# PART B — data refit
# ===========================================================================
def fl(s: str) -> float:
    try:
        return float(s)
    except ValueError:
        return float("nan")


def dbucket(d: float) -> str:
    if d <= -17:
        return "<=-17"
    if d <= -9:
        return "-16..-9"
    if d <= -1:
        return "-8..-1"
    if d == 0:
        return "0"
    if d <= 8:
        return "1..8"
    if d <= 16:
        return "9..16"
    return ">=17"


def load_pbp():
    games: dict = {}
    tg: dict = defaultdict(lambda: defaultdict(float))
    pr_diff = defaultdict(lambda: [0, 0])      # (half, bucket) -> [dropbacks, plays]
    pr_neutral = defaultdict(lambda: [0, 0])   # season
    pr_all = defaultdict(lambda: [0, 0])
    h2 = [0, 0.0, 0.0, 0.0, 0.0]               # n, Sd, Sdd, Sdb, Sd*db over -8..16, H2
    need = ["game_id", "season_type", "week", "posteam", "defteam", "home_team", "away_team",
            "spread_line", "total_line", "home_score", "away_score", "play_type",
            "complete_pass", "incomplete_pass", "interception", "sack", "rush_attempt",
            "qb_scramble", "qb_kneel", "pass", "score_differential", "qtr", "down",
            "touchdown", "pass_touchdown", "rush_touchdown", "return_touchdown", "td_team",
            "field_goal_result", "extra_point_result", "two_point_conv_result",
            "two_point_attempt", "safety", "wp", "yards_gained", "receiver_player_id"]
    for season in SEASONS:
        fn = DATA / f"play_by_play_{season}.csv.gz"
        with gzip.open(fn, "rt", encoding="utf-8", newline="") as fh:
            rd = csv.reader(fh)
            hdr = next(rd)
            ix = {c: i for i, c in enumerate(hdr)}
            missing = [c for c in need if c not in ix]
            if missing:
                raise SystemExit(f"missing pbp columns {missing}")
            (i_gid, i_st, i_wk, i_pos, i_def, i_home, i_away, i_spr, i_tot, i_hs, i_as, i_pt,
             i_cp, i_ip, i_int, i_sk, i_ra, i_scr, i_kn, i_pass, i_sd, i_qtr, i_dn, i_td, i_ptd,
             i_rtd, i_rettd, i_tdteam, i_fg, i_xp, i_2pc, i_2pa, i_saf, i_wp, i_yg, i_rcv) = \
                [ix[c] for c in need]
            for row in rd:
                if row[i_st] != "REG":
                    continue
                gid = row[i_gid]
                if gid not in games:
                    games[gid] = dict(season=season, week=int(row[i_wk]), home=row[i_home],
                                      away=row[i_away], spread=fl(row[i_spr]), total=fl(row[i_tot]),
                                      hs=fl(row[i_hs]), as_=fl(row[i_as]))
                pos = row[i_pos]
                if pos == "":
                    continue
                pt = row[i_pt]
                t = tg[(gid, pos)]
                if row[i_td] == "1":
                    tdteam = row[i_tdteam]
                    if tdteam == pos and row[i_rettd] != "1" and pt in ("pass", "run"):
                        t["off_td"] += 1
                    else:
                        tg[(gid, tdteam)]["nonoff_td"] += 1
                if row[i_fg] == "made":
                    t["fgm"] += 1
                if pt == "field_goal":
                    t["fga"] += 1
                if row[i_xp] == "good":
                    t["xp"] += 1
                if row[i_2pc] == "success":
                    t["two_pt"] += 1
                if row[i_saf] == "1":
                    tg[(gid, row[i_def])]["safety"] += 1
                if pt not in ("pass", "run", "qb_kneel", "qb_spike"):
                    continue
                cp = row[i_cp] == "1"
                pa = cp + (row[i_ip] == "1") + (row[i_int] == "1")
                sk = row[i_sk] == "1"
                ra = row[i_ra] == "1"
                db = row[i_pass] == "1"
                scr = row[i_scr] == "1"
                kn = row[i_kn] == "1"
                # researcher's analyze.py definition: 2-pt tries included
                t["plays_incl2pt"] += pa + sk + ra
                t["dropbacks_incl2pt"] += db
                t["rush_all_incl2pt"] += ra
                if row[i_2pa] == "1":
                    continue
                t["pass_att"] += pa
                t["sacks"] += sk
                t["rush_att_all"] += ra
                t["dropbacks"] += db
                t["scrambles"] += scr
                t["plays"] += pa + sk + ra
                if ra and not kn and not scr and pt == "run":
                    t["rush_designed"] += 1
                if row[i_ptd] == "1":
                    t["pass_td"] += 1
                if row[i_rtd] == "1":
                    t["rush_td"] += 1
                if pt == "pass" and row[i_rcv] != "":
                    t["targets"] += 1
                if cp:
                    t["rec"] += 1
                    t["rec_yds"] += fl(row[i_yg])
                if ra and not kn and pt == "run":
                    t["rush_att"] += 1
                    t["rush_yds"] += fl(row[i_yg])
                if db or (ra and not db):
                    d = fl(row[i_sd])
                    if not math.isnan(d):
                        qtr = int(fl(row[i_qtr]))
                        half = "H1" if qtr <= 2 else "H2"
                        k = (half, dbucket(d))
                        pr_diff[k][0] += db
                        pr_diff[k][1] += 1
                        pr_all[season][0] += db
                        pr_all[season][1] += 1
                        if half == "H2" and -8 <= d <= 16:
                            h2[0] += 1
                            h2[1] += d
                            h2[2] += d * d
                            h2[3] += db
                            h2[4] += d * db
                        dn = fl(row[i_dn])
                        wp = fl(row[i_wp])
                        if abs(d) <= 8 and qtr <= 3 and dn in (1.0, 2.0) and not math.isnan(wp) \
                                and 0.2 <= wp <= 0.8:
                            pr_neutral[season][0] += db
                            pr_neutral[season][1] += 1
        print(f"  loaded pbp {season}: {sum(1 for g in games.values() if g['season'] == season)} games",
              flush=True)
    return games, tg, pr_diff, pr_neutral, pr_all, h2


def build_teamgames(games, tg):
    keys = ["plays", "pass_att", "dropbacks", "sacks", "rush_att_all", "rush_att", "scrambles",
            "off_td", "pass_td", "rush_td", "nonoff_td", "fgm", "fga", "xp", "two_pt", "safety",
            "targets", "rec", "rec_yds", "rush_yds", "plays_incl2pt", "dropbacks_incl2pt",
            "rush_all_incl2pt", "rush_designed"]
    cols = defaultdict(list)
    for (gid, team), t in tg.items():
        g = games.get(gid)
        if g is None or team not in (g["home"], g["away"]):
            continue
        if math.isnan(g["spread"]) or math.isnan(g["total"]):
            continue
        home = team == g["home"]
        M = g["spread"] if home else -g["spread"]
        cols["season"].append(g["season"])
        cols["week"].append(g["week"])
        cols["team"].append(team)
        cols["M"].append(M)
        cols["T"].append(g["total"])
        cols["I"].append((g["total"] + M) / 2)
        cols["pts"].append(g["hs"] if home else g["as_"])
        for k in keys:
            cols[k].append(t.get(k, 0.0))
    A = {k: np.array(v) if k == "team" else np.array(v, float) for k, v in cols.items()}
    A["rec_ppr"] = A["rec"] + 0.1 * A["rec_yds"] + 6 * A["pass_td"]
    A["rush_pts"] = 0.1 * A["rush_yds"] + 6 * A["rush_td"]
    with np.errstate(divide="ignore", invalid="ignore"):
        A["ppr_per_tgt"] = np.where(A["targets"] > 0, A["rec_ppr"] / A["targets"], np.nan)
        A["pts_per_carry"] = np.where(A["rush_att"] > 0, A["rush_pts"] / A["rush_att"], np.nan)
    A["k_pts"] = 3 * A["fgm"] + A["xp"]
    return A


def loso(A, key, x="I"):
    """Leave-one-season-out: fit y = a + b*x on two seasons, score the third
    against a mean-only baseline. Returns rows (season, n, rmse_model,
    rmse_base, skill, r_oos) plus pooled."""
    rows = []
    allp, ally, allb = [], [], []
    for s in SEASONS:
        tr = A["season"] != s
        te = ~tr
        o = ols(A[x][tr], A[key][tr])
        pred = o["a"] + o["b"] * A[x][te]
        y = A[key][te]
        m = ~np.isnan(y)
        pred, y = pred[m], y[m]
        base = o["ybar"]
        mse_m = float(np.mean((y - pred) ** 2))
        mse_b = float(np.mean((y - base) ** 2))
        r = float(np.corrcoef(pred, y)[0, 1]) if pred.std() > 0 else float("nan")
        rows.append((s, len(y), math.sqrt(mse_m), math.sqrt(mse_b), 1 - mse_m / mse_b, r, o["b"]))
        allp.append(pred)
        ally.append(y)
        allb.append(np.full_like(y, base))
    P, Y, B = np.concatenate(allp), np.concatenate(ally), np.concatenate(allb)
    mse_m = float(np.mean((Y - P) ** 2))
    mse_b = float(np.mean((Y - B) ** 2))
    pooled = ("pooled", len(Y), math.sqrt(mse_m), math.sqrt(mse_b), 1 - mse_m / mse_b,
              float(np.corrcoef(P, Y)[0, 1]), float("nan"))
    return rows, pooled


def load_stats():
    pos_agg = defaultdict(lambda: defaultdict(float))
    team_week_pos = defaultdict(float)   # (season, week, team, pos) -> ppr
    wr_weeks = defaultdict(list)         # (season, player_id) -> list of weekly ppr (WR)
    wr_targets = defaultdict(float)
    for season in SEASONS:
        fn = DATA / f"stats_player_week_{season}.csv"
        with open(fn, "r", encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            for r in rd:
                if r.get("season_type", "REG") != "REG":
                    continue
                pos = r.get("position") or r.get("position_group")
                if pos not in ("QB", "RB", "WR", "TE"):
                    continue
                tm = r.get("team") or r.get("recent_team")
                wk = int(r["week"])
                vals = {k: fl(r.get(src, "")) for k, src in [
                    ("targets", "targets"), ("rec", "receptions"), ("rec_yds", "receiving_yards"),
                    ("rec_td", "receiving_tds"), ("air", "receiving_air_yards"),
                    ("carries", "carries"), ("rush_yds", "rushing_yards"), ("rush_td", "rushing_tds"),
                    ("pass_att", "attempts"), ("pass_yds", "passing_yards"), ("pass_td", "passing_tds"),
                    ("int", "passing_interceptions"), ("ppr", "fantasy_points_ppr")]}
                for k, v in vals.items():
                    if not math.isnan(v):
                        pos_agg[pos][k] += v
                if not math.isnan(vals["ppr"]):
                    team_week_pos[(season, wk, tm, pos)] += vals["ppr"]
                    if pos == "WR":
                        wr_weeks[(season, r["player_id"])].append(vals["ppr"])
                        if not math.isnan(vals["targets"]):
                            wr_targets[(season, r["player_id"])] += vals["targets"]
        print(f"  loaded stats_player_week {season}", flush=True)
    return pos_agg, team_week_pos, wr_weeks, wr_targets


def part_b(a_out: dict) -> None:
    section("B0  Loading nflverse files")
    games, tg, pr_diff, pr_neutral, pr_all, h2 = load_pbp()
    A = build_teamgames(games, tg)
    c = CLAIM
    n_tg = len(A["I"])
    print(f"  team-games with lines: {n_tg} (claimed 1632); games: {len(games)}")
    check("B0 team-game count", 1632, n_tg, tol_abs=0)

    section("B1  Game level (closing lines)")
    G = defaultdict(list)
    for g in games.values():
        if math.isnan(g["spread"]) or math.isnan(g["total"]):
            continue
        G["season"].append(g["season"])
        G["spread"].append(g["spread"])
        G["total"].append(g["total"])
        G["result"].append(g["hs"] - g["as_"])
        G["tot"].append(g["hs"] + g["as_"])
    G = {k: np.array(v, float) for k, v in G.items()}
    corr_rs = float(np.corrcoef(G["result"], G["spread"])[0, 1])
    sd_rs = float(np.std(G["result"] - G["spread"], ddof=1))
    corr_tt = float(np.corrcoef(G["tot"], G["total"])[0, 1])
    sd_tt = float(np.std(G["tot"] - G["total"], ddof=1))
    gap = float(np.mean(G["tot"] - G["total"]))
    print(f"  n={len(G['spread'])} corr(result, spread_line) {corr_rs:.3f}; mean(result - spread) "
          f"{np.mean(G['result'] - G['spread']):+.2f}; SD(result - spread) {sd_rs:.2f}")
    print(f"  corr(total, total_line) {corr_tt:.3f}; SD(total - line) {sd_tt:.2f}; mean gap {gap:+.2f}")
    print(f"  spread_line: mean {G['spread'].mean():+.2f}, SD {G['spread'].std(ddof=1):.2f}, "
          f"mean|spread| {np.abs(G['spread']).mean():.2f}; total_line mean {G['total'].mean():.2f} "
          f"SD {G['total'].std(ddof=1):.2f}")
    check("B1 corr(result, spread)", c["game"]["corr_result_spread"], corr_rs, tol_abs=0.005)
    check("B1 SD(result - spread)", c["game"]["sd_result_minus_spread"], sd_rs, tol_abs=0.05)
    check("B1 corr(total, total_line)", c["game"]["corr_total_line"], corr_tt, tol_abs=0.005)
    check("B1 SD(total - total_line)", c["game"]["sd_total_minus_line"], sd_tt, tol_abs=0.05)
    check("B1 mean total gap", c["game"]["mean_total_gap"], gap, tol_abs=0.05)
    check("B1 mean |spread|", c["game"]["mean_abs_spread"], float(np.abs(G["spread"]).mean()), tol_abs=0.1)
    for s in SEASONS:
        m = G["season"] == s
        print(f"    {s}: SD(result-spread) {np.std(G['result'][m] - G['spread'][m], ddof=1):.2f} "
              f"SD(total-line) {np.std(G['tot'][m] - G['total'][m], ddof=1):.2f} "
              f"mean total_line {G['total'][m].mean():.2f}")
    I = A["I"]
    pct = np.percentile(I, [5, 25, 50, 75, 95])
    print(f"  implied total: mean {I.mean():.2f} SD {I.std(ddof=1):.2f} pct5/25/50/75/95 {pct.round(1)}; "
          f"P(I>=24) {np.mean(I >= 24):.3f} P(I>=27) {np.mean(I >= 27):.3f} "
          f"(normal approx {a_out['p24_norm']:.3f} / {a_out['p27_norm']:.3f})")
    check("B1 mean implied total", c["game"]["mean_implied"], float(I.mean()), tol_abs=0.05)
    check("B1 SD implied total", c["game"]["sd_implied"], float(I.std(ddof=1)), tol_abs=0.1)

    section("B2  Team-game anchors per season")
    seas = A["season"]
    for s in SEASONS:
        m = seas == s
        n = m.sum()
        mean = lambda k: float(A[k][m].mean())  # noqa: E731
        recon = 6 * (A["off_td"][m] + A["nonoff_td"][m]) + A["xp"][m] + 2 * A["two_pt"][m] \
            + 3 * A["fgm"][m] + 2 * A["safety"][m]
        print(f"  {s}: n={n} pts {mean('pts'):.2f} (recon {recon.mean():.2f}) offTD {mean('off_td'):.3f} "
              f"pass {mean('pass_td'):.3f} rush {mean('rush_td'):.3f} nonoff {mean('nonoff_td'):.3f} "
              f"FGM {mean('fgm'):.3f} FGA {mean('fga'):.3f} XP {mean('xp'):.3f} 2pt {mean('two_pt'):.3f} "
              f"saf {mean('safety'):.3f}")
        pr = A["pass_att"][m].sum() / (A["pass_att"][m].sum() + A["rush_att_all"][m].sum())
        dbr = A["dropbacks"][m].sum() / (A["dropbacks"][m].sum() + A["rush_att_all"][m].sum()
                                         - A["scrambles"][m].sum())
        print(f"        plays {mean('plays'):.2f} (SD {A['plays'][m].std(ddof=1):.2f}) pass_att "
              f"{mean('pass_att'):.2f} (SD {A['pass_att'][m].std(ddof=1):.2f}) dropbacks "
              f"{mean('dropbacks'):.2f} sacks {mean('sacks'):.2f} rush_all {mean('rush_att_all'):.2f} "
              f"rush_nokneel {mean('rush_att'):.2f} scr {mean('scrambles'):.2f} targets {mean('targets'):.2f} "
              f"tgt/att {A['targets'][m].sum() / A['pass_att'][m].sum():.4f} pass_rate {pr:.3f} "
              f"dropback_rate {dbr:.3f} neutral {pr_neutral[s][0] / pr_neutral[s][1]:.3f}")
        check(f"B2 {s} pts/g", c["pts"][s], mean("pts"), tol_abs=0.02)
        check(f"B2 {s} identity recon == pts", mean("pts"), float(recon.mean()), tol_abs=0.02)
        check(f"B2 {s} offTD/g", c["off_td"][s], mean("off_td"), tol_abs=0.005)
        check(f"B2 {s} passTD/g", c["pass_td"][s], mean("pass_td"), tol_abs=0.005)
        check(f"B2 {s} rushTD/g", c["rush_td"][s], mean("rush_td"), tol_abs=0.005)
        check(f"B2 {s} nonoffTD/g", c["nonoff_td"][s], mean("nonoff_td"), tol_abs=0.003)
        check(f"B2 {s} FGM/g", c["fgm"][s], mean("fgm"), tol_abs=0.003)
        check(f"B2 {s} FGA/g", c["fga"][s], mean("fga"), tol_abs=0.003)
        print(f"        researcher's definitions (2-pt tries included): plays {mean('plays_incl2pt'):.2f} "
              f"dropbacks {mean('dropbacks_incl2pt'):.2f} rush_all {mean('rush_all_incl2pt'):.2f}; "
              f"designed rushes excl kneels+scrambles {mean('rush_designed'):.2f}")
        check(f"B2 {s} plays/g (incl 2-pt tries)", c["plays"][s], mean("plays_incl2pt"), tol_abs=0.03)
        check(f"B2 {s} pass_att/g", c["pass_att"][s], mean("pass_att"), tol_abs=0.02)
        check(f"B2 {s} rush_att/g incl kneels (incl 2-pt)", c["rush_att_all"][s],
              mean("rush_all_incl2pt"), tol_abs=0.06)
        check(f"B2 {s} rush_att/g excl kneels", c["rush_att_nokneel"][s], mean("rush_att"), tol_abs=0.02)
        check(f"B2 {s} dropbacks/g (incl 2-pt tries)", c["dropbacks"][s], mean("dropbacks_incl2pt"), tol_abs=0.03)
        check(f"B2 {s} targets/g", c["targets"][s], mean("targets"), tol_abs=0.02)
        check(f"B2 {s} targets/att", c["tgt_per_att"][s],
              float(A["targets"][m].sum() / A["pass_att"][m].sum()), tol_abs=0.001)
        check(f"B2 {s} pass rate", c["pass_rate"][s], float(pr), tol_abs=0.002)
        check(f"B2 {s} dropback rate", c["dropback_rate"][s], float(dbr), tol_abs=0.002)
        check(f"B2 {s} neutral dropback rate", c["neutral_db_rate"][s],
              pr_neutral[s][0] / pr_neutral[s][1], tol_abs=0.002)
    print(f"  pooled: plays SD {A['plays'].std(ddof=1):.2f} (claimed 8.39); team Y/C excl kneels "
          f"{A['rush_yds'].sum() / A['rush_att'].sum():.2f}; TD/C {A['rush_td'].sum() / A['rush_att'].sum():.4f}; "
          f"pts/carry (ratio of sums) {A['rush_pts'].sum() / A['rush_att'].sum():.3f}; "
          f"PPR/tgt ratio-of-sums {A['rec_ppr'].sum() / A['targets'].sum():.3f}, mean-of-ratios "
          f"{np.nanmean(A['ppr_per_tgt']):.3f}; catch {A['rec'].sum() / A['targets'].sum():.3f} "
          f"Y/T {A['rec_yds'].sum() / A['targets'].sum():.2f} passTD/tgt {A['pass_td'].sum() / A['targets'].sum():.4f}")
    check("B2 pooled plays SD", 8.39, float(A["plays"].std(ddof=1)), tol_abs=0.05)
    check("B2 team Y/C excl kneels", 4.49, float(A["rush_yds"].sum() / A["rush_att"].sum()), tol_abs=0.02)
    check("B2 team pts/carry", 0.659, float(A["rush_pts"].sum() / A["rush_att"].sum()), tol_abs=0.005)
    check("B2 team PPR/target", 1.70, float(A["rec_ppr"].sum() / A["targets"].sum()), tol_abs=0.01)

    section("B3  Pooled OLS on implied total I (claimed a, b, r, resid SD)")
    for k, (a, b, r, sd) in c["ols_I"].items():
        o = ols(A["I"], A[k])
        print(f"  {k:14s} = {o['a']:8.4f} + {o['b']:8.5f}*I  r={o['r']:.3f} sd={o['sd']:.3f} "
              f"(claimed {a}, {b}, r {r}, sd {sd})")
        check(f"B3 {k} slope", b, o["b"], tol_rel=0.02)
        check(f"B3 {k} intercept", a, o["a"], tol_rel=0.02 if abs(a) > 1 else 0.05,
              tol_abs=None if abs(a) > 1 else 0.02)
        if r is not None:
            check(f"B3 {k} r", r, o["r"], tol_abs=0.01)
        if sd is not None:
            check(f"B3 {k} resid SD", sd, o["sd"], tol_abs=0.02 * sd + 0.005)
    ok = ols(A["I"], A["k_pts"])
    print(f"  kicker (3*FGM + XP) = {ok['a']:.3f} + {ok['b']:.4f}*I  r={ok['r']:.3f}; at I=19: "
          f"{ok['a'] + 19 * ok['b']:.2f}, I=25: {ok['a'] + 25 * ok['b']:.2f} (claimed 6.7 / 7.8)")
    check("B3 kicker pts at I=25 (empirical OLS)", 7.8, ok["a"] + 25 * ok["b"], tol_abs=0.25)
    check("B3 kicker pts at I=19 (empirical OLS)", 6.7, ok["a"] + 19 * ok["b"], tol_abs=0.25)
    # The researcher's rush_att(M,T) came from analyze.py (kneels + scrambles +
    # 2-pt tries included); the univariate rush_att ~ I came from analyze2.py
    # (kneels excluded). Fit all definitions so the mix-up is visible.
    src = {"rush_att": "rush_all_incl2pt", "plays": "plays_incl2pt"}
    for k, (a, bm, ct, r2) in c["ols_MT"].items():
        o = ols2(A["M"], A["T"], A[src.get(k, k)])
        print(f"  {k:9s} = {o['a']:7.3f} + {o['b']:+.4f}*M + {o['c']:+.4f}*T  R2={o['r2']:.3f} sd={o['sd']:.2f} "
              f"(claimed {a}, {bm}, {ct}, R2 {r2}) [col {src.get(k, k)}]")
        check(f"B3 {k} M-coef", bm, o["b"], tol_abs=0.003)
        check(f"B3 {k} T-coef", ct, o["c"], tol_abs=0.003)
        if r2 is not None:
            check(f"B3 {k} R2", r2, o["r2"], tol_abs=0.003)
    print("  rush-attempt definitions, y = a + b*M + c*T  (M = expected margin, + favored):")
    rush_mt = {}
    for col, lab in [("rush_all_incl2pt", "incl kneels+scrambles+2pt (researcher)"),
                     ("rush_att", "excl kneels, incl scrambles"),
                     ("rush_designed", "designed: excl kneels+scrambles")]:
        o = ols2(A["M"], A["T"], A[col])
        rush_mt[col] = o
        ex = o["a"] + o["b"] * 3 + o["c"] * 47
        print(f"    {lab:40s} a={o['a']:6.2f} b_M={o['b']:+.4f} c_T={o['c']:+.4f} R2={o['r2']:.3f} "
              f"mean {A[col].mean():.2f}; at fav -3 / 47 -> {ex:.2f}")
    kneel_slope = rush_mt["rush_all_incl2pt"]["b"] - rush_mt["rush_att"]["b"]
    print(f"    => {kneel_slope:+.3f} rushes per point of expected margin are kneel-downs "
          f"(not fantasy carries); fantasy-relevant spread slope ~{rush_mt['rush_att']['b']:+.3f}/pt")
    # LOSO for the (M,T) volume models
    print("  LOSO skill of y = a + b*M + c*T vs mean baseline:")
    for k in ("pass_att", "targets", "rush_att", "rush_designed", "plays"):
        P, Y, B = [], [], []
        for s in SEASONS:
            tr = A["season"] != s
            o = ols2(A["M"][tr], A["T"][tr], A[k][tr])
            te = ~tr
            P.append(o["a"] + o["b"] * A["M"][te] + o["c"] * A["T"][te])
            Y.append(A[k][te])
            B.append(np.full(te.sum(), A[k][tr].mean()))
        P, Y, B = map(np.concatenate, (P, Y, B))
        sk = 1 - np.mean((Y - P) ** 2) / np.mean((Y - B) ** 2)
        print(f"    {k:14s} OOS skill {sk:.4f}  r_oos {np.corrcoef(P, Y)[0, 1]:.3f}")
    print("  implied-total buckets (n, offTD, FGM, targets, PPR/tgt, passTD, pts/carry):")
    for lo, hi in [(0, 17), (17, 20), (20, 23), (23, 26), (26, 29), (29, 99)]:
        m = (A["I"] >= lo) & (A["I"] < hi)
        print(f"    I {lo:2d}-{hi:2d}: n={m.sum():4d} offTD {A['off_td'][m].mean():.2f} FGM {A['fgm'][m].mean():.2f} "
              f"tgts {A['targets'][m].mean():.1f} PPR/tgt {A['rec_ppr'][m].sum() / A['targets'][m].sum():.3f} "
              f"passTD {A['pass_td'][m].mean():.2f} pts/carry {A['rush_pts'][m].sum() / A['rush_att'][m].sum():.3f}")
    print("  spread buckets (betting convention): plays / pass_att / rush_all")
    sb = -A["M"]
    for lo, hi, lab in [(-99, -9.5, "fav 10+"), (-3.5, -0.01, "fav 0.5-3.5"),
                        (0.01, 3.5, "dog 0.5-3.5"), (9.5, 99, "dog 10+")]:
        m = (sb > lo) & (sb <= hi)
        print(f"    {lab:12s} n={m.sum():4d} plays {A['plays'][m].mean():.1f} pass_att "
              f"{A['pass_att'][m].mean():.1f} rush {A['rush_att_all'][m].mean():.1f}")

    section("B4  RULE 5 — leave-one-season-out skill of y = a + b*I vs mean-only baseline")
    print(f"  {'target':14s} {'held':6s} {'n':>4s} {'rmse_mod':>9s} {'rmse_base':>9s} {'skill':>7s} {'r_oos':>6s} {'b_train':>8s}")
    b4 = {}
    for k in ["pts", "off_td", "pass_td", "rush_td", "fgm", "k_pts", "pass_att", "targets", "rush_att",
              "ppr_per_tgt", "rec_ppr", "rush_pts", "pts_per_carry"]:
        rows, pooled = loso(A, k)
        for (s, n, rm, rb, sk, r, b) in rows:
            print(f"  {k:14s} {s!s:6s} {n:4d} {rm:9.3f} {rb:9.3f} {sk:7.3f} {r:6.3f} {b:8.4f}")
        s, n, rm, rb, sk, r, _ = pooled
        print(f"  {k:14s} {s:6s} {n:4d} {rm:9.3f} {rb:9.3f} {sk:7.3f} {r:6.3f}")
        b4[k] = (sk, r)
    for k in ("pts", "off_td", "pass_td", "rush_td", "ppr_per_tgt", "rec_ppr", "rush_pts", "pts_per_carry"):
        sk, r = b4[k]
        line = f"B4 {k} OOS skill {sk:.3f} r_oos {r:.3f}"
        if sk > 0.02:
            PASSES.append(line + " (beats mean baseline out of sample)")
            print("  ok   " + line)
        else:
            FLAGS.append(line + " (does NOT beat baseline out of sample)")
            print("  FLAG " + line)
    for k in ("fgm", "pass_att", "targets"):
        sk, r = b4[k]
        print(f"  note {k}: OOS skill {sk:.3f} r_oos {r:.3f} -> confirms 'flat/near-zero' claim")

    section("B5  Within-team (team-season demeaned) vs between-team slopes")
    keys = ["targets", "pass_att", "rush_att", "ppr_per_tgt", "rec_ppr", "pass_td", "rush_pts",
            "pts_per_carry", "pts", "off_td"]
    grp = defaultdict(list)
    for i in range(n_tg):
        grp[(int(A["season"][i]), A["team"][i])].append(i)
    dm = {k: np.full(n_tg, np.nan) for k in ["I"] + keys}
    for idx in grp.values():
        idx = np.array(idx)
        for k in ["I"] + keys:
            v = A[k][idx]
            dm[k][idx] = v - np.nanmean(v)
    print(f"  team-seasons {len(grp)}; within-team SD of I {np.nanstd(dm['I']):.2f} (claimed 2.56)")
    check("B5 team-season count", 96, len(grp), tol_abs=0)
    check("B5 within-team SD of I", 2.56, float(np.nanstd(dm["I"])), tol_abs=0.03)
    mI = np.array([A["I"][idx].mean() for idx in grp.values()])
    print(f"  between-team SD of mean I {mI.std():.2f} (claimed 2.69)")
    for k in keys:
        o = ols(dm["I"], dm[k])
        y = np.array([np.nanmean(A[k][idx]) for idx in grp.values()])
        ob = ols(mI, y)
        per_season = []
        for s in SEASONS:
            m = A["season"] == s
            per_season.append(ols(dm["I"][m], dm[k][m])["b"])
        print(f"  {k:14s} within {o['b']:8.4f} (r {o['r']:.3f}) by season "
              f"{' '.join(f'{v:8.4f}' for v in per_season)} | between {ob['b']:8.4f} (r {ob['r']:.3f})")
        if k in c["within"]:
            check(f"B5 {k} within slope", c["within"][k], o["b"], tol_rel=0.03,
                  tol_abs=None if abs(c["within"][k]) > 0.1 else 0.0015)
        if k in c["between"]:
            check(f"B5 {k} between slope", c["between"][k], ob["b"], tol_rel=0.03,
                  tol_abs=None if abs(c["between"][k]) > 0.1 else 0.002)
        if k in c["ols_I"] and k in c["within"] and k != "targets":
            pooled_b = c["ols_I"][k][1]
            print(f"      within/pooled = {o['b'] / pooled_b:.2f}; within/between = {o['b'] / ob['b']:.2f} "
                  f"(claimed 'within retains 55-70% of the pooled effect')")

    section("B5b Expanding-window OOS test of the within-team rule "
            "y_hat = prior-weeks mean + b_within*(I - prior-weeks mean I)")
    print("  b_within taken from the OTHER two seasons' demeaned fit (no leakage); "
          "needs >= 4 prior games; pooled line model a + b*I also from other seasons.")
    for k in ("pts", "off_td", "ppr_per_tgt", "rec_ppr", "rush_pts", "pass_td"):
        b_other = {}
        pooled_other = {}
        for s in SEASONS:
            m = A["season"] != s
            b_other[s] = ols(dm["I"][m], dm[k][m])["b"]
            pooled_other[s] = ols(A["I"][m], A[k][m])
        Y, BASE, RULE, LINE, BLEND = [], [], [], [], []
        for (s, team), idx in grp.items():
            idx = np.array(idx)
            order = np.argsort(A["week"][idx])
            idx = idx[order]
            y = A[k][idx]
            Iv = A["I"][idx]
            for j in range(4, len(idx)):
                if np.isnan(y[j]):
                    continue
                prior_y = np.nanmean(y[:j])
                prior_I = Iv[:j].mean()
                base = prior_y
                rule = prior_y + b_other[s] * (Iv[j] - prior_I)
                line = pooled_other[s]["a"] + pooled_other[s]["b"] * Iv[j]
                Y.append(y[j])
                BASE.append(base)
                RULE.append(rule)
                LINE.append(line)
                BLEND.append(0.5 * (rule + line))
        Y, BASE, RULE, LINE, BLEND = map(np.array, (Y, BASE, RULE, LINE, BLEND))
        mse = lambda p: float(np.mean((Y - p) ** 2))  # noqa: E731
        print(f"  {k:12s} n={len(Y)} rmse: prior-mean {math.sqrt(mse(BASE)):.3f} | +within rule "
              f"{math.sqrt(mse(RULE)):.3f} (skill vs prior-mean {1 - mse(RULE) / mse(BASE):+.3f}) | "
              f"pooled line only {math.sqrt(mse(LINE)):.3f} ({1 - mse(LINE) / mse(BASE):+.3f}) | "
              f"blend {math.sqrt(mse(BLEND)):.3f} ({1 - mse(BLEND) / mse(BASE):+.3f})")
        line_txt = f"B5b {k}: within-team rule OOS skill vs prior-weeks mean {1 - mse(RULE) / mse(BASE):+.3f}"
        (PASSES if mse(RULE) < mse(BASE) else FLAGS).append(line_txt)

    section("B6  Positional efficiency (stats_player_week)")
    pos_agg, team_week_pos, wr_weeks, wr_targets = load_stats()
    for pos, (catch, yt, tdt, adot, pprt) in c["pos_eff"].items():
        a = pos_agg[pos]
        ct, y, t, ad = a["rec"] / a["targets"], a["rec_yds"] / a["targets"], a["rec_td"] / a["targets"], a["air"] / a["targets"]
        p = (a["rec"] + 0.1 * a["rec_yds"] + 6 * a["rec_td"]) / a["targets"]
        print(f"  {pos}: targets {a['targets']:.0f} catch {ct:.3f} Y/T {y:.2f} TD/T {t:.4f} aDOT {ad:.2f} PPR/tgt {p:.3f}")
        check(f"B6 {pos} catch", catch, ct, tol_abs=0.002)
        check(f"B6 {pos} Y/T", yt, y, tol_abs=0.02)
        check(f"B6 {pos} TD/T", tdt, t, tol_abs=0.0005)
        check(f"B6 {pos} aDOT", adot, ad, tol_abs=0.1)
        check(f"B6 {pos} PPR/target", pprt, p, tol_abs=0.006)
    a = pos_agg["RB"]
    yc, tdc = a["rush_yds"] / a["carries"], a["rush_td"] / a["carries"]
    print(f"  RB carries {a['carries']:.0f} Y/C {yc:.2f} TD/C {tdc:.4f} pts/carry {0.1 * yc + 6 * tdc:.3f}")
    check("B6 RB Y/C", c["rb_carry"][0], yc, tol_abs=0.01)
    check("B6 RB TD/C", c["rb_carry"][1], tdc, tol_abs=0.0005)
    q = pos_agg["QB"]
    ya, tda, inta = q["pass_yds"] / q["pass_att"], q["pass_td"] / q["pass_att"], q["int"] / q["pass_att"]
    print(f"  QB att {q['pass_att']:.0f} Y/A {ya:.2f} TD/A {tda:.4f} INT/A {inta:.4f} "
          f"pts/att {0.04 * ya + 4 * tda - 2 * inta:.3f}; QB rush Y/C {q['rush_yds'] / q['carries']:.2f} "
          f"TD/C {q['rush_td'] / q['carries']:.4f}")
    check("B6 QB Y/A", c["qb_pass"][0], ya, tol_abs=0.02)
    check("B6 QB TD/A", c["qb_pass"][1], tda, tol_abs=0.0005)
    check("B6 QB INT/A", c["qb_pass"][2], inta, tol_abs=0.0005)

    section("B7  Team-week fantasy PPR (QB+RB+WR+TE) vs implied total")
    line_key = {}
    for i in range(n_tg):
        line_key[(int(A["season"][i]), int(A["week"][i]), A["team"][i])] = (A["I"][i], A["M"][i], A["T"][i], A["pts"][i])
    agg_all = defaultdict(float)
    per_pos = defaultdict(lambda: defaultdict(float))
    for (s, w, tm, p), v in team_week_pos.items():
        agg_all[(s, w, tm)] += v
        per_pos[p][(s, w, tm)] += v
    TW = {"season": [], "I": [], "M": [], "T": [], "pts": [], "ALL": []}
    for k, v in agg_all.items():
        if k not in line_key:
            continue
        Ii, Mi, Ti, Pi = line_key[k]
        TW["season"].append(k[0])
        TW["I"].append(Ii)
        TW["M"].append(Mi)
        TW["T"].append(Ti)
        TW["pts"].append(Pi)
        TW["ALL"].append(v)
    TW = {k: np.array(v, float) for k, v in TW.items()}
    o = ols(TW["I"], TW["ALL"])
    o2 = ols(TW["pts"], TW["ALL"])
    om = ols2(TW["M"], TW["T"], TW["ALL"])
    print(f"  ALL: n={o['n']} mean {o['ybar']:.2f} SD {np.std(TW['ALL'], ddof=1):.2f} r={o['r']:.3f} "
          f"slope {o['b']:.3f}/pt | vs actual pts r={o2['r']:.3f} | {om['b']:.2f}*M + {om['c']:.2f}*T R2 {om['r2']:.3f}")
    r_c, b_c, mean_c, sd_c = c["team_ppr"]["ALL"]
    check("B7 ALL r", r_c, o["r"], tol_abs=0.01)
    check("B7 ALL slope", b_c, o["b"], tol_abs=0.05)
    check("B7 ALL mean", mean_c, o["ybar"], tol_abs=0.2)
    check("B7 ALL SD", sd_c, float(np.std(TW["ALL"], ddof=1)), tol_abs=0.2)
    check("B7 ALL r vs actual pts", 0.786, o2["r"], tol_abs=0.01)
    rows, pooled = loso(TW, "ALL")
    for (s, n, rm, rb, sk, r, b) in rows:
        print(f"    LOSO held {s}: rmse_model {rm:.2f} rmse_base {rb:.2f} skill {sk:.3f} r_oos {r:.3f}")
    print(f"    LOSO pooled: skill {pooled[4]:.3f} r_oos {pooled[5]:.3f}")
    line = f"B7 team-week PPR OOS skill {pooled[4]:.3f} r_oos {pooled[5]:.3f}"
    (PASSES if pooled[4] > 0.02 else FLAGS).append(line)
    for p in ("QB", "RB", "WR", "TE"):
        xs, ys, ms, ts = [], [], [], []
        for k, v in per_pos[p].items():
            if k in line_key:
                xs.append(line_key[k][0])
                ys.append(v)
                ms.append(line_key[k][1])
                ts.append(line_key[k][2])
        o = ols(xs, ys)
        om = ols2(ms, ts, ys)
        print(f"  {p}: n={o['n']} mean {o['ybar']:.2f} r={o['r']:.3f} slope {o['b']:.3f}/pt | "
              f"{om['b']:.2f}*M + {om['c']:.2f}*T")
        check(f"B7 {p} r", c["team_ppr"][p][0], o["r"], tol_abs=0.01)
        check(f"B7 {p} slope", c["team_ppr"][p][1], o["b"], tol_abs=0.03)

    section("B8  Worked-example neighbourhood and real WR weekly SD")
    m = (A["I"] >= 24) & (A["I"] <= 26) & (A["M"] >= 1) & (A["M"] <= 5)
    tg_n = A["targets"][m].mean()
    ppr_n = A["rec_ppr"][m].sum() / A["targets"][m].sum()
    print(f"  I in [24,26] & favored 1-5: n={m.sum()} targets {tg_n:.1f} PPR/tgt {ppr_n:.3f} passTD "
          f"{A['pass_td'][m].mean():.2f} rush_att {A['rush_att'][m].mean():.1f} team rec PPR "
          f"{A['rec_ppr'][m].mean():.1f} -> 24% share = {0.24 * tg_n:.2f} tgts, {0.24 * A['rec_ppr'][m].mean():.2f} PPR")
    check("B8 neighbourhood n", 168, int(m.sum()), tol_abs=0)
    check("B8 neighbourhood targets", 30.9, float(tg_n), tol_abs=0.1)
    check("B8 neighbourhood PPR/tgt", 1.805, float(ppr_n), tol_abs=0.01)
    check("B8 neighbourhood 24% share PPR", 13.4, float(0.24 * A["rec_ppr"][m].mean()), tol_abs=0.15)
    # WRs whose season mean sits at the example level
    sds, means_, tg_sd = [], [], []
    for k, weeks in wr_weeks.items():
        if len(weeks) < 12:
            continue
        w = np.array(weeks)
        if 11.5 <= w.mean() <= 15.5:
            sds.append(w.std(ddof=1))
            means_.append(w.mean())
    sds = np.array(sds)
    print(f"  WR player-seasons (>=12 games) with mean PPR 11.5-15.5: n={len(sds)}, mean of season means "
          f"{np.mean(means_):.2f}, weekly SD median {np.median(sds):.2f} mean {sds.mean():.2f} "
          f"IQR {np.percentile(sds, 25):.2f}-{np.percentile(sds, 75):.2f}")
    print(f"  claimed weekly SD 4.5-5 (stated as 'before target-share uncertainty'); algebra with "
          f"binomial share term gave {a_out['example']['sd_full']:.2f}")
    all_sd = []
    all_mean = []
    for k, weeks in wr_weeks.items():
        if len(weeks) >= 12:
            w = np.array(weeks)
            all_sd.append(w.std(ddof=1))
            all_mean.append(w.mean())
    o = ols(all_mean, all_sd)
    print(f"  WR weekly SD vs season mean (n={o['n']} player-seasons): SD = {o['a']:.2f} + {o['b']:.3f}*mean "
          f"(r {o['r']:.2f}); at mean 13.5 -> {o['a'] + o['b'] * 13.5:.2f}")
    line = (f"B8 real WR weekly SD at ~13.5 PPR: median {np.median(sds):.2f} "
            f"(claimed 4.5-5)")
    if np.median(sds) <= 5.2:
        PASSES.append(line)
    else:
        FLAGS.append(line + " -> claimed SD is too low")

    section("B9  Dropback rate by score differential and half")
    for half, claimed in (("H1", c["h1_dropback"]), ("H2", c["h2_dropback"])):
        for b in ["<=-17", "-16..-9", "-8..-1", "0", "1..8", "9..16", ">=17"]:
            d, n = pr_diff[(half, b)]
            rate = d / n
            print(f"  {half} diff {b:8s}: {rate:.3f} (n={n}, claimed {claimed[b]})")
            check(f"B9 {half} {b}", claimed[b], rate, tol_abs=0.003)
    n, sd_, sdd, sdb, sddb = h2
    slope = (sddb - sd_ * sdb / n) / (sdd - sd_ * sd_ / n)
    print(f"  H2 play-level OLS slope of dropback on score diff, -8..+16: {slope * 100:.2f} pp/pt "
          f"(n={n}; claimed ~-0.9)")
    check("B9 H2 play-level slope pp/pt (claimed -0.9)", -0.009, slope, tol_abs=0.0015)
    for s in SEASONS:
        print(f"  {s} overall dropback rate {pr_all[s][0] / pr_all[s][1]:.3f}")


def main() -> int:
    print("verify_vegas.py — adversarial verification of the Vegas-domain findings")
    print(f"repo: {REPO.name}; data dir present: {DATA.exists()}")
    a_out = part_a()
    have = all((DATA / f"play_by_play_{s}.csv.gz").exists() and
               (DATA / f"stats_player_week_{s}.csv").exists() for s in SEASONS)
    if have:
        part_b(a_out)
    else:
        print("\nPART B skipped: nflverse files not found in data/research/cache/nflverse/ "
              "(play_by_play_{2023,2024,2025}.csv.gz, stats_player_week_{2023,2024,2025}.csv)")
    section("SUMMARY")
    print(f"  passes: {len(PASSES)}   flags: {len(FLAGS)}")
    for f in FLAGS:
        print("  FLAG " + f)
    return 0


if __name__ == "__main__":
    sys.exit(main())
