"""Rebuild historical draft context from local, inspectable inputs. No network.

Run with PYTHONPATH=src. Update the source JSON only from a verified primary
source, preserving season/publication/retrieval metadata. Missing inputs fail
before replacing the existing output.
"""
import csv
import hashlib
import json
from datetime import datetime, timezone
from gridiron.paths import OUTPUTS, RESEARCH_CACHE
from gridiron.draft_context import build_context

def main():
    source = OUTPUTS / "draft2026_context_source.json"
    schedule = RESEARCH_CACHE / "draft2026" / "schedules_2026.csv"
    snapshot = json.loads(source.read_text(encoding="utf-8"))
    with schedule.open(encoding="utf-8", newline="") as f:
        games = list(csv.DictReader(f))
    now = datetime.now(timezone.utc).isoformat()
    teams = build_context(snapshot, games, season=2026, as_of=now)
    # A partial cache is not a full NFL schedule; never silently label it complete.
    scheduled = {code: set() for code in teams}
    for game in games:
        if int(game["season"]) == 2026 and game["game_type"] == "REG":
            from gridiron.draft_context import team_code
            for field in ("away_team", "home_team"):
                scheduled[team_code(game[field])].add(int(game["week"]))
    if len(teams) != 32 or any(len(weeks) != 17 for weeks in scheduled.values()):
        raise ValueError("Expected 32 teams with 17 regular-season games each")
    provenance = [
        {"name": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
         "saved": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).isoformat()}
        for p in (source, schedule)]
    data = dict(generated=now, season=2026, historical_season=snapshot["season"],
                source=snapshot["source"], source_url=snapshot["source_url"],
                published=snapshot["published"], retrieved=snapshot["retrieved"],
                schedule_saved=provenance[1]["saved"], inputs=provenance,
                adjustment=0, validated=False, teams=teams)
    target = OUTPUTS / "draft2026_context.json"
    staged = target.with_suffix(".json.tmp")
    staged.write_text(json.dumps(data, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    staged.replace(target)
    print("Historical context rebuilt:", len(teams), "teams; projection adjustment 0")

if __name__ == "__main__":
    main()
