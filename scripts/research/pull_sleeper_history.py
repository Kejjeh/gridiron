"""Walk this league's Sleeper history (previous_league_id chain) and cache
every season's users, rosters, drafts, picks, and transactions, plus the
FantasyFootballCalculator ADP for each of those seasons so picks can be
scored against the market of the day. Public API, no auth.

Run:  PYTHONPATH=src python scripts/research/pull_sleeper_history.py
Output -> data/research/cache/sleeper_history/ (gitignored)
"""
from __future__ import annotations

import json
import time
import urllib.request

from gridiron.config import get_settings
from gridiron.paths import RESEARCH_CACHE

OUT = RESEARCH_CACHE / "sleeper_history"
OUT.mkdir(parents=True, exist_ok=True)
UA = {"User-Agent": "gridiron-research/0.1 (python-urllib)"}


def get(url, tries=3):
    for t in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode())
        except Exception as e:  # noqa: BLE001
            if t == tries - 1:
                print("FAILED", url, e)
                return None
            time.sleep(1.5)


def dump(name, obj):
    (OUT / f"{name}.json").write_text(json.dumps(obj), encoding="utf-8")


league_id = get_settings().sleeper_league_id
chain = []
lid = league_id
while lid:
    lg = get(f"https://api.sleeper.app/v1/league/{lid}")
    if not lg:
        break
    chain.append(lg)
    print(f"season {lg['season']}  league {lid}  status {lg['status']}  name {lg['name']!r}  teams {lg['total_rosters']}")
    lid = lg.get("previous_league_id")
dump("chain", chain)

seasons = []
for lg in chain:
    lid, season = lg["league_id"], lg["season"]
    users = get(f"https://api.sleeper.app/v1/league/{lid}/users") or []
    rosters = get(f"https://api.sleeper.app/v1/league/{lid}/rosters") or []
    drafts = get(f"https://api.sleeper.app/v1/league/{lid}/drafts") or []
    picks = []
    for d in drafts:
        p = get(f"https://api.sleeper.app/v1/draft/{d['draft_id']}/picks") or []
        for row in p:
            row["draft_id"] = d["draft_id"]
        picks += p
    tx = []
    for wk in range(1, 19):
        t = get(f"https://api.sleeper.app/v1/league/{lid}/transactions/{wk}")
        if t:
            for row in t:
                row["week"] = wk
            tx += t
    matchups = {}
    for wk in range(1, 18):
        m = get(f"https://api.sleeper.app/v1/league/{lid}/matchups/{wk}")
        if m:
            matchups[wk] = m
    winners = get(f"https://api.sleeper.app/v1/league/{lid}/winners_bracket")
    rec = dict(league=lg, users=users, rosters=rosters, drafts=drafts, picks=picks, transactions=tx,
               matchups=matchups, winners_bracket=winners)
    dump(f"season_{season}", rec)
    seasons.append(season)
    print(f"  {season}: users {len(users)} rosters {len(rosters)} drafts {len(drafts)} picks {len(picks)} "
          f"transactions {len(tx)} matchup-weeks {len(matchups)}")

# market of the day for scoring picks (FFC ADP, 12-team; scoring format per season settings)
for lg in chain:
    season = lg["season"]
    rec = lg.get("scoring_settings", {}).get("rec", 0)
    fmt = "ppr" if rec >= 1 else ("half-ppr" if rec > 0 else "standard")
    j = get(f"https://fantasyfootballcalculator.com/api/v1/adp/{fmt}?teams={lg['total_rosters']}&year={season}")
    if j:
        dump(f"ffc_adp_{season}", j)
        print(f"  FFC ADP {season} {fmt}: {len(j.get('players', []))} players, {j.get('meta', {}).get('total_drafts')} drafts")

# my user id + everyone's names for the current league
print("current draft order:", chain[0].get("draft_order") if chain else None)
