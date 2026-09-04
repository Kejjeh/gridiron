"""verify_vor.py -- adversarial numeric verification of the VOR / replacement-level
research (the "vor" domain).

Offline, deterministic (every RNG seeded), pure Python + numpy (no scipy; the
normal CDF is math.erf). No absolute paths: everything derives from __file__.

What it recomputes and checks, section by section:

  PART A -- arithmetic that needs no data:
    A1  man-games replacement ranks N = starters / (a * bye_factor), and the
        bye-factor inconsistency (16/17 used for N, but G = 13 = 14 - 1 for VOR);
    A2  curve slopes read off the researcher's own F4-B 3-yr table;
    A3  linear-interpolation replacement levels at the fractional ranks
        (RB25.5 / RB31.5 / WR34.4 / WR42 / TE12.1 / TE14.6 / QB12 / QB15.5)
        and the claimed injury+bye shifts;
    A4  worked draft VOR (man-games and worst-starter baselines);
    A5  worked waiver / start-sit VOR;
    A6  injury arithmetic (availability, games per injury, per-team-season);
    A7  DST per-game arithmetic;
    A8  a Monte-Carlo check of the VOR_draft identity (finding 1);
    A9  VOR ordering for ranks 1-5 by position (does the curve reproduce the
        1-QB consensus ordering: elite RB/WR first, QB1/TE1 around the RB6-8
        / WR7-9 tier?).

  PART B -- recompute from nflverse stats_player_week_2023..2025.csv when they
        are present in data/research/cache/nflverse/ (gitignored, rule #10):
    B1  season PPG by PPG-rank (min 8 games, REG weeks 1-17, full PPR; K by
        the stated 3/4/5 FG + PAT - miss rule), 3-yr means vs the F4-B table;
    B2  hindsight weekly order statistics E_w[X_(k)];
    B3  hindsight flex fill (drop RB1-24 / WR1-24 / TE1-12, top 12 of the
        residual pool) and the 12th-flex threshold T;
    B4  ex-ante flex fill (season-to-date PPG projection, weeks 5-17);
    B5  ex-ante forward replacement band curve (pooled);
    B6  the season-rank vs forward-looking flips (Henderson, Allen, Jeudy,
        Jonnu Smith, Pitts, Moss) and a week-by-week look at WHEN the
        Henderson signal became visible ex ante;
    B7  QB streaming definitions: naive QB22-24 band, best-FA (QB22 by
        projection), matchup-selected FA, and top-12-week hit rates;
    B8  WR-vs-RB depth anchors (count above a PPG threshold);
    B9  games missed by top TEs / RBs / WRs (survivorship floor).

  PART C -- seeded 12-team league simulation from the researcher's curves:
        gamma weekly noise (CV 0.45, and a position-specific variant), byes on
        a 32-team schedule, iid availability a_p, balanced snake and random
        allocations, hindsight-optimal and mean-based lineups; measures the
        realized flex split RB/WR/TE, the 12th-flex value T, the number of
        starters per position (24 + F_p), the deepest rank started, the
        marginal-starter mean with/without injuries (the man-games shift), the
        lineup-marginal (best-bench) replacement, and the realized-rank
        inflation of a hindsight PPG-by-rank curve.

Run from the repo root:  python scripts/research/verify_vor.py   (< 60 s)
"""
from __future__ import annotations

import csv
import sys
import time
from collections import defaultdict
from math import erf, sqrt
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data" / "research" / "cache" / "nflverse"
SEED = 20260904
N_TEAMS = 12
N_SIM_SEASONS = 300

# --------------------------------------------------------------------------
# Constants under test (all from the research findings, inline on purpose)
# --------------------------------------------------------------------------
# F4-B "season PPG by PPG-rank, 3-yr mean 2023-25" as claimed.
B_CURVE = {
    "QB": {1: 23.8, 6: 19.8, 12: 17.2, 18: 15.6, 24: 13.5},
    "RB": {1: 24.0, 3: 19.9, 6: 17.9, 12: 15.8, 24: 12.4, 30: 11.1, 36: 9.5,
           48: 6.6, 60: 4.5},
    "WR": {1: 23.5, 6: 18.2, 12: 16.0, 24: 13.6, 36: 11.9, 48: 9.9, 60: 8.2,
           72: 6.8},
    "TE": {1: 16.7, 3: 14.6, 6: 12.6, 12: 10.3, 18: 8.7, 24: 7.8},
    "K": {1: 11.7, 6: 9.5, 12: 8.5, 18: 8.1},
}
AVAIL = {"RB": 0.86, "WR": 0.87, "QB": 0.82, "TE": 0.88, "K": 0.97}
STARTERS = {"QB": 12.0, "RB": 25.5, "WR": 34.4, "TE": 12.13}
BYE_FACTOR_CLAIMED = 16.0 / 17.0
G_CLAIMED = 13

# Claimed hindsight weekly order statistics (2023 / 2024 / 2025)
CLAIM_ORDER_STAT = {
    ("QB", 12): (17.7, 18.3, 18.8), ("RB", 24): (10.6, 11.4, 10.9),
    ("RB", 36): (6.9, 7.1, 7.3), ("WR", 24): (14.4, 14.8, 13.5),
    ("WR", 36): (10.7, 11.2, 9.9), ("TE", 12): (10.3, 10.5, 11.2),
    ("K", 12): (8.9, 9.1, 9.2),
}
CLAIM_HINDSIGHT_FLEX = {  # season -> (RB, WR, TE, T)
    2023: (0.59, 10.94, 0.47, 10.91), 2024: (1.12, 10.24, 0.65, 11.50),
    2025: (1.41, 9.06, 1.53, 10.67),
}
CLAIM_EXANTE_FLEX = {  # season -> (RB, WR, TE, realized mean of 12 picks)
    2023: (0.85, 11.15, 0.00, 12.07), 2024: (2.46, 9.54, 0.00, 11.97),
    2025: (1.15, 10.46, 0.38, 10.57),
}
CLAIM_EXANTE_T = 10.67
CLAIM_BANDS = {
    "QB": {(10, 12): 17.4, (13, 15): 16.0, (16, 18): 14.0, (19, 21): 14.4,
           (22, 24): 12.9},
    "RB": {(19, 24): 12.0, (25, 27): 10.5, (28, 30): 10.1, (31, 36): 7.8,
           (37, 42): 6.5, (43, 48): 5.8, (49, 60): 4.0},
    "WR": {(19, 24): 13.1, (25, 27): 12.9, (28, 30): 11.2, (31, 36): 10.8,
           (37, 42): 10.5, (43, 48): 8.3, (49, 60): 7.3},
    "TE": {(10, 12): 10.5, (13, 15): 9.0, (16, 18): 9.1, (19, 21): 8.0,
           (22, 24): 7.4},
    "K": {(7, 12): 8.0, (13, 15): 8.6, (16, 18): 7.8},
}
CLAIM_FLIPS = [  # (season, name, pos, claimed early rank, claimed late PPG)
    (2025, "TreVeyon Henderson", "RB", 42, 17.0),
    (2025, "Keenan Allen", "WR", 8, 6.5),
    (2024, "Jerry Jeudy", "WR", 51, 20.0),
    (2024, "Jonnu Smith", "TE", 19, 16.4),
    (2024, "Kyle Pitts", "TE", 3, 4.8),
    (2023, "Zack Moss", "RB", 4, 6.9),
]

POSN = ("QB", "RB", "WR", "TE", "K")
NFL_BYE_SCHEDULE = {5: 2, 6: 4, 7: 2, 8: 4, 9: 4, 10: 4, 11: 4, 12: 2, 13: 4,
                    14: 2}  # 32 teams across weeks 5-14

_PASS = 0
_FAIL = 0


def phi(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def section(title: str) -> None:
    print("\n" + "=" * 78)
    print(title)
    print("=" * 78)


def check(label: str, claimed: float, got: float, tol: float, unit: str = "") -> bool:
    """Print a labelled comparison and tally PASS/FAIL within +/- tol."""
    global _PASS, _FAIL
    ok = abs(claimed - got) <= tol
    _PASS += ok
    _FAIL += (not ok)
    flag = "PASS" if ok else "FAIL"
    print(f"  [{flag}] {label:<52s} claimed {claimed:8.2f}  recomputed {got:8.2f}"
          f"  (tol {tol:g}{unit})")
    return ok


def curve(pos: str, r: float) -> float:
    """Linear interpolation of the F4-B table; extrapolate with the last slope,
    floored at 0.5 PPG."""
    pts = sorted(B_CURVE[pos].items())
    ranks = [k for k, _ in pts]
    vals = [v for _, v in pts]
    if r <= ranks[0]:
        return vals[0]
    for i in range(1, len(ranks)):
        if r <= ranks[i]:
            r0, r1 = ranks[i - 1], ranks[i]
            v0, v1 = vals[i - 1], vals[i]
            return v0 + (v1 - v0) * (r - r0) / (r1 - r0)
    slope = (vals[-1] - vals[-2]) / (ranks[-1] - ranks[-2])
    return max(0.5, vals[-1] + slope * (r - ranks[-1]))


# ==========================================================================
# PART A -- arithmetic
# ==========================================================================
def part_a() -> dict:
    out = {}
    section("A1  Man-games replacement rank N = starters / (a * bye factor)")
    bf = BYE_FACTOR_CLAIMED
    claimed_n = {"RB": 31.5, "WR": 42.0, "TE": 14.6, "QB": 15.5}
    for pos in ("RB", "WR", "TE", "QB"):
        n = STARTERS[pos] / (AVAIL[pos] * bf)
        check(f"N^mg {pos} (bye 16/17)", claimed_n[pos], n, 0.1)
        out[f"N_mg_{pos}"] = n
    print("  Bye-factor consistency: N uses 16/17 = %.4f but VOR uses G = 13 = 14 - 1,"
          " i.e. a 14-week fantasy regular season whose bye factor is 13/14 = %.4f"
          % (bf, 13 / 14))
    for pos in ("RB", "WR", "TE", "QB"):
        n14 = STARTERS[pos] / (AVAIL[pos] * 13 / 14)
        print(f"    {pos}: N^mg with 13/14 = {n14:5.1f} (vs {claimed_n[pos]}) -> "
              f"replacement {curve(pos, n14):5.2f} vs {curve(pos, claimed_n[pos]):5.2f} PPG")

    section("A2  Curve slopes from the F4-B 3-yr table (PPG per rank)")
    slopes = {
        "RB6-48": (("RB", 6, 48), 0.27), "RB1-3": (("RB", 1, 3), 2.0),
        "WR1-6": (("WR", 1, 6), 1.06), "WR24-48": (("WR", 24, 48), 0.15),
        "TE6-12": (("TE", 6, 12), 0.38), "TE12-24": (("TE", 12, 24), 0.21),
        "QB1-24": (("QB", 1, 24), 0.45), "K6-18": (("K", 6, 18), 0.12),
    }
    for lab, ((pos, a, b), claimed) in slopes.items():
        s = (B_CURVE[pos][a] - B_CURVE[pos][b]) / (b - a)
        check(f"slope {lab}", claimed, s, 0.02)

    section("A3  Replacement level at fractional ranks (linear interp of F4-B)")
    interp_claims = [("RB", 25.5, 12.1), ("RB", 31.5, 10.7), ("WR", 34.4, 12.1),
                     ("WR", 42.0, 10.6), ("TE", 12.13, 10.3), ("TE", 14.6, 9.7),
                     ("QB", 12.0, 17.2), ("QB", 15.5, 16.2)]
    lv = {}
    for pos, r, claimed in interp_claims:
        v = curve(pos, r)
        lv[(pos, r)] = v
        check(f"R_{pos} at rank {r}", claimed, v, 0.15)
    print("  Injury+bye shift of the draft replacement (man-games rank minus last-starter rank):")
    shifts = {"RB": ((25.5, 31.5), -1.4), "WR": ((34.4, 42.0), -1.5),
              "TE": ((12.13, 14.6), -0.6), "QB": ((12.0, 15.5), -1.0)}
    for pos, ((r0, r1), claimed) in shifts.items():
        d = lv[(pos, r1)] - lv[(pos, r0)]
        check(f"shift {pos} {r0}->{r1}", claimed, d, 0.15)
    out["R_mg"] = {"RB": lv[("RB", 31.5)], "WR": lv[("WR", 42.0)],
                   "TE": lv[("TE", 14.6)], "QB": lv[("QB", 15.5)]}
    out["R_ws"] = {"RB": lv[("RB", 25.5)], "WR": lv[("WR", 34.4)],
                   "TE": lv[("TE", 12.13)], "QB": lv[("QB", 12.0)]}

    section("A4  Worked draft VOR = G * a * (mu - R), G = 13")
    G = G_CLAIMED
    # The researcher's stated baselines (rounded): man-games 10.7/16.2/9.7,
    # worst-starter 11.0/17.2/10.3.
    for lab, R, claimed in [("RB 16 PPG, man-games R=10.7", ("RB", 10.7), 59.3),
                            ("QB 16 PPG, man-games R=16.2", ("QB", 16.2), -1.7),
                            ("TE 12 PPG, man-games R=9.7", ("TE", 9.7), 26.4),
                            ("RB 16 PPG, worst-starter R=11.0", ("RB", 11.0), 55.9),
                            ("QB 16 PPG, worst-starter R=17.2", ("QB", 17.2), -12.8),
                            ("TE 12 PPG, worst-starter R=10.3", ("TE", 10.3), 19.4)]:
        pos, r = R
        mu = 16.0 if pos != "TE" else 12.0
        v = G * AVAIL[pos] * (mu - r)
        check(lab, claimed, v, 0.6)
    print("  Note: the QB -1.7 figure needs the un-rounded R_QB = 16.16 (13*0.82*(16-16.16) = -1.7);"
          " with the printed 16.2 it is -2.1. Same conclusion (at replacement).")
    print("  Using the interpolated (un-rounded) baselines from A3:")
    for pos, mu in (("RB", 16.0), ("QB", 16.0), ("TE", 12.0)):
        v_mg = G * AVAIL[pos] * (mu - out["R_mg"][pos])
        v_ws = G * AVAIL[pos] * (mu - out["R_ws"][pos])
        print(f"    {pos} {mu:.0f} PPG: man-games {v_mg:6.1f}   worst-starter {v_ws:6.1f}")

    section("A5  Worked waiver / start-sit VOR")
    for lab, mu, lo, hi, c_lo, c_hi in [("RB 16 vs FA 4.0-6.5", 16, 4.0, 6.5, 9.5, 12.0),
                                        ("QB 16 vs FA 12.9-16.3", 16, 12.9, 16.3, 0.0, 3.1),
                                        ("TE 12 vs FA 7.4-9.1", 12, 7.4, 9.1, 2.9, 4.6)]:
        v_hi, v_lo = mu - lo, mu - hi
        check(f"{lab} weekly low", c_lo, max(0.0, v_lo), 0.1)
        check(f"{lab} weekly high", c_hi, v_hi, 0.1)
        print(f"      6-week horizon: {6 * max(0.0, v_lo):5.1f} - {6 * v_hi:5.1f}")
    for lab, mu, bench, claimed in [("RB 16 vs bench 9.0", 16, 9.0, 7.0),
                                    ("QB 16 vs bench 14.0", 16, 14.0, 2.0),
                                    ("TE 12 vs bench 7.0", 12, 7.0, 5.0)]:
        check(f"start/sit {lab}", claimed, mu - bench, 0.01)

    section("A6  Injury arithmetic")
    for lab, missed, claimed in [("a_RB top-24 (2.4 missed)", 2.4, 0.86),
                                 ("a_WR top-24 (2.2 missed)", 2.2, 0.87),
                                 ("a_RB rounds 6-8 (3.8 missed)", 3.8, 0.78),
                                 ("a_QB 2023 (3.19 missed)", 102 / 32, 0.81),
                                 ("a_QB 2022 (2.75 missed)", 88 / 32, 0.84),
                                 ("a_TE (2 missed, own estimate)", 2.0, 0.88)]:
        check(lab, claimed, 1 - missed / 17, 0.01)
    check("QB games missed per starter 2023 (102/32)", 3.2, 102 / 32, 0.05)
    check("QB games missed per starter 2022 (88/32)", 2.75, 88 / 32, 0.05)
    pmc = {"QB": (174, 940, 5.4, 5.9), "RB": (578, 2832, 4.9, 17.7),
           "WR": (975, 5355, 5.5, 33.5), "TE": (473, 2673, 5.7, 16.7)}
    for pos, (inj, games, c_per, c_team) in pmc.items():
        check(f"PMC games per injury {pos}", c_per, games / inj, 0.06)
        check(f"PMC games per team-season {pos} (5 seasons)", c_team, games / (5 * 32), 0.1)
        print(f"      ... with 6 seasons (2017-2022 inclusive): {games / (6 * 32):5.1f}")
    print("  PMC totals: listed positions sum to %d injuries / %d games vs the study's"
          " 2,523 / 12,789 (other positions make up the rest)."
          % (sum(v[0] for v in pmc.values()), sum(v[1] for v in pmc.values())))

    section("A7  DST arithmetic (season points / 17)")
    for lab, pts, claimed in [("2023 DST1 172", 172, 10.1), ("2023 DST11 127", 127, 7.5),
                              ("2023 NYJ-opponent stream 202", 202, 11.9)]:
        check(lab, claimed, pts / 17, 0.06)

    section("A8  Monte-Carlo check of VOR_draft = sum_w a_w (mu_w - R_w)  (finding 1)")
    rng = np.random.default_rng(SEED)
    n, W = 200_000, 13
    a, mu, R, cv = 0.86, 16.0, 10.7, 0.5
    k = 1 / cv ** 2
    avail = rng.random((n, W)) < a
    pts_i = rng.gamma(k, mu / k, (n, W))
    pts_r = rng.gamma(k, R / k, (n, W))
    with_i = np.where(avail, pts_i, pts_r).sum(1)
    without = pts_r.sum(1)
    mc = (with_i - without).mean()
    se = (with_i - without).std() / sqrt(n)
    check("E[VOR] MC vs G*a*(mu-R)", W * a * (mu - R), mc, 4 * se + 0.05)
    print(f"      MC SE = {se:.3f}")

    section("A9  VOR ordering for ranks 1-5 by position (G=13, man-games baselines)")
    R_mg = out["R_mg"]
    rows = []
    for pos in ("RB", "WR", "QB", "TE"):
        for r in range(1, 6):
            v = G * AVAIL[pos] * (curve(pos, r) - R_mg[pos])
            rows.append((v, pos, r))
    rows.sort(reverse=True)
    print("  Rank  Pos  r   mu     VOR")
    for i, (v, pos, r) in enumerate(rows, 1):
        print(f"  {i:4d}  {pos:<3s} {r:2d}  {curve(pos, r):5.1f}  {v:6.1f}")
    # Where do QB1 and TE1 land in the RB/WR VOR ladder?
    v_qb1 = G * AVAIL["QB"] * (curve("QB", 1) - R_mg["QB"])
    v_te1 = G * AVAIL["TE"] * (curve("TE", 1) - R_mg["TE"])
    ladder = sorted([(G * AVAIL[p] * (curve(p, r) - R_mg[p]), p, r)
                     for p in ("RB", "WR") for r in range(1, 61)], reverse=True)
    n_above_qb = sum(1 for v, _, _ in ladder if v > v_qb1)
    n_above_te = sum(1 for v, _, _ in ladder if v > v_te1)
    print(f"  QB1 VOR {v_qb1:.1f}: {n_above_qb} RB/WR have higher VOR -> overall slot ~{n_above_qb + 1}")
    print(f"  TE1 VOR {v_te1:.1f}: {n_above_te} RB/WR have higher VOR -> overall slot ~{n_above_te + 1}")
    out["vor_ladder_qb1"] = n_above_qb + 1
    out["vor_ladder_te1"] = n_above_te + 1
    return out


# ==========================================================================
# PART B -- nflverse recompute
# ==========================================================================
def k_points(r: dict) -> float:
    def f(c):
        v = r.get(c, "")
        return float(v) if v not in ("", "NA") else 0.0
    short = f("fg_made_0_19") + f("fg_made_20_29") + f("fg_made_30_39")
    mid = f("fg_made_40_49")
    long_ = f("fg_made_50_59") + f("fg_made_60_")
    return 3 * short + 4 * mid + 5 * long_ + f("pat_made") - f("fg_missed") - f("pat_missed")


class Season:
    def __init__(self, year: int, rows):
        self.year = year
        # by_week[pos][w] -> list of (pts, pid)
        self.by_week = {p: defaultdict(list) for p in POSN}
        self.players = {}  # pid -> dict(name, pos, pts{w}, opp{w}, team{w})
        for pid, name, pos, w, team, opp, pts in rows:
            self.by_week[pos][w].append((pts, pid))
            d = self.players.setdefault(pid, {"name": name, "pos": pos, "pts": {},
                                              "opp": {}, "team": {}})
            d["pts"][w] = pts
            d["opp"][w] = opp
            d["team"][w] = team
        for p in POSN:
            for w in self.by_week[p]:
                self.by_week[p][w].sort(reverse=True)

    # ---- B1 ----
    def season_ppg(self, pos: str, min_games: int = 8):
        vals = []
        for pid, d in self.players.items():
            if d["pos"] != pos:
                continue
            g = len(d["pts"])
            if g >= min_games:
                vals.append(sum(d["pts"].values()) / g)
        vals.sort(reverse=True)
        return vals

    # ---- B2 ----
    def order_stat(self, pos: str, k: int) -> float:
        tot = 0.0
        for w in range(1, 18):
            lst = self.by_week[pos].get(w, [])
            tot += lst[k - 1][0] if len(lst) >= k else 0.0
        return tot / 17

    # ---- B3 ----
    def hindsight_flex(self):
        cnt = {"RB": 0, "WR": 0, "TE": 0}
        T = 0.0
        for w in range(1, 18):
            pool = []
            for pos, drop in (("RB", 24), ("WR", 24), ("TE", 12)):
                pool += [(v, pos) for v, _ in self.by_week[pos].get(w, [])[drop:]]
            pool.sort(reverse=True)
            top = pool[:12]
            for _, pos in top:
                cnt[pos] += 1
            T += top[11][0]
        return {p: cnt[p] / 17 for p in cnt}, T / 17

    # ---- ex-ante projection ----
    def projection(self, pid: str, w: int):
        d = self.players[pid]
        prior = [v for ww, v in d["pts"].items() if ww < w]
        need = max(3, (w - 1) // 2)
        if len(prior) < need:
            return None
        return sum(prior) / len(prior)

    def active_ranked(self, pos: str, w: int):
        """Active (has a row in week w) players ranked by season-to-date PPG.
        Returns list of (proj, realized, pid) sorted by proj desc."""
        out = []
        for _, pid in self.by_week[pos].get(w, []):
            pr = self.projection(pid, w)
            if pr is not None:
                out.append((pr, self.players[pid]["pts"][w], pid))
        out.sort(reverse=True)
        return out

    # ---- B4 ----
    def exante_flex(self):
        cnt = {"RB": 0, "WR": 0, "TE": 0}
        T_proj = 0.0
        real_sum = 0.0
        proj_mean = 0.0
        n_w = 0
        for w in range(5, 18):
            pool = []
            for pos, drop in (("RB", 24), ("WR", 24), ("TE", 12)):
                pool += [(pr, rl, pos) for pr, rl, _ in self.active_ranked(pos, w)[drop:]]
            pool.sort(reverse=True)
            top = pool[:12]
            for _, _, pos in top:
                cnt[pos] += 1
            T_proj += top[11][0]
            real_sum += sum(rl for _, rl, _ in top) / 12
            proj_mean += sum(pr for pr, _, _ in top) / 12
            n_w += 1
        return ({p: cnt[p] / n_w for p in cnt}, T_proj / n_w, real_sum / n_w,
                proj_mean / n_w)

    # ---- B5 ----
    def band_sums(self, pos: str, bands):
        sums = {b: [0.0, 0] for b in bands}
        for w in range(5, 18):
            ranked = self.active_ranked(pos, w)
            for i, (_, rl, _) in enumerate(ranked, 1):
                for (lo, hi) in bands:
                    if lo <= i <= hi:
                        sums[(lo, hi)][0] += rl
                        sums[(lo, hi)][1] += 1
        return sums

    # ---- B6 ----
    def find_player(self, name: str, pos: str):
        key = name.lower()
        hits = [(pid, d) for pid, d in self.players.items()
                if d["pos"] == pos and d["name"].lower().startswith(key)]
        return hits

    def early_rank_by_total(self, pid: str, pos: str, w_last: int = 8):
        totals = []
        for p2, d in self.players.items():
            if d["pos"] != pos:
                continue
            t = sum(v for ww, v in d["pts"].items() if ww <= w_last)
            totals.append((t, p2))
        totals.sort(reverse=True)
        for i, (t, p2) in enumerate(totals, 1):
            if p2 == pid:
                return i, t
        return None, None


def load_seasons():
    seasons = {}
    for yr in (2023, 2024, 2025):
        f = DATA / f"stats_player_week_{yr}.csv"
        if not f.exists():
            return None
        rows = []
        with f.open(encoding="utf-8", newline="") as fh:
            rd = csv.DictReader(fh)
            for r in rd:
                if r["season_type"] != "REG":
                    continue
                w = int(r["week"])
                if w > 17:
                    continue
                pos = r["position"]
                if pos not in POSN:
                    continue
                pts = k_points(r) if pos == "K" else float(r["fantasy_points_ppr"])
                rows.append((r["player_id"], r["player_display_name"], pos, w,
                             r["team"], r["opponent_team"], pts))
        seasons[yr] = Season(yr, rows)
    return seasons


def part_b(seasons: dict) -> dict:
    out = {}
    yrs = (2023, 2024, 2025)

    section("B1  Season PPG by PPG-rank (min 8 games, REG wk 1-17): 3-yr mean vs F4-B")
    ppg = {yr: {p: seasons[yr].season_ppg(p) for p in POSN} for yr in yrs}
    recomputed = {}
    for pos in POSN:
        recomputed[pos] = {}
        for r, claimed in B_CURVE[pos].items():
            vals = [ppg[yr][pos][r - 1] if len(ppg[yr][pos]) >= r else 0.0 for yr in yrs]
            m = sum(vals) / 3
            recomputed[pos][r] = m
            ok = check(f"{pos}{r} 3-yr mean", claimed, m, 0.25)
            if not ok:
                print(f"         per-year: {vals[0]:.2f} / {vals[1]:.2f} / {vals[2]:.2f}")
    out["ppg_curve"] = recomputed
    # direct read at the fractional ranks that A3 interpolated
    print("  Direct read (3-yr mean) at integer ranks around the interpolated points:")
    for pos, r in (("RB", 25), ("RB", 26), ("RB", 31), ("RB", 32), ("WR", 34), ("WR", 35),
                   ("WR", 42), ("TE", 12), ("TE", 13), ("TE", 15), ("QB", 12), ("QB", 15),
                   ("QB", 16)):
        m = sum(ppg[yr][pos][r - 1] for yr in yrs) / 3
        print(f"    {pos}{r:<3d} {m:6.2f}   (F4-B interp {curve(pos, r):6.2f})")
    # counts with >= 8 games
    for pos in POSN:
        print(f"  {pos}: players with >= 8 games = " +
              " / ".join(str(len(ppg[yr][pos])) for yr in yrs))

    section("B2  Hindsight weekly order statistic E_w[X_(k)] (byes included)")
    for (pos, k), claimed in CLAIM_ORDER_STAT.items():
        got = tuple(seasons[yr].order_stat(pos, k) for yr in yrs)
        for yr, c, g in zip(yrs, claimed, got):
            check(f"E[X_({k})] {pos} {yr}", c, g, 0.25)

    section("B3  Hindsight flex fill (drop RB1-24/WR1-24/TE1-12, top 12 of residual)")
    hf = {}
    for yr in yrs:
        cnt, T = seasons[yr].hindsight_flex()
        hf[yr] = (cnt, T)
        c = CLAIM_HINDSIGHT_FLEX[yr]
        check(f"{yr} flex RB", c[0], cnt["RB"], 0.15)
        check(f"{yr} flex WR", c[1], cnt["WR"], 0.15)
        check(f"{yr} flex TE", c[2], cnt["TE"], 0.15)
        check(f"{yr} T (12th flex)", c[3], T, 0.2)
    m = {p: sum(hf[yr][0][p] for yr in yrs) / 3 for p in ("RB", "WR", "TE")}
    mT = sum(hf[yr][1] for yr in yrs) / 3
    print(f"  3-yr mean: RB {m['RB']:.2f}  WR {m['WR']:.2f}  TE {m['TE']:.2f}  T {mT:.2f}")
    out["hindsight_flex"] = (m, mT)

    section("B4  Ex-ante flex fill (season-to-date PPG, min games max(3,floor((w-1)/2)), wk 5-17)")
    ef = {}
    for yr in yrs:
        cnt, T_proj, real_mean, proj_mean = seasons[yr].exante_flex()
        ef[yr] = (cnt, T_proj, real_mean, proj_mean)
        c = CLAIM_EXANTE_FLEX[yr]
        check(f"{yr} ex-ante flex RB", c[0], cnt["RB"], 0.2)
        check(f"{yr} ex-ante flex WR", c[1], cnt["WR"], 0.2)
        check(f"{yr} ex-ante flex TE", c[2], cnt["TE"], 0.2)
        check(f"{yr} realized mean of 12 ex-ante picks", c[3], real_mean, 0.3)
        print(f"         projected 12th value {T_proj:.2f}, projected mean of the 12 {proj_mean:.2f}")
    T_pool = sum(ef[yr][1] for yr in yrs) / 3
    check("ex-ante 12th flex projected value (3-yr)", CLAIM_EXANTE_T, T_pool, 0.2)
    pooled = {p: sum(ef[yr][0][p] for yr in yrs) / 3 for p in ("RB", "WR", "TE")}
    print(f"  pooled ex-ante split: RB {pooled['RB']:.2f}  WR {pooled['WR']:.2f}  TE {pooled['TE']:.2f}"
          f"   (claimed 1.49 / 10.38 / 0.13)")
    out["exante_flex"] = pooled

    section("B5  Ex-ante forward replacement band curve (pooled 2023-25, weeks 5-17)")
    band_out = {}
    for pos, bands in CLAIM_BANDS.items():
        agg = {b: [0.0, 0] for b in bands}
        for yr in yrs:
            s = seasons[yr].band_sums(pos, list(bands))
            for b in bands:
                agg[b][0] += s[b][0]
                agg[b][1] += s[b][1]
        for b, claimed in bands.items():
            got = agg[b][0] / agg[b][1] if agg[b][1] else float("nan")
            band_out[(pos, b)] = got
            check(f"{pos} {b[0]}-{b[1]} band", claimed, got, 0.3)
    out["bands"] = band_out

    section("B6  Season-rank vs forward-looking flips (rank by wk 1-8 total; PPG wk 9-17)")
    for yr, name, pos, c_rank, c_late in CLAIM_FLIPS:
        s = seasons[yr]
        hits = s.find_player(name, pos)
        if len(hits) != 1:
            print(f"  {yr} {name}: {len(hits)} name matches -> skipped: {[d['name'] for _, d in hits]}")
            continue
        pid, d = hits[0]
        rank, tot = s.early_rank_by_total(pid, pos)
        early = [v for w, v in d["pts"].items() if w <= 8]
        late = [v for w, v in d["pts"].items() if w >= 9]
        e_ppg = sum(early) / len(early) if early else 0.0
        l_ppg = sum(late) / len(late) if late else 0.0
        print(f"  {yr} {d['name']} ({pid}): wk1-8 total {tot:.1f} in {len(early)} g "
              f"({e_ppg:.1f} PPG) = {pos}{rank}; wk9-17 {l_ppg:.1f} PPG in {len(late)} g")
        check(f"{yr} {name} early rank", c_rank, rank, 2)
        check(f"{yr} {name} late PPG", c_late, l_ppg, 0.3)
        if name == "TreVeyon Henderson":
            out["henderson"] = (rank, e_ppg, l_ppg)
            print("  Henderson week-by-week (pts, season-to-date PPG rank among RBs, "
                  "trailing-3 PPG, realized PPG from this week to wk 17):")
            for w in range(5, 18):
                if w not in d["pts"]:
                    print(f"    wk{w:2d}: no game")
                    continue
                # season-to-date rank among active RBs by PPG (projection)
                ranked = s.active_ranked("RB", w)
                r_sd = next((i for i, (_, _, p2) in enumerate(ranked, 1) if p2 == pid), None)
                tr = [d["pts"][ww] for ww in range(w - 3, w) if ww in d["pts"]]
                fwd = [d["pts"][ww] for ww in range(w, 18) if ww in d["pts"]]
                print(f"    wk{w:2d}: {d['pts'][w]:5.1f} pts | s-t-d PPG rank RB{r_sd} | "
                      f"trailing-3 {sum(tr) / len(tr) if tr else 0:5.1f} | "
                      f"fwd realized {sum(fwd) / len(fwd):5.1f} PPG over {len(fwd)} g")
            # Sign test of the two rules at each week: season-rank rule says
            # value = s-t-d PPG - 11.0 ; forward rule (best available ex-ante
            # proxy = trailing-3 PPG) says value = trailing-3 - 6.5 (FA floor)
            print("  Rule comparison at week 9 and at the first week trailing-3 PPG >= 12:")
            for w in range(9, 18):
                if w not in d["pts"]:
                    continue
                prior = [d["pts"][ww] for ww in d["pts"] if ww < w]
                sd_ppg = sum(prior) / len(prior)
                tr = [d["pts"][ww] for ww in range(w - 3, w) if ww in d["pts"]]
                tr3 = sum(tr) / len(tr) if tr else 0.0
                fwd = [d["pts"][ww] for ww in range(w, 18) if ww in d["pts"]]
                fwd_ppg = sum(fwd) / len(fwd)
                if w == 9 or tr3 >= 12.0:
                    print(f"    wk{w:2d}: season-rank rule {sd_ppg:5.1f}-11.0 = {sd_ppg - 11.0:+5.1f} | "
                          f"forward (trailing-3) {tr3:5.1f}-6.5 = {tr3 - 6.5:+5.1f} | "
                          f"realized fwd {fwd_ppg:5.1f} -> true value vs FA {fwd_ppg - 6.5:+5.1f}")
                    if tr3 >= 12.0:
                        out["henderson_signal_week"] = w
                        break

    section("B7  QB streaming definitions (weeks 5-17, pooled)")
    naive = {"sum": 0.0, "n": 0}
    best_fa = {"sum": 0.0, "n": 0}
    matchup = {"sum": 0.0, "n": 0}
    hit_top12 = {"top12": [0, 0], "13-24": [0, 0], "22+": [0, 0]}
    for yr in yrs:
        s = seasons[yr]
        for w in range(5, 18):
            ranked = s.active_ranked("QB", w)
            # naive band 22-24
            for i, (_, rl, _) in enumerate(ranked, 1):
                if 22 <= i <= 24:
                    naive["sum"] += rl
                    naive["n"] += 1
            # best FA = rank 22 (21 rostered)
            if len(ranked) >= 22:
                best_fa["sum"] += ranked[21][1]
                best_fa["n"] += 1
            # matchup-selected FA: among rank >= 22 (plus unranked actives),
            # pick the opponent that has allowed the most PPR to QBs so far.
            allowed = defaultdict(list)
            for ww in range(1, w):
                for _, pid in s.by_week["QB"].get(ww, []):
                    d = s.players[pid]
                    allowed[d["opp"][ww]].append(d["pts"][ww])
            allowed_mean = {t: sum(v) / len(v) for t, v in allowed.items()}
            ranked_pids = {p2 for _, _, p2 in ranked[:21]}
            cands = []
            for _, pid in s.by_week["QB"].get(w, []):
                if pid in ranked_pids:
                    continue
                d = s.players[pid]
                # exclude backups with no prior role: require >= 2 prior games
                if sum(1 for ww in d["pts"] if ww < w) < 2:
                    continue
                cands.append((allowed_mean.get(d["opp"][w], 0.0), d["pts"][w]))
            if cands:
                cands.sort(reverse=True)
                matchup["sum"] += cands[0][1]
                matchup["n"] += 1
            # top-12-week hit rates
            realized_sorted = sorted((rl for _, rl, _ in ranked), reverse=True)
            cut = realized_sorted[11] if len(realized_sorted) >= 12 else 0.0
            for i, (_, rl, _) in enumerate(ranked, 1):
                key = "top12" if i <= 12 else ("13-24" if i <= 24 else "22+")
                if i >= 22:
                    hit_top12["22+"][0] += rl >= cut
                    hit_top12["22+"][1] += 1
                if i <= 24:
                    hit_top12[key][0] += rl >= cut
                    hit_top12[key][1] += 1
    v_naive = naive["sum"] / naive["n"]
    v_best = best_fa["sum"] / best_fa["n"]
    v_match = matchup["sum"] / matchup["n"]
    check("naive QB22-24 band PPG", 12.9, v_naive, 0.3)
    print(f"  best-FA (QB22 by s-t-d PPG) realized: {v_best:5.2f} PPG over {best_fa['n']} weeks")
    print(f"  matchup-selected FA (opp allowing most QB PPR to date): {v_match:5.2f} PPG "
          f"over {matchup['n']} weeks   (claimed 16.3, Fantasy Footballers)")
    for k_, (h, n_) in hit_top12.items():
        print(f"  P(top-12 week | ex-ante rank {k_}) = {h / n_:.3f}  (n={n_})")
    print("  claimed: streamers 36.7% vs top-12 QBs 49.4%")
    out["qb_stream"] = (v_naive, v_best, v_match)

    section("B8  WR-vs-RB depth anchors (players above a PPG threshold)")
    for yr, thr, c_wr, c_rb, src in [(2024, 8.0, 62, 41, "4for4"), (2023, 7.0, 66, 45, "Yahoo")]:
        for mg in (8, 4, 1):
            wr = sum(1 for v in seasons[yr].season_ppg("WR", mg) if v >= thr)
            rb = sum(1 for v in seasons[yr].season_ppg("RB", mg) if v >= thr)
            print(f"  {yr} >= {thr} PPG, min {mg} g: WR {wr}  RB {rb}   ({src} claims WR {c_wr} / RB {c_rb})")

    section("B9  Games missed by top players by season total (survivorship floor, of 16)")
    for pos, topn in (("TE", 12), ("RB", 24), ("WR", 24), ("QB", 12)):
        vals = []
        for yr in yrs:
            s = seasons[yr]
            tots = sorted(((sum(d["pts"].values()), pid) for pid, d in s.players.items()
                           if d["pos"] == pos), reverse=True)[:topn]
            vals.append(sum(16 - len(s.players[pid]["pts"]) for _, pid in tots) / topn)
        print(f"  top-{topn} {pos} by season total: games missed of 16 = "
              + " / ".join(f"{v:.2f}" for v in vals) + f"  (mean {sum(vals) / 3:.2f})")
    return out


# ==========================================================================
# PART C -- 12-team league simulation from the researcher's curves
# ==========================================================================
POOL_N = {"QB": 24, "RB": 72, "WR": 84, "TE": 30}
ROSTER_N = {"QB": 18, "RB": 60, "WR": 72, "TE": 18}  # rostered league-wide
CV_FLAT = {"QB": 0.45, "RB": 0.45, "WR": 0.45, "TE": 0.45}
CV_POS = {"QB": 0.42, "RB": 0.52, "WR": 0.55, "TE": 0.65}


def build_league(rng, allocation: str):
    """Return arrays pos, rank, mu, team (-1 = free agent), nflteam."""
    pos_l, rank_l, mu_l, team_l = [], [], [], []
    for pos, n in POOL_N.items():
        nr = ROSTER_N[pos]
        if allocation == "snake":
            teams = []
            for r in range(1, nr + 1):
                rnd, idx = divmod(r - 1, N_TEAMS)
                teams.append(idx if rnd % 2 == 0 else N_TEAMS - 1 - idx)
        else:  # random: same per-team counts, random ranks
            per = nr // N_TEAMS
            teams = list(rng.permutation(np.repeat(np.arange(N_TEAMS), per)))
            # leftover (nr not divisible by 12) go to random teams
            extra = nr - per * N_TEAMS
            teams += list(rng.integers(0, N_TEAMS, extra))
        for r in range(1, n + 1):
            pos_l.append(pos)
            rank_l.append(r)
            mu_l.append(curve(pos, r))
            team_l.append(teams[r - 1] if r <= nr else -1)
    pos_a = np.array(pos_l)
    rank_a = np.array(rank_l)
    mu_a = np.array(mu_l)
    team_a = np.array(team_l)
    nfl = rng.integers(0, 32, len(pos_a))
    return pos_a, rank_a, mu_a, team_a, nfl


def bye_weeks(rng):
    teams = rng.permutation(32)
    bye = np.zeros(32, dtype=int)
    i = 0
    for w, c in NFL_BYE_SCHEDULE.items():
        for _ in range(c):
            bye[teams[i]] = w
            i += 1
    return bye


def simulate(rng, allocation: str, cvs: dict, avail: dict, mode: str,
             n_seasons: int, weeks: int = 17) -> dict:
    """mode = 'hindsight' (pick lineup on realized points) or 'mean' (pick on mu)."""
    pos_a, rank_a, mu_a, team_a, nfl = build_league(rng, allocation)
    n = len(pos_a)
    pos_idx = {p: np.where(pos_a == p)[0] for p in POOL_N}
    team_pos = {(t, p): np.array([i for i in pos_idx[p] if team_a[i] == t])
                for t in range(N_TEAMS) for p in POOL_N}
    fa_pos = {p: np.array([i for i in pos_idx[p] if team_a[i] == -1]) for p in POOL_N}
    k_shape = np.array([1 / cvs[p] ** 2 for p in pos_a])
    a_vec = np.array([avail[p] for p in pos_a])

    flex_cnt = {"RB": 0.0, "WR": 0.0, "TE": 0.0}
    T_sum = 0.0            # 12th-best flex selection value
    T_real_sum = 0.0       # realized points of the 12th flex
    flex_real_mean = 0.0   # mean realized of the 12 flex starters
    n_start = {p: 0.0 for p in POOL_N}
    deepest = {p: 0.0 for p in POOL_N}
    marg_mu = {p: 0.0 for p in POOL_N}     # per-team worst-started mu
    marg_rank = {p: 0.0 for p in POOL_N}
    bench_mu = {p: 0.0 for p in POOL_N}    # per-team best-bench mu
    bench_n = {p: 0 for p in POOL_N}
    fa_mu = {p: 0.0 for p in POOL_N}       # best available FA mu
    n_tw = 0
    n_w = 0
    season_tot = np.zeros(n)
    season_g = np.zeros(n)

    for _ in range(n_seasons):
        bye = bye_weeks(rng)
        for w in range(1, weeks + 1):
            avail_m = (bye[nfl] != w) & (rng.random(n) < a_vec)
            pts = rng.gamma(k_shape, mu_a / k_shape)
            season_tot += np.where(avail_m, pts, 0.0)
            season_g += avail_m
            key = pts if mode == "hindsight" else mu_a
            flex_vals = []
            n_w += 1
            for p in POOL_N:
                fa = fa_pos[p][avail_m[fa_pos[p]]]
                fa_mu[p] += mu_a[fa].max() if len(fa) else 0.0
            for t in range(N_TEAMS):
                n_tw += 1
                started = {p: [] for p in POOL_N}
                cand = {}
                for p, ns in (("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)):
                    idx = team_pos[(t, p)]
                    idx = idx[avail_m[idx]]
                    order = idx[np.argsort(-key[idx])]
                    started[p] = list(order[:ns])
                    cand[p] = list(order[ns:])
                # flex
                pool = [(key[i], i, p) for p in ("RB", "WR", "TE") for i in cand[p]]
                if pool:
                    kv, i, p = max(pool)
                    started[p].append(i)
                    cand[p].remove(i)
                    flex_cnt[p] += 1
                    flex_vals.append((kv, pts[i]))
                for p in POOL_N:
                    n_start[p] += len(started[p])
                    if started[p]:
                        deepest[p] = max(deepest[p], 0)  # placeholder
                        worst = max(started[p], key=lambda i: rank_a[i])
                        marg_mu[p] += mu_a[worst]
                        marg_rank[p] += rank_a[worst]
                    if cand[p]:
                        bench_mu[p] += max(mu_a[i] for i in cand[p])
                        bench_n[p] += 1
                # league-wide deepest started rank accumulates below
                for p in POOL_N:
                    if started[p]:
                        cand.setdefault(("deep", p), 0)
                        cand[("deep", p)] = max(rank_a[i] for i in started[p])
                for p in POOL_N:
                    if ("deep", p) in cand:
                        deepest[p] += 0  # accumulate per week below
                if t == 0:
                    week_deep = {p: 0 for p in POOL_N}
                for p in POOL_N:
                    if ("deep", p) in cand:
                        week_deep[p] = max(week_deep[p], cand[("deep", p)])
            for p in POOL_N:
                deepest[p] += week_deep[p]
            flex_vals.sort(reverse=True)
            if len(flex_vals) >= 12:
                T_sum += flex_vals[11][0]
                T_real_sum += flex_vals[11][1]
            flex_real_mean += sum(v for _, v in flex_vals) / max(1, len(flex_vals))

    res = {
        "flex": {p: flex_cnt[p] / n_w for p in flex_cnt},
        "T_key": T_sum / n_w, "T_real": T_real_sum / n_w,
        "flex_real_mean": flex_real_mean / n_w,
        "n_start": {p: n_start[p] / n_w for p in POOL_N},
        "deepest": {p: deepest[p] / n_w for p in POOL_N},
        "marg_mu": {p: marg_mu[p] / n_tw for p in POOL_N},
        "marg_rank": {p: marg_rank[p] / n_tw for p in POOL_N},
        "bench_mu": {p: bench_mu[p] / max(1, bench_n[p]) for p in POOL_N},
        "fa_mu": {p: fa_mu[p] / n_w for p in POOL_N},
        "season_tot": season_tot, "season_g": season_g, "pos": pos_a, "mu": mu_a,
        "rank": rank_a, "n_seasons": n_seasons,
    }
    return res


def print_sim(label: str, r: dict) -> None:
    f = r["flex"]
    print(f"  {label}")
    print(f"    flex split RB {f['RB']:5.2f}  WR {f['WR']:5.2f}  TE {f['TE']:5.2f}   "
          f"T(12th flex, selection key) {r['T_key']:5.2f}   T realized {r['T_real']:5.2f}   "
          f"mean realized of flex starters {r['flex_real_mean']:5.2f}")
    print("    starters/week: " + "  ".join(f"{p} {r['n_start'][p]:5.2f}" for p in POOL_N))
    print("    deepest rank started (league max, mean over weeks): "
          + "  ".join(f"{p} {r['deepest'][p]:5.1f}" for p in POOL_N))
    print("    per-team marginal starter: rank "
          + "  ".join(f"{p} {r['marg_rank'][p]:5.1f}" for p in POOL_N))
    print("    per-team marginal starter: mu   "
          + "  ".join(f"{p} {r['marg_mu'][p]:5.2f}" for p in POOL_N))
    print("    per-team best-bench mu (lineup-marginal replacement): "
          + "  ".join(f"{p} {r['bench_mu'][p]:5.2f}" for p in POOL_N))
    print("    best free-agent mu: "
          + "  ".join(f"{p} {r['fa_mu'][p]:5.2f}" for p in POOL_N))


def part_c() -> dict:
    out = {}
    section("C1  League simulation: flex split and effective replacement (their curves + gamma noise)")
    print(f"  {N_TEAMS} teams, 1QB/2RB/2WR/1TE/1FLEX, rostered QB {ROSTER_N['QB']} / RB {ROSTER_N['RB']} / "
          f"WR {ROSTER_N['WR']} / TE {ROSTER_N['TE']}, {N_SIM_SEASONS} seasons x 17 weeks, byes wk 5-14, "
          f"iid availability RB .86 WR .87 TE .88 QB .82")
    configs = [
        ("snake / CV 0.45 / hindsight-optimal lineups", "snake", CV_FLAT, AVAIL, "hindsight"),
        ("snake / CV 0.45 / mean-based lineups", "snake", CV_FLAT, AVAIL, "mean"),
        ("random / CV 0.45 / hindsight-optimal lineups", "random", CV_FLAT, AVAIL, "hindsight"),
        ("random / CV 0.45 / mean-based lineups", "random", CV_FLAT, AVAIL, "mean"),
        ("snake / position CVs (.42/.52/.55/.65) / hindsight", "snake", CV_POS, AVAIL, "hindsight"),
        ("snake / position CVs / mean-based", "snake", CV_POS, AVAIL, "mean"),
    ]
    results = {}
    for label, alloc, cvs, av, mode in configs:
        rng = np.random.default_rng(SEED)
        t0 = time.time()
        r = simulate(rng, alloc, cvs, av, mode, N_SIM_SEASONS)
        results[label] = r
        print_sim(label + f"   [{time.time() - t0:.1f}s]", r)
    out["sim"] = {k: (v["flex"], v["T_key"], v["n_start"], v["marg_mu"], v["bench_mu"])
                  for k, v in results.items()}

    section("C2  Claims vs simulation (mean-based = what a manager actually starts)")
    m = results["snake / CV 0.45 / mean-based lineups"]
    h = results["snake / CV 0.45 / hindsight-optimal lineups"]
    print("  Claimed flex split: RB 1.0-1.5 / WR 9.5-11 / TE 0-1.5; T 10.7-11.0; last starters RB25-26 WR34-35 TE12-13")
    check("mean-based flex RB in [1.0,1.5] (mid 1.25)", 1.25, m["flex"]["RB"], 0.25)
    check("mean-based flex WR in [9.5,11] (mid 10.25)", 10.25, m["flex"]["WR"], 0.75)
    check("mean-based flex TE in [0,1.5] (mid 0.75)", 0.75, m["flex"]["TE"], 0.75)
    check("mean-based T (12th flex mu) ~10.85", 10.85, m["T_key"], 0.35)
    check("hindsight T (12th flex realized) ~10.85", 10.85, h["T_key"], 0.35)
    check("mean-based RB starters/week ~25.5", 25.5, m["n_start"]["RB"], 0.5)
    check("mean-based WR starters/week ~34.4", 34.4, m["n_start"]["WR"], 0.5)
    check("mean-based TE starters/week ~12.1", 12.13, m["n_start"]["TE"], 0.5)

    section("C3  Man-games shift: marginal-starter mu with vs without injuries/byes (mean-based, snake)")
    rng = np.random.default_rng(SEED)
    no_inj = simulate(rng, "snake", CV_FLAT, {p: 1.0 for p in AVAIL}, "mean", N_SIM_SEASONS // 3)
    rng = np.random.default_rng(SEED)
    no_inj_no_bye = simulate(rng, "snake", CV_FLAT, {p: 1.0 for p in AVAIL}, "mean",
                             N_SIM_SEASONS // 3, weeks=4)  # weeks 1-4 have no byes
    rng = np.random.default_rng(SEED)
    inj_no_bye = simulate(rng, "snake", CV_FLAT, AVAIL, "mean", N_SIM_SEASONS // 3, weeks=4)
    claimed_shift = {"RB": -1.4, "WR": -1.5, "TE": -0.6, "QB": -1.0}
    print("  per-team marginal-starter mu: no injury/no bye | bye only | injury only | both;"
          " shift(both - none) vs claimed injury+bye shift")
    for p in ("RB", "WR", "TE", "QB"):
        a0 = no_inj_no_bye["marg_mu"][p]
        a1 = no_inj["marg_mu"][p]
        a2 = inj_no_bye["marg_mu"][p]
        a3 = m["marg_mu"][p]
        print(f"    {p}: {a0:5.2f} | {a1:5.2f} | {a2:5.2f} | {a3:5.2f}   shift {a3 - a0:+5.2f}"
              f"  (claimed {claimed_shift[p]:+.1f})")
        check(f"man-games shift {p}", claimed_shift[p], a3 - a0, 0.5)
    print("  per-team marginal-starter RANK (both) vs claimed man-games rank:")
    for p, cn in (("RB", 31.5), ("WR", 42.0), ("TE", 14.6), ("QB", 15.5)):
        print(f"    {p}: sim marginal rank {m['marg_rank'][p]:5.1f}  deepest-started {m['deepest'][p]:5.1f}"
              f"  claimed N^mg {cn}")

    section("C4  Hindsight-rank inflation: realized season PPG by realized rank vs true mu by rank")
    r = h
    ppg = r["season_tot"] / np.maximum(1, r["season_g"])
    # use a single simulated season for a fair 'one season' curve: re-simulate 1 season
    rng = np.random.default_rng(SEED + 1)
    one = simulate(rng, "snake", CV_FLAT, AVAIL, "hindsight", 3)
    # per-season realized PPG curves: average over the 3 seasons is fine for slope
    ppg1 = one["season_tot"] / np.maximum(1, one["season_g"])
    for p, (r0, r1) in (("RB", (6, 48)), ("WR", (24, 48)), ("QB", (1, 24)), ("TE", (6, 12))):
        idx = np.where(one["pos"] == p)[0]
        realized_sorted = np.sort(ppg1[idx])[::-1]
        true_sorted = np.sort(one["mu"][idx])[::-1]
        s_real = (realized_sorted[r0 - 1] - realized_sorted[r1 - 1]) / (r1 - r0)
        s_true = (true_sorted[r0 - 1] - true_sorted[r1 - 1]) / (r1 - r0)
        print(f"  {p}{r0}-{r1}: true-mu slope {s_true:.3f}  realized-rank slope (3-season avg of "
              f"per-player PPG, single sort) {s_real:.3f}   top value true {true_sorted[0]:.1f} "
              f"vs realized {realized_sorted[0]:.1f}")
    print("  (a 3-season average understates single-season inflation; the point is the sign:"
          " ranking on realized PPG steepens the curve and inflates the top)")
    # explicit single-season inflation with 1 season
    rng = np.random.default_rng(SEED + 2)
    one1 = simulate(rng, "snake", CV_FLAT, AVAIL, "hindsight", 1)
    ppg11 = one1["season_tot"] / np.maximum(1, one1["season_g"])
    for p in ("RB", "WR"):
        idx = np.where(one1["pos"] == p)[0]
        rs = np.sort(ppg11[idx])[::-1]
        ts = np.sort(one1["mu"][idx])[::-1]
        print(f"  single season {p}: rank1 true {ts[0]:.1f} realized {rs[0]:.1f}; rank12 true {ts[11]:.1f}"
              f" realized {rs[11]:.1f}; rank24 true {ts[23]:.1f} realized {rs[23]:.1f}; "
              f"rank48 true {ts[47]:.1f} realized {rs[47]:.1f}")
    return out


# ==========================================================================
def main() -> int:
    t0 = time.time()
    print("verify_vor.py -- adversarial verification of the VOR / replacement research")
    print(f"numpy {np.__version__}, seed {SEED}")
    a = part_a()
    seasons = load_seasons()
    if seasons is None:
        print("\nPART B skipped: nflverse files not found in data/research/cache/nflverse/")
    else:
        part_b(seasons)
    part_c()
    section("SUMMARY")
    print(f"  checks passed {_PASS}, failed {_FAIL}   ({time.time() - t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
