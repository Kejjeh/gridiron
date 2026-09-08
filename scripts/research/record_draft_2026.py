"""After the draft: pull the real picks from Sleeper, write the decision
ledger, and grade the board's availability model.

Run:  PYTHONPATH=src python scripts/research/record_draft_2026.py
Writes data/ledger/draft_2026.csv (committed, rule #10) and prints the
grade. Safe to re-run mid-draft: it records whatever picks exist so far.
"""
from __future__ import annotations

import json
import urllib.request

import pandas as pd

from gridiron.config import get_settings
from gridiron.draft import snake_picks
from gridiron.league_config import DRAFT_ROUNDS, MY_DRAFT_SLOT, NUM_TEAMS
from gridiron.ledger import grade_availability, record_draft
from gridiron.paths import LEDGER, OUTPUTS, RESEARCH_CACHE

UA = {"User-Agent": "gridiron-research/0.1 (python-urllib)"}


def get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


league_id = get_settings().sleeper_league_id
drafts = get(f"https://api.sleeper.app/v1/league/{league_id}/drafts")
draft = drafts[0]
picks = get(f"https://api.sleeper.app/v1/draft/{draft['draft_id']}/picks")
users = {u["user_id"]: u.get("display_name") for u in get(f"https://api.sleeper.app/v1/league/{league_id}/users")}
me = next(uid for uid, slot in (draft.get("draft_order") or {}).items() if slot == MY_DRAFT_SLOT)

board = pd.read_csv(OUTPUTS / "draft2026_board.csv", dtype={"sleeper_id": str})
board = board[board.sleeper_id.notna()]
led = record_draft(picks, board, my_user_id=me)
led["manager_name"] = led.manager.map(users)
LEDGER.mkdir(parents=True, exist_ok=True)
led.to_csv(LEDGER / "draft_2026.csv", index=False)
(RESEARCH_CACHE / "draft2026" / "picks_final.json").write_text(json.dumps(picks), encoding="utf-8")

pd.set_option("display.width", 220)
print(f"draft {draft['draft_id']} status {draft['status']}: {len(picks)} picks recorded -> {LEDGER / 'draft_2026.csv'}")
mine = led[led.mine]
print("\nMy picks and the rejected side:")
print(mine[["pick", "round", "player", "pos", "proj", "vor", "reach", "alt_player", "alt_vor", "vor_given_up"]].to_string(index=False))
print(f"\nTotal VOR drafted {mine.vor.sum():.0f}; VOR given up vs best-available {mine.vor_given_up.fillna(0).sum():+.0f}")
g = grade_availability(led, board, snake_picks(MY_DRAFT_SLOT, NUM_TEAMS, DRAFT_ROUNDS))
print(f"\nAvailability model Brier {g['brier']:.4f} over {g['n']} player-picks (0 = perfect, 0.25 = coin flips)")
for k, v in g["by_pick"].items():
    print(f"  pick {k:3d}: expected {v['expected']:.1f} survivors, actual {v['available']}")
print("\nBiggest reaches in the room:")
print(led.dropna(subset=["reach"]).sort_values("reach", ascending=False).head(10)[["pick", "manager_name", "player", "pos", "adp", "reach"]].to_string(index=False))
