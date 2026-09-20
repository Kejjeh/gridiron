"""Synthetic Game Day scenarios, end to end through the production CLIs. OFFLINE.

    PYTHONPATH=src python scripts/weekly/gameday_scenarios.py --out .cache/gameday-scenarios
    PYTHONPATH=src python scripts/weekly/gameday_scenarios.py --out docs/review/gameday --screenshot --browser

Every scenario starts from the invented league that `dashboard_scenarios`
builds from the committed fixtures (invented owner, invented opponent, no
real roster), lays a week-3 matchup with platform points and a per-game
status feed over it, renders the PREGAME BOARD with `dashboard.py` so a real
decision-time archive exists, and then renders the Game Day page with
`gameday.py` at a frozen instant. Nothing here reads the real cache.

  pregame        Saturday noon: everything not started, one endorsed swap
                 still available with its deadline and backup.
  pregame_stale  the same, with the injury table's refresh FAILED: the board
                 withheld its actions, so Game Day offers none — while the
                 fresh score still displays.
  conflict       the schedule contradicts itself about one kickoff, so the
                 swap involving that team cannot be shown legal.
  mixed          Sunday 17:00 ET: ahead 15.46 with the opponent's RB, K and
                 DST yet to play; finals, a zero, a negative DST, a kicker,
                 an in-progress zero, a starter the platform sent no value
                 for; no win probability anywhere.
  custom         `mixed` with a commissioner override on the opponent total.
  empty          `mixed` with an empty FLEX slot in the owner's lineup.
  injury_after   Sunday night: a starter's designation moved to Out after
                 the pregame record; one game missing from the feed, one
                 with a status word the page does not know, one suspended,
                 one still in progress four hours after kickoff.
  rollover       the platform has moved to week 4; the week-3 record is not
                 joined, the week-3 snapshot is not diffed.
  no_archive     no decision archive at all: a clear absence, no invention.

`--screenshot` captures PNGs with the pre-installed headless Chromium and
measures the true phone-width fit; `--browser` starts a local fixture server
and drives the page's own Refresh through a correction, a 429, a recovery,
an older response and a week rollover, reading the DOM back after each.
Both are skipped, and said so, when the binary is absent. An unexecuted check
is never reported as a pass.
"""
from __future__ import annotations

import argparse
import base64
import http.server
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import ingest as ing
from gridiron.livesync import GAME_STATUS_FILE, GAME_STATUS_NAME
from gridiron.paths import REPO_ROOT

UTC = timezone.utc
SCENARIOS = ("pregame", "pregame_stale", "conflict", "mixed", "custom", "empty",
             "injury_after", "rollover", "no_archive")

#: Frozen clocks. Week 3 of the fixture calendar: Thursday 2026-09-24, the
#: Sunday slate 2026-09-27 (13:00 / 16:05 / 16:25 / 20:20 ET), Monday 09-28.
SATURDAY = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SUNDAY_LATE = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)        # 17:00 ET
SUNDAY_NIGHT = datetime(2026, 9, 28, 0, 30, tzinfo=UTC)       # 20:30 ET

#: Week-3 game rows of the fixture schedule, by (away, home). Statuses are
#: ASSIGNED per scenario, never derived from the clock: that inference is the
#: one the page refuses to make, so the fixture must not make it either.
_GAMES = (("DET", "BUF"), ("CAR", "ATL"), ("NO", "BAL"), ("MIN", "CHI"), ("CIN", "HOU"),
          ("PIT", "NE"), ("GB", "NYJ"), ("CLE", "TB"), ("PHI", "TEN"), ("JAX", "DEN"),
          ("LV", "LAC"), ("SEA", "ARI"), ("WAS", "DAL"), ("MIA", "SF"), ("IND", "KC"),
          ("NYG", "LAR"))
_EARLY = {("CAR", "ATL"), ("NO", "BAL"), ("MIN", "CHI"), ("CIN", "HOU"), ("PIT", "NE"),
          ("GB", "NYJ"), ("CLE", "TB"), ("PHI", "TEN")}
_LATE = {("JAX", "DEN"), ("LV", "LAC"), ("SEA", "ARI"), ("WAS", "DAL"), ("MIA", "SF")}
_NIGHT = {("IND", "KC"), ("NYG", "LAR")}

#: The opponent's synthetic lineup: fixture free agents plus invented ids
#: whose names are obviously invented. Ten starters so the lineup is full.
_OPP_PLAYERS = {
    "9228": ("Bryce Young", "QB", "CAR"), "9221": ("Jahmyr Gibbs", "RB", "DET"),
    "9901": ("Synthetic Back", "RB", "KC"), "9903": ("Synthetic Wideout", "WR", "DAL"),
    "9904": ("Synthetic Receiver", "WR", "ARI"), "9905": ("Synthetic End", "TE", "DEN"),
    "11646": ("Jalen Coker", "WR", "CAR"), "9906": ("Synthetic Runner", "RB", "NE"),
    "9902": ("Synthetic Kicker", "K", "LAR"),
}
_OPP_STARTERS = ["9228", "9221", "9901", "9903", "9904", "9905", "11646", "9906",
                 "9902", "IND"]
#: The fixture owner's starters (from tests/fixtures/sleeper_league.json).
_MY_STARTERS = ["3161", "3198", "6790", "6794", "8167", "3271", "8151", "9997", "1945", "SEA"]

_CHROME_CANDIDATES = (
    "/opt/pw-browsers/chromium", "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    shutil.which("chromium") or "", shutil.which("chromium-browser") or "",
    shutil.which("google-chrome") or "", shutil.which("chrome") or "",
)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _mods():
    return (_load("weekly_dashboard_scenarios", "scripts/weekly/dashboard_scenarios.py"),
            _load("weekly_dashboard_cli", "scripts/weekly/dashboard.py"),
            _load("weekly_gameday_cli", "scripts/weekly/gameday.py"))


def feed_rows(status_by_game: dict[tuple[str, str], str], *, week: int = 3,
              skip: set[tuple[str, str]] = frozenset()) -> list[dict]:
    """The status feed for one week, in the shape the sync validates."""
    rows = []
    for i, (away, home) in enumerate(_GAMES):
        if (away, home) in skip:
            continue
        rows.append({"week": week, "home": home, "away": away,
                     "status": status_by_game.get((away, home), "pre_game"),
                     "date": "2026-09-27", "game_id": f"2026103{i:02d}"})
    return rows


def slate(early: str, late: str, night: str, thursday: str = "complete") -> dict:
    out = {}
    for g in _GAMES:
        out[g] = (thursday if g == ("DET", "BUF") else early if g in _EARLY
                  else late if g in _LATE else night)
    return out


def write_feed(directory: Path, rows: list[dict], *, as_of: datetime,
               failed_at: datetime | None = None) -> None:
    manifest = ing.Manifest.load(directory, 2026)
    (directory / GAME_STATUS_FILE).write_text(json.dumps(
        {"as_of": as_of.isoformat(timespec="seconds"), "season": 2026,
         "source": "synthetic game status feed", "dropped": 0, "games": rows}), "utf-8")
    manifest.record(GAME_STATUS_NAME, path=directory / GAME_STATUS_FILE, rows=len(rows),
                    source="synthetic game status feed", as_of=as_of,
                    weeks=sorted({r["week"] for r in rows}))
    if failed_at is not None:
        manifest.record_failure(GAME_STATUS_NAME, source="synthetic game status feed",
                                error="HTTP 503 from upstream", at=failed_at)
    manifest.save()


def _matchup(roster_id: int, starters: list[str], points: dict[str, float | None],
             bench: dict[str, float] | None = None, *, total: float | None = None,
             custom: float | None = None, players: list[str] | None = None,
             short_points: bool = False) -> dict:
    """One Sleeper matchup row. `points` maps starter id -> platform value
    (None = the platform sent nothing); the total is the sum unless given."""
    sp = [points.get(s) for s in starters]
    if short_points:
        sp = sp[:-1]                       # the platform sent one value too few
    known = [v for v in sp if v is not None]
    pp = {s: v for s, v in zip(starters, sp) if v is not None and s != "0"}
    pp.update(bench or {})
    return {"roster_id": roster_id, "matchup_id": 1, "starters": list(starters),
            "players": list(players or [s for s in starters if s != "0"]),
            "starters_points": sp, "players_points": pp,
            "points": round(sum(known), 2) if total is None else total,
            "custom_points": custom}


MY_POINTS_MIXED = {"3161": 14.80, "3198": 10.20, "6790": 0.00, "6794": 17.10, "8167": 6.30,
                   "3271": 0.00, "8151": 0.00, "9997": 7.70, "1945": 7.00, "SEA": -1.00}
OPP_POINTS_MIXED = {"9228": 20.14, "9221": 12.50, "9901": 0.00, "9903": 8.00, "9904": 6.00,
                    "9905": 0.00, "11646": 0.00, "9906": 0.00, "9902": 0.00, "IND": 0.00}


def build(root: Path, kind: str) -> tuple[Path, datetime, dict]:
    """Write one synthetic cache for `kind` under `root`; returns
    (cache_root, frozen now, notes for the caller)."""
    if kind not in SCENARIOS:
        raise ValueError(f"unknown scenario {kind!r}; choose from {SCENARIOS}")
    scn, _, _ = _mods()
    base = "partial_schedule" if kind == "conflict" else "complete"
    now = {"pregame": SATURDAY, "pregame_stale": SATURDAY, "conflict": SATURDAY,
           "no_archive": SUNDAY_LATE, "injury_after": SUNDAY_NIGHT}.get(kind, SUNDAY_LATE)
    manifest = scn.build_scenario(root, base, now=SATURDAY)
    directory = manifest.directory
    snap = json.loads((directory / "sleeper_league.json").read_text("utf-8"))
    players = json.loads((directory / "sleeper_players.json").read_text("utf-8"))
    for sid, (name, pos, team) in _OPP_PLAYERS.items():
        players[sid] = {"player_id": sid, "full_name": name, "position": pos, "team": team,
                        "injury_status": None, "status": "Active", "gsis_id": None}
    opp = snap["rosters"][1]
    opp["starters"] = list(_OPP_STARTERS)
    opp["players"] = list(_OPP_STARTERS)
    info: dict = {}

    pregame_like = kind in ("pregame", "pregame_stale", "conflict")
    if pregame_like:
        my_pts = {s: 0.0 for s in _MY_STARTERS}
        opp_pts = {s: 0.0 for s in _OPP_STARTERS}
        status = slate("pre_game", "pre_game", "pre_game", thursday="pre_game")
        feed_as_of = SATURDAY - timedelta(minutes=5)
    else:
        my_pts, opp_pts = dict(MY_POINTS_MIXED), dict(OPP_POINTS_MIXED)
        status = slate("complete", "in_game", "pre_game")
        feed_as_of = now - timedelta(minutes=4)
    my_starters = list(_MY_STARTERS)
    kwargs: dict = {}
    if kind == "empty":
        my_starters[7] = "0"                  # FLEX 2 empty
        my_pts.pop("9997")
    if kind == "mixed":
        # The platform total includes the DST's -1.00, but starters_points
        # arrives one value short: that starter is UNKNOWN, not 0, and the
        # reconciliation says the totals cannot be tied out.
        kwargs["short_points"] = True
        kwargs["total"] = 62.10
    opp_kwargs: dict = {}
    if kind == "custom":
        opp_kwargs["custom"] = 50.0
    if kind == "injury_after":
        # The designation change itself is applied in `render`, AFTER the
        # board has frozen its record: that is what "discovered after the
        # record" means, and applying it here would put "Out" in the record.
        status[("CLE", "TB")] = "suspended"
        status[("GB", "NYJ")] = "postponed"            # a word the page does not know
        status[("SEA", "ARI")] = "in_game"             # 4h05 after kickoff: overtime
        status[("IND", "KC")] = "in_game"
        status[("NYG", "LAR")] = "pre_game"
        my_pts["SEA"] = 4.00
        my_pts["8151"] = 3.10
    skip = {("MIN", "CHI")} if kind == "injury_after" else set()

    week = 4 if kind == "rollover" else 3
    snap["state"] = {**snap["state"], "week": week, "display_week": week}
    snap["week"] = week
    snap["as_of"] = (now - timedelta(minutes=3)).isoformat(timespec="seconds")
    snap["rosters"][0]["starters"] = my_starters
    bench_pts = {"5022": 11.20, "4892": 0.00, "4984": 22.30} if not pregame_like else {}
    snap["matchups"] = [
        _matchup(1, my_starters, my_pts, bench_pts, players=snap["rosters"][0]["players"],
                 **kwargs),
        _matchup(2, _OPP_STARTERS, opp_pts, **opp_kwargs)]
    (directory / "sleeper_league.json").write_text(json.dumps(snap, indent=1), "utf-8")
    (directory / "sleeper_players.json").write_text(json.dumps(players, indent=1), "utf-8")
    m = ing.Manifest.load(directory, 2026)
    m.record("sleeper_league", path=directory / "sleeper_league.json",
             rows=len(snap["rosters"]), as_of=now - timedelta(minutes=3),
             source="api.sleeper.app (read-only)", weeks=[week])
    m.record("sleeper_players", path=directory / "sleeper_players.json", rows=len(players),
             as_of=now - timedelta(minutes=50), source="api.sleeper.app (read-only)")
    if kind == "pregame_stale":
        # The board's lineup gate rests on the injury table; a failed refresh
        # withholds the actions while the league snapshot stays FRESH.
        m.record_failure("injuries", source="nflreadpy.load_injuries",
                         error="HTTP 503 from upstream", at=now)
    m.save()
    write_feed(directory, feed_rows(status, week=week, skip=skip), as_of=feed_as_of)
    info["now"] = now
    return root, now, info


def render(kind: str, out: Path, *, take_screenshot: bool = False, drive: bool = False
           ) -> dict:
    """Board first (so an archive exists), then Game Day. Returns a summary
    dict with exit codes, the record, and any browser/fit results."""
    scn, board_cli, gd_cli = _mods()
    root = out / "caches" / kind
    if root.exists():
        shutil.rmtree(root)
    build(root, kind)
    page_dir = out / kind
    if page_dir.exists():
        shutil.rmtree(page_dir)
    archive = page_dir / "archive"
    summary: dict = {"kind": kind}
    # 1. the pregame board, frozen at Saturday noon, writes the decision-time
    #    archive Game Day will join. For the rollover scenario the board is
    #    rendered for week 3 so that the week-4 page finds NO eligible record.
    if kind != "no_archive":
        rc = board_cli.main(["--cache-root", str(root), "--owner", "fixture_owner",
                             "--write", "--anonymous", "--out-dir", str(page_dir),
                             "--archive-root", str(archive), "--now", SATURDAY.isoformat(),
                             *(["--week", "3"] if kind == "rollover" else [])])
        summary["board_rc"] = rc
    now = _now_for(kind)
    if kind == "injury_after":
        players_p = root / "season2026" / "sleeper_players.json"
        players = json.loads(players_p.read_text("utf-8"))
        players["6790"]["injury_status"] = "Out"       # Questionable in the frozen record
        players_p.write_text(json.dumps(players, indent=1), "utf-8")
    args = ["--cache-root", str(root), "--owner", "fixture_owner", "--write", "--anonymous",
            "--out-dir", str(page_dir), "--archive-root", str(archive),
            "--now", now.isoformat()]
    if drive:
        summary["browser"] = drive_browser(root, page_dir, archive, now, gd_cli)
    rc = gd_cli.main(args)
    summary["gameday_rc"] = rc
    html_path = page_dir / "gameday_latest.html"
    summary["record"] = json.loads((page_dir / "gameday_latest.json").read_text("utf-8"))
    if kind == "rollover":
        # A second render of the same week with a changed lineup: the page
        # must diff against its own previous record, and only that.
        snap_p = root / "season2026" / "sleeper_league.json"
        snap = json.loads(snap_p.read_text("utf-8"))
        snap["rosters"][0]["starters"][4] = "5022"
        snap["matchups"][0]["starters"][4] = "5022"
        snap["matchups"][0]["points"] = round(snap["matchups"][0]["points"] + 4.9, 2)
        snap_p.write_text(json.dumps(snap), "utf-8")
        rc2 = gd_cli.main(args[:-1] + [(now + timedelta(minutes=20)).isoformat()])
        summary["second_rc"] = rc2
        summary["second"] = json.loads((page_dir / "gameday_latest.json").read_text("utf-8"))
    if take_screenshot:
        for label, width in (("desktop", 1280), ("narrow500", 500)):
            png = out / (f"{kind}.png" if label == "desktop" else f"{kind}-{label}.png")
            ok = scn.screenshot(html_path, png, width=width)
            print(f"[gameday] {kind}: {label} screenshot "
                  f"{png if ok else 'SKIPPED (no headless Chromium)'}", file=sys.stderr)
        fit = scn.horizontal_overflow(html_path, 375)
        summary["fit375"] = None if fit is None else fit.line()
        print(f"[gameday] {kind}: 375px fit: "
              f"{'UNAVAILABLE — not a pass' if fit is None else fit.line()}", file=sys.stderr)
    return summary


def _now_for(kind: str) -> datetime:
    return {"pregame": SATURDAY, "pregame_stale": SATURDAY, "conflict": SATURDAY,
            "injury_after": SUNDAY_NIGHT}.get(kind, SUNDAY_LATE)


# --------------------------------------------------------------------------
# Browser drive: a fixture server and the page's own Refresh
# --------------------------------------------------------------------------
def chrome_binary() -> str | None:
    for c in _CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    return None


class _Fixture(http.server.BaseHTTPRequestHandler):
    """Serves the three endpoints the page fetches. The matchups endpoint
    walks a SEQUENCE: each request gets the next scripted response, so one
    page drive covers a correction, a rate limit, a recovery, an older
    response and a malformed body in order."""

    sequence: list = []
    state: dict = {}
    feed: list = []
    hits: list = []

    def _send(self, status: int, body: bytes | None, *, date: str | None = None,
              ctype: str = "application/json") -> None:
        self.send_response_only(status)
        self.send_header("Date", date or self.date_time_string())
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "etag,date")
        self.send_header("Cache-Control", "no-store")
        if body is not None:
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (http.server API)
        path = self.path.split("?")[0]
        type(self).hits.append(path)
        if path.endswith("/state/nfl"):
            step = type(self).state.get("step", 0)
            body = {"season": "2026", "week": 4 if step >= 5 else 3, "season_type": "regular"}
            return self._send(200, json.dumps(body).encode())
        if "/matchups/" in path:
            seq = type(self).sequence
            step = type(self).state.get("step", 0)
            type(self).state["step"] = step + 1
            item = seq[min(step, len(seq) - 1)]
            if item["kind"] == "rows":
                return self._send(200, json.dumps(item["rows"]).encode(), date=item.get("date"))
            if item["kind"] == "status":
                return self._send(item["status"], b"")
            if item["kind"] == "malformed":
                return self._send(200, b"{not json", date=None)
        if "/nfl/regular/" in path:
            return self._send(200, json.dumps(type(self).feed).encode())
        return self._send(404, b"null")

    def log_message(self, *a):
        pass


_HARNESS = """<!doctype html><html><body style="margin:0">
<iframe id="f" src="{url}" style="width:400px;height:1200px;border:0" onload="run()"></iframe>
<script>
async function run(){{
  var out = [];
  try {{
    var f = document.getElementById('f'), w = f.contentWindow, d = f.contentDocument;
    var api = w.gridironGameDay;
    function txt(sel){{ var e = d.querySelector(sel); return e ? e.textContent : null; }}
    function snap(label, r){{
      var pts = d.querySelectorAll('#gd-score .pts');
      out.push({{label: label, result: r || null,
        mode: txt('#gd-modepill'), status: txt('#gd-status'),
        mine: pts[0] ? pts[0].textContent : null, opp: pts[1] ? pts[1].textContent : null,
        lead: txt('#gd-score .lead'), settled: txt('#gd-score .settle'),
        changes: d.querySelectorAll('#gd-changes .chg li').length,
        changeText: Array.prototype.map.call(d.querySelectorAll('#gd-changes .chg li'), function(e){{ return e.textContent; }}),
        available: d.querySelectorAll('#gd-actions .act:not(.off)').length,
        states: Array.prototype.map.call(d.querySelectorAll('#gd-mine .roster .st'), function(e){{ return e.textContent; }}),
        oppStates: Array.prototype.map.call(d.querySelectorAll('#gd-opp .roster .st'), function(e){{ return e.textContent; }}),
        failures: api ? api.state().failures : null, live: api ? api.state().live : null,
        rollover: api ? api.state().rollover : null, nextPoll: api ? api.state().nextPollMs : null,
        oddsOnScore: /%|win prob|P\\(win\\)|odds/i.test(txt('#gd-score') + ' ' + txt('#gd-actions'))}});
    }}
    snap('initial');
    if (!api) throw new Error('page script did not expose gridironGameDay');
    var steps = {steps};
    for (var i = 0; i < steps.length; i++) {{
      var r = await api.refresh(false);
      snap(steps[i], r);
    }}
    // keyboard: the refresh control is a native button, focusable and labelled
    var b = d.getElementById('gd-refresh'); b.focus();
    var focusables = d.querySelectorAll('button, a[href], summary');
    out.push({{label: 'keyboard', activeIsRefresh: d.activeElement === b,
      tag: b.tagName, disabled: b.disabled, focusableCount: focusables.length,
      firstFocusable: focusables[0] ? focusables[0].tagName + ':' + focusables[0].textContent.trim().slice(0, 20) : null,
      outline: d.defaultView.getComputedStyle(b).outlineStyle}});
  }} catch (err) {{ out.push({{label: 'ERROR', error: String(err && err.stack || err)}}); }}
  document.title = 'RESULT ' + btoa(unescape(encodeURIComponent(JSON.stringify(out))));
}}
</script></body></html>"""


def drive_browser(root: Path, page_dir: Path, archive: Path, now: datetime, gd_cli) -> dict:
    """Render the page against a local fixture server and drive Refresh.

    Steps the matchups endpoint walks, one per refresh:
      1. correction  — the opponent's total is LOWERED, one of ours moves up;
      2. 429         — rate limited: last good kept, page marked STALE;
      3. recovery    — a good payload again;
      4. older       — a good payload whose Date header is OLDER than the one
                       applied in step 3: discarded, never applied;
      5. malformed   — a body that is not JSON: last good kept;
      6. rollover    — NFL state says week 4: scores shown, polling stops.
    """
    binary = chrome_binary()
    if binary is None:
        return {"executed": False, "reason": "no headless Chromium found"}
    snap = json.loads((root / "season2026" / "sleeper_league.json").read_text("utf-8"))
    rows = snap["matchups"]

    def variant(my_delta: float, opp_total: float, mine_wr: float) -> list:
        out = json.loads(json.dumps(rows))
        out[0]["starters_points"][3] = mine_wr
        out[0]["players_points"]["6794"] = mine_wr
        out[0]["points"] = round(out[0]["points"] + my_delta, 2)
        out[1]["points"] = opp_total
        return out

    base_my = rows[0]["points"]
    # Older than the fixture server's own clock (the Date header on every
    # other response is real wall time), not older than the scenario's frozen
    # instant: the page compares server dates with server dates.
    old_date = (datetime.now(UTC) - timedelta(days=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")
    _Fixture.sequence = [
        {"kind": "rows", "rows": variant(2.0, 44.10, 19.10)},          # correction
        {"kind": "status", "status": 429},                             # rate limit
        {"kind": "rows", "rows": variant(3.5, 45.00, 20.60)},          # recovery
        {"kind": "rows", "rows": variant(-10.0, 30.00, 5.00), "date": old_date},  # older
        {"kind": "malformed"},                                         # malformed
        {"kind": "rows", "rows": variant(3.5, 45.00, 20.60)},          # rollover (state wk 4)
    ]
    _Fixture.state = {"step": 0}
    _Fixture.feed = json.loads((root / "season2026" / GAME_STATUS_FILE).read_text("utf-8"))["games"]
    _Fixture.hits = []
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Fixture)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{port}"
        live_dir = page_dir / "browser"
        live_dir.mkdir(parents=True, exist_ok=True)
        rc = gd_cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                          "--anonymous", "--out-dir", str(live_dir), "--archive-root",
                          str(archive), "--now", now.isoformat(),
                          "--api-base", base, "--api-base", base])
        html_path = live_dir / "gameday_latest.html"
        steps = ["correction", "429", "recovery", "older", "malformed", "rollover"]
        harness = live_dir / "harness.html"
        harness.write_text(_HARNESS.format(url=html_path.resolve().as_uri(),
                                           steps=json.dumps(steps)), "utf-8")
        proc = subprocess.run(
            [binary, "--headless=new", "--no-sandbox", "--disable-gpu",
             "--allow-file-access-from-files", "--window-size=900,1400",
             "--virtual-time-budget=30000", "--dump-dom", harness.resolve().as_uri()],
            capture_output=True, timeout=240)
        m = re.search(r"<title>RESULT ([A-Za-z0-9+/=]+)</title>", proc.stdout.decode("utf-8", "replace"))
        if rc != 0 or not m:
            return {"executed": False, "reason": f"render rc={rc}; harness produced no result",
                    "hits": list(_Fixture.hits)}
        out = json.loads(base64.b64decode(m.group(1)).decode("utf-8"))
        return {"executed": True, "base": base, "steps": out, "hits": list(_Fixture.hits),
                "base_my": base_my}
    finally:
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, default=REPO_ROOT / ".cache" / "gameday-scenarios")
    ap.add_argument("--only", choices=SCENARIOS, default=None)
    ap.add_argument("--screenshot", action="store_true")
    ap.add_argument("--browser", action="store_true",
                    help="drive the mixed scenario's Refresh against a local fixture server")
    args = ap.parse_args(argv)
    worst = 0
    for kind in SCENARIOS if args.only is None else (args.only,):
        print(f"=== scenario: {kind} ===")
        s = render(kind, args.out, take_screenshot=args.screenshot,
                   drive=args.browser and kind == "mixed")
        worst = max(worst, s.get("board_rc", 0), s["gameday_rc"])
        b = s.get("browser")
        if b is not None:
            if not b.get("executed"):
                print(f"[gameday] browser drive NOT executed: {b.get('reason')}", file=sys.stderr)
            else:
                for step in b["steps"]:
                    print(f"[browser] {json.dumps(step)[:400]}", file=sys.stderr)
                (args.out / "browser_drive.json").write_text(json.dumps(b, indent=1), "utf-8")
    return worst


if __name__ == "__main__":
    sys.exit(main())
