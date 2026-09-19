"""Synthetic dashboard scenarios: complete, stale, missing. OFFLINE.

    PYTHONPATH=src python scripts/weekly/dashboard_scenarios.py --out .cache/dashboard-scenarios
    PYTHONPATH=src python scripts/weekly/dashboard_scenarios.py --out docs/review/dashboard --screenshot

Builds three season caches from the committed test fixtures (an invented
league, an invented owner, no real roster) and renders the dashboard from
each, so a reviewer can see — without a network and without the owner's
data — what the page does when everything is current, when inputs are
stale, and when inputs are missing. Nothing here reads the real cache.

  complete  two weeks of box scores, a week-3 schedule, an opponent lineup,
            three free agents, every source pulled an hour ago; rendered
            Saturday before kickoff.
  stale     the same cache, pulled five days ago, with a FAILED refresh
            recorded against the player dump (so its designation is stale)
            and the league snapshot only covering week 2.
  missing   no schedule (locks unknown → start/sit and upgrades abstain),
            no box scores (every projection abstains), no injury table.

`--screenshot` renders each page to PNG with the pre-installed headless
Chromium when one is found (no Python dependency is added); it is skipped,
and said so, when the binary is absent.

tests/test_dashboard_cli.py drives the same builders through `main()`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gridiron import ingest as ing
from gridiron.paths import REPO_ROOT

FIXTURES = REPO_ROOT / "tests" / "fixtures"
UTC = timezone.utc
SCENARIOS = ("complete", "stale", "missing")

#: Saturday of NFL week 3 in the fixture calendar (games Sun 2026-09-27),
#: rendered at noon UTC: pregame, nothing locked, waivers cleared.
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)

#: Unrostered fixture players with box scores AND crosswalk rows. Two go to
#: the opponent's lineup, three become free agents.
_OPPONENT = {"9221": ("Jahmyr Gibbs", "RB", "DET"), "9228": ("Bryce Young", "QB", "CAR")}
_FREE_AGENTS = {"11646": ("Jalen Coker", "WR", "CAR"), "11564": ("Drake Maye", "QB", "NE"),
                "11560": ("Caleb Williams", "QB", "CHI")}

_CHROME_CANDIDATES = (
    os.environ.get("GRIDIRON_CHROME", ""),
    "/opt/pw-browsers/chromium",
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
    shutil.which("google-chrome") or "", shutil.which("chrome") or "",
)


def _week3_schedule(sched: pd.DataFrame) -> pd.DataFrame:
    wk2 = sched.loc[sched["week"] == 2].copy()
    wk3 = wk2.copy()
    wk3["week"] = 3
    wk3["gameday"] = [(pd.Timestamp(d) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
                      for d in wk2["gameday"]]
    wk3["game_id"] = [g.replace("2026_02_", "2026_03_") for g in wk2["game_id"]]
    wk3["result"] = float("nan")
    return pd.concat([sched, wk3], ignore_index=True)


def _perturbed_week2(weekly: pd.DataFrame) -> pd.DataFrame:
    """Week 2 = week 1 with deterministic, per-row scaled counting stats, so
    the chronological evaluation has a second week that is not a copy."""
    wk2 = weekly.copy()
    wk2["week"] = 2
    stat_cols = [c for c in wk2.columns if wk2[c].dtype.kind in "fi"
                 and c not in ("season", "week", "target_share", "air_yards_share", "wopr")]
    for i, idx in enumerate(wk2.index):
        factor = 0.7 + 0.6 * ((i * 7919) % 11) / 10.0      # 0.7 .. 1.3, deterministic
        for c in stat_cols:
            v = wk2.at[idx, c]
            if pd.notna(v):
                wk2.at[idx, c] = round(float(v) * factor) if float(v).is_integer() else float(v) * factor
    return wk2


def build_scenario(root: Path, kind: str, *, now: datetime = NOW) -> ing.Manifest:
    """Write one synthetic season-2026 cache under `root`."""
    if kind not in SCENARIOS:
        raise ValueError(f"unknown scenario {kind!r}; choose from {SCENARIOS}")
    season = 2026
    fresh = now - timedelta(hours=1)
    old = now - timedelta(days=5)
    stamp = old if kind == "stale" else fresh
    directory = ing.season_cache(season, root)
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)
    manifest = ing.Manifest(directory, season=season)

    weekly1 = pd.concat([pd.read_csv(FIXTURES / "weekly_offense_wk1.csv"),
                         pd.read_csv(FIXTURES / "weekly_kickers_wk1.csv")], ignore_index=True)
    weekly = pd.concat([weekly1, _perturbed_week2(weekly1)], ignore_index=True)
    snaps1 = pd.read_csv(FIXTURES / "snaps_wk1.csv")
    snaps = pd.concat([snaps1, snaps1.assign(week=2)], ignore_index=True)
    schedules = _week3_schedule(pd.read_csv(FIXTURES / "schedules_wk1_2.csv"))
    injuries = pd.read_csv(FIXTURES / "injuries_wk1_2.csv")
    injuries = pd.concat([injuries, injuries.loc[injuries["week"] == 2].assign(week=3)],
                         ignore_index=True)

    frames = [("schedules", schedules, "nflreadpy.load_schedules"),
              ("injuries", injuries, "nflreadpy.load_injuries"),
              ("weekly_stats", weekly, "nflreadpy.load_player_stats"),
              ("snap_counts", snaps, "nflreadpy.load_snap_counts")]
    if kind == "missing":
        frames = [f for f in frames if f[0] == "snap_counts"]   # snaps survive; nothing to join them to
    for name, frame, src in frames:
        path = directory / f"{name}.parquet"
        frame.to_parquet(path, index=False)
        manifest.record(name, path=path, rows=len(frame), source=src, as_of=stamp,
                        weeks=sorted({int(w) for w in frame["week"].dropna()}))
    if kind == "missing":
        manifest.record_failure("schedules", source="nflreadpy.load_schedules",
                                error="HTTP 503 from upstream", at=now)
        manifest.record_failure("weekly_stats", source="nflreadpy.load_player_stats",
                                error="HTTP 503 from upstream", at=now)

    snapshot = json.loads((FIXTURES / "sleeper_league.json").read_text("utf-8"))
    snapshot["state"] = {**snapshot["state"], "week": 3, "display_week": 3}
    snapshot["week"] = 3
    opp = snapshot["rosters"][1]
    opp["starters"] = ["9228", "9221", "0", "0", "0", "0", "0", "0", "0", "0"]
    opp["players"] = ["9228", "9221"]
    snapshot["matchups"][1]["starters"] = list(opp["starters"])
    snapshot["matchups"][1]["players"] = list(opp["players"])
    league_path = directory / "sleeper_league.json"
    league_path.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    manifest.record("sleeper_league", path=league_path, rows=len(snapshot["rosters"]),
                    as_of=stamp, source="api.sleeper.app (read-only)",
                    weeks=[2 if kind == "stale" else 3])

    players = json.loads((FIXTURES / "sleeper_players_small.json").read_text("utf-8"))
    for sid, (name, pos, team) in {**_OPPONENT, **_FREE_AGENTS}.items():
        players[sid] = {"player_id": sid, "full_name": name, "position": pos, "team": team,
                        "injury_status": None, "status": "Active", "gsis_id": None}
    if kind == "stale":
        players["3198"]["injury_status"] = "Questionable"     # a designation that will read STALE
    players_path = directory / "sleeper_players.json"
    players_path.write_text(json.dumps(players, indent=1), encoding="utf-8")
    manifest.record("sleeper_players", path=players_path, rows=len(players),
                    as_of=stamp, source="api.sleeper.app (read-only)")
    if kind == "stale":
        manifest.record_failure("sleeper_players", source="api.sleeper.app (read-only)",
                                error="timed out after 60s", at=now)

    cross_path = directory / "crosswalk.csv"
    cross_path.write_bytes((FIXTURES / "crosswalk_small.csv").read_bytes())
    manifest.record("crosswalk", path=cross_path, rows=18, as_of=stamp,
                    source="dynastyprocess db_playerids.csv")
    manifest.save()
    return manifest


def _cli():
    spec = importlib.util.spec_from_file_location(
        "weekly_dashboard_cli", Path(__file__).resolve().parent / "dashboard.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def chrome_binary() -> str | None:
    for c in _CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


def screenshot(html_path: Path, png_path: Path, *, height: int = 3200) -> bool:
    binary = chrome_binary()
    if binary is None:
        return False
    cmd = [binary, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
           f"--window-size=1280,{height}", f"--screenshot={png_path}",
           html_path.resolve().as_uri()]
    subprocess.run(cmd, capture_output=True, timeout=120)
    return png_path.exists()


def render(kind: str, out: Path, *, take_screenshot: bool, now: datetime = NOW) -> int:
    root = out / "caches" / kind
    build_scenario(root, kind, now=now)
    cli = _cli()
    rc = cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                   "--anonymous", "--out-dir", str(out / kind),
                   "--archive-root", str(out / kind / "archive"),
                   "--now", now.isoformat()])
    html_path = out / kind / "dashboard_latest.html"
    if take_screenshot:
        png = out / f"{kind}.png"
        if screenshot(html_path, png):
            print(f"[scenario] {kind}: screenshot {png}", file=sys.stderr)
        else:
            print(f"[scenario] {kind}: no headless Chromium found; screenshot skipped",
                  file=sys.stderr)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / ".cache" / "dashboard-scenarios")
    ap.add_argument("--only", choices=SCENARIOS, default=None)
    ap.add_argument("--screenshot", action="store_true")
    args = ap.parse_args(argv)
    worst = 0
    for kind in SCENARIOS if args.only is None else (args.only,):
        print(f"=== scenario: {kind} ===")
        rc = render(kind, args.out, take_screenshot=args.screenshot)
        worst = max(worst, rc)
    return worst


if __name__ == "__main__":
    sys.exit(main())
