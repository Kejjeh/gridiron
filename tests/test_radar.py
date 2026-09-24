"""The Free Agent Radar: every projected pool player verdicted, diffed by id,
and shown with its whole comparison.

Pins the milestone of 2026-09-21 (second part): the board writes down its
comparison for EVERY projected available player (LINEUP / RESEARCH /
COVERAGE / BELOW / LOCKED / UNKNOWN / UNRANKED), the archive carries that
block by stable id, and a later page diffs the two blocks like for like —
newly available, now owned, verdict or projection moved, evidence expired —
separating "the inputs are newer" from "a number changed", and refusing to
compare across a week rollover, against a record that predates the radar,
or on a first run. Game Day reports what the board found without re-judging
a pickup. Both pages share one navigation, one build stamp, and a
published-build check that only ever offers a reload.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

from gridiron import radar as R
from gridiron import theme
from gridiron.gameday import summarise_radar
from gridiron.waivers import (BELOW, COVERAGE, LINEUP, LOCKED, RESEARCH, UNKNOWN,
                              UNRANKED, CANDIDATES_PER_POSITION, build_board)

import test_waivers as W

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


SCN = _load("radar_dashboard_scenarios", "scripts/weekly/dashboard_scenarios.py")
CLI = _load("radar_dashboard_cli", "scripts/weekly/dashboard.py")
DRIVE = _load("radar_drive_module", "scripts/weekly/radar_drive.py")
NOW = SCN.NOW


def render(tmp_path: Path, kind: str, *, now=NOW, run: int = 1, archive: Path | None = None):
    root = tmp_path / kind
    SCN.build_scenario(root, kind, now=now, run=run)
    out = tmp_path / "out" / f"{kind}{run}"
    rc = CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                   "--anonymous", "--out-dir", str(out),
                   "--archive-root", str(archive or tmp_path / "arch" / kind),
                   "--now", now.isoformat()])
    assert rc == 0
    html = (out / "dashboard_latest.html").read_text("utf-8")
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    return html, rec, out


# ------------------------------------------------------------ the verdicts

def test_every_projected_pool_player_gets_exactly_one_verdict_and_the_counts_add_up():
    pool = [W.P("fa_rb", "RB", 13), W.P("fa_te", "TE", 5), W.P("fa_qb", "QB", 1),
            W.P("fa_wr_locked", "WR", 20, locked=True), W.P("fa_k", "K", 2, unprojected=True)]
    board = build_board(W.roster(), pool, W.STARTERS, W.SLOTS, locks_known=True)
    by_id = {c.add.sleeper_id: c for c in board.candidates}
    assert set(by_id) == {"fa_rb", "fa_te", "fa_qb", "fa_wr_locked"}   # unprojected: counted, not listed
    assert by_id["fa_rb"].verdict == LINEUP and by_id["fa_rb"].drop is not None
    assert by_id["fa_rb"].lineup_gain == pytest.approx(7) and by_id["fa_rb"].slot == "FLEX"
    assert by_id["fa_te"].verdict == RESEARCH and by_id["fa_te"].versus.sleeper_id == "t2"
    assert by_id["fa_qb"].verdict == BELOW and by_id["fa_qb"].gap < 0
    assert by_id["fa_wr_locked"].verdict == LOCKED and by_id["fa_wr_locked"].drop is None
    # reading order: the move first, then research, then the rest
    assert [c.verdict for c in board.candidates] == [LINEUP, RESEARCH, BELOW, LOCKED]
    cov = {pc.position: pc for pc in board.positions}
    assert cov["K"].pool == 1 and cov["K"].unprojected == 1 and cov["K"].evaluated == 0
    assert cov["WR"].locked == 1 and cov["WR"].evaluated == 0
    assert sum(pc.pool for pc in board.positions) == board.pool_size == 5
    assert sum(pc.evaluated for pc in board.positions) == board.evaluated == 3


def test_a_position_with_no_droppable_comparator_is_coverage_never_a_number():
    r = W.roster()
    r[0] = W.P("q1", "QB", 20, "START", locked=True)          # the only QB, locked
    board = build_board(r, [W.P("backup_qb", "QB", 17)], W.STARTERS, W.SLOTS, locks_known=True)
    c = board.candidates[0]
    assert c.verdict == COVERAGE and c.gap is None and c.versus is None and c.drop is None
    assert "not priced" in c.reason


def test_over_the_cap_is_unranked_and_said_so_not_dropped():
    pool = [W.P(f"fa_wr{i}", "WR", 1.0 + i * 0.01) for i in range(CANDIDATES_PER_POSITION + 3)]
    board = build_board(W.roster(), pool, W.STARTERS, W.SLOTS, locks_known=True)
    verdicts = [c.verdict for c in board.candidates]
    assert verdicts.count(UNRANKED) == 3
    assert board.evaluated == CANDIDATES_PER_POSITION
    assert all(c.add.value <= 1.02 for c in board.candidates if c.verdict == UNRANKED)


def test_unknown_locks_verdict_every_candidate_unknown_and_abstain():
    board = build_board(W.roster(), [W.P("fa_rb", "RB", 13)], W.STARTERS, W.SLOTS,
                        locks_known=False)
    assert board.abstained and [c.verdict for c in board.candidates] == [UNKNOWN]


# ---------------------------------------------------------------- the diff

def _rec(*, week=3, generated="2026-09-26T12:00:00+00:00", pool=(), owned=(), cands=(),
         snapshot="2026-09-26 11:00 UTC", status=None, with_radar=True, league="L1", roster=1):
    rec = {"generated": generated, "season": 2026, "week": week, "league_id": league,
           "my_roster_id": roster}
    if with_radar:
        rec["radar"] = {"version": R.RADAR_VERSION, "snapshot_as_of": snapshot,
                        "evidence": {"sleeper_league": generated}, "status": status or {},
                        "pool": [{"id": i, "name": f"p{i}", "position": "WR"} for i in pool],
                        "owned": list(owned),
                        "candidates": [dict(c) for c in cands]}
    return rec


def _c(i, verdict=BELOW, projected=5.0):
    return {"id": i, "name": f"p{i}", "position": "WR", "verdict": verdict,
            "projected": projected, "reason": "r"}


def test_a_first_run_and_a_week_rollover_are_no_comparison_not_no_change():
    first = R.diff_radar(None, _rec())
    assert not first.comparable and "first run" in first.why and not first.items
    assert first.summary().startswith("No comparison")
    roll = R.diff_radar(_rec(week=3), _rec(week=4))
    assert not roll.comparable and "week rollover" in roll.why and not roll.items
    other = R.diff_radar(_rec(league="L2"), _rec())
    assert not other.comparable and "league_id" in other.why
    old = R.diff_radar(_rec(with_radar=False), _rec())
    assert not old.comparable and "predates the radar" in old.why
    ver = _rec()
    ver["radar"]["version"] = 99
    assert not R.diff_radar(ver, _rec()).comparable


def test_a_refreshed_snapshot_with_the_same_numbers_is_refreshed_not_revised():
    a = _rec(pool=("1", "2"), cands=[_c("1", RESEARCH, 6.0)], snapshot="2026-09-26 11:00 UTC")
    b = _rec(pool=("1", "2"), cands=[_c("1", RESEARCH, 6.0)], snapshot="2026-09-26 13:00 UTC",
             generated="2026-09-26T14:00:00+00:00")
    ch = R.diff_radar(a, b)
    assert ch.comparable and not ch.items and not ch.revised
    assert ch.refreshed and "league snapshot" in ch.refreshed[0]
    assert ch.summary().startswith("Refreshed, not revised")
    same = R.diff_radar(a, dict(a, generated="2026-09-26T14:00:00+00:00"))
    assert same.comparable and not same.items and not same.refreshed
    assert same.summary().startswith("Unchanged")


def test_pool_membership_verdicts_projections_and_evidence_are_diffed_by_id():
    a = _rec(pool=("1", "2", "3", "5"), owned=("9",),
             cands=[_c("1", RESEARCH, 6.0), _c("2", BELOW, 3.0), _c("5", LINEUP, 20.0)],
             status={"injuries": "fresh"})
    b = _rec(pool=("1", "2", "4"), owned=("9", "3"),
             cands=[_c("1", LINEUP, 6.4), _c("2", BELOW, 5.0)],
             status={"injuries": "stale"}, generated="2026-09-26T14:00:00+00:00")
    ch = R.diff_radar(a, b)
    kinds = {c.kind: c for c in ch.items}
    assert "p4" in kinds["available"].detail
    assert "p3" in kinds["owned"].detail and "no longer suggested" in kinds["owned"].detail
    assert "p5" in kinds["gone"].detail and "p3" not in kinds["gone"].detail
    verd = [c for c in ch.items if c.kind == "verdict"]
    assert any(c.player_id == "1" and "RESEARCH → LINEUP" in c.detail for c in verd)
    proj = [c for c in ch.items if c.kind == "projection"]
    assert any(c.player_id == "2" and "+2.00" in c.detail for c in proj)
    assert not any(c.player_id == "1" for c in proj)      # 0.4 is under the noise threshold
    ev = [c for c in ch.items if c.kind == "evidence"]
    assert ev and "expired" in ev[0].detail
    assert ch.revised and "revision" in ch.summary()


def test_a_lineup_candidate_that_vanished_while_still_available_is_named():
    a = _rec(pool=("5",), cands=[_c("5", LINEUP, 20.0)])
    b = _rec(pool=("5",), cands=[], generated="2026-09-26T14:00:00+00:00")
    ch = R.diff_radar(a, b)
    assert any(c.kind == "verdict" and c.player_id == "5" and "not listed" in c.detail
               for c in ch.items)


# ---------------------------------------------------------- the two pages

def test_the_archive_carries_the_radar_block_by_id_and_the_page_shows_it(tmp_path):
    html, rec, _ = render(tmp_path, "complete")
    block = rec["radar"]
    assert block["version"] == R.RADAR_VERSION
    assert block["counts"]["pool"] == rec["radar"]["counts"]["pool"] == 3
    ids = {c["id"] for c in block["candidates"]}
    assert ids == {p["id"] for p in block["pool"]}       # every pool player projected here
    roster_ids = {p["sleeper_id"] for p in rec["roster"]}
    assert not (ids & roster_ids) and not (ids & set(block["owned"]))
    lineup = [c for c in block["candidates"] if c["verdict"] == LINEUP]
    assert lineup and all(c["drop"]["id"] in roster_ids and c["lineup_gain"] > 0 for c in lineup)
    assert all(a["drop"]["id"] in roster_ids for c in lineup for a in c["alternatives"])
    # the page: section 5 is the radar, rows carry their data, no depth column
    assert 'id="free-agents"' in html and 'id="radar-list"' in html
    assert html.count('class="rrow"') == len(block["candidates"])
    assert "Δ depth" not in html and "DEPTH" not in html
    assert 'data-verdict="LINEUP"' in html and "Coverage after" in html
    assert "Availability is UNVERIFIED for every row" in html
    # the radar's changes block says first run, and the top line says so too
    assert rec["radar_changes"]["comparable"] is False
    assert "no earlier record" in rec["radar_changes"]["why"]
    assert "What changed:" in html and "No comparison" in html


def test_an_unchanged_rerender_raises_no_changed_badge(tmp_path):
    """Same cache, two hours later: refreshed nothing, revised nothing —
    the radar says Unchanged rather than fabricating movement."""
    root = tmp_path / "complete"
    SCN.build_scenario(root, "complete", now=NOW)
    arch = tmp_path / "arch"
    common = ["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--archive-root", str(arch)]
    assert CLI.main([*common, "--out-dir", str(tmp_path / "o1"), "--now", NOW.isoformat()]) == 0
    later = NOW + timedelta(hours=2)
    assert CLI.main([*common, "--out-dir", str(tmp_path / "o2"), "--now", later.isoformat()]) == 0
    rec = json.loads((tmp_path / "o2" / "dashboard_latest.json").read_text("utf-8"))
    ch = rec["radar_changes"]
    assert ch["comparable"] and ch["items"] == [] and ch["refreshed"] == []
    assert ch["revised"] is False and ch["summary"].startswith("Unchanged")


@pytest.mark.parametrize("kind", ["taken", "roster_changed", "next_week", "hold", "sparse"])
def test_the_scenarios_say_what_they_are_for(tmp_path, kind):
    rc = SCN.render(kind, tmp_path, take_screenshot=False, now=SCN.SCENARIO_NOW.get(kind, NOW))
    assert rc == 0
    rec = json.loads((tmp_path / kind / "dashboard_latest.json").read_text("utf-8"))
    html = (tmp_path / kind / "dashboard_latest.html").read_text("utf-8")
    ch = rec["radar_changes"]
    if kind == "taken":
        kinds = {c["kind"]: c for c in ch["items"]}
        assert ch["comparable"] and "Jalen Coker" in kinds["available"]["detail"]
        assert "Caleb Williams" in kinds["owned"]["detail"]
        assert "11560" not in {c["id"] for c in rec["radar"]["candidates"]}   # never suggested
        assert ch["refreshed"] and ch["summary"].startswith("Refreshed and revised")
    elif kind == "roster_changed":
        assert any("GONE" in line for line in rec["changes"]["items"])
        assert ch["comparable"] and any(c["kind"] == "available" for c in ch["items"])
    elif kind == "next_week":
        assert rec["week"] == 4 and not ch["comparable"] and "week rollover" in ch["why"]
        assert ch["items"] == []
    elif kind == "hold":
        assert rec["actionable"] == 0 and rec["conditional"] == 0 and not rec["degraded"]
        assert "Hold — no supported change" in html
        assert rec["radar"]["counts"]["pool"] == 0 and rec["radar"]["candidates"] == []
    elif kind == "sparse":
        c = rec["radar"]["counts"]
        assert c["unprojected"] >= 2 and c["listed"] == c["projected"]
        assert all(x["projected"] is not None for x in rec["radar"]["candidates"])


def test_both_pages_share_the_nav_the_build_stamp_and_a_self_only_check(tmp_path):
    html, rec, out = render(tmp_path, "complete")
    for key, label, href in theme.NAV:
        assert f'href="{href}"' in html and f">{label}</a>" in html
    assert 'aria-current="page">Board</a>' in html
    assert f'name="{theme.BUILD_META}" content="{rec["generated"]}"' in html
    assert "connect-src 'self';" in html and "default-src 'none'" in html
    assert 'id="snap-status"' in html and 'id="snap-reload"' in html
    assert "no guarantee" in html.lower() or "no guarantee of timing" in html
    # no CDN, no font download, no telemetry: the only external string is none
    assert "https://" not in html.split("<body")[0].replace("http-equiv", "")
    gd = _load("radar_gd_cli", "scripts/weekly/gameday.py")
    root = tmp_path / "complete"
    assert gd.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                    "--anonymous", "--out-dir", str(out),
                    "--archive-root", str(tmp_path / "arch" / "complete"),
                    "--now", NOW.isoformat()]) == 0
    page = (out / "gameday_latest.html").read_text("utf-8")
    assert 'aria-current="page">Game Day</a>' in page
    assert f'name="{theme.BUILD_META}"' in page and "connect-src 'self' " in page
    assert "Free agents — what the pregame board found" in page
    assert "improved that week" in page and "not a game-day move" in page


def test_game_day_reads_the_radar_block_as_data_and_says_when_it_is_absent():
    s = summarise_radar({"counts": {"pool": 3, "projected": 3, "evaluated": 3, "unprojected": 0},
                         "snapshot_as_of": "x", "candidates": [
                             {"verdict": "LINEUP", "id": "1", "name": "A", "position": "QB",
                              "lineup_gain": 6.1, "slot": "QB", "drop": {"id": "9", "name": "B"}},
                             {"verdict": "BELOW", "id": "2", "name": "C"}]})
    assert s["pool"] == 3 and len(s["moves"]) == 1 and s["moves"][0]["drop"] == "B"
    assert summarise_radar(None) == {} and summarise_radar("junk") == {}


# ---------------------------------------------------------- in a browser

@pytest.mark.skipif(DRIVE.chrome_binary() is None, reason="no headless Chromium on this machine")
def test_the_radar_and_the_published_build_check_hold_up_in_a_real_browser(tmp_path):
    """Executed only where a browser exists. The fixture plays the hosting
    server through an unchanged build, a 429, a dropped socket, a hang, a
    recovery, an in-flight pair, a newer build, pause/resume and a reload;
    the radar's controls are driven for real. See scripts/weekly/radar_drive.py."""
    _, _, out = render(tmp_path, "complete")
    result = DRIVE.drive_radar(out / "dashboard_latest.html")
    assert result["executed"], result
    assert DRIVE.verdict(result) == [], DRIVE.verdict(result)
