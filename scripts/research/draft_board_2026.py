"""Draft-day board, 2026: league-scored projections, replacement levels, VOR,
availability at slot-1 picks, and a Monte Carlo snake-draft simulation.

Inputs (all pulled 2026-09-08 into data/research/cache/draft2026/, gitignored):
  sleeper_projections_2026.csv  Sleeper season projections + Sleeper ADP
  fp_ecr_half.json              FantasyPros half-PPR expert consensus (978 players)
  ffc_adp_half-ppr.json         FantasyFootballCalculator 12-team half-PPR ADP
  ff_playerids.csv              nflverse id crosswalk (sleeper <-> fantasypros <-> gsis)
  stats_season_2025.csv         nflverse 2025 season totals
  sleeper_players.json          Sleeper player dump (injury status, depth chart, age)

Run:  PYTHONPATH=src python scripts/research/draft_board_2026.py
Outputs: data/outputs/draft2026_board.csv (committed), plus stdout report.
"""
from __future__ import annotations

import json
import math
import re
import sys

import numpy as np
import pandas as pd

from gridiron.draft import (adp_sd, optimal_lineup, p_available, p_survive,
                            replacement_levels, snake_picks)
from gridiron.league_config import (DEFAULT_SCORING, DRAFT_ROUNDS, MY_DRAFT_SLOT,
                                    NUM_TEAMS, ROSTER_SLOTS)
from gridiron.paths import OUTPUTS, RESEARCH_CACHE
from gridiron.scoring import fantasy_points

CACHE = RESEARCH_CACHE / "draft2026"
POS = ["QB", "RB", "WR", "TE", "K", "DEF"]
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")
rng = np.random.default_rng(20260908)


def name_key(s: str) -> str:
    s = str(s).lower().replace(".", "").replace("'", "")
    s = SUFFIX.sub("", s)
    return re.sub(r"[^a-z]", "", s)


MY_PICKS = snake_picks(MY_DRAFT_SLOT, NUM_TEAMS, DRAFT_ROUNDS)

# Known games-missed overrides applied to the Sleeper leg only (ECR already
# prices news in). Source: FantasyPros/NBC/Yahoo injury reports 2026-09-08.
GAMES_MISSED = {
    "treveyonhenderson": 1, "jordyntyson": 5, "joshjacobs": 6, "kylemonangai": 1,
    "jaydenhiggins": 17, "rickypearsall": 17, "alvinkamara": 2, "michaelpenix": 2,
    "isiahpacheco": 17, "keatonmitchell": 0,
}

# ---------------------------------------------------------------- load
sl = pd.read_csv(CACHE / "sleeper_projections_2026.csv", dtype={"sleeper_id": str})
sl = sl[sl.pos.isin(POS)].copy()
ids = pd.read_csv(CACHE / "ff_playerids.csv", dtype=str)
ecr_raw = json.load(open(CACHE / "fp_ecr_half.json", encoding="utf-8"))["players"]
ecr = pd.DataFrame(ecr_raw)
for c in ["rank_ecr", "rank_min", "rank_max", "rank_ave", "rank_std", "tier", "player_bye_week", "player_id"]:
    ecr[c] = pd.to_numeric(ecr[c], errors="coerce")
ecr = ecr.rename(columns={"player_id": "fantasypros_id", "player_name": "fp_name",
                          "player_position_id": "fp_pos", "player_team_id": "fp_team",
                          "player_bye_week": "bye"})
ecr["fp_pos"] = ecr.fp_pos.replace({"DST": "DEF"})
ffc = pd.DataFrame(json.load(open(CACHE / "ffc_adp_half-ppr.json"))["players"])
ffc["position"] = ffc.position.replace({"PK": "K"})
st25 = pd.read_csv(CACHE / "stats_season_2025.csv", low_memory=False)
players = json.load(open(CACHE / "sleeper_players.json", encoding="utf-8"))

# ---------------------------------------------------------------- league points from Sleeper components
def sleeper_league_pts(r) -> float:
    if r.pos in ("K", "DEF"):
        return float(r.pts_half) if pd.notna(r.pts_half) else 0.0
    g = lambda k: float(r[k]) if k in r and pd.notna(r[k]) else 0.0
    return fantasy_points({
        "passing_yards": g("pass_yd"), "passing_tds": g("pass_td"), "interceptions": g("pass_int"),
        "rushing_yards": g("rush_yd"), "rushing_tds": g("rush_td"), "receptions": g("rec"),
        "receiving_yards": g("rec_yd"), "receiving_tds": g("rec_td"), "fumbles_lost": g("fum_lost"),
    }, DEFAULT_SCORING)

sl["pts_sl"] = sl.apply(sleeper_league_pts, axis=1)
sl["name_key"] = sl.name.map(name_key)
sl.loc[sl.pos == "DEF", "name_key"] = "def" + sl.loc[sl.pos == "DEF", "sleeper_id"].str.lower()

# crosswalk sleeper -> fantasypros / gsis
xw = ids[["sleeper_id", "fantasypros_id", "gsis_id"]].dropna(subset=["sleeper_id"]).copy()
xw["sleeper_id"] = xw.sleeper_id.str.replace(r"\.0$", "", regex=True)  # crosswalk stores ids as floats
xw = xw.drop_duplicates("sleeper_id")
xw["fantasypros_id"] = pd.to_numeric(xw.fantasypros_id, errors="coerce")
sl = sl.merge(xw, on="sleeper_id", how="left")

# ECR join: by fantasypros id, fallback name+pos, DEF by team
ecr["name_key"] = ecr.fp_name.map(name_key)
ecr["fp_team"] = ecr.fp_team.replace({"JAC": "JAX"})
ecr.loc[ecr.fp_pos == "DEF", "name_key"] = "def" + ecr.loc[ecr.fp_pos == "DEF", "fp_team"].str.lower()
ecr_cols = ["fantasypros_id", "rank_ecr", "rank_ave", "rank_std", "rank_min", "rank_max", "tier", "pos_rank", "bye", "fp_name", "fp_pos", "name_key"]
m1 = sl.merge(ecr[ecr_cols].drop(columns=["name_key", "fp_pos"]), on="fantasypros_id", how="left")
need = m1.rank_ave.isna()
fallback = (ecr[ecr_cols].drop(columns=["fantasypros_id"]).rename(columns={"fp_pos": "pos"})
            .sort_values("rank_ave").drop_duplicates(["name_key", "pos"]))
m2 = m1.loc[need, ["name_key", "pos"]].merge(fallback, on=["name_key", "pos"], how="left")
assert len(m2) == int(need.sum())
for c in ["rank_ecr", "rank_ave", "rank_std", "rank_min", "rank_max", "tier", "pos_rank", "bye", "fp_name"]:
    m1.loc[need, c] = m2[c].values
board = m1
# ECR players missing from Sleeper projections (rare): append
missing = ecr[~ecr.fantasypros_id.isin(board.fantasypros_id.dropna()) & ~ecr.name_key.isin(board.name_key)]
missing = missing[missing.rank_ave <= 220]
if len(missing):
    add = pd.DataFrame({"sleeper_id": None, "name": missing.fp_name, "pos": missing.fp_pos, "team": missing.fp_team,
                        "name_key": missing.name_key, "fantasypros_id": missing.fantasypros_id})
    for c in ["rank_ecr", "rank_ave", "rank_std", "rank_min", "rank_max", "tier", "pos_rank", "bye", "fp_name"]:
        add[c] = missing[c].values
    board = pd.concat([board, add], ignore_index=True)

# FFC ADP join by name+pos (FFC publishes no ids)
ffc["name_key"] = ffc.name.map(name_key)
ffc.loc[ffc.position == "DEF", "name_key"] = "def" + ffc.loc[ffc.position == "DEF", "team"].str.lower()
ffc_cols = ffc[["name_key", "position", "adp", "stdev", "times_drafted"]].rename(
    columns={"position": "pos", "adp": "ffc_adp", "stdev": "ffc_sd", "times_drafted": "ffc_n"})
board = board.merge(ffc_cols, on=["name_key", "pos"], how="left")

# 2025 stats via gsis
def col(df, *names):
    for n in names:
        if n in df.columns:
            return df[n]
    return pd.Series(0.0, index=df.index)

s = st25[st25.season_type.eq("REG")] if "season_type" in st25.columns else st25
s = s.copy()
s["pts_2025"] = [fantasy_points({
    "passing_yards": a, "passing_tds": b, "interceptions": c, "rushing_yards": d, "rushing_tds": e,
    "receptions": f, "receiving_yards": g, "receiving_tds": h, "fumbles_lost": i}, DEFAULT_SCORING)
    for a, b, c, d, e, f, g, h, i in zip(
        col(s, "passing_yards"), col(s, "passing_tds"), col(s, "passing_interceptions", "interceptions"),
        col(s, "rushing_yards"), col(s, "rushing_tds"), col(s, "receptions"), col(s, "receiving_yards"),
        col(s, "receiving_tds"),
        col(s, "sack_fumbles_lost") + col(s, "rushing_fumbles_lost") + col(s, "receiving_fumbles_lost"))]
s["ppg_2025"] = s.pts_2025 / s.games.replace(0, np.nan)
s["targets_2025"] = col(s, "targets"); s["carries_2025"] = col(s, "carries")
s25 = s[["player_id", "games", "pts_2025", "ppg_2025", "targets_2025", "carries_2025"]].rename(
    columns={"player_id": "gsis_id", "games": "g_2025"}).dropna(subset=["gsis_id"]).drop_duplicates("gsis_id")
board = board.merge(s25, on="gsis_id", how="left")

# Sleeper player dump: injuries, age, depth
def pinfo(pid):
    p = players.get(str(pid)) if pd.notna(pid) else None
    if not p:
        return pd.Series({"injury": None, "injury_part": None, "age": None, "exp": None, "depth": None})
    return pd.Series({"injury": p.get("injury_status"), "injury_part": p.get("injury_body_part"),
                      "age": p.get("age"), "exp": p.get("years_exp"), "depth": p.get("depth_chart_order")})
board = pd.concat([board.drop(columns=["injury"], errors="ignore"), board.sleeper_id.apply(pinfo)], axis=1)

# ---------------------------------------------------------------- ECR-implied points and blend
board["pts_sl_adj"] = board.pts_sl
for k, miss in GAMES_MISSED.items():
    board.loc[board.name_key == k, "pts_sl_adj"] = board.loc[board.name_key == k, "pts_sl"] * (17 - miss) / 17
board["ecr_pos_rank"] = board.groupby("pos").rank_ave.rank(method="first")
board["pts_ecr"] = np.nan
for p in POS:
    sub = board[(board.pos == p) & board.pts_sl_adj.gt(0)]
    curve = np.sort(sub.pts_sl_adj.values)[::-1]
    if len(curve) < 3:
        continue
    ranks = np.arange(1, len(curve) + 1)
    # smooth monotone curve: running mean of 3 then enforce monotone decreasing
    sm = pd.Series(curve).rolling(3, center=True, min_periods=1).mean().values
    sm = np.minimum.accumulate(sm)
    mask = (board.pos == p) & board.ecr_pos_rank.notna()
    board.loc[mask, "pts_ecr"] = np.interp(board.loc[mask, "ecr_pos_rank"], ranks, sm,
                                           right=max(sm[-1] * 0.8, 0))
w_sl = 0.5
board["proj"] = np.where(board.pts_ecr.notna() & board.pts_sl_adj.gt(0),
                         w_sl * board.pts_sl_adj + (1 - w_sl) * board.pts_ecr,
                         board.pts_ecr.fillna(board.pts_sl_adj))
board["proj"] = board.proj.fillna(0)
board = board[board.proj > 0].copy()

# ---------------------------------------------------------------- ADP blend + availability
board["adp"] = np.where(board.adp_half.notna() & board.ffc_adp.notna(),
                        0.6 * board.adp_half + 0.4 * board.ffc_adp,
                        board.adp_half.fillna(board.ffc_adp))
# undrafted-in-ADP players: park them past the ECR-implied slot
board["adp"] = board.adp.fillna(board.rank_ave + 25).fillna(300)
board["adp_sd"] = board.adp.map(adp_sd)
for pk in MY_PICKS[:10]:
    board[f"p{pk}"] = [p_available(a, s_, pk) for a, s_ in zip(board.adp, board.adp_sd)]

# ---------------------------------------------------------------- replacement levels (order-statistic fill)
STARTERS = {p: ROSTER_SLOTS[p] * NUM_TEAMS for p in ["QB", "RB", "WR", "TE", "K"]}
STARTERS["DEF"] = ROSTER_SLOTS["DST"] * NUM_TEAMS
N_FLEX = ROSTER_SLOTS["FLEX"] * NUM_TEAMS
REPL, FLEX_MIX = replacement_levels(board, STARTERS, N_FLEX)
# bench/waiver baseline: what is freely available mid-season ~ starters + 1 per team for RB/WR
WAIVER_RANK = {"QB": 16, "RB": 40, "WR": 44, "TE": 16, "K": 14, "DEF": 14}
WAIVER = {p: float(board[board.pos == p].nlargest(WAIVER_RANK[p], "proj").proj.min()) for p in POS}
board["repl"] = board.pos.map(REPL)
board["vor"] = board.proj - board.repl
board["vor_waiver"] = board.proj - board.pos.map(WAIVER)
board["pos_rank_proj"] = board.groupby("pos").proj.rank(ascending=False, method="first").astype(int)
board = board.sort_values(["vor", "proj"], ascending=False).reset_index(drop=True)
board["rank_vor"] = np.arange(1, len(board) + 1)

# ---------------------------------------------------------------- Monte Carlo snake draft
N_TEAMS, ROUNDS = NUM_TEAMS, DRAFT_ROUNDS
ME = MY_DRAFT_SLOT - 1
CAPS = {"QB": 2, "RB": 7, "WR": 8, "TE": 2, "K": 1, "DEF": 1}
sim = board[board.adp < 260].reset_index(drop=True)
n = len(sim)
pos_arr = sim.pos.values; proj_arr = sim.proj.values; adp_arr = sim.adp.values; sd_arr = sim.adp_sd.values
pos_idx = {p: np.where(pos_arr == p)[0] for p in POS}

def pick_order():
    order = []
    for r in range(ROUNDS):
        slots = list(range(N_TEAMS)) if r % 2 == 0 else list(range(N_TEAMS))[::-1]
        order += [(r + 1, s) for s in slots]
    return order
ORDER = pick_order()

def opp_allowed(counts, rnd, p):
    if counts[p] >= CAPS[p]:
        return False
    if p in ("K", "DEF") and rnd < 12:
        return False
    if p == "QB" and counts["QB"] >= 1 and rnd < 11:
        return False
    if p == "TE" and counts["TE"] >= 1 and rnd < 11:
        return False
    return True

def lineup_points(idxs):
    """Optimal starting lineup points + a bench term (0.25 x best bench RB/WR over waiver)."""
    idxs = list(idxs)
    total, starters = optimal_lineup([(pos_arr[i], float(proj_arr[i])) for i in idxs], ROSTER_SLOTS)
    bench = [idxs[j] for j in range(len(idxs)) if j not in starters and pos_arr[idxs[j]] in ("RB", "WR")]
    bench_val = float(np.clip(sim.loc[bench, "vor_waiver"].nlargest(3).sum(), 0, None)) if bench else 0.0
    return total + 0.25 * bench_val

def expected_best(avail_mask, p, now, nxt):
    """E[max proj available at pick nxt] for position p, given availability now."""
    ii = pos_idx[p][avail_mask[pos_idx[p]]]
    if len(ii) == 0:
        return 0.0
    ii = ii[np.argsort(-proj_arr[ii])]
    # P(still there at nxt | there now)
    pa = np.array([p_survive(adp_arr[i], sd_arr[i], now, nxt) for i in ii])
    e, surv = 0.0, 1.0
    for v, q in zip(proj_arr[ii], pa):
        e += surv * q * v
        surv *= (1 - q)
    return e

def my_choice(policy, avail_mask, counts, rnd, pick_no, next_pick, forced=None):
    if forced:
        cands = pos_idx[forced][avail_mask[pos_idx[forced]]]
        if len(cands):
            return cands[np.argmax(proj_arr[cands])]
    best, best_val = None, -1e9
    for p in POS:
        cands = pos_idx[p][avail_mask[pos_idx[p]]]
        if len(cands) == 0 or counts[p] >= CAPS[p]:
            continue
        i = cands[np.argmax(proj_arr[cands])]
        if p in ("K", "DEF"):
            if rnd < 14:
                continue
            val = proj_arr[i] - REPL[p]
        else:
            starters_need = {"QB": 1, "RB": 2, "WR": 2, "TE": 1}[p]
            if policy == "static":
                val = proj_arr[i] - REPL[p]
            else:  # dynamic VONA
                val = proj_arr[i] - expected_best(avail_mask, p, pick_no, next_pick)
            # need weighting: past the starter count the marginal player is a flex/bench piece
            if counts[p] >= starters_need:
                val = val * (0.85 if p in ("RB", "WR") else 0.35)
            if p == "QB" and counts["QB"] >= 1:
                val -= 40
            if p == "TE" and counts["TE"] >= 1:
                val -= 40
        if val > best_val:
            best, best_val = i, val
    return best

POLICIES = {
    "static_vor": ("static", {}),
    "dynamic_vona": ("dynamic", {}),
    "rb_rb_rb": ("dynamic", {1: "RB", 2: "RB", 3: "RB"}),
    "rb_wr_wr": ("dynamic", {1: "RB", 2: "WR", 3: "WR"}),
    "rb_rb_wr": ("dynamic", {1: "RB", 2: "RB", 3: "WR"}),
    "rb_te_wr": ("dynamic", {1: "RB", 2: "TE", 3: "WR"}),
    "rb_wr_qb": ("dynamic", {1: "RB", 2: "WR", 3: "QB"}),
    "wr_at_1": ("dynamic", {1: "WR"}),
}

# Manager-specific behaviour from league history (analyze_competition.py):
# per draft slot, how many picks EARLIER than the market each manager takes
# his first QB/TE/RB/WR, and how noisy his board is relative to the league.
SHIFT = np.zeros((N_TEAMS, n)); SD_MULT = np.ones(N_TEAMS)
_shift_file = OUTPUTS / "competition_shifts_2026.csv"
if _shift_file.exists():
    _sh = pd.read_csv(_shift_file)
    for _, r in _sh.iterrows():
        t = int(r.slot) - 1
        for p in ["QB", "TE", "RB", "WR"]:
            SHIFT[t, pos_idx[p]] = -float(r[f"{p}_shift"])   # earlier = lower effective ADP
        SD_MULT[t] = float(r.sd_mult)
    USE_HISTORY = True
else:
    USE_HISTORY = False


def run_sim(policy_name, n_sims=300, ghost_me=False):
    """ghost_me: my picks do not remove players from the pool, so avail_at records
    the true counterfactual 'if I pass on him, does the room leave him for me'."""
    kind, script = POLICIES[policy_name]
    scores, rosters, avail_at = [], [], {pk: [] for pk in MY_PICKS}
    for _ in range(n_sims):
        boards = [np.argsort(adp_arr + SHIFT[t] + rng.normal(0, 1, n) * sd_arr * SD_MULT[t]) for t in range(N_TEAMS)]
        ptr = [0] * N_TEAMS
        avail = np.ones(n, bool)
        # Counterfactual availability for the war-room page: who the ROOM has
        # left alone by each of my picks, ignoring my own selections, so a
        # player I usually take at 25 still shows his true odds of lasting to 48.
        avail_opp = np.ones(n, bool)
        counts = [dict.fromkeys(POS, 0) for _ in range(N_TEAMS)]
        mine = []
        for k, (rnd, team) in enumerate(ORDER):
            pick_no = k + 1
            if team == ME:
                avail_at[pick_no].append(np.where(avail_opp)[0].copy())
                nxt = next((q for q in MY_PICKS if q > pick_no), 200)
                i = my_choice(kind, avail, counts[ME], rnd, pick_no, nxt, script.get(rnd))
                if i is None:
                    i = np.where(avail)[0][0]
            else:
                b = boards[team]
                while True:
                    i = b[ptr[team]]
                    if avail[i] and opp_allowed(counts[team], rnd, pos_arr[i]):
                        break
                    ptr[team] += 1
                    if ptr[team] >= n:
                        i = np.where(avail)[0][0]; break
                # roster-fill: last rounds force K/DEF if missing
                if rnd >= 14 and counts[team]["K"] == 0 and rnd == 14:
                    kk = pos_idx["K"][avail[pos_idx["K"]]]
                    if len(kk): i = kk[np.argmax(proj_arr[kk])]
                if rnd == 15 and counts[team]["DEF"] == 0:
                    dd = pos_idx["DEF"][avail[pos_idx["DEF"]]]
                    if len(dd): i = dd[np.argmax(proj_arr[dd])]
            if not (team == ME and ghost_me):
                avail[i] = False
            counts[team][pos_arr[i]] += 1
            if team == ME:
                mine.append(i)
            else:
                avail_opp[i] = False
        scores.append(lineup_points(mine))
        rosters.append(mine)
    return np.array(scores), rosters, avail_at

if __name__ == "__main__":
    pd.set_option("display.width", 250)
    print(f"My picks (slot {MY_DRAFT_SLOT}/{NUM_TEAMS}): {MY_PICKS}")
    print(f"Join coverage top-200 by ECR: ECR matched {board.rank_ave.notna().sum()} rows; "
          f"FFC ADP matched {board.ffc_adp.notna().sum()}; Sleeper ADP {board.adp_half.notna().sum()}; 2025 stats {board.pts_2025.notna().sum()}")
    print("\nReplacement (starter fill):", {k: round(v, 1) for k, v in REPL.items()}, "| flex mix:", FLEX_MIX)
    print("Waiver baseline:", {k: round(v, 1) for k, v in WAIVER.items()})
    cols = ["rank_vor", "name", "pos", "team", "bye", "proj", "pts_sl_adj", "pts_ecr", "vor", "tier", "rank_ave", "rank_std", "adp_half", "ffc_adp", "adp", "p24", "p25", "p48", "p49", "injury", "ppg_2025", "g_2025", "age"]
    print("\nTOP 120 BY VOR\n", board[cols].head(120).round(1).to_string(index=False))
    for p in POS:
        print(f"\n{p} top by proj\n", board[board.pos == p][["pos_rank_proj", "name", "team", "bye", "proj", "vor", "rank_ave", "adp", "p24", "p48", "p72", "p96", "injury", "ppg_2025", "age"]].head(36 if p in ("RB", "WR") else 16).round(2).to_string(index=False))

    OUTPUTS.mkdir(parents=True, exist_ok=True)
    board.to_csv(OUTPUTS / "draft2026_board.csv", index=False)

    n_sims = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    print(f"\n=== Monte Carlo, {n_sims} drafts per policy ===")
    results = {}
    for name in POLICIES:
        sc, ro, av = run_sim(name, n_sims)
        results[name] = (sc, ro, av)
        print(f"{name:14s} mean {sc.mean():7.1f}  p10 {np.percentile(sc, 10):7.1f}  p90 {np.percentile(sc, 90):7.1f}")
    best = max(results, key=lambda k: results[k][0].mean())
    sc, ro, av = results[best]
    # History-aware survival odds at each of my picks, from a larger run of the
    # best policy; exported as ph{pick} columns for the war-room page.
    n_avail = int(sys.argv[2]) if len(sys.argv) > 2 else 1000
    _, _, av_big = run_sim(best, n_avail, ghost_me=True)
    for pk in MY_PICKS:
        cnt = np.zeros(n)
        for arr in av_big[pk]:
            cnt[arr] += 1
        ph = pd.Series(cnt / max(1, len(av_big[pk])), index=sim.name_key)
        board[f"ph{pk}"] = board.name_key.map(ph).fillna(0.0).round(3)
    board.to_csv(OUTPUTS / "draft2026_board.csv", index=False)
    print(f"\nExported ph{{pick}} survival odds from {n_avail} history-aware drafts ({best}, ghost-me counterfactual).")
    print(f"\nBest policy: {best}. Most common picks by round:")
    for r, pk in enumerate(MY_PICKS, 1):
        c = pd.Series([sim.name[m[r - 1]] + " (" + sim.pos[m[r - 1]] + ")" for m in ro]).value_counts(normalize=True).head(5)
        print(f"  R{r:2d} pick {pk:3d}: " + ", ".join(f"{k} {v:.0%}" for k, v in c.items()))
    print("\nWho is on the board at each of my picks (dynamic policy), P(available) from simulation:")
    sc, ro, av = results["dynamic_vona"]
    for pk in MY_PICKS[:8]:
        cnt = pd.Series(np.concatenate(av[pk])).value_counts() / len(av[pk])
        top = cnt[cnt.index.map(lambda i: adp_arr[i] < pk + 30)].sort_index()
        lst = sorted([(sim.name[i], sim.pos[i], round(float(sim.proj[i])), round(float(cnt[i]), 2)) for i in top.index], key=lambda t: -t[2])[:16]
        print(f"  pick {pk}: " + "; ".join(f"{a} {b} {c} p={d}" for a, b, c, d in lst))
