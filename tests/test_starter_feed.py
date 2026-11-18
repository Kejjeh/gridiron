"""Sleeper's projection for every high-snap RB starter, recorded every build
(owner decision 2026-11-18, Kejjeh/gridiron#29 item 2, option B: "record
first, decide later"). Record-only: nothing fires on it, and nothing on the
page or in the grade changes.

Pins:
  1. who is recorded: the role-at-risk subset (`openings.high_snap_starters`:
     an RB at 45%+ of snaps over his last 3 games), owned or not; a declared
     bye is skipped; `role_risks` uses the same subset;
  2. three answers, never two: LISTED (with his number), ABSENT (a full feed
     that does not list him), UNKNOWN (no feed, a feed for another week, or
     a thin position, which is a failed fetch, never an absence; sop-fail-loud);
  3. the record says when the feed was fetched and whether that was before
     his kickoff, so a later study can time the feed against a ruling;
  4. the page and every decision are unchanged with or without it.
Invented ids; the snap numbers are test_role_risk's.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from test_role_risk import GSIS, SLEEPER, THROUGH, WEEK, frame, report

from gridiron import openings as O
from gridiron import shadow as SH

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
FETCHED = datetime(2026, 11, 14, 1, 30, tzinfo=UTC)
NOW = FETCHED + timedelta(minutes=11)
KICK = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)


def starters(plays=None, usage=None):
    return O.high_snap_starters(SLEEPER, O.lines_by_player(frame() if usage is None else usage,
                                                           through_week=THROUGH),
                                through_week=THROUGH, gsis_of=GSIS.get, plays=plays)


def feed(listed: dict[str, float], *, n_rb=25, week=WEEK, fetched=FETCHED):
    players = {f"filler{i}": {"points": 5.0, "position": "RB"} for i in range(n_rb)}
    players.update({sid: {"points": pts, "position": "RB"} for sid, pts in listed.items()})
    return {"source": "sleeper", "season": 2026, "week": week,
            "fetched_at": fetched.isoformat(), "fetched_at_dt": fetched, "players": players}


def block(blob, plays=None):
    return SH.starter_feed_block(blob, starters(plays), week=WEEK, now=NOW,
                                 kickoff_of=lambda team: KICK, min_per_position=20)


# ------------------------------------------------------------ 1. who
def test_the_subset_is_the_role_at_risk_one():
    got = {(s.team, s.back.sleeper_id): s.back.snap_pct for s in starters()}
    # KC lead (75% over weeks 7-9) and the committee lead (50%); the 44% lead is not
    assert set(got) == {("KC", "kclead"), ("COM", "colead")}
    assert got[("KC", "kclead")] == round((65 + 75 + 78) / 3, 1)


def test_a_declared_bye_is_skipped_and_unknown_is_not_a_bye():
    assert {s.team for s in starters(plays=lambda t: False if t == "KC" else None)} == {"COM"}
    assert {s.team for s in starters(plays=lambda t: None)} == {"KC", "COM"}


def test_role_risks_and_the_feed_record_watch_the_same_starters():
    # every role-at-risk row's starter is in the recorded subset
    fired = O.role_risks(SLEEPER, O.lines_by_player(frame(), through_week=THROUGH), report(),
                         week=WEEK, through_week=THROUGH, gsis_of=GSIS.get)
    assert fired and {r.starter.sleeper_id for r in fired} <= \
        {s.back.sleeper_id for s in starters()}


# ------------------------------------------------------------ 2. three answers
def test_listed_absent_and_a_zero_are_kept_apart():
    b = block(feed({"kclead": 0.0}))
    assert b["feed"] == "FULL" and b["rb_listed"] == 26          # 25 fillers + him
    kc, co = b["players"]["kclead"], b["players"]["colead"]
    assert kc["feed"] == "LISTED" and kc["points"] == 0.0     # a zero is a number, not absence
    assert co["feed"] == "ABSENT" and co["points"] is None
    assert kc["team"] == "KC" and kc["snap_pct"] == round((65 + 75 + 78) / 3, 1)
    assert kc["gsis_id"] == "00-kclead"                       # the stable id joins it (rule #3)


def test_no_feed_another_week_or_a_thin_position_is_unknown_never_absent():
    for blob, why in ((None, "no projections feed"),
                      (feed({}, week=WEEK - 1), "another week"),
                      (feed({"kclead": 12.0}, n_rb=5), "thin")):
        b = block(blob)
        assert b["feed"] == "UNKNOWN" and why in b["reason"], (why, b)
        assert {v["feed"] for v in b["players"].values()} == {"UNKNOWN"}, why
        assert all(v["points"] is None for v in b["players"].values()), why


# ------------------------------------------------------------ 3. timing
def test_the_record_states_when_the_feed_was_fetched_and_against_which_kickoff():
    b = block(feed({"kclead": 14.2}))
    assert b["fetched_at"] == FETCHED.isoformat() and b["age_hours"] == round(11 / 60, 2)
    kc = b["players"]["kclead"]
    assert kc["kickoff"] == KICK.isoformat() and kc["pre_kickoff"] is True
    late = SH.starter_feed_block(feed({"kclead": 14.2}, fetched=KICK + timedelta(hours=1)),
                                 starters(), week=WEEK, now=KICK + timedelta(hours=2),
                                 kickoff_of=lambda team: KICK, min_per_position=20)
    assert late["players"]["kclead"]["pre_kickoff"] is False
    unknown_kick = SH.starter_feed_block(feed({"kclead": 14.2}), starters(), week=WEEK,
                                         now=NOW, kickoff_of=lambda team: None,
                                         min_per_position=20)
    assert unknown_kick["players"]["kclead"]["pre_kickoff"] is None   # unknown, not False
    assert "never" in b["note"] and "record" in b["note"]


def test_no_starters_known_still_records_why():
    b = SH.starter_feed_block(feed({}), [], week=WEEK, now=NOW, kickoff_of=lambda t: KICK,
                              min_per_position=20)
    assert b["players"] == {} and b["feed"] == "FULL" and b["reason"] == "25 RBs listed"


# ------------------------------------------------------------ 4. the page
def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_the_record_carries_the_block_and_nothing_else_changes(tmp_path):
    scn = _load("feed_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("feed_dash", "scripts/weekly/dashboard.py")

    def render(tag, with_shadow):
        root = tmp_path / tag
        scn.build_scenario(root, "complete", now=scn.NOW)
        if with_shadow:
            SH.write_shadow(root / "season2026", {
                "source": "sleeper", "season": 2026, "week": 3,
                "fetched_at": (scn.NOW - timedelta(hours=1)).isoformat(),
                "players": {sid: {"points": 9.5, "position": "RB"}
                            for sid in ("6790", "3198", "6794")}})
        out = tmp_path / f"out_{tag}"
        cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                  "--out-dir", str(out), "--archive-root", str(tmp_path / f"arch_{tag}"),
                  "--now", scn.NOW.isoformat(), "--no-archive"])
        rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
        return rec, (out / "dashboard_latest.html").read_text("utf-8")

    (plain, plain_html), (fed, fed_html) = render("plain", False), render("fed", True)
    for rec in (plain, fed):
        b = rec["starter_feed"]
        assert b is not None and b["subset"].startswith("RB")
        assert set(b) >= {"feed", "reason", "fetched_at", "players", "note"}
    assert plain["starter_feed"]["feed"] == "UNKNOWN"
    # three RBs is a thin feed: a failed fetch, never an absence
    assert fed["starter_feed"]["feed"] == "UNKNOWN"
    # record-only: never on the page (the public HTML), never in a decision
    for html in (plain_html, fed_html):
        assert "starter_feed" not in html and fed["starter_feed"]["note"] not in html
        assert fed["starter_feed"]["subset"] not in html
    for key in ("roster", "best_lineup", "actions", "upgrades", "gate", "degraded"):
        assert plain[key] == fed[key], f"{key} changed"


def test_no_usage_lines_says_the_subset_is_unknown():
    b = SH.starter_feed_block(feed({}), [], week=WEEK, now=NOW, kickoff_of=lambda t: KICK,
                              min_per_position=20, starters_known=False)
    assert b["starters_known"] is False and b["players"] == {}
    assert block(feed({}))["starters_known"] is True


# ------------------------------------------------------------ the study script
def test_the_reaction_study_times_drops_and_never_assumes_who_played():
    fr = _load("feed_reaction", "scripts/research/feed_reaction.py")

    def rec(at, feed_state, points=None, *, sid="kclead"):
        return {"generated": at.isoformat(), "starter_feed": {"week": WEEK, "players": {sid: {
            "gsis_id": f"00-{sid}", "team": "KC", "feed": feed_state, "points": points,
            "kickoff": KICK.isoformat()}}}}

    sat_0141 = datetime(2026, 11, 14, 1, 41, tzinfo=UTC)
    records = [rec(FETCHED, "LISTED", 14.0), rec(sat_0141, "UNKNOWN"),
               rec(sat_0141 + timedelta(hours=6), "LISTED", 0.0),       # dropped to 0
               rec(KICK + timedelta(hours=1), "ABSENT"),                # after kickoff: ignored
               rec(FETCHED, "LISTED", 12.0, sid="colead")]
    rows = fr.reaction_rows(records)
    kc = next(r for r in rows if r["sleeper_id"] == "kclead")
    assert kc["builds"] == 3 and kc["unknown_builds"] == 1
    assert kc["first_drop"] == (sat_0141 + timedelta(hours=6)).isoformat()
    assert kc["hours_before_kickoff"] == round(
        (KICK - sat_0141 - timedelta(hours=6)).total_seconds() / 3600, 1)
    s = fr.summarize(rows, {("00-kclead", WEEK): False})
    assert s["sat_dropped"] == 1 and s["false_drops"] == 0 and s["played_unknown"] == 1
    assert s["enough_weeks"] is False                        # one week is not evidence
    s = fr.summarize(rows, {("00-kclead", WEEK): True})
    assert s["false_drops"] == 1 and s["sat"] == 0
    # an UNKNOWN build is never a drop
    only_unknown = fr.reaction_rows([rec(sat_0141, "UNKNOWN")])
    assert only_unknown[0]["first_drop"] is None and only_unknown[0]["unknown_builds"] == 1


# ------------------------------------------------------------ the carried log
def test_the_log_keeps_changes_only_and_a_rerun_adds_nothing(tmp_path):
    log = tmp_path / "starter_feed" / "season2026.csv"
    t0 = NOW
    assert SH.append_feed_log(log, block(feed({"kclead": 14.2})), t0) == 2   # both starters
    assert SH.append_feed_log(log, block(feed({"kclead": 14.2})), t0 + timedelta(minutes=15)) == 0
    assert SH.append_feed_log(log, block(feed({"kclead": 14.2})), t0) == 0   # a re-run
    later = feed({}, fetched=t0 + timedelta(hours=3))                       # he drops out
    assert SH.append_feed_log(log, block(later), t0 + timedelta(hours=3, minutes=2)) == 1
    assert SH.append_feed_log(log, None, t0) == 0
    rows = log.read_text("utf-8").splitlines()
    assert rows[0].split(",") == list(SH.FEED_LOG_FIELDS) and len(rows) == 4
    assert "ABSENT" in rows[-1] and "00-kclead" in rows[-1]


def test_the_study_reads_the_log_and_times_a_drop_by_the_fetch(tmp_path):
    fr = _load("feed_reaction_log", "scripts/research/feed_reaction.py")
    log = tmp_path / "season2026.csv"
    SH.append_feed_log(log, block(feed({"kclead": 14.2, "colead": 9.0})), NOW)
    fetched = NOW + timedelta(hours=3)
    SH.append_feed_log(log, SH.starter_feed_block(
        feed({"colead": 9.0}, fetched=fetched), starters(), week=WEEK,
        now=fetched + timedelta(hours=1), kickoff_of=lambda t: KICK, min_per_position=20),
        fetched + timedelta(hours=1))                     # the board saw it an hour later
    rows = fr.reaction_rows(fr.records_from_log(log))
    kc = next(r for r in rows if r["sleeper_id"] == "kclead")
    assert kc["first_drop"] == fetched.isoformat()        # Sleeper's fetch, not the build
    assert kc["seen_at"] == (fetched + timedelta(hours=1)).isoformat()
    assert kc["hours_before_kickoff"] == round((KICK - fetched).total_seconds() / 3600, 1)
    co = next(r for r in rows if r["sleeper_id"] == "colead")
    assert co["first_drop"] is None and co["answered"]


def test_a_week_the_feed_never_answered_is_unanswered_not_a_miss():
    fr = _load("feed_reaction_unk", "scripts/research/feed_reaction.py")
    rec = {"generated": NOW.isoformat(), "starter_feed": {"week": WEEK, "players": {"kclead": {
        "gsis_id": "00-kclead", "team": "KC", "feed": "UNKNOWN", "points": None,
        "kickoff": KICK.isoformat()}}}}
    s = fr.summarize(fr.reaction_rows([rec]), {("00-kclead", WEEK): False})
    assert s["sat"] == 1 and s["sat_unanswered"] == 1 and s["sat_missed"] == 0
