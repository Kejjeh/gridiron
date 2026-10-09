"""Build the 2026 changes panel offline. Never applies projection weights."""
import argparse
import csv
import hashlib
import json
from datetime import datetime, timezone
from gridiron.paths import OUTPUTS, RESEARCH_CACHE
from gridiron.personnel import build_personnel, pff_index

def read(path):
    with path.open(encoding="utf-8-sig",newline="") as f:
        return list(csv.DictReader(f))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--as-of",default=None,help="Timezone-aware cutoff; defaults to now")
    args=parser.parse_args()
    now=args.as_of or datetime.now(timezone.utc).isoformat()
    cache=RESEARCH_CACHE/"draft2026"
    paths=[cache/"rosters_2025.csv",cache/"rosters_2026.csv",cache/"depth_charts_2026.csv",
           OUTPUTS/"draft2026_changes_source.json"]
    old,new,depth=map(read,paths[:3])
    coaching=json.loads(paths[3].read_text(encoding="utf-8"))
    pff_path=cache/"pff_blocking_2025.csv"
    grades={}
    if pff_path.exists():
        grades=pff_index(read(pff_path),old+new,season=2026,as_of=now)
        paths.append(pff_path)
    # Snapshot files must themselves exist by cutoff. Historical backtests
    # require original captured snapshots, not today's roster with an old label.
    cutoff=datetime.fromisoformat(now)
    for path in paths:
        if datetime.fromtimestamp(path.stat().st_mtime,timezone.utc)>cutoff:
            raise ValueError("Input snapshot saved after requested cutoff")
    teams=build_personnel(old,new,depth,coaching,as_of=now,grades=grades)
    if len(teams)!=32:
        raise ValueError("Expected 32 teams in current roster")
    data=dict(as_of=now,adjustment=0,validated=False,pff_loaded=len(grades),
              roster_source="https://github.com/nflverse/nflverse-data/releases/tag/rosters",
              depth_source="https://github.com/nflverse/nflverse-data/releases/tag/depth_charts",
              inputs=[dict(name=p.name,location="outputs" if p.parent==OUTPUTS else "cache",
                           sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                           saved=datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat()) for p in paths],
              teams=teams)
    target=OUTPUTS/"draft2026_changes.json"
    staged=target.with_suffix(".json.tmp")
    staged.write_text(json.dumps(data,indent=2,allow_nan=False)+"\n",encoding="utf-8")
    staged.replace(target)
    print("Changes panel:",len(teams),"teams;",sum(len(t["starters"]) for t in teams.values()),
          "chart entries;",sum(t["identity_warnings"] for t in teams.values()),"identity warnings;",
          len(grades),"PFF records")

if __name__=="__main__":
    main()
