"""The next-decision section: trustworthy weekly next decisions.

Pins the milestone of 2026-09-21: an acquisition is a move only when it
improves THIS WEEK's best legal lineup; it names its displaced starter, its
drop, the coverage kept and its feasible alternative drops; it is
CONDITIONAL, never an unqualified ACTIONABLE, because availability is never
established from a cache; two cards that want the same drop are shown as an
either/or; bench-only pickups are research; and the page says what this
week's advice is for, what can still be done once the slate is locked, and
what waits for next week's inputs — with no next-week projection invented.
Older archives (depth pairs, ACTIONABLE acquisitions, no `next`) still
diff, grade and join.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from gridiron.dashboard import Action, next_decision
from gridiron.decisions import diff_archives, grade_archive
from gridiron.freshness import Phase, WeekContext
from gridiron.gameday import _judge_action
from gridiron.lineup import Player, plan_lineup, slot_order
from gridiron.projection import Projection

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCN = _load("nd_scenarios", "scripts/weekly/dashboard_scenarios.py")
CLI = _load("nd_dashboard_cli", "scripts/weekly/dashboard.py")


def render(tmp_path: Path, kind: str, *, now: datetime = SCN.NOW) -> tuple[int, str, dict]:
    root = tmp_path / kind
    SCN.build_scenario(root, kind, now=now)
    out = tmp_path / "out" / kind
    rc = CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                   "--out-dir", str(out), "--archive-root", str(tmp_path / "arch" / kind),
                   "--now", now.isoformat()])
    html = (out / "dashboard_latest.html").read_text("utf-8")
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    return rc, html, rec


def _names(rec: dict) -> dict[str, str]:
    return {p["sleeper_id"]: p["name"] for p in rec["roster"]}


# ------------------------------------------------------------ acquisitions

def test_an_acquisition_is_conditional_never_an_unqualified_actionable(tmp_path):
    _, html, rec = render(tmp_path, "complete")
    acq = [a for a in rec["actions"] if a["kind"] == "acquire"]
    assert acq and all(a["status"] == "CONDITIONAL" for a in acq)
    assert rec["actionable"] == sum(1 for a in rec["actions"] if a["status"] == "ACTIONABLE")
    assert rec["conditional"] == len(acq)
    assert "CONDITIONAL — if available" in html
    assert "Endorsed only if Sleeper shows him available" in html
    for a in acq:
        assert a["title"].startswith("If available, claim")
        body = a["body"]
        for needed in ("Benefit:", "THIS WEEK", "Cost: drop", "Coverage after the move:",
                       "Availability UNVERIFIED", "Limits:"):
            assert needed in body, needed
        assert any("FREE AGENT" in v for v in a["verify"])
        assert a["delta_points"] > 0


def test_two_pickups_that_cost_the_same_drop_are_an_either_or(tmp_path):
    _, html, rec = render(tmp_path, "complete")
    names = _names(rec)
    acq = [a for a in rec["actions"] if a["kind"] == "acquire"]
    assert len(acq) == 2
    drops = {a["player_ids"][1] for a in acq}
    assert len(drops) == 1, "the fixture's two pickups share the cheapest drop"
    drop_name = names[drops.pop()]
    for a in acq:
        assert "Either/or with" in a["backup"] and drop_name in a["backup"]
        assert "next verified drop" in a["backup"]
        # the fallback is a different player than the shared drop
        fallback = a["backup"].split("next verified drop for ")[1].split(" is ")[1].split(" (")[0]
        assert fallback != drop_name
    assert "are an either/or, not two moves" in html


def test_the_shortlist_lists_lineup_gains_only_and_no_depth_column(tmp_path):
    _, html, rec = render(tmp_path, "complete")
    assert rec["upgrades"] and all(u["lineup_gain"] > 0 and u["kind"] == "lineup"
                                   for u in rec["upgrades"])
    assert "Δ depth" not in html and "DEPTH" not in html
    # the radar names every feasible alternative drop for a lineup gain
    assert "Alternatives" in html
    lineup = [c for c in rec["radar"]["candidates"] if c["verdict"] == "LINEUP"]
    assert len(lineup) == len(rec["upgrades"])
    assert all(c["drop"] and c["lineup_gain"] > 0 for c in lineup)


def test_a_bench_only_pickup_is_on_the_watchlist_with_unpriced_future_value(tmp_path):
    _, html, rec = render(tmp_path, "complete")
    names = _names(rec)
    assert rec["watchlist"]
    for w in rec["watchlist"]:
        versus = next(p for p in rec["roster"] if p["sleeper_id"] == w["versus_id"])
        assert versus["position"] == w["add"]["position"], "like for like only"
        assert w["gap"] > 0
        assert w["add"]["sleeper_id"] not in {u["add"]["sleeper_id"] for u in rec["upgrades"]}
    assert "Watchlist and withheld — research, not moves" in html
    assert "not priced" in html
    assert names  # the roster is named on the local page (rule #10 carve-out)


def test_stale_inputs_withhold_the_acquisition_with_neutral_wording(tmp_path):
    _, html, rec = render(tmp_path, "stale")
    acq = [a for a in rec["actions"] if a["kind"] == "acquire"]
    assert acq and all(a["status"] == "WITHHELD" for a in acq)
    for a in acq:
        assert not a["title"].startswith(("If available", "Consider", "Add ", "Claim "))
        assert "would have improved" in a["title"]
    assert "If available, claim" not in html
    assert "WITHHELD" in rec["next"]["still_possible"][-1]


# ------------------------------------------------------ the week transition

def test_pregame_labels_the_week_and_the_open_starters(tmp_path):
    _, html, rec = render(tmp_path, "complete")
    nd = rec["next"]
    assert nd["week"] == 3 and nd["evidence"][0].startswith("advice is for WEEK 3")
    assert any("box scores through week 2" in e for e in nd["evidence"])
    assert nd["open_starters"] and nd["locked_starters"] < nd["total_starters"]
    assert nd["next_week"] == 4
    assert any("UNAVAILABLE" in line for line in nd["next_week_lines"])
    assert "Action Desk — week 3" in html
    assert "Week transition" in html and "Must wait for next-week inputs" in html


def test_a_locked_slate_says_what_can_still_be_done_and_what_waits(tmp_path):
    _, html, rec = render(tmp_path, "slate_end", now=SCN.NOW_SLATE_END)
    nd = rec["next"]
    assert nd["locked_starters"] == nd["total_starters"] == 10 and nd["open_starters"] == []
    assert rec["upgrades"] == [] and rec["actions"] == []
    assert any("all 10 starters have kicked off" in x for x in nd["still_possible"])
    assert any("roster moves for week 4" in x and "timing this page does not know" in x
               for x in nd["still_possible"])
    assert any("week 4 projections" in x and "not next week's" in x for x in nd["must_wait"])
    assert "Every starter has kicked off" in html
    assert "Hold — no supported change" in html
    assert "If available, claim" not in html and "Consider claiming" not in html
    assert "week 4 preview UNAVAILABLE" in html


def test_a_page_with_nothing_supported_shows_a_hold_and_why(tmp_path):
    _, html, rec = render(tmp_path, "missing")
    assert rec["actionable"] == 0 and rec["conditional"] == 0
    assert "Hold — no supported change" in html
    assert "What would change it" in html


def _player(sid, pos, team, mean=10.0, lineup="BENCH"):
    return Player(sid, f"p{sid}", pos, team, Projection(mean, 3.0), lineup, False, "",
                  "", (), "g" + sid)


def test_the_next_week_preview_reads_schedule_rows_and_invents_no_bye():
    now = datetime(2026, 9, 21, 12, tzinfo=UTC)
    sched = pd.DataFrame([
        {"season": 2026, "week": 3, "game_type": "REG", "home_team": "KC", "away_team": "LAR",
         "gameday": "2026-09-27", "gametime": "13:00", "game_id": "2026_03_LAR_KC"},
        {"season": 2026, "week": 3, "game_type": "REG", "home_team": "SEA", "away_team": "NYG",
         "gameday": "2026-09-27", "gametime": None, "game_id": "2026_03_NYG_SEA"},
        {"season": 2026, "week": 2, "game_type": "REG", "home_team": "DET", "away_team": "PIT",
         "gameday": "2026-09-20", "gametime": "13:00", "game_id": "2026_02_PIT_DET"},
    ])
    roster = [_player("1", "QB", "KC", 20, "START"), _player("2", "WR", "SEA", 9, "START"),
              _player("3", "RB", "DET", 8, "START")]
    ctx = WeekContext(2026, 2, 1, Phase.COMPLETE, now)
    plan = plan_lineup(roster, ["1", "2", "3"], ("QB", "WR", "RB"))
    nd = next_decision(context=ctx, roster=roster, plan=plan, schedule=sched, sources=(),
                       snapshot_as_of="2026-09-21 10:00 UTC", now=now, actions=())
    text = " ".join(nd.next_week_lines)
    assert nd.next_week == 3
    assert "1 of 3 roster players have a timed game" in text
    assert "no usable kickoff time: p2" in text
    assert "no week-3 row in the schedule for: p3 (DET)" in text
    assert "UNKNOWN, not read as a bye" in text
    assert "no week-3 projection" in text
    assert nd.evidence[0] == "advice is for WEEK 2 (complete)"
    # no schedule at all: unavailable, in so many words
    nd2 = next_decision(context=ctx, roster=roster, plan=plan, schedule=None, sources=(),
                        snapshot_as_of="x", now=now, actions=())
    assert nd2.next_week_lines == ("week 3 preview UNAVAILABLE: no schedule is loaded",)


# ------------------------------------------------- archive backward compatibility

def _old_record() -> dict:
    """A record as the released board wrote it: a depth pair, an ACTIONABLE
    acquisition, no `next`, no `conditional`, no `watchlist`."""
    return {
        "generated": "2026-09-20T12:07:52+00:00", "season": 2026, "week": 2,
        "sources": ["sleeper_league   FRESH    as-of x", "injuries         STALE    as-of y"],
        "roster": [
            {"sleeper_id": "5947", "gsis_id": "00-1", "name": "Bench WR", "position": "WR",
             "projected": 3.9, "withheld": False, "availability": "", "locked": False},
            {"sleeper_id": "11564", "gsis_id": "00-2", "name": "Starter QB", "position": "QB",
             "projected": 18.0, "withheld": False, "availability": "", "locked": False}],
        "current_lineup": ["11564"], "best_lineup": ["11564"],
        "alternatives": [],
        "upgrades": [{"add": {"sleeper_id": "421", "gsis_id": "00-3", "name": "Backup QB",
                              "position": "QB", "projected": 17.7},
                      "drop_id": "5947", "slot": "", "lineup_gain": 0.0,
                      "depth_gain": 13.8, "kind": "depth"}],
        "gate": {"waiver": {"allowed": True, "blockers": []},
                 "lineup": {"allowed": True, "blockers": []}},
        "actions": [{"kind": "acquire", "status": "ACTIONABLE", "urgency": "INFO",
                     "title": "Consider claiming Backup QB (QB)", "body": "add ...",
                     "deadline": None, "deadline_note": "", "backup": "",
                     "player_ids": ["421", "5947"], "slot": ""}],
    }


def test_an_old_archive_still_diffs_grades_and_joins_without_being_upgraded(tmp_path):
    old = _old_record()
    _, _, new = render(tmp_path, "complete")
    # diff: the new record's extra keys are simply not compared
    changes = diff_archives(old, new)
    assert changes.previous == old["generated"]
    # grade: the depth pair is a waiver comparison, UNVERIFIED, never scorable —
    # a speculative pair is not promoted by the grader
    grade = grade_archive(old, {"00-1": 4.0, "00-3": 20.0})
    waiver = [c for c in grade.comparisons if c.kind == "waiver"]
    assert len(waiver) == 1 and waiver[0].eligibility == "UNVERIFIED" and not waiver[0].scorable
    assert grade.agreement() == (0, 0)
    # join: Game Day refuses the old acquisition as a game-day move, and the
    # new CONDITIONAL one the same way, before either status is even read
    now = datetime(2026, 9, 20, 17, tzinfo=UTC)
    for raw in (old["actions"][0], next(a for a in new["actions"] if a["kind"] == "acquire")):
        live = _judge_action(raw, generated=now - timedelta(hours=1), now=now, by_id={},
                             starter_ids=())
        assert live.available is False and "not a game-day move" in live.why
    # and a new record carries what an old one lacked, without breaking old readers
    assert new["next"]["week"] == 3 and "conditional" in new and "watchlist" in new


def test_a_conditional_action_keeps_its_wording_and_is_not_counted_actionable():
    a = Action("acquire", "CONDITIONAL", "INFO", "If available, claim X", "detail", None,
               "note", "backup", neutral_headline="The last snapshot found X",
               neutral_detail="neutral")
    assert a.conditional and not a.actionable and not a.withheld
    assert a.title == "If available, claim X" and a.body == "detail"
    held = Action("acquire", "WITHHELD", "INFO", "If available, claim X", "detail", None,
                  "note", "backup", neutral_headline="The last snapshot found X",
                  neutral_detail="neutral")
    assert held.title == "The last snapshot found X" and held.body == "neutral"


# ------------------------------------------------------------ phone layout

@pytest.mark.skipif(SCN.chrome_binary() is None, reason="no headless Chromium on this machine")
def test_the_next_decision_section_fits_a_phone_and_its_disclosures_take_the_keyboard(tmp_path):
    """Executed only where a browser exists: the section must not push the
    page sideways at phone width, and every `details` disclosure it adds
    must be reachable by Tab (a native summary is focusable; this pins that
    nothing in the section removed that)."""
    import re
    import subprocess

    _, html, _ = render(tmp_path, "complete")
    page = tmp_path / "out" / "complete" / "dashboard_latest.html"
    fit = SCN.horizontal_overflow(page, 375)
    assert fit is not None and fit.fits, fit and fit.line()
    harness = tmp_path / "keys.html"
    harness.write_text(
        "<!doctype html><html><body style=\"margin:0\">"
        f"<iframe id=f src=\"{page.resolve().as_uri()}\" style=\"width:375px;height:2000px\" "
        "onload=\"probe()\"></iframe><script>function probe(){try{"
        "var d=document.getElementById('f').contentDocument;"
        "var s=d.querySelectorAll('#desk summary');var ok=0;"
        "var n=0;for(var i=0;i<s.length;i++){if(!s[i].getClientRects().length||(s[i].checkVisibility&&!s[i].checkVisibility()))continue;n++;s[i].focus();if(d.activeElement===s[i])ok++;}"
        "document.title='KEYS '+n+' '+ok;}catch(e){document.title='KEYSFAIL '+e.name}}"
        "</script></body></html>", encoding="utf-8")
    proc = subprocess.run(
        [SCN.chrome_binary(), "--headless=new", "--no-sandbox", "--disable-gpu",
         "--allow-file-access-from-files", "--window-size=900,2200",
         "--virtual-time-budget=5000", "--dump-dom", harness.resolve().as_uri()],
        capture_output=True, timeout=180)
    m = re.search(r"KEYS (\d+) (\d+)", proc.stdout.decode("utf-8", "replace"))
    assert m, proc.stdout[:200]
    assert int(m.group(1)) > 0 and m.group(1) == m.group(2)
