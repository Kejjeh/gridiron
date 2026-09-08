"""Deeper league-history cuts: manager affinities (repeat players, NFL-team
loyalty, QB stacks), what roster shapes won, draft value realized vs price,
weekly scoring distribution, and the waiver market.

Run:  PYTHONPATH=src python scripts/research/analyze_competition_deep.py
Reads data/research/cache/sleeper_history/ + data/outputs/competition_picks_2023_2025.csv
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from gridiron.paths import OUTPUTS, RESEARCH_CACHE

H = RESEARCH_CACHE / "sleeper_history"
pd.set_option("display.width", 250)
picks = pd.read_csv(OUTPUTS / "competition_picks_2023_2025.csv", dtype={"uid": str})
prof = pd.read_csv(OUTPUTS / "competition_2026.csv")
cur = json.load(open(H / "season_2026.json", encoding="utf-8"))
cur_users = {u["user_id"]: u.get("display_name") for u in cur["users"]}
slot_of = {r["name"]: int(r.slot) for _, r in prof.iterrows()}
picks["name"] = picks.uid.map(cur_users)
picks = picks[picks.name.notna()].copy()
players = json.load(open(RESEARCH_CACHE / "draft2026" / "sleeper_players.json", encoding="utf-8"))

seasons = {}
for s in (2023, 2024, 2025):
    f = H / f"season_{s}.json"
    if f.exists():
        seasons[s] = json.load(open(f, encoding="utf-8"))

# ---------------------------------------------------------------- A. affinities
print("=== A. Repeat players (same manager drafted him in 2+ seasons) ===")
rep = picks.groupby(["name", "player"]).season.nunique().reset_index()
rep = rep[rep.season >= 2].sort_values(["name", "season"], ascending=[True, False])
for nm, g in rep.groupby("name"):
    print(f"  {nm} (slot {slot_of.get(nm)}): " + ", ".join(f"{p} x{n}" for p, n in zip(g.player, g.season)))

print("\n=== A2. NFL-team loyalty in rounds 1-8 (team share vs league-wide share; flagged if >= 3 picks and 2x) ===")
early = picks[(picks["round"] <= 8) & picks.team.notna()]
league_share = early.team.value_counts(normalize=True)
for nm, g in early.groupby("name"):
    vc = g.team.value_counts()
    flags = [(t, int(c), round(c / len(g) / league_share.get(t, 1e-9), 1)) for t, c in vc.items()
             if c >= 3 and c / len(g) >= 2 * league_share.get(t, 0)]
    if flags:
        print(f"  {nm} (slot {slot_of.get(nm)}): " + ", ".join(f"{t} {c} picks ({r}x league rate)" for t, c, r in flags))

print("\n=== A3. QB stacks drafted (same NFL team QB + pass-catcher by one manager, same season) ===")
for (nm, s), g in picks.groupby(["name", "season"]):
    qbs = g[g.pos == "QB"]
    for _, q in qbs.iterrows():
        mates = g[(g.team == q.team) & g.pos.isin(["WR", "TE"])]
        if len(mates):
            print(f"  {s} {nm}: {q.player} + " + ", ".join(mates.player))

# ---------------------------------------------------------------- B. what won
print("\n=== B. Roster shape by finish: positions taken in rounds 1-6, and first QB/TE round ===")
res = pd.read_csv(OUTPUTS / "competition_results_2023_2025.csv", dtype={"uid": str})
res["name"] = res.uid.map(cur_users)
rows = []
for (uid, s), g in picks.groupby(["uid", "season"]):
    r6 = g[g["round"] <= 6]
    rr = res[(res.uid == uid) & (res.season == s)]
    if not len(rr):
        continue
    rr = rr.iloc[0]
    rows.append(dict(season=s, name=cur_users.get(uid), wins=rr.wins, pf=rr.pf, champ=rr.champion,
                     rb6=int((r6.pos == "RB").sum()), wr6=int((r6.pos == "WR").sum()),
                     qb_rd=int(g[g.pos == "QB"]["round"].min()) if (g.pos == "QB").any() else 99,
                     te_rd=int(g[g.pos == "TE"]["round"].min()) if (g.pos == "TE").any() else 99,
                     rookies=int(g.is_rookie.sum()), reach=round(g.reach.mean(), 1)))
shape = pd.DataFrame(rows)
shape["ppg"] = shape.pf / 14
print(shape.sort_values(["season", "wins", "pf"], ascending=[False, False, False]).to_string(index=False))
print("\n  correlations with points per game (30 team-seasons, so treat as hints):")
for c in ["rb6", "wr6", "qb_rd", "te_rd", "rookies", "reach"]:
    print(f"    {c:8s} r = {shape[c].corr(shape.ppg):+.2f}")

# ---------------------------------------------------------------- C. draft value realized
print("\n=== C. Draft skill: season points of drafted players vs what the pick slot usually returns ===")
# realized points: use the roster's final fpts as team outcome; for player-level, use Sleeper weekly matchups' players_points
val = []
for s, rec in seasons.items():
    pts = Counter()
    for wk, ms in rec["matchups"].items():
        for m in ms:
            for pid, p in (m.get("players_points") or {}).items():
                pts[pid] += p
    pk = picks[picks.season == s]
    # map draft pick -> sleeper player_id via the raw picks
    raw = {p["pick_no"]: p["player_id"] for p in rec["picks"]}
    for _, r in pk.iterrows():
        pid = raw.get(r.pick)
        val.append(dict(season=s, name=r["name"], round=r["round"], pick=r.pick, pos=r.pos, player=r.player,
                        pts=pts.get(pid, 0.0)))
val = pd.DataFrame(val)
# expected points by pick number within season: rolling median of pts vs pick (12-pick window)
val["exp"] = np.nan
for s, g in val.groupby("season"):
    g = g.sort_values("pick")
    med = g.pts.rolling(15, center=True, min_periods=5).median()
    val.loc[g.index, "exp"] = med.values
val["surplus"] = val.pts - val.exp
skill = val[val["round"] <= 10].groupby("name").agg(picks=("pick", "count"), surplus_pg=("surplus", "mean"),
                                                   hits=("surplus", lambda x: (x > 40).mean()),
                                                   busts=("surplus", lambda x: (x < -40).mean())).reset_index()
skill["slot"] = skill.name.map(slot_of)
print(skill.sort_values("surplus_pg", ascending=False).round(2).to_string(index=False))
print("\n  best and worst individual picks (rounds 1-6), points scored FOR THAT MANAGER'S LINEUPS that season:")
top = val[val["round"] <= 6].sort_values("surplus")
print("  worst:", "; ".join(f"{r.season} {r['name']} R{r['round']} {r.player} {r.pts:.0f} pts ({r.surplus:+.0f})" for _, r in top.head(8).iterrows()))
print("  best: ", "; ".join(f"{r.season} {r['name']} R{r['round']} {r.player} {r.pts:.0f} pts ({r.surplus:+.0f})" for _, r in top.tail(8).iterrows()))

# ---------------------------------------------------------------- D. weekly scoring in this league
print("\n=== D. Weekly scoring (2025, 12-team, half-PPR): what a lineup needs ===")
wk = []
for s, rec in seasons.items():
    for w, ms in rec["matchups"].items():
        for m in ms:
            wk.append(dict(season=s, week=int(w), roster=m["roster_id"], pts=m.get("points", 0), mid=m.get("matchup_id")))
wk = pd.DataFrame(wk)
w25 = wk[(wk.season == 2025) & (wk.week <= 14) & (wk.pts > 0)]
print(f"  2025 team-week mean {w25.pts.mean():.1f}, sd {w25.pts.std():.1f}, median {w25.pts.median():.1f}, "
      f"80th pct {w25.pts.quantile(.8):.1f}, 20th pct {w25.pts.quantile(.2):.1f}")
mg = w25.groupby(["week", "mid"]).pts.agg(["max", "min"]).reset_index()
print(f"  average margin of victory {(mg['max'] - mg['min']).mean():.1f}; one-score games (<10) {((mg['max'] - mg['min']) < 10).mean():.0%}")
print(f"  a team scoring the league median wins {(w25.pts > w25.pts.median()).mean():.0%} of the time by construction; "
      f"points to win 70% of weeks ~ {w25.pts.quantile(.7):.0f}")

# ---------------------------------------------------------------- E. waiver market
print("\n=== E. Waiver market (FAAB $100): winning bids by position, biggest bids, who bids ===")
bids = []
for s, rec in seasons.items():
    rid_name = {r["roster_id"]: cur_users.get(r.get("owner_id"), r.get("owner_id")) for r in rec["rosters"]}
    for t in rec["transactions"]:
        if t.get("type") != "waiver" or t.get("status") != "complete":
            continue
        bid = (t.get("settings") or {}).get("waiver_bid", 0)
        for pid in (t.get("adds") or {}):
            p = players.get(pid, {})
            bids.append(dict(season=s, week=t["week"], name=rid_name.get((t.get("roster_ids") or [None])[0]),
                             player=p.get("full_name") or pid, pos=p.get("position"), bid=bid))
bids = pd.DataFrame(bids)
if len(bids):
    print(bids.groupby("pos").bid.agg(["count", "mean", "median", "max"]).round(1).to_string())
    print("  biggest winning bids:", "; ".join(f"{r.season} wk{r.week} {r['name']} ${r.bid} {r.player} ({r.pos})" for _, r in bids.sort_values("bid", ascending=False).head(10).iterrows()))
    print("  $0-$1 claims that won:", int((bids.bid <= 1).sum()), "of", len(bids))
    print("  claims by manager:", bids.groupby("name").bid.agg(["count", "sum"]).sort_values("count", ascending=False).to_dict("index"))
