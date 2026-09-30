"""The live Sleeper comparison (`gridiron.shadow`): captured, recorded,
graded — and never allowed to touch the page. Synthetic data only; no
network (every fetch here is a fake)."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from gridiron import shadow as SH
from gridiron.ingest import Manifest

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _fake_fetch(rows_by_pos):
    calls = []

    def fetch(url):
        calls.append(url)
        pos = url.split("position%5B%5D=")[1]
        return rows_by_pos.get(pos, [])
    fetch.calls = calls
    return fetch


WR_ROWS = [{"player_id": "100", "team": "NYJ", "opponent": "MIA",
            "stats": {"rec": 5, "rec_yd": 60, "rec_td": 1, "pts_ppr": 99, "adp_dd_ppr": 3}},
           {"player_id": "", "stats": {"rec": 1}},
           {"player_id": "101", "stats": {"adp_dd_ppr": 40}},
           "garbage"]


def test_a_projected_line_is_scored_with_the_leagues_rules():
    # half-PPR: 5 * 0.5 + 60 * 0.1 + 1 * 6 = 14.5 — Sleeper's own pts_* ignored
    assert SH.score_projection(WR_ROWS[0]["stats"]) == 14.5
    assert SH.score_projection({"pass_yd": 250, "pass_td": 2, "pass_int": 1,
                                "rush_yd": 20}) == 10 + 8 - 1 + 2
    assert SH.score_projection({"adp_dd_ppr": 3}) is None


def test_fetch_keeps_scored_players_and_refuses_an_empty_week():
    fetch = _fake_fetch({"WR": WR_ROWS})
    blob = SH.fetch_sleeper(2026, 4, fetch=fetch, now=NOW)
    assert set(blob["players"]) == {"100"}
    assert blob["players"]["100"]["points"] == 14.5
    assert len(fetch.calls) == 4 and all("/2026/4?" in u for u in fetch.calls)
    with pytest.raises(RuntimeError):
        SH.fetch_sleeper(2026, 4, fetch=_fake_fetch({}), now=NOW)


def test_the_file_round_trips_and_only_for_its_own_week(tmp_path):
    SH.write_shadow(tmp_path, SH.fetch_sleeper(2026, 4, fetch=_fake_fetch({"WR": WR_ROWS}),
                                               now=NOW))
    blob = SH.read_shadow(tmp_path, season=2026, week=4)
    assert blob["players"] == {"100": {"points": 14.5}}
    assert SH.read_shadow(tmp_path, season=2026, week=5) is None
    assert SH.is_fresh(tmp_path, season=2026, week=4, now=NOW + timedelta(hours=1))
    assert not SH.is_fresh(tmp_path, season=2026, week=4, now=NOW + timedelta(hours=3))
    (tmp_path / SH.SHADOW_FILE).write_text("{not json", encoding="utf-8")
    assert SH.read_shadow(tmp_path, season=2026, week=4) is None


def test_the_block_marks_only_pre_kickoff_captures(tmp_path):
    blob = {"source": "sleeper", "week": 4, "fetched_at": NOW.isoformat(),
            "fetched_at_dt": NOW, "players": {"1": {"points": 10.0},
                                               "2": {"points": 7.0},
                                               "3": {"points": 3.0}}}
    block = SH.shadow_block(blob, [("1", NOW + timedelta(days=3)),
                                   ("2", NOW - timedelta(hours=1)),
                                   ("3", None), ("9", NOW + timedelta(days=1))])
    assert block["players"] == {"1": {"points": 10.0, "pre_kickoff": True},
                                "2": {"points": 7.0, "pre_kickoff": False},
                                "3": {"points": 3.0, "pre_kickoff": False}}
    assert "never used" in block["note"]
    assert SH.shadow_block(None, [("1", None)]) is None


def _cache_with_state(tmp_path, week=4):
    m = Manifest(tmp_path, {}, 2026)
    snap = tmp_path / "sleeper_league.json"
    snap.write_text(json.dumps({"state": {"season": 2026, "week": week}}), encoding="utf-8")
    m.record("sleeper_league", path=snap, rows=1, source="t", as_of=NOW)
    return m


def test_the_pull_step_is_best_effort_and_outside_the_manifest(tmp_path, capsys):
    pw = _load("shadow_pull_week", "scripts/ingest/pull_week.py")
    m = _cache_with_state(tmp_path)
    before = dict(m.entries)

    def boom(url):
        raise RuntimeError("network down")
    pw.pull_shadow(m, NOW, False, fetch=boom)
    assert "FAILED" in capsys.readouterr().err
    assert not (tmp_path / SH.SHADOW_FILE).exists()

    fetch = _fake_fetch({"WR": WR_ROWS})
    pw.pull_shadow(m, NOW, False, fetch=fetch)
    assert SH.read_shadow(tmp_path, season=2026, week=4)["players"]["100"]["points"] == 14.5
    pw.pull_shadow(m, NOW + timedelta(minutes=30), False, fetch=fetch)
    assert len(fetch.calls) == 4                              # reused, not re-fetched
    assert m.entries == before                                # gates nothing


def test_the_record_carries_the_shadow_and_the_page_does_not_change(tmp_path):
    scn = _load("shadow_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("shadow_dash", "scripts/weekly/dashboard.py")

    def render(tag, with_shadow):
        root = tmp_path / tag
        scn.build_scenario(root, "complete", now=scn.NOW)
        if with_shadow:
            rec_ids = ["6790", "3198", "6794"]
            SH.write_shadow(root / "season2026", {
                "source": "sleeper", "season": 2026, "week": 3,
                "fetched_at": (scn.NOW - timedelta(hours=1)).isoformat(),
                "players": {sid: {"points": 9.5} for sid in rec_ids}})
        out = tmp_path / f"out_{tag}"
        cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                  "--out-dir", str(out), "--archive-root", str(tmp_path / f"arch_{tag}"),
                  "--now", scn.NOW.isoformat(), "--no-archive"])
        return json.loads((out / "dashboard_latest.json").read_text("utf-8"))

    plain, shadowed = render("plain", False), render("shadowed", True)
    assert plain["shadow"] is None
    block = shadowed["shadow"]
    assert block["source"] == "sleeper" and block["players"]
    assert all(v["points"] == 9.5 for v in block["players"].values())
    for key in ("roster", "best_lineup", "actions", "upgrades", "gate", "degraded"):
        assert plain[key] == shadowed[key], f"the shadow changed {key}"


def test_the_weekly_grade_scores_all_three_on_the_same_players():
    gw = _load("shadow_grade_week", "scripts/weekly/grade_week.py")
    def p(sid, pos, proj, withheld=False):
        return {"sleeper_id": sid, "id": sid, "gsis_id": f"g{sid}", "position": pos,
                "projected": proj, "withheld": withheld}
    archive = {
        "roster": [p("1", "WR", 12.0), p("2", "WR", 8.0), p("3", "WR", 0.0, True)],
        "radar": {"candidates": [p("4", "WR", 6.0), p("5", "RB", 9.0)]},
        "shadow": {"players": {"1": {"points": 9.0, "pre_kickoff": True},
                               "2": {"points": 11.0, "pre_kickoff": True},
                               "3": {"points": 7.0, "pre_kickoff": True},
                               "4": {"points": 6.0, "pre_kickoff": False},
                               "5": {"points": 9.0, "pre_kickoff": True}}}}
    actuals = {"g1": 5.0, "g2": 15.0, "g3": 0.0, "g4": 20.0, "g5": 9.0}
    out = gw.shootout(archive, actuals)
    # scored: 1, 2 (WR) and 5 (RB); 3 is withheld, 4 was captured after kickoff
    assert out["n"] == 3 and out["pairs"] == 1
    assert out["sleeper_pairwise"] == 1.0 and out["baseline_pairwise"] == 0.0
    assert out["baseline_mae"] == round((7 + 7 + 0) / 3, 3)
    assert gw.shootout({"roster": []}, actuals) is None
