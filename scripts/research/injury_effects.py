"""What an injury type does to fantasy production, measured on 2023-25 data.

For each player-season, an "episode" is a run of weeks on the injury report
with one primary injury during which the player missed at least one game.
We compare league-scored points per game AFTER return (next 6 games) with
BEFORE the episode (same season), for players who were fantasy-relevant
before (>= 3 games, >= 6 PPG). We also measure the "questionable tag"
discount: points in games a player was listed Questionable but played,
versus his other games that season.

Run:  PYTHONPATH=src python scripts/research/injury_effects.py
Inputs: nflreadpy injuries 2023-25 (downloaded here), weekly stats cached in
data/research/cache/draft2026/ and data/research/cache/nflverse/.
"""
from __future__ import annotations

import re
from math import sqrt

import numpy as np
import pandas as pd

from gridiron.league_config import DEFAULT_SCORING
from gridiron.paths import RESEARCH_CACHE
from gridiron.scoring import fantasy_points

pd.set_option("display.width", 220)
C = RESEARCH_CACHE
inj = pd.concat([pd.read_csv(C / "draft2026" / "injuries_2025.csv", low_memory=False)] +
                [pd.read_csv(C / "injuries" / f"injuries_{s}.csv", low_memory=False)
                 for s in (2023, 2024) if (C / "injuries" / f"injuries_{s}.csv").exists()], ignore_index=True)
wk = pd.concat([pd.read_csv(C / "nflverse" / f"stats_player_week_{s}.csv", low_memory=False) for s in (2023, 2024, 2025)],
               ignore_index=True)
wk = wk[wk.season_type.eq("REG") & wk.position.isin(["QB", "RB", "WR", "TE"])].copy()


def col(df, *names):
    for n in names:
        if n in df.columns:
            return df[n].fillna(0)
    return pd.Series(0.0, index=df.index)


wk["pts"] = [fantasy_points({
    "passing_yards": a, "passing_tds": b, "interceptions": c, "rushing_yards": d, "rushing_tds": e,
    "receptions": f, "receiving_yards": g, "receiving_tds": h, "fumbles_lost": i}, DEFAULT_SCORING)
    for a, b, c, d, e, f, g, h, i in zip(
        col(wk, "passing_yards"), col(wk, "passing_tds"), col(wk, "passing_interceptions", "interceptions"),
        col(wk, "rushing_yards"), col(wk, "rushing_tds"), col(wk, "receptions"), col(wk, "receiving_yards"),
        col(wk, "receiving_tds"), col(wk, "sack_fumbles_lost") + col(wk, "rushing_fumbles_lost") + col(wk, "receiving_fumbles_lost"))]
wk["played"] = (col(wk, "attempts") + col(wk, "carries") + col(wk, "targets")) > 0
played = wk[wk.played][["season", "week", "player_id", "position", "pts"]]

inj = inj[inj.game_type.eq("REG") & inj.position.isin(["QB", "RB", "WR", "TE"])].copy()
inj["injury"] = inj.report_primary_injury.fillna("").str.strip().str.title()
NORM = {r"Ankle": "Ankle", r"Hamstring": "Hamstring", r"Knee|Acl|Mcl|Meniscus": "Knee", r"Groin": "Groin",
        r"Concussion": "Concussion", r"Shoulder": "Shoulder", r"Foot|Toe|Turf": "Foot/Toe", r"Calf": "Calf",
        r"Quad|Thigh": "Quad", r"Hip": "Hip", r"Back": "Back", r"Rib|Chest|Oblique|Abdomen|Core": "Ribs/Core",
        r"Achilles": "Achilles", r"Hand|Finger|Thumb|Wrist": "Hand/Wrist", r"Elbow|Arm|Bicep|Tricep": "Arm",
        r"Neck": "Neck", r"Illness": "Illness"}
def norm(s):
    for pat, lab in NORM.items():
        if re.search(pat, s, re.I):
            return lab
    return "Other" if s else "Unlisted"
inj["injury"] = inj.injury.map(norm)
inj["status"] = inj.report_status.fillna("").str.title()

# ---------------------------------------------------------------- episodes: missed games
rows = []
for (s, pid), g in inj.groupby(["season", "gsis_id"]):
    g = g.sort_values("week")
    p = played[(played.season == s) & (played.player_id == pid)]
    pweeks = set(p.week)
    pos = g.position.iloc[0]
    for injury, gg in g.groupby("injury"):
        weeks = sorted(set(gg.week))
        # runs of consecutive report weeks
        runs, cur = [], [weeks[0]]
        for w in weeks[1:]:
            if w == cur[-1] + 1:
                cur.append(w)
            else:
                runs.append(cur); cur = [w]
        runs.append(cur)
        for run in runs:
            missed = [w for w in run if w not in pweeks]
            if not missed:
                continue
            first_missed = min(missed)
            ret = min([w for w in pweeks if w > max(missed)], default=None)
            pre = p[p.week < first_missed]
            post = p[(p.week >= ret)].head(6) if ret else p.iloc[0:0]
            rows.append(dict(season=s, pid=pid, pos=pos, injury=injury, first_missed=first_missed, games_missed=len(missed),
                             returned=ret is not None, pre_g=len(pre), pre_ppg=pre.pts.mean() if len(pre) else np.nan,
                             post_g=len(post), post_ppg=post.pts.mean() if len(post) else np.nan))
ep = pd.DataFrame(rows)
rel = ep[(ep.pre_g >= 3) & (ep.pre_ppg >= 6)]
print("=== Games missed per episode (fantasy-relevant players, 2023-25 regular season) ===")
gm = rel.groupby("injury").agg(n=("pid", "count"), games_missed_mean=("games_missed", "mean"),
                              games_missed_median=("games_missed", "median"), season_ending_share=("returned", lambda x: 1 - x.mean()))
print(gm[gm.n >= 8].sort_values("games_missed_mean", ascending=False).round(2).to_string())

print("\n=== After return vs before (same season, next 6 games), points per game ratio ===")
ret = rel[rel.returned & (rel.post_g >= 2)].copy()
ret["ratio"] = ret.post_ppg / ret.pre_ppg
ret["diff"] = ret.post_ppg - ret.pre_ppg
def summarize(g):
    n = len(g); d = g["diff"]; se = d.std(ddof=1) / sqrt(n) if n > 1 else np.nan
    return pd.Series(dict(n=n, pre_ppg=g.pre_ppg.mean(), post_ppg=g.post_ppg.mean(), ratio=g.post_ppg.sum() / g.pre_ppg.sum(),
                          diff=d.mean(), t=d.mean() / se if se else np.nan, worse_share=(d < 0).mean()))
by_inj = ret.groupby("injury").apply(summarize, include_groups=False)
print(by_inj[by_inj.n >= 8].sort_values("ratio").round(2).to_string())
print("\n  by position (all injuries pooled):")
print(ret.groupby("pos").apply(summarize, include_groups=False).round(2).to_string())
print("\n  ankle / hamstring / knee by position:")
sub = ret[ret.injury.isin(["Ankle", "Hamstring", "Knee", "Groin"])]
print(sub.groupby(["injury", "pos"]).apply(summarize, include_groups=False).round(2).to_string())
print("\n  baseline: healthy players' PPG drift over a season (same-season regression to the mean), pre = first 5 games, post = games 6-11:")
base = []
for (s, pid), p in played.groupby(["season", "player_id"]):
    p = p.sort_values("week")
    if len(p) >= 11 and p.head(5).pts.mean() >= 6:
        base.append(dict(pre=p.head(5).pts.mean(), post=p.iloc[5:11].pts.mean()))
base = pd.DataFrame(base)
print(f"    n={len(base)}, ratio={base.post.sum()/base.pre.sum():.2f}, mean diff={ (base.post-base.pre).mean():+.2f}")

# ---------------------------------------------------------------- questionable tag discount
print("\n=== Played while listed Questionable: that game's points vs the player's other games that season ===")
q = inj[inj.status.eq("Questionable")][["season", "week", "gsis_id", "injury"]].drop_duplicates()
m = played.merge(q, left_on=["season", "week", "player_id"], right_on=["season", "week", "gsis_id"], how="left")
m["q"] = m.gsis_id.notna()
out = []
for (s, pid), g in m.groupby(["season", "player_id"]):
    if g.q.sum() >= 1 and (~g.q).sum() >= 4 and g[~g.q].pts.mean() >= 6:
        out.append(dict(pos=g.position.iloc[0], q_ppg=g[g.q].pts.mean(), other_ppg=g[~g.q].pts.mean(), nq=int(g.q.sum()),
                        injury=g[g.q].injury.iloc[0]))
out = pd.DataFrame(out)
out["diff"] = out.q_ppg - out.other_ppg
print(f"  player-seasons {len(out)}: questionable-and-played PPG {out.q_ppg.mean():.1f} vs other games {out.other_ppg.mean():.1f} "
      f"(ratio {out.q_ppg.sum()/out.other_ppg.sum():.2f}, t={out['diff'].mean()/(out['diff'].std()/sqrt(len(out))):.1f})")
print(out.groupby("pos").agg(n=("diff", "count"), q_ppg=("q_ppg", "mean"), other_ppg=("other_ppg", "mean")).round(1).to_string())
bi = out.groupby("injury").agg(n=("diff", "count"), q_ppg=("q_ppg", "mean"), other_ppg=("other_ppg", "mean"))
bi["ratio"] = (bi.q_ppg / bi.other_ppg).round(2)
print(bi[bi.n >= 8].sort_values("ratio").round(1).to_string())
