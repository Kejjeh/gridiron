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
    # action-first: what to do comes before the evidence behind it
    assert "1. This week — what to do" in html
    assert "9. Decision-time archive" in html
    assert html.index("1. This week") < html.index("2. Inputs,") < html.index("6. Roster")


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
    block = html.split("2. Inputs, and what they are good enough for")[1] \
        .split("3. Since the last snapshot")[0]
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
    assert "Acquisitions — shortlist, with what each one costs" in html
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


def test_stale_inputs_withhold_every_action_but_keep_the_comparison(tmp_path):
    """Regression, source review at f82623e: that commit rendered this exact
    scenario — 120h-old league, player dump, injuries and box scores, with
    `degraded=True` — and still emitted TWO lineup alternatives and THREE
    waiver upgrades, with `lineup_abstained` empty and nothing marking any of
    it as unsupported. A DEGRADED banner over live-looking advice is not a
    gate; it is a disclaimer the reader learns to scroll past.

    The fix withholds the ACTIONS and keeps the EVIDENCE: old information is
    still information as long as it is labelled as old, and deleting it would
    leave the owner with nothing at all on a bad cache.
    """
    rc, html, rec = render(tmp_path, "stale")
    assert rc == 0 and rec["degraded"]

    # the actions are all withheld: not one of them is endorsed
    assert rec["actionable"] == 0, "a stale cache endorsed an action"
    assert rec["actions"], "withholding must not mean silence"
    assert all(a["status"] == "WITHHELD" for a in rec["actions"])
    assert set(rec["withheld_actions"]) >= {"lineup", "waiver"}

    # ...and each one says which input is stale and what to check
    for a in rec["actions"]:
        assert a["withheld_reasons"] and a["verify"]
        assert "STALE" in " ".join(a["withheld_reasons"])

    # the gate record is explicit enough for a later grader to tell an action
    # this page ENDORSED from one it merely displayed
    assert rec["gate"]["lineup"]["allowed"] is False
    assert rec["gate"]["waiver"]["allowed"] is False
    assert any(b["source"] == "sleeper_league" for b in rec["gate"]["lineup"]["blockers"])

    # the comparisons behind them survive, exactly as before the fix
    assert len(rec["alternatives"]) == 2
    assert len(rec["upgrades"]) == 3
    assert rec["current_points"] > 0 and rec["best_points"] >= rec["current_points"]

    # and the page says which of the two it is showing
    assert "Not advice right now." in html
    assert "last known picture" in html
    assert "No action is endorsed on this data." in html


def test_a_fresh_cache_still_endorses_its_actions(tmp_path):
    """The gate has to be able to open, or it is just a broken page."""
    _, html, rec = render(tmp_path, "complete")
    assert rec["withheld_actions"] == []
    assert rec["actionable"] == len(rec["actions"]) > 0
    assert all(a["status"] == "ACTIONABLE" for a in rec["actions"])
    assert "No action is endorsed" not in html


def test_the_page_leads_with_actions_and_demotes_uncalibrated_win_probability(tmp_path):
    _, html, rec = render(tmp_path, "complete")
    assert html.index("1. This week — what to do") < html.index("7. Matchup")
    # P(win) is present, labelled, and behind a disclosure rather than in the
    # headline: rule #7 denominates decisions in DP(win), and this baseline has
    # never been calibrated, so it ranks nothing.
    assert "UNCALIBRATED" in html
    assert "not used to rank any action above" in html
    for a in rec["actions"]:
        assert "P(win)" not in a["headline"]
    # the deadline on a lineup action is a real kickoff, not a placeholder
    lineup_actions = [a for a in rec["actions"] if a["kind"] in ("swap", "inactive_starter",
                                                                "empty_slot")]
    assert lineup_actions
    assert all(a["deadline_note"] for a in lineup_actions)
    assert any(a["backup"] for a in lineup_actions)


def test_protected_players_are_named_on_the_page_not_silently_dropped(tmp_path):
    _, html, rec = render(tmp_path, "complete")
    assert rec["protected_from_drop"], "the fixture roster holds an IR player and a DST"
    assert "Protected from the drop list" in html
    for row in rec["protected_from_drop"]:
        assert row["reason"]
    # eligibility is never asserted from the cache
    assert "UNVERIFIED" in html and "transactions feed" in html
    assert "Add now" not in html


def test_a_second_render_reports_what_changed_since_the_first(tmp_path):
    """The archive diff reads two frozen pages and recomputes nothing."""
    from datetime import timedelta
    root = tmp_path / "complete"
    SCN.build_scenario(root, "complete", now=NOW)
    arch = tmp_path / "arch"
    common = ["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--archive-root", str(arch)]
    CLI.main([*common, "--out-dir", str(tmp_path / "o1"), "--now", NOW.isoformat()])
    later = NOW + timedelta(hours=2)
    CLI.main([*common, "--out-dir", str(tmp_path / "o2"), "--now", later.isoformat()])
    rec = json.loads((tmp_path / "o2" / "dashboard_latest.json").read_text("utf-8"))
    assert rec["changes"] is not None
    assert rec["changes"]["previous"].startswith("2026-09-26")
    # nothing about the cache changed between the two renders, so the diff is
    # empty and says so rather than inventing news
    assert rec["changes"]["items"] == []
    assert "nothing material changed" in rec["changes"]["note"]


# -------------------------------------------------------- partial schedule

def test_a_half_readable_schedule_freezes_by_name_and_invents_nothing(tmp_path):
    """End-to-end for the first three source-review findings at once.

    The fixture damages two week-3 rows: one loses its `gametime`, one its
    `gameday`. At f82623e the first became a 13:00 ET kickoff out of thin air
    and the second vanished, so all four teams read as unlocked or on a bye and
    every swap involving them was offered as legal.
    """
    rc, html, rec = render(tmp_path, "partial_schedule")
    assert rc == 0 and rec["degraded"]
    locks = rec["locks"]
    assert locks["rows_intact"] is False
    assert len(locks["time_unknown"]) == 4, "both damaged games' teams are named"
    assert locks["timed_teams"], "the games that DID parse are still usable"
    assert not set(locks["time_unknown"]) & set(locks["timed_teams"])

    # no invented kickoff, and no missing team silently read as a bye
    assert any("no kickoff time" in p for p in locks["problems"])
    # the unreadable value is echoed VERBATIM rather than substituted, which is
    # how a reader can tell the parser refused it instead of repairing it
    assert any("'not-a-date 13:00'" in p for p in locks["problems"])
    # and neither damaged game contributed a kickoff to the timed set
    assert len(locks["timed_teams"]) == 28

    # the schedule is FRESH on disk and still gates the actions, because a
    # file's timestamp says nothing about whether its contents parse
    sched = next(s for s in rec["sources"] if s.startswith("schedules"))
    assert "FRESH" in sched
    assert rec["gate"]["lineup"]["allowed"] is False
    assert "not fully readable" in rec["gate"]["lineup"]["blockers"][0]["reason"]
    assert rec["actionable"] == 0

    # per-player: an unknown-lock player is frozen by name, never moved
    unknown = [p for p in rec["roster"] if not p["lock_known"]]
    assert unknown, "the damaged games must reach at least one roster player"
    frozen = {f["sleeper_id"] for f in rec["frozen"]}
    for p in unknown:
        assert p["sleeper_id"] in frozen or p["lineup"] == "IR"
        assert "UNKNOWN" in p["lock_note"]
    # ...and none of them is proposed in a swap
    unknown_ids = {p["sleeper_id"] for p in unknown}
    for a in rec["alternatives"]:
        assert a["bench_id"] not in unknown_ids
        assert a["starter_id"] not in unknown_ids

    assert "DAMAGED" in html and "no time was invented" in html


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


# ------------------------------------------- withheld cards lose the imperative

def test_a_withheld_card_states_the_last_known_picture_not_an_instruction(tmp_path):
    """Second review pass: the badge said WITHHELD and the sentence under it
    still said "Consider claiming X" and "start X over Y". A reader who skims
    the sentence has been told to act on a five-day-old roster. The badge is a
    label; the sentence is the instruction, and the instruction had to go."""
    _, html, rec = render(tmp_path, "stale")
    assert rec["actionable"] == 0 and rec["actions"]

    for action in rec["actions"]:
        assert action["status"] == "WITHHELD"
        title, body = action["title"], action["body"]
        for imperative in ("Consider claiming", "Fill it with", "Start ",
                           "start ", ": start"):
            assert imperative not in title, (imperative, title)
        assert not title.startswith(("Add ", "Drop ", "Claim ")), title
        # it says WHEN the picture is from instead
        assert "last snapshot" in title.lower() or "was " in title.lower(), title
        assert body

    # and the page itself carries the neutral wording, not the imperative
    assert "the last snapshot ranked" in html.lower()
    assert "Consider claiming" not in html
    assert "Last known picture — no action is being recommended." in html


def test_a_fresh_page_still_gives_the_instruction(tmp_path):
    """The neutral wording is for withheld cards only. Softening endorsed
    advice into 'the snapshot ranked X above Y' would be its own failure."""
    _, html, rec = render(tmp_path, "complete")
    assert rec["actionable"] == len(rec["actions"]) > 0
    assert any(a["title"].startswith("Consider claiming") for a in rec["actions"])
    assert "Consider claiming" in html
    assert "Last known picture" not in html
    for action in rec["actions"]:
        assert action["title"] == action["headline"]


def test_the_edge_is_never_called_larger_than_the_uncertainty_it_is_smaller_than(
        tmp_path):
    """z is the edge in multiples of the combined SD, so z=0.7 means the edge
    is 0.7x that uncertainty — smaller than it. Every swap above the 0.5 noise
    floor used to claim the opposite, in the most confident sentence on the
    page. The floor is unchanged; the claim is."""
    from gridiron.dashboard import _edge_sentence

    assert "inside the noise" in _edge_sentence(0.4, 0.05)
    for z in (0.5, 0.7, 0.99):
        text = _edge_sentence(5.0, z)
        assert "SMALLER than that uncertainty" in text, z
        assert "larger than" not in text, z
    for z in (1.0, 2.4):
        text = _edge_sentence(20.0, z)
        assert "larger than that uncertainty" in text, z
    assert "unknown" in _edge_sentence(3.0, None)

    _, html, _ = render(tmp_path, "complete")
    assert "The edge is larger than the combined uncertainty" not in html


# ------------------------------------------- grading knows what it was shown

def test_grading_a_withheld_page_scores_no_advice_and_claims_no_decision(tmp_path):
    """The stale page shows two lineup comparisons and three waiver pairs and
    endorses none of them. Grading them as recommendations grades advice that
    was explicitly not given, and calling the roster's contents a 'choice'
    claims the owner did something nobody watched."""
    from gridiron.decisions import grade_archive

    _, _, rec = render(tmp_path, "stale")
    assert rec["withheld_actions"] == ["lineup", "waiver", "matchup"]
    actuals = {p["gsis_id"]: (p["projected"] or 0.0) + 3.0
               for p in rec["roster"] if p["gsis_id"]}

    grade = grade_archive(rec, actuals)
    assert grade.comparisons, "the comparisons are still recorded"
    assert all(c.stance == "WITHHELD" for c in grade.comparisons)
    assert grade.scorable == () and grade.agreement() == (0, 0)
    assert grade.decisions == ()
    text = grade.summary()
    assert "0 confirmed owner decision(s)" in text
    assert "advice was not given" in text


def test_grading_a_fresh_page_scores_the_lineup_advice_it_did_endorse(tmp_path):
    from gridiron.decisions import grade_archive

    _, _, rec = render(tmp_path, "complete")
    actuals = {p["gsis_id"]: (p["projected"] or 0.0) + 3.0
               for p in rec["roster"] if p["gsis_id"]}
    grade = grade_archive(rec, actuals)

    assert all(c.stance == "ENDORSED" for c in grade.comparisons)
    assert grade.scorable, "endorsed start/sit comparisons are scored"
    assert all(c.kind == "start_sit" for c in grade.scorable), \
        "a waiver add this page never proved available is not scored"
    assert all(c.eligibility == "UNVERIFIED"
               for c in grade.comparisons if c.kind == "waiver")
    assert grade.decisions == (), "endorsing is still not observing"


def test_the_archive_this_run_wrote_is_content_addressed(tmp_path):
    """Two pages written in the same second must not share a filename."""
    _, _, rec = render(tmp_path, "complete")
    files = list((tmp_path / "arch" / "complete" / "season2026").glob("*.json"))
    assert len(files) == 1
    stem = files[0].stem
    assert re.fullmatch(r"week03_\d{8}T\d{6}Z_[0-9a-f]{8}", stem), stem
    assert rec["week"] == 3
