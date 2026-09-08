"""Pull Sleeper league/draft settings via the public API (no auth). Output -> data/research/cache/draft2026/."""
import json, sys, urllib.request, pathlib
OUT = pathlib.Path("data/research/cache/draft2026"); OUT.mkdir(parents=True, exist_ok=True)
def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "gridiron-research/0.1 (python-urllib)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())
user = get("https://api.sleeper.app/v1/user/Kejjeh")
print("user_id", user["user_id"], user.get("display_name"))
leagues = get(f"https://api.sleeper.app/v1/user/{user['user_id']}/leagues/nfl/2026")
print("leagues:", [(l["league_id"], l["name"], l["total_rosters"]) for l in leagues])
for l in leagues:
    lid = l["league_id"]
    league = get(f"https://api.sleeper.app/v1/league/{lid}")
    (OUT / f"league_{lid}.json").write_text(json.dumps(league, indent=1))
    drafts = get(f"https://api.sleeper.app/v1/league/{lid}/drafts")
    (OUT / f"drafts_{lid}.json").write_text(json.dumps(drafts, indent=1))
    print("==", league["name"], "| status", league["status"], "| roster_positions", league["roster_positions"])
    ss = league["scoring_settings"]
    print("scoring (nonzero):", {k: v for k, v in ss.items() if v})
    for d in drafts:
        print("draft", d["draft_id"], d["status"], d["type"], "start_time", d.get("start_time"), "settings", d.get("settings"))
        print("slot_to_roster", d.get("slot_to_roster_id"), "draft_order", d.get("draft_order"))
        picks = get(f"https://api.sleeper.app/v1/draft/{d['draft_id']}/picks")
        (OUT / f"picks_{d['draft_id']}.json").write_text(json.dumps(picks, indent=1))
        print("picks made so far:", len(picks))
users = get(f"https://api.sleeper.app/v1/league/{leagues[0]['league_id']}/users") if leagues else []
(OUT / "users.json").write_text(json.dumps(users, indent=1))
