"""Release review of the Free Agent Radar (PR #7): the defects a skeptical
pass found, each pinned by a reproduction written before its fix.

  1. A page left open is not advice forever. The build knows the instant its
     gated evidence expires (the same cadences the gate used) and every
     move's kickoff deadline; the page carries both and re-judges them on the
     reader's clock, so a LINEUP pickup or a lineup swap stops reading as
     current once its deadline or its evidence has passed. Ages alone were
     not a gate.
  2. The radar diff compares like for like on the whole basis (season,
     scoring, projection baseline, slots), never calls an OLDER input a
     refresh, and reports a LINEUP move whose drop, displaced starter or gain
     changed on the roster side as exactly that, never as a projection move.
  3. The cloud run does not re-download every input on every run: a carried
     input is judged by the age of the pull that fetched it, and a source's
     refresh threshold is half the limit IN EFFECT, so a game-day limit is
     not outlived by a weekday threshold.
  4. The published-build check reads only its own page's stamp, compares
     stamps as instants, and recovers when the device comes back online.
"""
from __future__ import annotations

import importlib.util
import shutil
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gridiron import gating
from gridiron import radar as R
from gridiron import theme
from gridiron.freshness import CADENCES, SourceFreshness, Status, assess, expires_at
from gridiron.ingest import Manifest
from gridiron.waivers import BELOW, LINEUP, RESEARCH

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCN = _load("validity_dashboard_scenarios", "scripts/weekly/dashboard_scenarios.py")
CLI = _load("validity_dashboard_cli", "scripts/weekly/dashboard.py")
NOW = SCN.NOW                                  # Saturday 2026-09-26 12:00 UTC


def _render(tmp_path: Path, kind: str, *, now=NOW):
    root = tmp_path / kind
    SCN.build_scenario(root, kind, now=now)
    out = tmp_path / "out" / kind
    assert CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                     "--anonymous", "--out-dir", str(out),
                     "--archive-root", str(tmp_path / "arch" / kind),
                     "--now", now.isoformat()]) == 0
    return (out / "dashboard_latest.html").read_text("utf-8")


# ------------------------------------------------ 1. evidence has an expiry

def _fresh(name: str, as_of: datetime, now: datetime) -> SourceFreshness:
    s = assess(name, now=now, as_of=as_of, rows=10)
    assert s.status is Status.FRESH
    return s


def test_a_fresh_source_expires_where_its_own_cadence_says():
    # Pulled Saturday 11:00 UTC (a planning day, 24 h limit). At Sunday 00:00
    # ET the game-day limit of 6 h applies and it is already 17 h old.
    s = _fresh("sleeper_league", datetime(2026, 9, 26, 11, tzinfo=UTC), NOW)
    assert expires_at(s, NOW) == datetime(2026, 9, 27, 4, tzinfo=UTC)
    # Pulled Sunday 14:00 UTC: 6 h later, on the same game day.
    sun = datetime(2026, 9, 27, 15, tzinfo=UTC)
    s = _fresh("sleeper_league", datetime(2026, 9, 27, 14, tzinfo=UTC), sun)
    assert expires_at(s, sun) == datetime(2026, 9, 27, 20, tzinfo=UTC)
    # No game-day tightening: the plain limit.
    s = _fresh("schedules", datetime(2026, 9, 26, 11, tzinfo=UTC), NOW)
    assert expires_at(s, NOW) == datetime(2026, 9, 26, 11, tzinfo=UTC) + timedelta(hours=168)
    # The instant it names is the first instant `assess` calls it STALE.
    for name in ("sleeper_league", "sleeper_players", "injuries"):
        s = _fresh(name, datetime(2026, 9, 26, 11, tzinfo=UTC), NOW)
        t = expires_at(s, NOW)
        assert assess(name, now=t - timedelta(minutes=1), as_of=s.as_of, rows=1).status is Status.FRESH
        assert assess(name, now=t + timedelta(minutes=1), as_of=s.as_of, rows=1).status is Status.STALE
    stale = SourceFreshness("sleeper_league", Status.STALE, NOW - timedelta(days=3), 1, None, "x")
    assert expires_at(stale, NOW) is None


def test_the_gate_names_the_first_expiry_among_the_sources_an_action_rests_on():
    now = NOW
    srcs = [_fresh("sleeper_league", datetime(2026, 9, 26, 11, tzinfo=UTC), now),
            _fresh("schedules", datetime(2026, 9, 26, 11, tzinfo=UTC), now),
            _fresh("injuries", datetime(2026, 9, 26, 11, tzinfo=UTC), now),
            _fresh("sleeper_players", datetime(2026, 9, 26, 10, tzinfo=UTC), now),
            _fresh("weekly_stats", datetime(2026, 9, 25, 11, tzinfo=UTC), now)]
    when, name = gating.valid_until(srcs, ("lineup", "waiver"), now)
    assert when == datetime(2026, 9, 27, 4, tzinfo=UTC)
    assert name in ("sleeper_league", "sleeper_players")      # both tighten at midnight ET
    # box scores are gated on coverage, never on age, so they never expire the page
    assert gating.valid_until(srcs[-1:], ("lineup",), now) == (None, "")


def test_the_board_carries_its_expiry_and_every_move_carries_its_deadline(tmp_path):
    html = _render(tmp_path, "complete")
    meta = re.search(r'<meta name="gridiron-valid-until" content="([^"]+)"', html)
    assert meta and meta.group(1) == "2026-09-27T04:00:00+00:00"
    assert 'id="validity"' in html and "gridironValidity" in html
    rows = re.findall(r'<li class="rrow"([^>]*)>', html)
    lineup = [r for r in rows if 'data-verdict="LINEUP"' in r]
    assert len(lineup) == 2
    for r in lineup:
        assert 'data-deadline="2026-09-27T17:00:00+00:00"' in r
        assert "data-gated" in r and 'data-lapse-verdict="LOCKED"' in r
    research = [r for r in rows if 'data-verdict="RESEARCH"' in r]
    assert research and all('data-deadline="2026-09-27T17:00:00+00:00"' in r for r in research)
    assert all("data-gated" not in r for r in research)     # research is not a move
    cards = re.findall(r'<article class="dcard ([^"]*)"([^>]*)>', html)
    moves = [a for c, a in cards if re.search(r"tone-(go|cond|opt)", c)]
    # the pick-one pickup card and the optional swap: each lapses at its own
    # kickoff and is struck when the evidence expires
    assert len(moves) == 2 and all("data-gated" in a and "data-deadline" in a for a in moves)
    assert 'data-live-count="LINEUP"' in html
    # the build-time "all current" line is itself gated: it is a claim about now
    assert re.search(r'<span class="chip ok" data-gated="" data-expire-text="No longer true',
                     html)
    # and so is the headline that counts the moves
    assert re.search(r'<div class="headline" data-gated="" data-expire-text="EXPIRED', html)


def test_a_board_built_on_stale_inputs_marks_its_moves_held_in_the_row_itself(tmp_path):
    html = _render(tmp_path, "stale")
    rows = re.findall(r'<li class="rrow"([^>]*)>(.*?)</summary>', html, re.S)
    lineup = [(a, s) for a, s in rows if 'data-verdict="LINEUP"' in a]
    assert lineup, "the stale scenario should still show the last known comparison"
    for attrs, summary in lineup:
        assert "data-held=" in attrs
        assert 'class="vstate">WITHHELD' in summary
    assert '<meta name="gridiron-valid-until"' not in html      # nothing to expire


def test_game_day_does_not_show_a_pregame_pickup_as_a_current_lineup_move():
    from gridiron.gameday import summarise_radar
    block = {"counts": {"pool": 3, "evaluated": 2, "unprojected": 1}, "snapshot_as_of": "x",
             "candidates": [{"id": "9", "name": "Nine", "position": "WR", "verdict": LINEUP,
                             "slot": "FLEX", "lineup_gain": 4.0, "drop": {"name": "Drop"},
                             "kickoff": "2026-09-27T17:00:00+00:00",
                             "displaces": {"id": "7", "name": "Seven"}}]}
    s = summarise_radar(block)
    assert s["moves"][0]["deadline"] == "2026-09-27T17:00:00+00:00"


def test_game_day_built_after_kickoff_shows_the_pregame_pickup_off(tmp_path):
    gd = _load("validity_gameday_scenarios", "scripts/weekly/gameday_scenarios.py")
    out = tmp_path / "gd"
    assert gd.main(["--out", str(out), "--only", "pregame"]) == 0
    html = (out / "pregame" / "gameday_latest.html").read_text("utf-8")
    card = html[html.index('id="gd-radar"'):html.index("</div>", html.index('id="gd-radar"'))]
    assert "LINEUP</span>" not in card and 'class="pill ok"' not in card
    if "PREGAME</span>" in card:          # the scenario's record carries a LINEUP move
        assert 'data-deadline="' in card


# ------------------------------------------------ 2. like for like diff

def _rec(**kw):
    base = {"generated": "2026-09-26T12:00:00+00:00", "season": 2026, "week": 3,
            "league_id": "L1", "my_roster_id": 1, "baseline": "B", "slots": ["QB", "FLEX"],
            "roster": [{"sleeper_id": "d1", "name": "Dee"}, {"sleeper_id": "d2", "name": "Two"}]}
    radar = {"version": R.RADAR_VERSION, "snapshot_as_of": "2026-09-26 11:00 UTC",
             "basis": {"scoring": "S1"},
             "evidence": {"sleeper_league": "2026-09-26T11:00:00+00:00"},
             "status": {"sleeper_league": "fresh"}, "pool": [], "owned": [], "candidates": []}
    radar.update(kw.pop("radar", {}))
    base.update(kw)
    base["radar"] = radar
    return base


def _lineup(i="5", drop="d1", displaces="s1", gain=4.0, projected=12.0):
    return {"id": i, "name": f"p{i}", "position": "WR", "verdict": LINEUP, "reason": "r",
            "projected": projected, "lineup_gain": gain, "slot": "FLEX",
            "drop": {"id": drop, "name": f"drop {drop}"},
            "displaces": {"id": displaces, "name": f"starter {displaces}"}}


@pytest.mark.parametrize("key,prev,cur", [
    ("season", 2025, 2026), ("baseline", "A", "B"), ("slots", ["QB"], ["QB", "FLEX"])])
def test_a_different_season_or_basis_is_no_comparison(key, prev, cur):
    ch = R.diff_radar(_rec(**{key: prev}), _rec(**{key: cur}))
    assert not ch.comparable and key in ch.why and not ch.items


def test_a_different_scoring_basis_is_no_comparison():
    ch = R.diff_radar(_rec(radar={"basis": {"scoring": "S0"}}), _rec())
    assert not ch.comparable and "scoring" in ch.why
    old = _rec()
    del old["radar"]["basis"]
    assert not R.diff_radar(old, _rec()).comparable


def test_an_older_input_is_a_regression_never_a_refresh():
    prev = _rec(radar={"snapshot_as_of": "2026-09-26 11:00 UTC",
                       "evidence": {"sleeper_league": "2026-09-26T11:00:00+00:00"}})
    cur = _rec(generated="2026-09-26T12:15:00+00:00",
               radar={"snapshot_as_of": "2026-09-25 09:00 UTC",
                      "evidence": {"sleeper_league": "2026-09-25T09:00:00+00:00"}})
    ch = R.diff_radar(prev, cur)
    assert ch.comparable and not ch.refreshed
    ev = [c for c in ch.items if c.kind == "evidence"]
    assert ev and "OLDER" in ev[0].detail
    assert not ch.summary().startswith("Refreshed")


def test_unreadable_or_vanished_evidence_is_said_not_skipped():
    prev = _rec(radar={"evidence": {"sleeper_league": "2026-09-26T11:00:00+00:00",
                                    "injuries": "2026-09-26T10:00:00+00:00"},
                       "status": {"sleeper_league": "fresh", "injuries": "fresh"}})
    cur = _rec(generated="2026-09-26T12:15:00+00:00",
               radar={"evidence": {"sleeper_league": "not-a-time"},
                      "status": {"sleeper_league": "fresh"}})
    ch = R.diff_radar(prev, cur)
    assert not ch.refreshed
    details = " | ".join(c.subject + ": " + c.detail for c in ch.items if c.kind == "evidence")
    assert "injuries" in details and "no longer" in details
    assert "unreadable" in details


def test_a_changed_drop_or_gain_on_the_roster_side_is_a_lineup_change_not_a_projection():
    prev = _rec(radar={"candidates": [_lineup(drop="d1", gain=4.0)]})
    cur = _rec(generated="2026-09-26T12:15:00+00:00",
               roster=[{"sleeper_id": "d2", "name": "Two"}, {"sleeper_id": "n3", "name": "New"}],
               radar={"candidates": [_lineup(drop="d2", gain=1.5)]})
    ch = R.diff_radar(prev, cur)
    assert not any(c.kind == "projection" for c in ch.items)
    lineup = [c for c in ch.items if c.kind == "lineup"]
    assert lineup and "drop d1" in lineup[0].detail and "drop d2" in lineup[0].detail
    assert "+4.00 → +1.50" in lineup[0].detail and "did not move" in lineup[0].detail
    roster = [c for c in ch.items if c.kind == "roster"]
    assert roster and "New" in roster[0].detail and "Dee" in roster[0].detail
    assert ch.revised


def test_the_same_lineup_move_with_the_same_numbers_is_not_a_change():
    prev = _rec(radar={"candidates": [_lineup()]})
    cur = _rec(generated="2026-09-26T12:15:00+00:00", radar={"candidates": [_lineup()]})
    ch = R.diff_radar(prev, cur)
    assert ch.comparable and not ch.items


# ------------------------------------------------ 3. the cloud refresh policy

def _pull_week():
    return _load("validity_pull_week", "scripts/ingest/pull_week.py")


def test_a_carried_input_is_judged_by_its_pull_time_not_refetched_every_run(tmp_path, monkeypatch):
    from gridiron import carryover
    pw = _pull_week()
    cache = tmp_path / "cache"
    cache.mkdir()
    pulled = datetime(2026, 9, 26, 11, tzinfo=UTC)
    (cache / "weekly_stats.parquet").write_bytes(b"x")
    m = Manifest.load(cache, 2026)
    m.record("weekly_stats", path=cache / "weekly_stats.parquet", rows=10,
             source="t", weeks=[1, 2])
    e = m.get("weekly_stats")
    m.entries["weekly_stats"] = replace(e, as_of=pulled.isoformat(timespec="seconds"),
                                        error=carryover.CARRIED_FORWARD)
    m.save()
    calls: list[str] = []

    class FakeNfl:
        def __getattr__(self, name):
            def load(*a, **kw):
                calls.append(name)
                raise RuntimeError("offline")
            return load

    monkeypatch.setitem(sys.modules, "nflreadpy", FakeNfl())
    manifest = Manifest.load(cache, 2026)
    pw.pull_nflverse(manifest, 2026, pulled + timedelta(hours=2), force=False)
    assert "load_player_stats" not in calls           # 2 h old against a 36 h threshold
    entry = manifest.get("weekly_stats")
    assert entry.error == "" and entry.as_of == pulled.isoformat(timespec="seconds")


def test_the_refresh_threshold_is_half_the_limit_in_effect_now():
    pw = _pull_week()
    sunday = datetime(2026, 9, 27, 15, tzinfo=UTC)
    saturday = datetime(2026, 9, 26, 15, tzinfo=UTC)
    assert pw.refresh_after_hours("injuries", sunday) == CADENCES["injuries"].gameday_max_age_hours / 2
    assert pw.refresh_after_hours("injuries", saturday) == CADENCES["injuries"].max_age_hours / 2
    assert pw.refresh_after_hours("weekly_stats", sunday) == 36.0


# ------------------------------------------------ 4. the published-build check

def test_the_build_check_reads_its_own_page_and_compares_instants():
    js = theme.SNAPSHOT_JS
    assert "data-page" in js and "wrong page" in js
    assert "res.stamp>OWN" not in js and "Date.parse(res.stamp)" in js
    assert "'online'" in js
    assert "still usable" not in js


# ---------------------------------------------- the matchup's own expiry

def test_the_win_probability_expires_with_the_evidence_it_rests_on(tmp_path):
    """The P(win) block used to carry no expiry: the moves beside it read
    EXPIRED on the reader's clock while '55%' stayed up as if current."""
    html = _render(tmp_path, "complete")
    block = re.search(r'<div class="pwin"([^>]*)>', html)
    assert block, "the matchup numbers sit in one gated block"
    attrs = block.group(1)
    assert 'data-gated=""' in attrs
    # the matchup gate's own sources (league snapshot, designations) expire at
    # midnight ET in this fixture, the same instant as the page's moves
    assert 'data-valid-until="2026-09-27T04:00:00+00:00"' in attrs
    assert "data-expire-text=\"EXPIRED — the " in attrs and "P(win)" in attrs
    body = html[block.end():html.index("</details></div>", block.end())]
    assert '<span class="vstate"></span>' in body and "win probability" in body
    assert "LAST KNOWN" not in body
    # the first-screen copy of the margin carries the same expiry
    panel = re.search(r'<p class="pwin"([^>]*)><b>Matchup:</b>(.*?)</p>', html, re.S)
    assert panel and 'data-valid-until="2026-09-27T04:00:00+00:00"' in panel.group(1)
    assert '<span class="vstate"></span>' in panel.group(2)
    # every rendering of the matchup means sits under a gated or held element
    assert html.count("<b>Matchup:</b>") == 1


def test_the_matchup_keeps_its_own_expiry_when_the_page_has_none(tmp_path):
    """Damaged kickoff rows close the lineup/waiver gates (no page expiry),
    while the matchup's own evidence is fresh: the block keeps its time."""
    html = _render(tmp_path, "partial_schedule")
    assert '<meta name="gridiron-valid-until"' not in html
    block = re.search(r'<div class="pwin"([^>]*)>', html)
    assert block and 'data-valid-until="' in block.group(1) and 'data-gated=""' in block.group(1)


def test_the_held_matchup_names_what_actually_closed_its_gate(tmp_path):
    html = _render(tmp_path, "missing")
    held = re.search(r'<div class="pwin" data-held="([^"]*)"', html)
    if held is None:
        pytest.skip("no matchup view in this scenario")
    assert "box scores" in held.group(1)


def test_a_win_probability_built_on_stale_inputs_is_marked_last_known(tmp_path):
    html = _render(tmp_path, "stale")
    block = re.search(r'<div class="pwin"([^>]*)>', html)
    assert block and "data-held=" in block.group(1) and "data-gated" not in block.group(1)
    body = html[block.end():html.index("</details></div>", block.end())]
    assert "<b>LAST KNOWN</b>" in body and "(last known) win probability" in body


NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists()
                                else None)

_VALIDITY_RUNNER = r"""
const fs = require('fs');
const [,, jsPath] = process.argv;
function el(attrs){ const a = Object.assign({}, attrs), v = {textContent:''};
  return {getAttribute(k){ return k in a ? a[k] : null; }, setAttribute(k, x){ a[k] = String(x); },
          removeAttribute(k){ delete a[k]; }, hasAttribute(k){ return k in a; },
          querySelector(q){ return q === '.vstate' ? v : null; }, attrs: a, vstate: v}; }
const meta = el({content: '2026-09-27T04:00:00Z', 'data-why': 'why'});
const page = el({'data-gated': '', 'data-expire-text': 'page expired'});
const later = el({'data-gated': '', 'data-valid-until': '2026-09-27T10:00:00Z', 'data-expire-text': 'own expired'});
const earlier = el({'data-gated': '', 'data-valid-until': '2026-09-27T01:00:00Z', 'data-expire-text': 'own expired'});
const ungated = el({'data-valid-until': '2026-09-27T01:00:00Z'});
const nodes = [page, later, earlier, ungated];
global.document = {querySelector(q){ return q.indexOf('gridiron-valid-until') >= 0 ? meta : null; },
  querySelectorAll(q){ return q.indexOf('data-live-count') >= 0 ? [] : nodes.slice(0, 3); },
  getElementById(){ return null; }, addEventListener(){}, hidden: false};
global.window = {}; global.setInterval = function(){};
const noMeta = process.argv[3] === 'nometa';
const box = {hidden: true}, head = {textContent: ''}, text = {textContent: ''};
if (noMeta) { document.querySelector = function(){ return null; };
  document.getElementById = function(id){ return id === 'validity' ? box : id === 'validity-head' ? head : id === 'validity-text' ? text : null; }; }
eval(fs.readFileSync(jsPath, 'utf8'));
const out = {};
for (const [k, t] of [['t0', '2026-09-27T00:00:00Z'], ['t1', '2026-09-27T02:00:00Z'],
                      ['t2', '2026-09-27T05:00:00Z'], ['t3', '2026-09-27T11:00:00Z']]) {
  window.gridironValidity.tick(Date.parse(t));
  out[k] = {page: page.attrs['data-live'] || '', later: later.attrs['data-live'] || '',
            earlier: earlier.attrs['data-live'] || '', text: later.vstate.textContent,
            banner: noMeta ? (box.hidden ? '' : head.textContent + ' | ' + text.textContent) : null};
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="no Node runtime on this machine")
def test_an_element_with_its_own_evidence_expires_on_its_own_instant(tmp_path):
    import subprocess
    js = tmp_path / "validity.js"
    js.write_text(theme.VALIDITY_JS, "utf-8")
    runner = tmp_path / "run.js"
    runner.write_text(_VALIDITY_RUNNER, "utf-8")
    proc = subprocess.run([NODE, str(runner), str(js)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["t0"] == {"page": "", "later": "", "earlier": "", "text": "", "banner": None}
    assert got["t1"]["earlier"] == "expired" and not got["t1"]["page"] and not got["t1"]["later"]
    # past the page's instant but not the element's own: the element stays current
    assert got["t2"]["page"] == "expired" and got["t2"]["later"] == ""
    assert got["t3"]["later"] == "expired" and got["t3"]["text"] == "own expired"


@pytest.mark.skipif(NODE is None, reason="no Node runtime on this machine")
def test_an_element_that_expires_on_its_own_raises_the_banner_without_a_page_expiry(tmp_path):
    import subprocess
    js = tmp_path / "validity.js"
    js.write_text(theme.VALIDITY_JS, "utf-8")
    runner = tmp_path / "run.js"
    runner.write_text(_VALIDITY_RUNNER, "utf-8")
    proc = subprocess.run([NODE, str(runner), str(js), "nometa"], capture_output=True,
                          text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["t0"]["banner"] == ""
    # no page meta: the gated element with no time of its own never expires...
    assert got["t3"]["page"] == ""
    # ...the ones with their own time do, and the banner says so
    assert got["t1"]["earlier"] == "expired" and got["t1"]["banner"].startswith(
        "Evidence expired for 1 item(s) on this page.")
    assert "own expired" in got["t3"]["banner"] and "2 item(s)" in got["t3"]["banner"]


# ------------------------------------------------ live free-agent counts

def test_the_first_screen_free_agent_count_is_a_live_counter(tmp_path):
    """The side panel's 'N improve this week's lineup' was fixed at build
    time: after kickoff the radar's own counter fell to 0 while it stayed."""
    html = _render(tmp_path, "complete")
    panel = re.search(r"<b>Free agents:</b>(.*?)</p>", html, re.S)
    assert panel and 'data-live-count="LINEUP">2</b> improve this week' in panel.group(1)
    assert html.count('data-live-count="LINEUP"') == 2      # the panel and the radar KPI


_COUNT_RUNNER = r"""
const fs = require('fs');
const [,, jsPath] = process.argv;
function el(attrs, cls){ const a = Object.assign({}, attrs), c = new Set(cls || []), v = {textContent: ''};
  return {getAttribute(k){ return k in a ? a[k] : null; }, setAttribute(k, x){ a[k] = String(x); },
          removeAttribute(k){ delete a[k]; }, hasAttribute(k){ return k in a; },
          querySelector(q){ return q === '.vstate' ? v : null; }, attrs: a, textContent: '',
          classList: {toggle(n, on){ if (on) c.add(n); else c.delete(n); }, has(n){ return c.has(n); }}}; }
const rows = [0, 1].map(i => el({'data-deadline': '2026-09-27T17:00:00Z', 'data-verdict': 'LINEUP',
  'data-verdict-built': 'LINEUP', 'data-lapse-verdict': 'LOCKED', 'data-lapse-text': 'locked'}));
const panel = el({'data-live-count': 'LINEUP'}, ['lime']); panel.textContent = '2';
const kpi = el({'data-live-count': 'LINEUP'}, ['lime']); kpi.textContent = '2';
global.document = {querySelector(){ return null; }, getElementById(){ return null; }, addEventListener(){}, hidden: false,
  querySelectorAll(q){
    if (q.indexOf('data-live-count') >= 0) return [panel, kpi];
    const m = q.match(/^li\.rrow\[data-verdict="([^"]+)"\]$/);
    if (m) return rows.filter(r => r.attrs['data-verdict'] === m[1]);
    return rows; }};
global.window = {}; global.setInterval = function(){};
eval(fs.readFileSync(jsPath, 'utf8'));
const out = {};
for (const [k, t] of [['before', '2026-09-27T16:00:00Z'], ['after', '2026-09-27T17:05:00Z']]) {
  window.gridironValidity.tick(Date.parse(t));
  out[k] = {panel: panel.textContent, kpi: kpi.textContent, lime: panel.classList.has('lime')};
}
process.stdout.write(JSON.stringify(out));
"""


@pytest.mark.skipif(NODE is None, reason="no Node runtime on this machine")
def test_the_live_counts_fall_and_lose_their_highlight_when_the_moves_lock(tmp_path):
    import subprocess
    js = tmp_path / "validity.js"
    js.write_text(theme.VALIDITY_JS, "utf-8")
    runner = tmp_path / "count.js"
    runner.write_text(_COUNT_RUNNER, "utf-8")
    proc = subprocess.run([NODE, str(runner), str(js)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    got = json.loads(proc.stdout)
    assert got["before"] == {"panel": "2", "kpi": "2", "lime": True}
    assert got["after"] == {"panel": "0", "kpi": "0", "lime": False}
