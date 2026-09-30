"""ros_rankings.py — rest-of-season rankings for every position, with who holds whom.

    PYTHONPATH=src python scripts/weekly/ros_rankings.py [--week 4] [--nflverse]
        [--top 40] [--out data/outputs/review/]

Ranks every QB, RB, WR, TE, K and team defense by projected league points
from `--week` through week 17 (`gridiron.ros`), with the playoff weeks
(15-17) on their own, and marks each player MINE, FA, or the manager who
holds him. Writes `ros_weekNN.md` and `ros_weekNN.csv` to `--out`
(data/outputs/review/, gitignored — they name the owner's players, rule #10).

Inputs:
  default     the season cache only (offline): box scores, snaps, schedule,
              injuries, crosswalk, player map, league snapshot, and the
              advanced model's inputs beside it (pull_week writes them);
  --nflverse  box scores, snaps, schedule, injuries and the model inputs
              fresh from nflverse (public, read-only) — for a stale local
              cache. Ids, eligibility and rosters still come from the cache
              (or `--league-json`); the player map is never fetched here.

What it is not: a trade chart. A ROS number is points, not ΔP(win) (rule
#7) — a start/sit or add still goes through the page. Injury: a player
ruled Out for `--week` on the official injury report is credited 0 that
week only; IR and longer absences are FLAGGED from the player map, never
guessed into the number (rule #11).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

from gridiron import ingest as ing
from gridiron import ros as R
from gridiron.ids import Crosswalk, nflverse_team, sleeper_gsis_overlay
from gridiron.league_config import MY_SLEEPER_USERNAME, SEASON_YEAR
from gridiron.models import advanced as A
from gridiron.paths import OUTPUTS
from gridiron.usage import player_weeks

OUT_DIR = OUTPUTS / "review"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
#: how deep each position's table goes by default (a 12-team league's
#: starters plus a bench's worth)
DEPTH = {"QB": 30, "RB": 60, "WR": 72, "TE": 30, "K": 24, "DEF": 32}


def find_owner_id(snapshot: dict, owner: str) -> str | None:
    for u in snapshot.get("users") or []:
        if str(u.get("user_id")) == str(owner) \
                or str(u.get("display_name") or "").lower() == str(owner).lower():
            return str(u.get("user_id"))
    return None


def load_cache(directory: Path, season: int) -> dict:
    m = ing.Manifest.load(directory, season)
    cw_path = m.file("crosswalk")
    return {"schedule": m.read_frame("schedules"), "weekly": m.read_frame("weekly_stats"),
            "snaps": m.read_frame("snap_counts"), "injuries": m.read_frame("injuries"),
            "players": m.read_json("sleeper_players") or {},
            "snapshot": m.read_json("sleeper_league") or {},
            "crosswalk": Crosswalk.from_csv(cw_path) if cw_path else Crosswalk({}, {}),
            "inputs": A.load_inputs(directory, season)}


def load_nflverse(season: int) -> dict:
    import nflreadpy as nfl
    return {"schedule": nfl.load_schedules([season]).to_pandas(),
            "weekly": nfl.load_player_stats([season], summary_level="week").to_pandas(),
            "snaps": nfl.load_snap_counts([season]).to_pandas(),
            "injuries": nfl.load_injuries([season]).to_pandas(),
            "inputs": {**A.fetch_inputs(season),
                       "meta": {"fetched_at": pd.Timestamp.now(tz="UTC").isoformat(
                           timespec="seconds")}}}


def out_for_week(injuries: pd.DataFrame | None, players: dict, crosswalk: Crosswalk,
                 week: int) -> tuple[set[str], dict[str, str]]:
    """(gsis ids the injury report rules Out for `week`, gsis -> a status to
    show). Only the week's official report zeroes a week; the player map's
    status (IR, Questionable...) is displayed as a flag and changes nothing."""
    out: set[str] = set()
    flag: dict[str, str] = {}
    if injuries is not None and len(injuries) and "report_status" in injuries:
        wk = injuries.loc[(injuries["week"] == week)
                          & (injuries["report_status"].astype(str).str.upper() == "OUT")]
        out |= {str(g) for g in wk["gsis_id"].dropna()}
    for sid, rec in players.items():
        st = str((rec or {}).get("injury_status") or "").upper()
        gid = crosswalk.gsis(sid)
        if not gid or not st:
            continue
        flag[gid] = st                 # shown, never acted on: the map may be days old
    for gid in out:
        flag[gid] = "OUT wk" + str(week)
    return out, flag


def render(table: pd.DataFrame, *, week: int, status: str, depth: dict,
           built_from: str) -> str:
    lines = [f"# Rest-of-season rankings — from week {week} through week {R.HORIZON_END}",
             "", f"Weekly rates: {status}.", f"Inputs: {built_from}.",
             f"ROS = projected league points over the games left (byes from the "
             f"schedule); playoffs = weeks {R.PLAYOFF_WEEKS[0]}-{R.PLAYOFF_WEEKS[-1]}. "
             "Points, not ΔP(win) — a lineup call still goes through the page. "
             "Out this week = 0 this week only; IR / longer absences are flagged, "
             "never guessed. `depth N` = not first on the depth chart before the week: "
             "the number assumes he keeps playing — a shared or lost job is flagged, "
             "not priced.", ""]
    mine = table.loc[table["held_by"] == "MINE"].sort_values(["position", "pos_rank"])
    if len(mine):
        lines += ["## My roster", "",
                  "| pos | rank | player | team | ROS | /game | games left | playoffs | best FA at pos (ROS) |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for r in mine.to_dict("records"):
            fa = table.loc[(table["position"] == r["position"]) & (table["held_by"] == "FA")]
            best = fa.iloc[0] if len(fa) else None
            lines.append(
                f"| {r['position']} | {r['pos_rank']} | {r['name']}{_flag(r)} | {r['team']} | "
                f"{r['ros']:.1f} | {r['ros_pg']:.1f} | {r['games_left']} | {r['playoff']:.1f} | "
                + (f"{best['name']} #{best['pos_rank']} ({best['ros']:.1f})" if best is not None
                   else "—") + " |")
        lines.append("")
    for pos in POSITIONS:
        g = table.loc[table["position"] == pos].head(depth.get(pos, 40))
        if len(g) == 0:
            continue
        lines += [f"## {pos}", "",
                  "| # | player | team | held by | ROS | /game | left | byes | playoffs | next wk | rate from |",
                  "|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in g.to_dict("records"):
            lines.append(
                f"| {r['pos_rank']} | {r['name']}{_flag(r)} | {r['team']} | {r['held_by']} | "
                f"{r['ros']:.1f} | {r['ros_pg']:.1f} | {r['games_left']} | {r['byes'] or '—'} | "
                f"{r['playoff']:.1f} | {r['rate']:.1f} | {r['source']} |")
        lines.append("")
    return "\n".join(lines)


def _flag(r) -> str:
    bits = []
    st = str(r.get("injury") or "").upper()
    if st and st != "NAN":
        bits.append(st)
    dr = r.get("depth_rank")
    if dr == dr and dr is not None and int(dr) > 1:
        bits.append(f"depth {int(dr)}")
    return f" ({'; '.join(bits)})" if bits else ""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", type=int, default=SEASON_YEAR)
    ap.add_argument("--week", type=int, default=None,
                    help="first week counted (default: the league snapshot's week)")
    ap.add_argument("--owner", default=MY_SLEEPER_USERNAME)
    ap.add_argument("--cache-root", type=Path, default=None)
    ap.add_argument("--league-json", type=Path, default=None,
                    help="a Sleeper league snapshot to take rosters from")
    ap.add_argument("--nflverse", action="store_true",
                    help="stats, schedule, injuries and model inputs from nflverse")
    ap.add_argument("--top", type=int, default=None, help="rows per position")
    ap.add_argument("--out", type=Path, default=OUT_DIR)
    ap.add_argument("--print", action="store_true", help="also print the tables")
    args = ap.parse_args(argv)

    directory = ing.season_cache(args.season, args.cache_root)
    cache = load_cache(directory, args.season)
    built_from = f"season cache {directory}"
    pm = ing.Manifest.load(directory, args.season).entries.get("sleeper_players")
    pm_at = getattr(pm, "as_of", None)
    if args.nflverse:
        cache.update(load_nflverse(args.season))
        built_from = "nflverse (fresh) + ids/rosters from the cache"
    if args.league_json:
        cache["snapshot"] = json.loads(args.league_json.read_text(encoding="utf-8"))
        built_from += f"; rosters from {args.league_json.name}"
    snapshot, players = cache["snapshot"], cache["players"]
    week = int(args.week or (snapshot.get("state") or {}).get("week") or 0)
    if week < 1:
        print("No week: pass --week.", file=sys.stderr)
        return 2
    crosswalk = cache["crosswalk"]
    if players:
        crosswalk = crosswalk.with_overlay(sleeper_gsis_overlay(players))
    weekly = cache["weekly"]
    if weekly is None or len(weekly) == 0:
        print("No box scores in the inputs.", file=sys.stderr)
        return 2
    if "season_type" in weekly:
        weekly = weekly.loc[weekly["season_type"] == "REG"]
    weeks = player_weeks(weekly.loc[weekly["position"].isin(["QB", "RB", "WR", "TE", "K"])],
                         cache["snaps"], crosswalk)
    weeks = weeks.loc[weeks["week"] < week]
    positions, teams = {}, {}
    for sid, rec in players.items():
        gid = crosswalk.gsis(sid)
        if gid and isinstance(rec, dict):
            if rec.get("position"):
                positions[gid] = str(rec["position"]).upper()
            if rec.get("team"):
                teams[gid] = nflverse_team(rec["team"])
    out_ids, flags = out_for_week(cache["injuries"], players, crosswalk, week)
    table, status = R.build_table(weeks=weeks, schedule=cache["schedule"],
                                  injuries=cache["injuries"], inputs=cache["inputs"],
                                  week=week, positions=positions, teams=teams, out=out_ids)
    if len(table) == 0:
        print(f"Nothing to rank: {status}", file=sys.stderr)
        return 2
    table = R.ownership(table, snapshot=snapshot, players=players, crosswalk=crosswalk,
                        owner_id=find_owner_id(snapshot, args.owner), manager_names=True)
    table["injury"] = [flags.get(g, i) or i for g, i in zip(table["gsis_id"], table["injury"])]
    depth = {p: args.top for p in POSITIONS} if args.top else DEPTH
    if pm_at:
        built_from += f"; player map (ids, teams, injury flags) as of {pm_at}"
    md = render(table, week=week, status=status, depth=depth, built_from=built_from)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / f"ros_week{week:02d}.md").write_text(md, encoding="utf-8")
    table.drop(columns=["weeks"]).to_csv(args.out / f"ros_week{week:02d}.csv", index=False)
    print(f"[ros] {len(table)} players ranked from week {week}; wrote "
          f"{args.out / f'ros_week{week:02d}.md'} (+ .csv)", file=sys.stderr)
    if args.print:
        print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
