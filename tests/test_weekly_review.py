"""The weekly review loop: the record carries usage, the review reads it,
and nothing in it outruns the page's gates. Synthetic records only."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


WR = _load("weekly_review_cli", "scripts/weekly/weekly_review.py")


def _wk(week, pts, snap, opps):
    return {"week": week, "points": pts, "snap_pct": snap, "opportunities": opps,
            "targets": opps, "target_share": 0.2, "carries": 0, "receptions": 3,
            "yards": 40, "touchdowns": 0}


def _usage(gid, trend, ppg, *weeks):
    return {"gsis_id": gid, "games": len(weeks), "points": ppg * len(weeks),
            "ppg": ppg, "weeks": list(weeks), "trend": trend, "trend_why": "why"}


def _record(**over):
    rec = {
        "generated": "2026-09-30T14:59:33+00:00", "season": 2026, "week": 4,
        "phase": "pregame", "degraded": True, "actionable": 0, "conditional": 0,
        "sources": ["sleeper_league   FRESH    as-of x",
                    "sleeper_players  STALE    as-of y  pulled 7h ago"],
        "gate": {"lineup": {"allowed": False}, "waiver": {"allowed": False}},
        "slots": ["WR", "FLEX"],
        "roster": [
            {"sleeper_id": "90001", "gsis_id": "g-a", "name": "Wing Alpha",
             "position": "WR", "team": "NO", "lineup": "BENCH", "projected": 11.2},
            {"sleeper_id": "90002", "gsis_id": "g-b", "name": "Wing Beta",
             "position": "WR", "team": "JAX", "lineup": "START", "projected": 5.2},
            {"sleeper_id": "90003", "gsis_id": "g-k", "name": "Kicker Gamma",
             "position": "K", "team": "PIT", "lineup": "START", "projected": 8.0}],
        "current_lineup": ["90002", "90003"], "best_lineup": ["90001", "90003"],
        "alternatives": [{"slot": "FLEX", "bench_id": "90001", "starter_id": "90002",
                          "delta_points": 6.0}],
        "actions": [{"kind": "swap", "status": "WITHHELD", "headline":
                     "FLEX: start Wing Alpha over Wing Beta",
                     "deadline": "2026-10-04T17:00:00+00:00",
                     "player_ids": ["90001", "90002"], "verify": ["read tags"]}],
        "radar": {"pool": [
            {"id": "90004", "name": "Tight Delta", "position": "TE", "team": "NYJ"},
            {"id": "90005", "name": "Back Epsilon", "position": "RB", "team": "SF"}],
            "candidates": [
            {"id": "90004", "name": "Tight Delta", "position": "TE", "team": "NYJ",
             "verdict": "BELOW", "lineup_gain": None, "gap": -6.2},
            {"id": "90005", "name": "Back Epsilon", "position": "RB", "team": "SF",
             "verdict": "RESEARCH", "gap": 1.5}]},
        "usage": {"through_week": 3, "window": 4, "basis": "volume only (test)",
                  "players": {
                      "90001": _usage("g-a", "FALLING", 10.2, _wk(1, 16.4, 91, 9),
                                      _wk(2, 8.9, 96, 7), _wk(3, 5.4, 88, 6)),
                      "90002": _usage("g-b", "RISING", 10.1, _wk(1, 11.2, 65, 3),
                                     _wk(2, 3.3, 90, 1), _wk(3, 15.9, 81, 8)),
                      "90004": _usage("g-d", "RISING", 10.9, _wk(1, 9.9, 42, 4),
                                      _wk(2, 2.7, 36, 3), _wk(3, 20.0, 58, 8)),
                      "90005": _usage("g-e", "FALLING", 3.9, _wk(1, 7.5, 43, 15),
                                      _wk(2, 2.5, 33, 7), _wk(3, 1.7, 15, 5))}},
    }
    rec.update(over)
    return rec


def test_the_review_flags_a_projection_that_runs_against_usage():
    text = WR.build_review(_record())
    section = text.split("## Projection vs actual usage")[1].split("##")[0]
    assert "Wing Alpha" in section and "FALLING" in section
    assert "Wing Beta" in section and "RISING" in section
    assert "not overridden" in section


def test_agreeing_signals_are_not_flagged():
    rec = _record()
    rec["usage"]["players"]["90001"]["trend"] = "RISING"
    text = WR.build_review(rec)
    assert "No start/sit where the projection and the volume trend disagree" in text


def test_withheld_advice_stays_withheld_and_nothing_is_submitted():
    text = WR.build_review(_record())
    assert "gates open: none" in text and "withheld: lineup, waiver" in text
    assert "| WITHHELD | FLEX: start Wing Alpha" in text
    checks = text.split("## Before acting")[1]
    assert "never submit" in text.lower()
    assert "FLEX: start Wing Alpha" not in checks          # a withheld action is no check-list item


def test_risers_watch_list_and_kickers():
    text = WR.build_review(_record(), watch=["90005"])
    risers = text.split("role is growing")[1].split("**Watch list")[0]
    assert "Tight Delta" in risers and "Back Epsilon" not in risers
    assert "| Back Epsilon (RB) | available | RESEARCH | — | FALLING |" in text
    assert "Kicker Gamma" not in text.split("## My roster")[1].split("##")[0]


def test_outside_rankings_resolve_and_compare(tmp_path):
    csv = tmp_path / "fti.csv"
    csv.write_text("rank,name,position,team\n90,Wing Beta,WR,JAC\n"
                   "94,Back Epsilon,RB,SF\n114,Wing Alpha,WR,NO\n"
                   "5,Somebody Else,RB,KC\n", encoding="utf-8")
    text = WR.build_review(_record(), ranks=[WR.load_ranks(csv)])
    block = text.split("## Outside ranking: fti")[1]
    assert "| Wing Beta | #90 | — | none |" in block
    assert "| Wing Alpha | #114 | — | Back Epsilon (#94) |" in block
    assert "1 listed player(s) are neither mine" in block


def test_watch_names_resolve_strictly():
    ids, problems = WR.resolve_watch(_record(), ["Tight Delta,TE", "90005",
                                                 "Delta,TE", "no comma"])
    assert ids == ["90004", "90005"]
    assert len(problems) == 2


def test_a_record_without_usage_says_so():
    rec = _record()
    rec.pop("usage")
    text = WR.build_review(rec)
    assert "carries no usage block" in text and "NO LINE" in text


def test_the_cli_writes_only_where_told(tmp_path, capsys):
    rec = tmp_path / "r.json"
    rec.write_text(json.dumps(_record()), encoding="utf-8")
    out = tmp_path / "review" / "w4.md"
    assert WR.main(["--record", str(rec), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Weekly review")
    assert WR.main(["--record", str(tmp_path / "missing.json")]) == 2


def test_a_rendered_board_carries_the_usage_block(tmp_path):
    """End to end on the synthetic cache: the dashboard's record has usage for
    its roster, bounded by the page's own stats boundary."""
    scn = _load("review_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("review_dash", "scripts/weekly/dashboard.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    out = tmp_path / "out"
    cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch"),
              "--now", scn.NOW.isoformat()])
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    usage = rec["usage"]
    assert usage["through_week"] == rec["stats_through"]
    roster_ids = {p["sleeper_id"] for p in rec["roster"] if p["gsis_id"]}
    assert roster_ids & set(usage["players"])
    for line in usage["players"].values():
        assert all(w["week"] <= rec["stats_through"] for w in line["weeks"])
    assert WR.build_review(rec).startswith("# Weekly review")
