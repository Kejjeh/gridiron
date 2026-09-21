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
  partial_schedule
            the complete cache with two week-3 kickoffs damaged.
  slate_end the complete cache rendered on the Tuesday after week 3's last
            game: every starter locked, so the page has to say what can
            still be done and what waits for next week's inputs.
  hold      fresh inputs, no improvement anywhere: the free agents are all
            rostered by the opponent (empty pool), and the owner already
            starts his best legal lineup — HOLD, with the radar saying the
            pool is empty rather than inventing a candidate.
  sparse    box scores for week 1 only and for a third of the players: most
            projections abstain, the radar counts the free agents as
            MISSING EVIDENCE and lists no number for them.
  taken     two runs two hours apart. In the first, one free agent is held
            by the opponent; in the second he has been released and a
            different one (a LINEUP candidate) has been claimed. The radar's
            change list says "newly available" and "now owned" by id, and
            the claimed player is no longer suggested.
  roster_changed
            two runs: the owner has dropped his cheapest droppable player
            and moved another into the lineup between them. Section 3 names
            the roster change; the radar re-prices its drops.
  next_week two runs a week apart: the platform has moved to week 4 (with
            week-4 schedule rows). The radar says "week rollover — no
            comparison" instead of diffing week-3 verdicts against week 4.

`--screenshot` renders each page to PNG at 375, 768 and 1440 CSS px with the
pre-installed headless Chromium when one is found (no Python dependency is
added) and measures the true fit at each width; it is skipped, and said so,
when the binary is absent. `--browser` serves the `complete` page from a
loopback fixture that plays the hosting server and drives the page's own
script in real time: the radar's filters, search, sort and disclosure; and
the published-build check through an unchanged build (200 then 304), a 429,
a dropped connection, a recovery, an in-flight guard, a newer build (banner,
checks paused), pause/resume, and a reload that keeps the filters. Reported
as executed or UNAVAILABLE; never as a pass it did not run.

tests/test_dashboard_cli.py drives the same builders through `main()`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gridiron import ingest as ing
from gridiron.paths import REPO_ROOT

FIXTURES = REPO_ROOT / "tests" / "fixtures"
UTC = timezone.utc
SCENARIOS = ("complete", "stale", "missing", "partial_schedule", "slate_end",
             "hold", "sparse", "taken", "roster_changed", "next_week")

#: Saturday of NFL week 3 in the fixture calendar (games Sun 2026-09-27),
#: rendered at noon UTC: pregame, nothing locked, waivers cleared.
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
#: The Tuesday after: every week-3 game has kicked off and finished.
NOW_SLATE_END = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
#: Two hours on: the second run of a two-run scenario.
NOW_LATER = datetime(2026, 9, 26, 14, 0, tzinfo=UTC)
#: A week on: the platform has rolled to week 4.
NOW_NEXT_WEEK = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
#: Scenarios rendered at an instant other than NOW (for a two-run scenario,
#: the instant of the SECOND run; the first is always NOW).
SCENARIO_NOW = {"slate_end": NOW_SLATE_END, "taken": NOW_LATER,
                "roster_changed": NOW_LATER, "next_week": NOW_NEXT_WEEK}
#: Scenarios that render twice against one archive, so the second page has
#: a like-for-like previous record to diff against.
TWO_RUN = ("taken", "roster_changed", "next_week")
#: The synthetic scenarios that are the `complete` cache with a twist.
_BASE = {"slate_end": "complete", "hold": "complete", "sparse": "complete",
         "taken": "complete", "roster_changed": "complete", "next_week": "complete"}

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


def _break_some_kickoffs(sched: pd.DataFrame) -> pd.DataFrame:
    """Damage SOME of week 3's kickoff information, the way a partial upstream
    pull does: one game loses its `gametime`, another its `gameday`.

    This is the scenario the source review found at f82623e: the old reader
    stamped a missing time as 13:00 ET and dropped an unparsable date on the
    floor, so the affected teams read as unlocked or on a bye and every swap
    involving them was offered as legal.
    """
    out = sched.copy()
    wk3 = out.index[out["week"] == 3].tolist()
    if len(wk3) >= 1:
        out.loc[wk3[0], "gametime"] = None
    if len(wk3) >= 2:
        out.loc[wk3[1], "gameday"] = "not-a-date"
    return out


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


def _week4_schedule(sched: pd.DataFrame) -> pd.DataFrame:
    wk3 = sched.loc[sched["week"] == 3].copy()
    wk4 = wk3.copy()
    wk4["week"] = 4
    wk4["gameday"] = [(pd.Timestamp(d) + pd.Timedelta(days=7)).strftime("%Y-%m-%d")
                      for d in wk3["gameday"]]
    wk4["game_id"] = [g.replace("2026_03_", "2026_04_") for g in wk3["game_id"]]
    return pd.concat([sched, wk4], ignore_index=True)


def build_scenario(root: Path, kind: str, *, now: datetime = NOW, run: int = 1) -> ing.Manifest:
    """Write one synthetic season-2026 cache under `root`. `run` is 1 or 2
    for the two-run scenarios (see TWO_RUN); the second run's cache differs
    from the first in exactly the way the docstring describes."""
    if kind not in SCENARIOS:
        raise ValueError(f"unknown scenario {kind!r}; choose from {SCENARIOS}")
    twist = kind if kind in _BASE else ""
    kind = _BASE.get(kind, kind)               # the cache to start from
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
    if twist == "sparse":
        # week 1 only, and only every third player: most projections abstain
        weekly = weekly1.iloc[::3].reset_index(drop=True)
        snaps = snaps1
    schedules = _week3_schedule(pd.read_csv(FIXTURES / "schedules_wk1_2.csv"))
    if twist == "next_week":
        schedules = _week4_schedule(schedules)
    if kind == "partial_schedule":
        schedules = _break_some_kickoffs(schedules)
    injuries = pd.read_csv(FIXTURES / "injuries_wk1_2.csv")
    injuries = pd.concat([injuries, injuries.loc[injuries["week"] == 2].assign(week=3)],
                         ignore_index=True)
    if twist == "next_week" and run == 2:
        injuries = pd.concat([injuries, injuries.loc[injuries["week"] == 3].assign(week=4)],
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
    week = 4 if (twist == "next_week" and run == 2) else 3
    snapshot["state"] = {**snapshot["state"], "week": week, "display_week": week}
    snapshot["week"] = week
    mine = snapshot["rosters"][0]
    opp = snapshot["rosters"][1]
    opp["starters"] = ["9228", "9221", "0", "0", "0", "0", "0", "0", "0", "0"]
    opp["players"] = ["9228", "9221"]
    if twist == "hold":
        # every free agent is rostered by the opponent, and the owner already
        # starts his best legal lineup (the bench TE takes the TE slot)
        opp["players"] += list(_FREE_AGENTS)
        mine["starters"] = ["5022" if s == "3271" else s for s in mine["starters"]]
    if twist == "taken":
        # run 1: the WR is held by the opponent; run 2: he is released and
        # the LINEUP-candidate QB has been claimed instead
        opp["players"].append("11646" if run == 1 else "11560")
    if twist == "roster_changed" and run == 2:
        # the owner dropped his cheapest droppable TE and starts the other
        mine["players"] = [s for s in mine["players"] if s != "3271"]
        mine["starters"] = ["5022" if s == "3271" else s for s in mine["starters"]]
    for m in snapshot["matchups"]:
        if str(m.get("roster_id")) == str(mine.get("roster_id")):
            m["starters"] = list(mine["starters"])
            m["players"] = list(mine["players"])
    snapshot["matchups"][1]["starters"] = list(opp["starters"])
    snapshot["matchups"][1]["players"] = list(opp["players"])
    league_path = directory / "sleeper_league.json"
    league_path.write_text(json.dumps(snapshot, indent=1), encoding="utf-8")
    manifest.record("sleeper_league", path=league_path, rows=len(snapshot["rosters"]),
                    as_of=stamp, source="api.sleeper.app (read-only)",
                    weeks=[2 if kind == "stale" else week])

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


#: (label, css width, capture height) for the review screenshots.
#:
#: The narrow capture is 500 px, NOT a phone's 390, because this headless
#: Chromium clamps its layout viewport to a 500 px minimum: asking for 390
#: renders the page at 500 and crops the PNG, which looks like clipped text
#: and is a lie about what a phone would show. 500 is still below the 560 px
#: breakpoint, so the narrow layout is genuinely exercised. The true phone
#: width is measured separately, in an iframe, by `horizontal_overflow`.
NARROW_CAPTURE_WIDTH = 500
#: The width the fit check actually targets: a 390 CSS-px iPhone-class viewport.
PHONE_WIDTH = 390

#: (label, css width). Heights are MEASURED, never guessed: a fixed capture
#: height silently truncates the page, which is the one thing a review
#: screenshot must not do. A width under the browser's 500 px floor is
#: captured through the same fixed-width iframe the fit check uses, so the
#: PNG shows a true phone layout (the frame sits at the left of the image).
VIEWPORTS: tuple[tuple[str, int], ...] = (
    ("desktop", 1440),
    ("tablet768", 768),
    ("phone375", 375),
)
#: The widths the fit check measures.
FIT_WIDTHS: tuple[int, ...] = (375, 768, 1440)
#: Captured at less than 1 device pixel per CSS pixel: these pages run to
#: several thousand CSS pixels and a 1:1 capture of the full set costs several
#: megabytes in the repository. Still legible as an overview; read the HTML
#: for detail.
CAPTURE_SCALE = 0.6
#: A guard against a runaway page consuming the whole disk in one capture.
MAX_CAPTURE_HEIGHT = 12000


def content_height(html_path: Path, width: int) -> int | None:
    """The page's real rendered height at `width`, measured in the browser."""
    fit = _probe(html_path, width, height=400)
    return None if fit is None else fit[1]


def screenshot(html_path: Path, png_path: Path, *, width: int = 1280,
               height: int | None = None) -> bool:
    binary = chrome_binary()
    if binary is None:
        return False
    if height is None:
        height = content_height(html_path, width) or 4000
    height = min(int(height) + 24, MAX_CAPTURE_HEIGHT)
    target = html_path.resolve().as_uri()
    frame = None
    if width < NARROW_CAPTURE_WIDTH:
        frame = html_path.with_name(html_path.stem + f".frame{width}.html")
        frame.write_text(
            "<!doctype html><html><body style=\"margin:0;background:#0b1020\">"
            f"<iframe src=\"{target}\" style=\"width:{width}px;height:{height}px;border:0;"
            "display:block\"></iframe></body></html>", encoding="utf-8")
        target = frame.resolve().as_uri()
    cmd = [binary, "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
           "--allow-file-access-from-files",
           f"--force-device-scale-factor={CAPTURE_SCALE}",
           f"--window-size={max(width, NARROW_CAPTURE_WIDTH)},{height}", f"--screenshot={png_path}",
           target]
    try:
        subprocess.run(cmd, capture_output=True, timeout=180)
    finally:
        if frame is not None:
            frame.unlink(missing_ok=True)
    return png_path.exists()


#: A harness page that loads the dashboard in a fixed-width iframe and asks the
#: BROWSER for the resulting layout. The iframe is the trick that gets a real
#: phone-width viewport out of a headless build whose own window will not go
#: below 500 px; the reported `clientWidth` is what was actually measured, so a
#: check can never claim a width it did not achieve.
_HARNESS = """<!doctype html><html><body style="margin:0">
<iframe id="f" src="{url}" style="width:{width}px;height:{height}px;border:0"
        onload="probe()"></iframe>
<script>
function probe(){{
  try{{
    var d = document.getElementById('f').contentDocument;
    var e = d.documentElement, b = d.body;
    var over = Math.max(e.scrollWidth - e.clientWidth, b.scrollWidth - e.clientWidth);
    // A wide table inside an overflow-x:auto box is DESIGNED to scroll on its
    // own; only content that pushes the PAGE sideways is a layout bug, so
    // anything inside a scroll container is skipped.
    function scrolls(el) {{
      for (var n = el.parentElement; n && n !== d.body; n = n.parentElement) {{
        var ox = d.defaultView.getComputedStyle(n).overflowX;
        if (ox === 'auto' || ox === 'scroll') return true;
      }}
      return false;
    }}
    var widest = 0, tag = 'none';
    var all = d.body.getElementsByTagName('*');
    for (var i = 0; i < all.length; i++) {{
      var r = all[i].getBoundingClientRect();
      if (r.right > widest && !scrolls(all[i])) {{ widest = r.right;
        tag = all[i].tagName +
          (all[i].className ? '.' + String(all[i].className).split(' ')[0] : ''); }}
    }}
    document.title = 'PROBE ' + e.clientWidth + ' ' + e.scrollHeight + ' ' +
                     Math.round(over) + ' ' + Math.round(widest) + ' ' + tag;
  }} catch (err) {{ document.title = 'PROBEFAIL ' + err.name; }}
}}
</script></body></html>"""


@dataclass(frozen=True)
class FitCheck:
    """The result of one executed narrow-viewport layout check."""

    measured_width: int          # the layout width the browser actually used
    overflow: float              # scrollWidth - clientWidth, in CSS px
    widest: float                # right edge of the widest element
    element: str

    @property
    def fits(self) -> bool:
        """The page does not scroll sideways, and nothing outside a deliberate
        scroll container reaches past the viewport. A wide table inside
        `.wrap` is allowed: that box scrolls on its own by design."""
        return self.overflow <= 0 and self.widest <= self.measured_width + 1

    def line(self) -> str:
        if self.fits:
            return (f"fits at {self.measured_width}px "
                    f"(widest element ends at {self.widest:.0f}px)")
        return (f"OVERFLOWS at {self.measured_width}px by {self.overflow:.0f}px; "
                f"widest element {self.element} ends at {self.widest:.0f}px")


def _probe(html_path: Path, width: int, *, height: int = 4000
           ) -> tuple[int, int, float, float, str] | None:
    """Load the page in a fixed-width iframe and read the layout back out.

    The iframe is the trick that gets a real phone-width viewport out of a
    headless build whose own window will not go below 500 px. Returns
    `(client_width, scroll_height, overflow, widest, element)` or None when no
    browser is available or the harness could not read the frame.
    """
    binary = chrome_binary()
    if binary is None:
        return None
    harness = html_path.with_name(html_path.stem + f".harness{width}.html")
    harness.write_text(_HARNESS.format(url=html_path.resolve().as_uri(),
                                       width=width, height=height), encoding="utf-8")
    try:
        proc = subprocess.run(
            [binary, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--allow-file-access-from-files",
             f"--window-size={max(width + 80, 900)},{min(height + 200, 12800)}",
             "--virtual-time-budget=5000", "--dump-dom", harness.resolve().as_uri()],
            capture_output=True, timeout=180)
        if proc.returncode != 0:
            return None
        m = re.search(r"PROBE (\d+) (\d+) (-?\d+) (-?\d+) ([^\s<]*)",
                      proc.stdout.decode("utf-8", "replace"))
        if not m:
            return None
        return (int(m.group(1)), int(m.group(2)), float(m.group(3)),
                float(m.group(4)), m.group(5))
    finally:
        harness.unlink(missing_ok=True)


def horizontal_overflow(html_path: Path, width: int = PHONE_WIDTH) -> FitCheck | None:
    """Does the page fit a true phone width without scrolling sideways?

    Returns None when the check could not be EXECUTED — the caller reports
    that as UNAVAILABLE, never as a pass. An unexecuted check is not green.
    """
    probe = _probe(html_path, width)
    if probe is None:
        return None
    client, _height, over, widest, tag = probe
    return FitCheck(client, over, widest, tag)


def render(kind: str, out: Path, *, take_screenshot: bool, now: datetime = NOW) -> int:
    root = out / "caches" / kind
    cli = _cli()
    archive = out / kind / "archive"
    rc = 0
    if kind in TWO_RUN:
        # the first run at NOW, into its own page directory; its archive is
        # what the second run diffs against
        build_scenario(root, kind, now=NOW, run=1)
        rc = cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                       "--anonymous", "--out-dir", str(out / kind / "run1"),
                       "--archive-root", str(archive), "--now", NOW.isoformat()])
        build_scenario(root, kind, now=now, run=2)
    else:
        build_scenario(root, kind, now=now)
    rc = max(rc, cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                           "--anonymous", "--out-dir", str(out / kind),
                           "--archive-root", str(archive), "--now", now.isoformat()]))
    html_path = out / kind / "dashboard_latest.html"
    if take_screenshot:
        for label, width in VIEWPORTS:
            png = out / (f"{kind}.png" if label == "desktop" else f"{kind}-{label}.png")
            if screenshot(html_path, png, width=width):
                print(f"[scenario] {kind}: {label} screenshot {png}", file=sys.stderr)
            else:
                print(f"[scenario] {kind}: no headless Chromium found; {label} "
                      f"screenshot skipped", file=sys.stderr)
        for width in FIT_WIDTHS:
            fit = horizontal_overflow(html_path, width)
            if fit is None:
                print(f"[scenario] {kind}: {width}px fit check UNAVAILABLE "
                      f"(no headless browser on this machine) — NOT a pass",
                      file=sys.stderr)
            else:
                print(f"[scenario] {kind}: {width}px fit: {fit.line()}", file=sys.stderr)
                if not fit.fits:
                    rc = max(rc, 1)
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / ".cache" / "dashboard-scenarios")
    ap.add_argument("--only", choices=SCENARIOS, default=None)
    ap.add_argument("--screenshot", action="store_true")
    ap.add_argument("--browser", action="store_true",
                    help="drive the complete page's radar and published-build check "
                         "against a loopback fixture (needs the complete scenario)")
    args = ap.parse_args(argv)
    worst = 0
    kinds = SCENARIOS if args.only is None else (args.only,)
    for kind in kinds:
        print(f"=== scenario: {kind} ===")
        rc = render(kind, args.out, take_screenshot=args.screenshot,
                    now=SCENARIO_NOW.get(kind, NOW))
        worst = max(worst, rc)
    if args.browser:
        spec = importlib.util.spec_from_file_location(
            "weekly_radar_drive", Path(__file__).resolve().parent / "radar_drive.py")
        drive = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = drive
        spec.loader.exec_module(drive)
        drive_radar, drive_verdict = drive.drive_radar, drive.verdict
        page = args.out / "complete" / "dashboard_latest.html"
        if not page.exists():
            render("complete", args.out, take_screenshot=False)
        result = drive_radar(page)
        (args.out / "browser_drive_radar.json").write_text(
            json.dumps(result, indent=1), encoding="utf-8")
        bad = drive_verdict(result)
        for line in result.get("lines", []):
            print(f"[drive] {line}")
        for line in bad:
            print(f"[drive] FAIL: {line}")
        print("[drive] " + ("PASS" if result.get("executed") and not bad else
                            "UNAVAILABLE" if not result.get("executed") else "FAIL"))
        if not result.get("executed") or bad:
            worst = max(worst, 1)
    return worst


if __name__ == "__main__":
    sys.exit(main())
