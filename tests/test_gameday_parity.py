"""Python/browser parity: the inline script must judge the SAME fixtures the
same way the Python model does — statuses, locks, points, the lead, the
capacity sentence, and every action's availability and reason.

The script is run under Node (pre-installed on the build machine, not a
dependency of the repo) with a stub DOM, its stdout decoded as UTF-8
explicitly (the page text carries typographic quotes; a Windows console
codepage turned every case into a decode error); the test is skipped, and says so,
when Node is absent. Both sides read one embedded payload: the page's own
`gd-data` block, which is what the browser reads.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from gridiron import gameday as gd

import test_gameday as T

NODE = shutil.which("node") or ("/opt/node22/bin/node" if Path("/opt/node22/bin/node").exists() else None)

_RUNNER = r"""
const fs = require('fs');
const [,, jsPath, dataPath] = process.argv;
const D = JSON.parse(fs.readFileSync(dataPath, 'utf8'));
function mkEl(){ return {textContent:'', className:'', disabled:false, title:'', children:[], firstChild:null,
  appendChild(c){ this.children.push(c); this.firstChild=this.children[0]; return c; },
  removeChild(c){ this.children.shift(); this.firstChild=this.children[0]||null; }, addEventListener(){}, focus(){}}; }
const els = {}; const dataEl = mkEl(); dataEl.textContent = JSON.stringify(D);
global.document = {getElementById(id){ return id==='gd-data'?dataEl:(els[id]||(els[id]=mkEl())); },
  createElement(){ return mkEl(); }, createTextNode(t){ return {nodeValue:t}; }, addEventListener(){}, hidden:false};
global.window = {fetch(){}}; global.fetch = function(){}; global.setInterval = function(){};
eval(fs.readFileSync(jsPath, 'utf8'));
const api = window.gridironGameDay; const vm = api.state().lastGood;
function side(sd){ return sd ? {platform_points: sd.platform_points, reconciliation: sd.reconciliation, exposure: null,
  starters: sd.starters.map(s => ({slot:s.slot, sleeper_id:s.sleeper_id, state:s.state, lock:s.lock, points:s.points, injury_note:s.injury_note})),
  bench: sd.bench.map(s => ({slot:s.slot, sleeper_id:s.sleeper_id, state:s.state, lock:s.lock, points:s.points}))} : null; }
process.stdout.write(JSON.stringify({mine: side(vm.mine), opp: side(vm.opp), lead: vm.lead, settled: vm.settled,
  capacity: vm.capacity.sentence, blockers: vm.blockers,
  actions: vm.actions.map(a => ({available:a.available, why:a.why, eligible:a.eligible}))}));
"""


def _js_view(day: gd.GameDay, tmp_path: Path) -> dict:
    js = tmp_path / "gameday.js"
    js.write_text(gd._JS, "utf-8")
    data = tmp_path / "data.json"
    data.write_text(json.dumps(day.embedded, default=str), "utf-8")
    runner = tmp_path / "run.js"
    runner.write_text(_RUNNER, "utf-8")
    proc = subprocess.run([NODE, str(runner), str(js), str(data)], capture_output=True, text=True,
                          encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _py_view(day: gd.GameDay) -> dict:
    def side(sd):
        return None if sd is None else {
            "platform_points": sd.platform_points, "reconciliation": sd.reconciliation,
            "exposure": None,
            "starters": [{"slot": s.slot, "sleeper_id": s.sleeper_id, "state": s.state,
                          "lock": s.lock, "points": s.points, "injury_note": s.injury_note}
                         for s in sd.starters],
            "bench": [{"slot": s.slot, "sleeper_id": s.sleeper_id, "state": s.state,
                       "lock": s.lock, "points": s.points} for s in sd.bench]}
    return {"mine": side(day.score.mine), "opp": side(day.score.opp), "lead": day.score.lead(),
            "settled": day.score.settled(), "capacity": day.capacity.sentence(),
            "actions": [{"available": a.available, "why": a.why, "eligible": a.eligible}
                        for a in day.actions]}


def _norm(x):
    """Typography the two sides spell differently (’ vs ') is not a difference."""
    if isinstance(x, str):
        return x.replace("’", "'")
    if isinstance(x, list):
        return [_norm(v) for v in x]
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in x.items()}
    return x


CASES = {
    "legal": {},
    "league_stale": {"srcs": T.sources(league_fresh=False)},
    "players_stale": {"players_fresh": False},
    "out": {"players": {**T.PLAYERS, "5": {**T.PLAYERS["5"], "injury_status": "Out"}}},
    "doubtful": {"players": {**T.PLAYERS, "5": {**T.PLAYERS["5"], "injury_status": "Doubtful"}}},
    "ineligible": {"players": {**T.PLAYERS, "5": {**T.PLAYERS["5"], "position": "RB"}}},
    "multi_position": {"players": {**T.PLAYERS, "5": {**T.PLAYERS["5"], "position": "RB",
                                                      "fantasy_positions": ["RB", "WR"]}}},
    "no_feed": {"fd": T.feed({}, present=False)},
    "stale_feed": {"fd": T.feed({"KC": "pre_game", "LAR": "pre_game"}, fresh=False)},
    "on_ir": {"snap": T.snapshot(reserve=("5",))},
    "on_taxi": {"snap": T.snapshot(taxi=("5",))},
    "lineup_moved": {"snap": T.snapshot(my_starters=("1", "2", "5", "4", "SEA"))},
    "feed_locks": {"fd": T.feed({"KC": "in_game", "LAR": "pre_game"})},
    "unknown_id": {"players": {k: v for k, v in T.PLAYERS.items() if k != "5"}},
    "empty_slot": {"snap": T.snapshot(my_starters=("1", "2", "0", "4", "SEA"),
                                      my_points=(14.0, 0.0, 0.0, 7.0, -1.0))},
    "unknown_word": {"fd": T.feed({"KC": "unrecognized_live_status", "LAR": "pre_game"})},
    "canceled_incoming": {"fd": T.feed({"LAR": "canceled", "KC": "pre_game"})},
    "canceled_outgoing": {"fd": T.feed({"KC": "canceled", "LAR": "pre_game"})},
}


@pytest.mark.skipif(NODE is None, reason="no Node runtime on this machine")
@pytest.mark.parametrize("name", sorted(CASES))
def test_the_script_judges_the_same_fixture_the_same_way(schedule, tmp_path, name):
    kw = dict(CASES[name])
    if name == "empty_slot":
        d = T._actions(schedule, tmp_path, kind="empty_slot", ids=("5",), slot="WR", **kw)
    else:
        d = T._actions(schedule, tmp_path, **kw)
    assert d.actions, "every case carries one archived action"
    py, js = _norm(_py_view(d)), _norm(_js_view(d, tmp_path))
    assert js["actions"] == py["actions"], name
    assert js["mine"] == py["mine"] and js["opp"] == py["opp"], name
    assert (js["lead"], js["settled"], js["capacity"]) == (py["lead"], py["settled"], py["capacity"])


@pytest.mark.skipif(NODE is None, reason="no Node runtime on this machine")
def test_the_script_sees_the_same_deadline_and_future_record_gates(schedule, tmp_path):
    passed = T._actions(schedule, tmp_path, deadline=T.NOW - timedelta(minutes=1))
    assert _norm(_js_view(passed, tmp_path))["actions"] == _norm(_py_view(passed))["actions"]
    late = T._actions(schedule, tmp_path, generated=T.NOW - timedelta(minutes=5),
                      deadline=T.NOW - timedelta(hours=1))
    assert _norm(_js_view(late, tmp_path))["actions"] == _norm(_py_view(late))["actions"]
    assert not late.actions[0].eligible


schedule = T.schedule
