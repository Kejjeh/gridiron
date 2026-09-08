"""Profile every manager in the league from its Sleeper history (2023-25):
draft tendencies (position timing, reach vs the market of the day, rookies),
results (record, points, playoffs), and activity (waivers, FAAB, trades).

Run:  PYTHONPATH=src python scripts/research/analyze_competition.py
Reads data/research/cache/sleeper_history/ (pull_sleeper_history.py).
Writes data/outputs/competition_2026.csv + stdout report.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

import numpy as np
import pandas as pd

from gridiron.paths import OUTPUTS, RESEARCH_CACHE

H = RESEARCH_CACHE / "sleeper_history"
pd.set_option("display.width", 250)
SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")


def name_key(s):
    s = str(s).lower().replace(".", "").replace("'", "")
    return re.sub(r"[^a-z]", "", SUFFIX.sub("", s))


chain = json.load(open(H / "chain.json"))
seasons = {}
for lg in chain:
    f = H / f"season_{lg['season']}.json"
    if f.exists():
        seasons[int(lg["season"])] = json.load(open(f, encoding="utf-8"))

# ---------------------------------------------------------------- identity
names = {}
for s, rec in seasons.items():
    for u in rec["users"]:
        names[u["user_id"]] = u.get("display_name") or u["user_id"]
cur = seasons[max(seasons)]
cur_users = {u["user_id"]: u.get("display_name") for u in cur["users"]}
draft_order = (cur["drafts"][0].get("draft_order") or {}) if cur["drafts"] else {}
slot_of = {uid: slot for uid, slot in draft_order.items()}

# ---------------------------------------------------------------- picks vs market
rows = []
for s, rec in seasons.items():
    if not rec["picks"]:
        continue
    adp = {}
    fa = H / f"ffc_adp_{s}.json"
    if fa.exists():
        for p in json.load(open(fa))["players"]:
            adp[(name_key(p["name"]), p["position"].replace("PK", "K").replace("DEF", "DEF"))] = p["adp"]
    n_teams = rec["league"]["total_rosters"]
    roster_owner = {r["roster_id"]: r.get("owner_id") for r in rec["rosters"]}
    for p in rec["picks"]:
        md = p.get("metadata") or {}
        pos = md.get("position") or ""
        key = (name_key(f"{md.get('first_name','')} {md.get('last_name','')}"), pos)
        a = adp.get(key)
        uid = p.get("picked_by") or roster_owner.get(p.get("roster_id"))
        rows.append(dict(season=s, teams=n_teams, uid=uid, name=names.get(uid, uid), round=p["round"], pick=p["pick_no"],
                         slot=p.get("draft_slot"), pos=pos, player=f"{md.get('first_name','')} {md.get('last_name','')}",
                         team=md.get("team"), yrs=pd.to_numeric(md.get("years_exp"), errors="coerce"), adp=a,
                         reach=(a - p["pick_no"]) if a else np.nan,  # + = took him before the market would
                         keeper=bool(p.get("is_keeper"))))
picks = pd.DataFrame(rows)
picks["is_rookie"] = picks.yrs.eq(0)
OUT_PICKS = OUTPUTS / "competition_picks_2023_2025.csv"

# ---------------------------------------------------------------- results + activity
res = []
for s, rec in seasons.items():
    if not rec["rosters"] or not rec["matchups"]:
        continue
    champ = None
    wb = rec.get("winners_bracket") or []
    finals = [m for m in wb if m.get("p") == 1]
    if finals:
        champ = finals[0].get("w")
    tx = rec["transactions"]
    by_roster = defaultdict(lambda: Counter())
    faab = Counter()
    for t in tx:
        if t.get("status") != "complete":
            continue
        for rid in t.get("roster_ids") or []:
            by_roster[rid][t["type"]] += 1
            if t["type"] == "waiver" and (t.get("settings") or {}).get("waiver_bid"):
                faab[rid] += t["settings"]["waiver_bid"]
    for r in rec["rosters"]:
        st = r.get("settings", {})
        pf = st.get("fpts", 0) + st.get("fpts_decimal", 0) / 100
        pa = st.get("fpts_against", 0) + st.get("fpts_against_decimal", 0) / 100
        res.append(dict(season=s, uid=r.get("owner_id"), name=names.get(r.get("owner_id"), r.get("owner_id")),
                        wins=st.get("wins", 0), losses=st.get("losses", 0), pf=round(pf, 1), pa=round(pa, 1),
                        waivers=by_roster[r["roster_id"]]["waiver"], fa_adds=by_roster[r["roster_id"]]["free_agent"],
                        trades=by_roster[r["roster_id"]]["trade"], faab=faab[r["roster_id"]],
                        champion=(r["roster_id"] == champ), teams=rec["league"]["total_rosters"]))
results = pd.DataFrame(res)

# ---------------------------------------------------------------- per-manager profile
prof = []
for uid, dn in cur_users.items():
    pk = picks[picks.uid == uid]
    rs = results[results.uid == uid]
    d = dict(slot=slot_of.get(uid), name=dn, seasons=sorted(pk.season.unique().tolist()))
    if len(pk):
        early = pk[pk["round"] <= 3]
        d.update(
            r1_pos="/".join(pk[pk["round"] == 1].sort_values("season").pos.tolist()),
            rb_r1_3=int((early.pos == "RB").sum()), wr_r1_3=int((early.pos == "WR").sum()),
            qb_round="/".join(str(x) for x in pk[pk.pos == "QB"].groupby("season")["round"].min().tolist()),
            te_round="/".join(str(x) for x in pk[pk.pos == "TE"].groupby("season")["round"].min().tolist()),
            k_round="/".join(str(x) for x in pk[pk.pos == "K"].groupby("season")["round"].min().tolist()),
            def_round="/".join(str(x) for x in pk[pk.pos == "DEF"].groupby("season")["round"].min().tolist()),
            qb2_by_r10=int(((pk.pos == "QB") & (pk["round"] <= 10)).groupby(pk.season).sum().ge(2).sum()),
            reach_mean=round(pk.reach.mean(), 1), reach_gt12=round((pk.reach > 12).mean(), 2),
            value_gt12=round((pk.reach < -12).mean(), 2), rookie_share=round(pk.is_rookie.mean(), 2),
            rookies_r1_5=int(pk[(pk["round"] <= 5) & pk.is_rookie].shape[0]),
            unmatched_adp=round(pk.adp.isna().mean(), 2),
        )
    if len(rs):
        d.update(record="/".join(f"{w}-{l}" for w, l in zip(rs.wins, rs.losses)),
                 win_pct=round(rs.wins.sum() / max(1, (rs.wins + rs.losses).sum()), 3),
                 pf_pg=round((rs.pf / (rs.wins + rs.losses).clip(lower=1)).mean(), 1),
                 titles=int(rs.champion.sum()), waivers=int(rs.waivers.sum()), faab=int(rs.faab.sum()),
                 trades=int(rs.trades.sum()))
    prof.append(d)
profile = pd.DataFrame(prof).sort_values("slot")

if __name__ == "__main__":
    print("Seasons:", {s: (rec["league"]["total_rosters"], rec["league"]["status"]) for s, rec in seasons.items()})
    print("\n=== 2026 draft order, with each manager's history ===")
    print(profile.to_string(index=False))
    print("\n=== Season results ===")
    print(results.sort_values(["season", "wins", "pf"], ascending=[False, False, False]).to_string(index=False))
    print("\n=== Position flow in the early rounds, this league vs ADP ===")
    for s in sorted(picks.season.unique()):
        pk = picks[picks.season == s]
        for lo, hi in [(1, 12), (13, 24), (25, 36), (37, 48)]:
            seg = pk[(pk.pick >= lo) & (pk.pick <= hi)]
            print(f"  {s} picks {lo:2d}-{hi:2d}: {dict(Counter(seg.pos))}")
    print("\n=== QB and TE timing league-wide (first QB/TE taken per team, by pick number) ===")
    for s in sorted(picks.season.unique()):
        pk = picks[picks.season == s]
        for pos in ["QB", "TE"]:
            firsts = pk[pk.pos == pos].groupby("uid").pick.min().sort_values().tolist()
            print(f"  {s} {pos}: {firsts}")
    print("\n=== Biggest reaches (pick vs FFC ADP that year), by manager ===")
    big = picks[picks.reach > 20].sort_values("reach", ascending=False)
    print(big[["season", "name", "round", "pick", "pos", "player", "adp", "reach"]].head(30).to_string(index=False))
    print("\n=== Managers' 2025 picks, rounds 1-6 (the 12-team year) ===")
    p25 = picks[(picks.season == 2025) & (picks["round"] <= 6)].sort_values("pick")
    print(p25[["round", "pick", "name", "pos", "player", "adp", "reach"]].to_string(index=False))
    OUTPUTS.mkdir(parents=True, exist_ok=True)
    profile.to_csv(OUTPUTS / "competition_2026.csv", index=False)
    picks.to_csv(OUT_PICKS, index=False)
    results.to_csv(OUTPUTS / "competition_results_2023_2025.csv", index=False)
