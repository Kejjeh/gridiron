"""The dashboard, end to end, with the network unplugged.

Drives `scripts/weekly/dashboard.py` through `main(argv)` against the three
synthetic scenarios `scripts/weekly/dashboard_scenarios.py` builds from the
committed fixtures (invented league, invented owner). Pins:

  1. it renders offline, from the cache alone;
  2. every source it reads declares its freshness on the page — derived
     from the script's own syntax, not from its SOURCES tuple;
  3. COMPLETE: projections, matchup, alternatives, upgrades with a drop,
     an archive, an evaluation verdict, and the UNCALIBRATED label on P(win);
  4. STALE: the DEGRADED banner, stale lines, a STALE designation;
  5. MISSING: every projection abstains, start/sit and upgrades abstain,
     P(win) abstains — nothing is filled in;
  6. the archive is what the page showed, and grading it leaks nothing;
  7. --anonymous keeps the league name off the page; the stdout summary
     never names a player.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CLI = _load("weekly_dashboard_cli", "scripts/weekly/dashboard.py")
SCN = _load("weekly_dashboard_scenarios", "scripts/weekly/dashboard_scenarios.py")
NOW = SCN.NOW


class NetworkUsed(AssertionError):
    pass


@pytest.fixture
def no_network(monkeypatch):
    import socket

    def boom(*a, **k):
        raise NetworkUsed("the dashboard opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    return True


def render(tmp_path: Path, kind: str, *extra: str, now: datetime = NOW) -> tuple[int, str, dict]:
    root = tmp_path / kind
    SCN.build_scenario(root, kind, now=now)
    out = tmp_path / "out" / kind
    rc = CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                   "--out-dir", str(out), "--archive-root", str(tmp_path / "arch" / kind),
                   "--now", now.isoformat(), *extra])
    html = (out / "dashboard_latest.html").read_text("utf-8") if (out / "dashboard_latest.html").exists() else ""
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8")) if html else {}
    return rc, html, rec


# ------------------------------------------------------------------ offline

def test_the_dashboard_renders_from_the_cache_with_no_network(tmp_path, no_network):
    rc, html, rec = render(tmp_path, "complete")
    assert rc == 0
    assert "<title>Week 3 decision dashboard" in html
    assert "1. Input freshness" in html and "7. Decision-time archive" in html


def test_every_source_the_dashboard_reads_declares_its_freshness(tmp_path):
    """Derived from the script's syntax: every literal name passed to
    read_frame / read_json / file must be in SOURCES, and on the page."""
    tree = ast.parse((ROOT / "scripts" / "weekly" / "dashboard.py").read_text("utf-8"))
    consumed = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", None) in (
                "read_frame", "read_json", "file"):
            if node.args and isinstance(node.args[0], ast.Constant):
                consumed.add(node.args[0].value)
    assert consumed == set(CLI.SOURCES), (consumed, CLI.SOURCES)
    assert set(CLI.SOURCES) == {"sleeper_league", "sleeper_players", "injuries", "schedules",
                                "weekly_stats", "snap_counts", "crosswalk"}
    _, html, _ = render(tmp_path, "complete")
    block = html.split("1. Input freshness")[1].split("2. How the baseline")[0]
    for s in CLI.SOURCES:
        assert f"<td>{s}</td>" in block, f"{s} has no freshness line"


# ----------------------------------------------------------------- complete

def test_complete_projects_matches_and_recommends_with_labels(tmp_path):
    rc, html, rec = render(tmp_path, "complete")
    assert rc == 0 and not rec["degraded"]
    assert "All inputs current." in html
    # projections: 13 of 14 rows, the DST abstains with its reason
    projected = [p for p in rec["roster"] if p["projected"] is not None]
    assert len(projected) == 13
    dst = next(p for p in rec["roster"] if p["position"] == "DEF")
    assert dst["projected"] is None and "team defense" in dst["reasons"][0]
    assert "UNVALIDATED" in rec["baseline"] and "BASELINE" in html
    # matchup, labelled
    m = rec["matchup"]
    assert m["opponent_roster_id"] == 2 and m["pwin"] is not None
    assert "UNCALIBRATED" in m["label"] and "UNCALIBRATED" in html
    assert m["opp_mean"] > 0
    # start/sit: the current lineup is scored, alternatives carry z and ΔP(win)
    assert rec["lineup_abstained"] == ""
    assert rec["best_points"] >= rec["current_points"]
    assert rec["alternatives"] and all("z" in a and "delta_pwin" in a for a in rec["alternatives"])
    # a Questionable player is flagged, not zeroed (rule #11)
    q = next(p for p in rec["roster"] if any("QUESTIONABLE" in f for f in p["flags"]))
    assert q["projected"] > 0
    # a Thursday player is LOCKED at the Saturday render
    assert any(p["locked"] for p in rec["roster"])
    # upgrades name a drop, and the drop is a roster player
    assert rec["upgrades"]
    roster_ids = {p["sleeper_id"] for p in rec["roster"]}
    for u in rec["upgrades"]:
        assert u["drop_id"] in roster_ids and u["kind"] in ("lineup", "depth")
    assert any(u["kind"] == "lineup" and u["lineup_gain"] > 0 for u in rec["upgrades"])
    assert "with the drop" in html
    # evaluation ran, and does not claim calibration
    assert "evaluated chronologically" in rec["evaluation"]["verdict"]
    assert rec["evaluation"]["pwin_calibrated"] is False
    assert "NOT validated" in html


def test_the_best_lineup_is_legal_under_the_locks(tmp_path):
    _, _, rec = render(tmp_path, "complete")
    by_id = {p["sleeper_id"]: p for p in rec["roster"]}
    from gridiron.lineup import eligible
    for slot, sid in zip(rec["slots"], rec["best_lineup"]):
        if sid is None:
            continue
        p = by_id[sid]
        assert eligible(slot, p["position"]), (slot, p["position"])
        assert p["lineup"] != "IR"
    for cur, best, slot in zip(rec["current_lineup"], rec["best_lineup"], rec["slots"]):
        if cur and by_id[cur]["locked"]:
            assert best == cur, f"locked starter moved out of {slot}"


# -------------------------------------------------------------------- stale

def test_stale_inputs_are_shown_labelled_and_the_banner_is_up(tmp_path):
    rc, html, rec = render(tmp_path, "stale")
    assert rc == 0 and rec["degraded"]
    assert "DEGRADED" in html
    assert any("sleeper_players" in n and "STALE" in n for n in rec["notes"])
    assert any("REFRESH FAILED" in s for s in rec["sources"])
    # the designation from the stale dump is labelled stale, not current
    henry = next(p for p in rec["roster"] if p["sleeper_id"] == "3198")
    assert "STALE designation" in henry["availability"]
    assert henry["projected"] > 0                       # Questionable is not Out
    # and the page still shows the numbers it has, rather than blanking them
    assert rec["matchup"]["pwin"] is not None
    rc2, *_ = render(tmp_path / "again", "stale", "--fail-on-degraded")
    assert rc2 == 1


# ------------------------------------------------------------------ missing

def test_missing_inputs_abstain_everywhere_and_fill_nothing_in(tmp_path):
    rc, html, rec = render(tmp_path, "missing")
    assert rc == 0 and rec["degraded"]
    assert all(p["projected"] is None for p in rec["roster"])
    assert all(p["reasons"] for p in rec["roster"])
    assert "UNKNOWN" in rec["lineup_abstained"]
    assert rec["alternatives"] == [] and rec["best_lineup"] == rec["current_lineup"]
    assert rec["matchup"]["pwin"] is None and "ABSTAINED" in rec["matchup"]["pwin_reason"]
    assert "UNKNOWN" in rec["waiver_abstained"] and rec["upgrades"] == []
    assert "NOT EVALUATED" in rec["evaluation"]["verdict"]
    assert "ABSTAINED" in html
    assert any("weekly_stats" in n and "MISSING" in n for n in rec["notes"])
    assert any("schedules" in n and "MISSING" in n for n in rec["notes"])
    # no invented numbers: every projection cell reads "abstain"
    opp = [p for p in rec["matchup"]["opp_starters"] if p is not None]
    assert html.count(">abstain<") == len(rec["roster"]) + len(opp)


# ------------------------------------------------------------------ archive

def test_the_archive_is_the_page_and_grading_it_leaks_nothing(tmp_path):
    rc, html, rec = render(tmp_path, "complete")
    arch_files = list((tmp_path / "arch" / "complete").rglob("week03_*.json"))
    assert len(arch_files) == 1
    from gridiron.decisions import grade_archive, read_archive
    archive = read_archive(arch_files[0])
    assert archive["archive_version"] == 1
    assert archive["roster"] == rec["roster"]
    assert archive["alternatives"] == rec["alternatives"]
    assert archive["evidence_boundary"] == 2 and archive["week"] == 3
    # Grade with invented week-3 actuals: nothing is re-projected.
    actuals = {p["gsis_id"]: 10.0 for p in archive["roster"] if p["gsis_id"]}
    grade = grade_archive(archive, actuals)
    assert grade.n == len(archive["alternatives"]) + len(archive["upgrades"])
    assert grade.projection_n > 0
    # a rejected side with no actual is ungradeable, not zero
    thin = {k: v for k, v in actuals.items() if k != archive["alternatives"][0]["bench_id"]}
    bench_gsis = next(p["gsis_id"] for p in archive["roster"]
                      if p["sleeper_id"] == archive["alternatives"][0]["bench_id"])
    thin.pop(bench_gsis, None)
    assert grade_archive(archive, thin).ungradeable >= 1


def test_no_archive_flag_writes_nothing_to_the_ledger(tmp_path):
    rc, html, rec = render(tmp_path, "complete", "--no-archive")
    assert rc == 0
    assert not (tmp_path / "arch" / "complete").exists()
    assert "Archive not written" in html


# ----------------------------------------------------------- data boundary

def test_anonymous_keeps_the_league_name_off_the_page(tmp_path):
    from gridiron.league_config import LEAGUE_NAME
    _, html, _ = render(tmp_path, "complete", "--anonymous")
    assert LEAGUE_NAME not in html
    _, html2, _ = render(tmp_path / "named", "complete")
    assert LEAGUE_NAME in html2


def test_the_stdout_summary_names_no_player(tmp_path, capsys):
    rc, _, rec = render(tmp_path, "complete")
    out = capsys.readouterr().out
    for p in rec["roster"]:
        assert p["name"] not in out


def test_the_opponent_is_a_roster_number_not_a_display_name(tmp_path):
    _, html, _ = render(tmp_path, "complete")
    assert "roster #2" in html
    assert "rival" not in html and "fixture_owner" not in html


# --------------------------------------------------------------- refusals

def test_a_missing_cache_is_an_instruction_not_a_traceback(tmp_path, capsys):
    assert CLI.main(["--cache-root", str(tmp_path), "--owner", "fixture_owner"]) == 2
    assert "pull_week.py" in capsys.readouterr().err


def test_a_snapshot_from_another_season_is_refused(tmp_path, capsys):
    root = tmp_path / "c"
    SCN.build_scenario(root, "complete")
    from gridiron import ingest as ing
    m = ing.Manifest.load(ing.season_cache(2026, root), 2026)
    path = m.file("sleeper_league")
    blob = json.loads(path.read_text("utf-8"))
    blob["state"]["season"] = 2025
    path.write_text(json.dumps(blob), "utf-8")
    assert CLI.main(["--cache-root", str(root), "--owner", "fixture_owner",
                     "--now", NOW.isoformat()]) == 2
    assert "season 2025" in capsys.readouterr().err
