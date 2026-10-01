"""Game Day end to end, through the production CLIs, with the network unplugged.

Drives `scripts/weekly/dashboard.py` (the pregame board, which writes the
decision-time archive) and then `scripts/weekly/gameday.py` against the
synthetic scenarios in `scripts/weekly/gameday_scenarios.py`. The acceptance
matrix in HANDOFF.md points at these by name.

  A. pregame / pregame_stale / conflict — one legal improvement with its
     deadline and backup; withheld when the board withheld; not offered when
     a kickoff cannot be established; the score displays either way.
  B. mixed / custom / empty — ahead 15.46 with the opponent's RB, K and DST
     yet to play; zero, negative, kicker, unknown value, override, empty slot.
  D. injury_after — a designation moved after the record; a game missing
     from the feed, a status word the page does not know, a suspended game,
     a game still in progress four hours after kickoff.
  E. rollover / no_archive / stale — week boundary respected; archive
     untouched; absence stated; a stale league snapshot shows its score and
     withholds its advice.
  F. the browser drives (when a headless Chromium is present), in real time:
     mixed — a correction, a 429, a recovery, an older response, a malformed
     body, an unreadable NFL state, a failed feed, a hung request past the
     timeout, a partial payload, a duplicate roster, a rollover;
     pregame — a legal move goes live, then Out, then ineligible, then the
     sources age, a refresh renews the roster but not the designations, and
     the deadline passes idle: withheld every time, the score visible.
"""
from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

from gridiron.theme import page_copy

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


SCN = _load("weekly_gameday_scenarios", "scripts/weekly/gameday_scenarios.py")
CLI = _load("weekly_gameday_cli", "scripts/weekly/gameday.py")


@pytest.fixture
def no_network(monkeypatch):
    import socket

    def boom(*a, **k):
        raise AssertionError("the game day page opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    return True


def run(tmp_path: Path, kind: str, **kw) -> tuple[dict, str, Path]:
    s = SCN.render(kind, tmp_path / "out", **kw)
    assert s.get("board_rc", 0) == 0 and s["gameday_rc"] == 0, s
    html = (tmp_path / "out" / kind / "gameday_latest.html").read_text("utf-8")
    return s["record"], html, tmp_path / "out" / kind


def starters(rec: dict, side: str = "mine") -> dict[str, dict]:
    return {s["slot"]: s for s in rec["score"][side]["starters"]}


# ------------------------------------------------------------------ offline

def test_the_page_renders_from_the_cache_with_no_network(tmp_path, no_network):
    rec, html, _ = run(tmp_path, "mixed")
    assert rec["week"] == 3 and "Game Day" in html


def test_every_source_the_page_reads_declares_its_freshness(tmp_path):
    """Derived from the script's own syntax, not from its SOURCES tuple."""
    tree = ast.parse((ROOT / "scripts/weekly/gameday.py").read_text("utf-8"))
    consumed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr in ("read_frame", "read_json", "file") and node.args \
                and isinstance(node.args[0], ast.Constant):
            consumed.add(str(node.args[0].value))
    names = set(CLI.SOURCES) | {"game_status"}       # GAME_STATUS_NAME is read by constant
    assert consumed <= names, consumed - names
    assert {"sleeper_league", "sleeper_players", "schedules"} <= consumed
    rec, html, _ = run(tmp_path, "mixed")
    declared = {line.split()[0] for line in rec["sources"]}
    assert set(CLI.SOURCES) == declared
    for name in CLI.SOURCES:
        assert name in html


# ---------------------------------------------------------------- A: pregame

def test_pregame_offers_one_legal_improvement_with_deadline_and_backup(tmp_path):
    rec, html, _ = run(tmp_path, "pregame")
    live = [a for a in rec["actions"] if a["available"]]
    assert len(live) == 1 and live[0]["kind"] == "swap"
    assert live[0]["deadline_note"].startswith("act before") and live[0]["backup"]
    assert "AVAILABLE" in html and "Backup —" in html
    assert rec["score"]["lead"] == "level"
    assert all(s["state"] == "NOT STARTED" for s in rec["score"]["mine"]["starters"])


def test_a_board_that_withheld_its_actions_offers_none_while_the_score_still_shows(tmp_path):
    rec, html, _ = run(tmp_path, "pregame_stale")
    assert rec["available_actions"] == 0 and rec["actions"]
    assert all("WITHHELD" in a["why"] for a in rec["actions"] if a["kind"] != "acquire")
    assert any(a["kind"] != "acquire" for a in rec["actions"])
    assert rec["score"]["mine"]["platform_points"] == 0.0
    assert any(line.startswith("sleeper_league") and "FRESH" in line for line in rec["sources"])
    assert "None of the pregame board" in html


def test_a_conflicting_kickoff_means_the_move_cannot_be_shown_legal(tmp_path):
    rec, _, _ = run(tmp_path, "conflict")
    assert rec["available_actions"] == 0
    assert any("kickoff schedule DAMAGED" in n for n in rec["notes"])


# ------------------------------------------------------------------ B: mixed

def test_mixed_slate_is_ahead_15_46_with_their_rb_k_dst_yet_to_play(tmp_path):
    rec, html, _ = run(tmp_path, "mixed")
    sc = rec["score"]
    assert sc["mine"]["platform_points"] == 62.10 and sc["opp"]["platform_points"] == 46.64
    assert sc["margin"] == 15.46 and sc["lead"] == "ahead by 15.46"
    assert sc["opp"]["exposure"] == "3 yet to play (RB, K, DST); 3 playing (WR, WR, TE); 4 final"
    assert sc["mine"]["exposure"] == "2 yet to play (TE, RB); 1 playing (DST); 7 final"
    assert sc["settled"].startswith("not settled") and "they still have 6" in sc["settled"]
    for pos in ("RB", "K", "DST"):
        assert pos in sc["settled"]
    me = starters(rec)
    assert me["RB"]["points"] == 0.0 and me["RB"]["state"] == "FINAL"       # a real zero
    assert me["K"]["points"] == 7.0                                           # a kicker actual
    assert me["DST"]["points"] is None and "UNKNOWN, not 0" in me["DST"]["points_note"]
    assert "cannot be reconciled" in sc["mine"]["reconciliation"]
    assert "safe" not in page_copy(html).lower()
    score_card = html.split('id="gd-score"')[1].split("</div>\n")[0]
    assert "%" not in score_card and "P(win)" not in score_card


def test_a_negative_actual_and_an_override_are_shown_as_sent(tmp_path):
    rec2, _, _ = run(tmp_path, "custom")
    assert starters(rec2)["DST"]["points"] == -1.0
    # the mixed lineup carries the -1.00 DST in the platform total
    assert rec2["score"]["mine"]["platform_points"] == 62.10
    assert rec2["score"]["opp"]["platform_points"] == 50.0
    assert "OVERRIDE" in rec2["score"]["opp"]["reconciliation"]


def test_an_empty_slot_is_named_and_scores_nothing(tmp_path):
    rec, html, _ = run(tmp_path, "empty")
    me = starters(rec)
    assert me["FLEX"]["empty"] or any(s["empty"] for s in rec["score"]["mine"]["starters"])
    assert "1 empty slot" in rec["score"]["mine"]["exposure"]
    assert rec["capacity"]["empty_slots"] == 1


# ------------------------------------------------------------ D: game states

def test_injury_after_kickoff_unknown_status_postponed_and_overtime(tmp_path):
    rec, html, _ = run(tmp_path, "injury_after")
    me = starters(rec)
    # MIN@CHI missing from the feed: UNKNOWN, not final, not bye
    assert me["QB"]["state"] == "UNKNOWN" and "missing row is not a bye" in me["QB"]["game_note"]
    # GB@NYJ carries a status word the page does not know
    assert me["WR"]["state"] == "UNKNOWN" or starters(rec)["WR"]["game_raw"] == "postponed"
    wr_rows = [s for s in rec["score"]["mine"]["starters"] if s["team"] == "GB"]
    assert me["DST"]["points"] == 4.0
    assert wr_rows and wr_rows[0]["game_raw"] == "postponed" and wr_rows[0]["state"] == "UNKNOWN"
    # SEA@ARI still in progress four hours after kickoff: PLAYING, never final
    assert me["DST"]["state"] == "PLAYING"
    # a suspended game on the bench is SUSPENDED, not final
    bench = {b["team"]: b for b in rec["score"]["mine"]["bench"]}
    assert bench["TB"]["state"] == "SUSPENDED"
    # the designation moved after the record: reported, without blame
    changes = rec["pregame"]["designation_changes"]
    assert len(changes) == 1 and "Out" in changes[0] and "cannot show what was knowable" in changes[0]
    assert "should have" not in html
    assert rec["score"]["settled"].startswith("not settled: at least one")


# ---------------------------------------------------------------- E: weeks

def test_week_rollover_joins_no_record_and_diffs_only_its_own_week(tmp_path):
    s = SCN.render("rollover", tmp_path / "out")
    first, second = s["record"], s["second"]
    assert first["week"] == 4 and not first["pregame"]["path"]
    assert first["pregame"]["note"].startswith("no pregame record for week 4")
    assert first["changes"]["items"] == []            # the week-3 record is not compared
    assert [c[0] for c in second["changes"]["items"]] == ["score", "lineup"]
    assert second["changes"]["previous"] == first["generated"]


def test_the_archived_pregame_record_is_byte_identical_after_the_page_is_built(tmp_path):
    out = tmp_path / "out"
    SCN.render("mixed", out)
    archive = out / "mixed" / "archive" / "season2026"
    files = sorted(archive.glob("*.json"))
    assert files
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    root = out / "caches" / "mixed"
    for _ in range(2):
        assert CLI.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                         "--anonymous", "--out-dir", str(out / "mixed"), "--archive-root",
                         str(out / "mixed" / "archive"),
                         "--now", SCN.SUNDAY_LATE.isoformat()]) == 0
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(archive.glob("*.json"))}
    assert after == before


def test_no_archive_is_a_clear_absence(tmp_path):
    rec, html, _ = run(tmp_path, "no_archive")
    assert not rec["pregame"]["path"] and rec["actions"] == []
    assert "no pregame record for week 3" in html and "no advice to re-check" in html


def test_a_stale_league_snapshot_shows_its_score_and_withholds_its_advice(tmp_path):
    rec, html, _ = run(tmp_path, "stale")
    assert rec["degraded"] and any("STALE" in n and "last one seen" in n for n in rec["notes"])
    assert rec["score"]["mine"]["platform_points"] == 0.0 and rec["score"]["lead"] == "level"
    assert rec["available_actions"] == 0
    swaps = [a for a in rec["actions"] if a["kind"] == "swap"]
    assert swaps and all("sleeper_league is STALE" in a["why"] for a in swaps)
    assert all("score above stands on its own" in a["why"] for a in swaps)
    assert "Read first" in html and "NOT NOW" in html


# ---------------------------------------------------------------- privacy

def test_the_stdout_summary_names_no_player(tmp_path, capsys):
    run(tmp_path, "mixed")
    out = capsys.readouterr().out
    players = json.loads((ROOT / "tests/fixtures/sleeper_players_small.json").read_text("utf-8"))
    for rec in players.values():
        name = rec.get("full_name")
        assert not name or name not in out
    assert "Synthetic" not in out


def test_the_page_names_the_opponent_by_roster_number_only(tmp_path):
    _, html, _ = run(tmp_path, "mixed")
    assert "Roster #2" in html and "rival" not in html


def test_the_page_writer_only_writes_under_data_outputs():
    source = (ROOT / "scripts/weekly/gameday.py").read_text("utf-8")
    assert "OUTPUTS /" in source
    for escape in ("..", "os.path.expanduser", "Path.home()", "/tmp"):
        assert escape not in source


# ------------------------------------------------------------ F: the browser

@pytest.mark.skipif(SCN.chrome_binary() is None, reason="no headless Chromium on this machine")
def test_refresh_applies_corrections_keeps_last_good_and_rejects_bad_payloads(tmp_path):
    """The page's own script against a local fixture server, in real time.
    Not fixture-only evidence of the network path (see HANDOFF for the
    real-endpoint run); it is the evidence for what the page DOES with each
    kind of response."""
    s = SCN.render("mixed", tmp_path / "out", drive=True)
    b = s["browser"]
    assert b["executed"] and b["real_time"], b
    steps = {x["label"]: x for x in b["steps"]}
    assert "ERROR" not in steps, steps.get("ERROR")
    (init, corr, limited, rec, older, bad, nostate, nofeed, hung, partial, dup, roll) = (
        steps[k] for k in ("initial", "correction", "429", "recovery", "older", "malformed",
                           "state_fail", "feed_fail", "timeout", "partial", "duplicate",
                           "rollover"))
    assert init["mode"] == "SNAPSHOT" and init["mine"] == "62.10" and init["opp"] == "46.64"
    # a correction: the opponent total went DOWN and is named a correction
    assert corr["mode"] == "LIVE" and corr["opp"] == "44.10"
    assert any("CORRECTION" in t and "Roster #2" in t for t in corr["changeText"])
    assert "not refreshed by this page" in corr["status"]       # designations are not
    # rate limited: last good kept, page says STALE, backoff scheduled
    assert limited["result"] == "failed" and limited["mode"] == "STALE"
    assert (limited["mine"], limited["opp"]) == (corr["mine"], corr["opp"])
    assert "429" in limited["status"] and limited["nextPoll"] == 60000
    # recovery
    assert rec["mode"] == "LIVE" and rec["opp"] == "45.00" and rec["failures"] == 0
    # a response dated before the one applied never replaces it
    assert older["result"] == "failed" and (older["mine"], older["opp"]) == (rec["mine"], rec["opp"])
    assert "older" in older["status"]
    # malformed body: last good kept
    assert bad["result"] == "failed" and (bad["mine"], bad["opp"]) == (rec["mine"], rec["opp"])
    # NFL state unreadable: the scores still apply, the week is said not re-confirmed
    assert nostate["result"] == "ok" and "not re-confirmed" in nostate["status"]
    # the feed fails: scores apply, the last good feed is kept and dated
    assert nofeed["result"] == "ok" and "feed not refreshed" in nofeed["status"]
    assert "HTTP 503" in nofeed["status"] and set(nofeed["states"]) == set(rec["states"])
    # a request that hangs: the page's own timeout fires, last good kept, the button recovers
    assert hung["result"] == "failed" and "timeout after 1500 ms" in hung["status"]
    assert (hung["mine"], hung["opp"]) == (rec["mine"], rec["opp"])
    assert hung["buttonDisabled"] is False and hung["inflight"] is False
    # [{roster_id: 1}] is not a matchup row: rejected, nothing replaced
    assert partial["result"] == "failed" and "matchup_id" in partial["status"]
    assert (partial["mine"], partial["opp"]) == (rec["mine"], rec["opp"])
    assert partial["states"] == rec["states"]
    # a duplicate roster in the payload: rejected
    assert dup["result"] == "failed" and "appears twice" in dup["status"]
    assert (dup["mine"], dup["opp"]) == (rec["mine"], rec["opp"])
    # the platform moved to week 4: scores stand, polling stops
    assert roll["rollover"] is True and roll["nextPoll"] is None and "week 4" in roll["status"]
    # never any live odds on the score or action cards
    assert not any(x.get("oddsOnScore") for x in b["steps"] if "oddsOnScore" in x)
    # three read-only requests per refresh, nothing else, no bulk player pull
    assert len(b["hits"]) == 11 * 3
    assert all(re.search(r"/state/nfl$|/matchups/3$|/nfl/regular/2026$", h) for h in b["hits"])
    kb = steps["keyboard"]
    assert kb["activeIsRefresh"] and kb["tag"] == "BUTTON" and kb["outline"] == "solid"


@pytest.mark.skipif(SCN.chrome_binary() is None, reason="no headless Chromium on this machine")
def test_the_legality_journey_withholds_at_every_gate_while_the_score_stays_visible(tmp_path):
    """Fresh legal action -> Out -> ineligible -> restored -> sources aged
    past their gameday cadence with NO fetch -> a refresh that renews the
    roster but not the designations -> the deadline passes while the page
    sits idle. The score line never disappears; no lineup is submitted."""
    s = SCN.render("pregame", tmp_path / "out", drive=True)
    b = s["browser"]
    assert b["executed"], b
    steps = {x["label"]: x for x in b["steps"]}
    assert "ERROR" not in steps, steps.get("ERROR")
    init, live, out, inel, back, aged, sunday, idle = (steps[k] for k in (
        "initial", "live", "out", "ineligible", "restored", "aged", "refresh_sunday",
        "cross_kickoff_idle"))
    assert init["available"] == 1 and live["available"] == 1 and live["mode"] == "LIVE"
    assert out["available"] == 0 and any("designation is Out" in t for t in out["actionText"])
    assert inel["available"] == 0 and any("not eligible for the TE slot" in t for t in inel["actionText"])
    assert back["available"] == 1
    # the sources aged past the gameday cadence: no fetch happened, the flags moved anyway
    assert aged["available"] == 0 and aged["blockers"]
    assert any("sleeper_players is STALE" in x for x in aged["blockers"])
    assert any("sleeper_league is STALE" in x for x in aged["blockers"])
    assert set(aged["states"]) == {"UNKNOWN"}                 # the feed aged too
    # a refresh renews the roster and scores; the designations stay from the build
    assert sunday["result"] == "ok" and sunday["available"] == 0
    assert any("sleeper_players is STALE" in t and "not renewed by a browser refresh" in t
               for t in sunday["actionText"])
    assert not any("sleeper_league is STALE" in t for t in sunday["actionText"])
    assert "not refreshed by this page" in sunday["status"]
    # the deadline passed while idle: no successful refresh, no actionable old move
    assert idle["available"] == 0 and any("deadline passed" in t for t in idle["actionText"])
    # the score stayed on the page at every step
    for step in (init, live, out, inel, back, aged, sunday, idle):
        assert step["mine"] == "0.00" and step["opp"] == "0.00" and step["lead"] == "level"
    # exactly two refreshes hit the fixture, three read-only requests each
    assert len(b["hits"]) == 2 * 3
