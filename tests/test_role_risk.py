"""RB role at risk (owner decisions 2026-11-18, Kejjeh/gridiron#29 item 1:
option C, then "DNP or Questionable"): a board watch row, never a move,
never a projection.

Pins:
  1. the week-10 KC case: a lead back on 65-82% of snaps who did not practise
     and is Questionable on the week's FINAL report puts his backup on the
     watch, with the backup's snap share. On the inputs the board actually
     held at Fri 21:32 UTC (no week-10 report row for him, Sleeper's pull a
     day old and untagged) nothing fires. At Sat 01:41 the fresh Sleeper pull
     tagged him Questionable: that fires, marked QUESTIONABLE and never read
     as out (rule #11), about 18 h before the backup was claimed; a stale
     tag never fires;
  2. who qualifies: an RB starter at 45%+ of snaps over his last 3 games; a
     DNP on a report with no game status yet (Wednesday, Thursday) is not the
     final report; Out / Doubtful (report or Sleeper) stays with the role-
     opening watch; RBs only;
  3. committees are flagged: no back behind him at 25%+ of snaps, or two
     within 10 points of each other;
  4. who holds the backup: the pool (with the board's waiver clock), the
     owner, or another team;
  5. it alerts on the TRANSITION only (rule #8): new against a comparable
     previous record, carried after, nothing from a legacy record;
  6. the page renders it as research, not a move, and says UNKNOWN without a
     report.
Invented ids; the KC usage numbers are the week-10 archive's.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pandas as pd
from test_notable import SLOTS, lineup

from gridiron import notable as N
from gridiron import openings as O
from gridiron.dashboard import _risk_alert, _risk_html
from gridiron.lineup import Player
from gridiron.projection import Projection

UTC = timezone.utc
WEEK, THROUGH = 10, 9
#: The builds of 2026 week 10 (archive times).
FRI_2132 = datetime(2026, 11, 13, 21, 32, tzinfo=UTC)
SAT_2326 = datetime(2026, 11, 14, 23, 26, tzinfo=UTC)
KICK = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
LAST_KICK = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)
NEXT_KICK = datetime(2026, 11, 22, 18, 0, tzinfo=UTC)


def row(gid, week, team, snap, carries=0, targets=0):
    return {"gsis_id": gid, "week": week, "team": team, "position": "RB",
            "offense_pct": snap, "targets": targets, "carries": carries}


def frame():
    rows = []
    # KC, weeks 6-9: the lead back's 24 opportunities a game at 65-82% of
    # snaps; his backup's 6/3/6/6 at 30/26/19/30%; a third back barely used
    for wk, lead, back, third in ((6, .82, .30, .04), (7, .65, .26, .05), (8, .75, .19, .03),
                                  (9, .78, .30, .04)):
        rows.append(row("00-kclead", wk, "KC", lead, carries=19, targets=5))
        rows.append(row("00-kcback", wk, "KC", back, carries=4, targets=2))
        rows.append(row("00-kcthird", wk, "KC", third, carries=1))
    # a committee: the starter at 50%, two backs at 30% and 25%
    for wk in (7, 8, 9):
        rows.append(row("00-colead", wk, "COM", .50, carries=12))
        rows.append(row("00-coa", wk, "COM", .30, carries=6))
        rows.append(row("00-cob", wk, "COM", .25, carries=5))
    # a lead back just under the bar (44%), and his backup
    for wk in (7, 8, 9):
        rows.append(row("00-lowlead", wk, "LOW", .44, carries=11))
        rows.append(row("00-lowback", wk, "LOW", .35, carries=8))
    return pd.DataFrame(rows)


SLEEPER = {
    "kclead": {"full_name": "KC Lead Back", "position": "RB", "team": "KC"},
    "kcback": {"full_name": "KC Second Back", "position": "RB", "team": "KC",
               "depth_chart_order": 2},
    "kcthird": {"full_name": "KC Third Back", "position": "RB", "team": "KC"},
    "colead": {"full_name": "Committee Lead", "position": "RB", "team": "COM"},
    "coa": {"full_name": "Committee A", "position": "RB", "team": "COM"},
    "cob": {"full_name": "Committee B", "position": "RB", "team": "COM"},
    "lowlead": {"full_name": "Low Lead", "position": "RB", "team": "LOW"},
    "lowback": {"full_name": "Low Back", "position": "RB", "team": "LOW"},
}
GSIS = {sid: f"00-{sid}" for sid in SLEEPER}


def report(status="Questionable", practice="Did Not Participate In Practice", week=WEEK,
           gid="00-kclead"):
    return {gid: {"week": week, "report_status": status, "practice_status": practice,
                  "body_part": "Groin"}}


def risks(injury_rows, sleeper=None, *, tags_fresh=False):
    return O.role_risks(sleeper or SLEEPER, O.lines_by_player(frame(), through_week=THROUGH),
                        injury_rows, week=WEEK, through_week=THROUGH, gsis_of=GSIS.get,
                        tags_fresh=tags_fresh)


#: The Sat 01:41 Sleeper pull: the KC lead back tagged "Questionable (Groin)".
TAGGED = {**SLEEPER, "kclead": {**SLEEPER["kclead"], "injury_status": "Questionable",
                                "injury_body_part": "Groin"}}
SAT_0141 = datetime(2026, 11, 14, 1, 41, tzinfo=UTC)


# ------------------------------------------------------------ 1. the case

def test_the_week10_kc_case_names_the_backup_once_the_final_report_carries_the_dnp():
    got = risks(report())
    assert [r.starter.sleeper_id for r in got] == ["kclead"]
    r = got[0]
    assert r.team == "KC" and r.backup.sleeper_id == "kcback"
    assert round(r.starter.snap_pct) == 73 and round(r.starter_opps) == 24
    assert round(r.backup.snap_pct) == 25, "his last three games: 26, 19, 30"
    assert r.runner_up.sleeper_id == "kcthird" and not r.committee, "one clear backup"
    assert r.kind == "DNP" and "Questionable" in r.signal and "did not practise" in r.signal


def test_on_the_fri_2132_inputs_nothing_fires_because_the_report_had_no_row_for_him():
    # the archive: "no designation on the wk10 report" (no report row at all;
    # Sleeper's tag was a day old and empty) — the row never invents a DNP
    assert risks({}) == []
    assert risks({"00-other": report()["00-kclead"]}) == []
    assert risks({}, tags_fresh=True) == [], "no tag on him at all"


def test_on_the_sat_0141_inputs_the_fresh_questionable_tag_fires_and_is_not_out():
    # the injury pull was still Friday's (no row for him); the Sleeper pull
    # of 01:41 was fresh and tagged him Questionable
    (r,) = risks({}, sleeper=TAGGED, tags_fresh=True)
    assert r.kind == "QUESTIONABLE" and r.backup.sleeper_id == "kcback"
    assert "Questionable" in r.signal and "(Groin)" in r.signal
    assert "not out" in r.signal and "Out " not in r.signal, "rule #11"
    assert risks({}, sleeper=TAGGED, tags_fresh=False) == [], "a stale tag is not newly posted"
    # the final report's DNP outranks the tag: the row says what fired it
    assert risks(report(), sleeper=TAGGED, tags_fresh=True)[0].kind == "DNP"
    # a Questionable report row that practised, with a fresh tag: the tag fires
    limited = report(practice="Limited Participation in Practice")
    assert risks(limited, sleeper=TAGGED, tags_fresh=True)[0].kind == "QUESTIONABLE"
    low = {**SLEEPER, "lowlead": {**SLEEPER["lowlead"], "injury_status": "Questionable"}}
    assert risks({}, sleeper=low, tags_fresh=True) == [], "44% of snaps is under the bar"
    assert risks(None, sleeper=TAGGED, tags_fresh=True) == [], "no current report: UNKNOWN"


# ------------------------------------------------------------ 2. who qualifies

def test_only_the_final_report_s_dnp_on_a_45_percent_rb_starter_triggers():
    assert risks(report(status="")) == [], "a DNP with no game status is not the final report"
    assert risks(report(practice="Limited Participation in Practice")) == []
    assert risks(report(week=WEEK - 1)) == [], "last week's report is not this week's"
    assert risks(report(gid="00-lowlead")) == [], "44% of snaps is under the bar"
    co = risks(report(gid="00-colead"))
    assert [r.starter.sleeper_id for r in co] == ["colead"], "50% is over it"


def test_out_or_doubtful_stays_with_the_role_opening_watch():
    assert risks(report(status="Out")) == []
    assert risks(report(status="Doubtful")) == []
    tagged = {**SLEEPER, "kclead": {**SLEEPER["kclead"], "injury_status": "Out"}}
    assert risks(report(), sleeper=tagged) == []
    # a backup ruled out himself is never the one named
    hurt = {**SLEEPER, "kcback": {**SLEEPER["kcback"], "injury_status": "Out"}}
    assert risks(report(), sleeper=hurt)[0].backup.sleeper_id == "kcthird"


def test_receivers_and_tight_ends_are_not_watched():
    wr = {**SLEEPER, "kclead": {**SLEEPER["kclead"], "position": "WR"}}
    assert risks(report(), sleeper=wr) == []


# ------------------------------------------------------------ 3. committees

def test_a_committee_backfield_is_flagged():
    r = risks(report(gid="00-colead"))[0]
    assert r.backup.sleeper_id == "coa" and r.runner_up.sleeper_id == "cob"
    assert "within 10" in r.committee
    thin = risks(report(), sleeper={**SLEEPER, "kcback": {**SLEEPER["kcback"], "team": "NYJ"}})
    assert thin[0].backup.sleeper_id == "kcthird" and "25%" in thin[0].committee


# ------------------------------------------------------------ 4-5. the watch row

def P(sid, team, mean=6.4):
    return Player(sid, SLEEPER[sid]["full_name"], "RB", team, Projection(mean, 3.0), "BENCH",
                  False, "", "", (), f"00-{sid}", kickoff=KICK)


def watch(pool, *, now=SAT_2326, previous=None, injury_rows=None, holders=None,
          roster=(), drops=None, sleeper=None, tags_fresh=False):
    return N.build_watch(
        pool, sleeper_players=sleeper or SLEEPER, tags_fresh=tags_fresh, market_of=lambda p: None, prior={}, prior_season=2025,
        drops=drops or {}, drops_note="", now=now, best=lineup(), slots=SLOTS, roster=roster,
        kickoffs_of=lambda p: (LAST_KICK, KICK, NEXT_KICK),
        slate_ends=(LAST_KICK + timedelta(hours=27), KICK + timedelta(hours=27)),
        previous=previous, usage_frame=frame(), through_week=THROUGH, gsis_of=GSIS.get,
        injury_rows=report() if injury_rows is None else injury_rows, report_week=WEEK,
        holders=holders or {}, my_roster_id=3)


def test_the_row_says_who_holds_the_backup_and_his_waiver_phase():
    w = watch([P("kcback", "KC"), P("kcthird", "KC")])
    (r,) = w.risk
    assert r.holder == "pool" and r.clock.phase == "FREE_AGENT"
    assert "FREE AGENT" in r.waiver and "Sun 15 Nov 18:00 UTC" in r.waiver
    assert w.work == () or all(x.kind != "risk" for x in w.work), "a separate list"
    mine = watch([], roster=[P("kcback", "KC")], holders={"kcback": 3})
    assert mine.risk[0].holder == "yours" and "your roster" in mine.risk[0].waiver
    theirs = watch([], holders={"kcback": 7})
    assert theirs.risk[0].holder == "other" and "roster #7" in theirs.risk[0].waiver
    rec = w.record()
    assert rec["risk_total"] == 1 and rec["risk"][0]["backup"]["id"] == "kcback"
    assert rec["risk"][0]["waiver_phase"] == "FREE_AGENT"
    assert "projected" not in rec["risk"][0], "never a projection"


def test_it_alerts_on_the_transition_only():
    pool = [P("kcback", "KC")]
    first = watch(pool)
    assert not first.risk_alerts, "no comparable previous record: nothing is NEW"
    prev = {"radar": {"pool": [{"id": "kcback"}]}, "watch": {**first.record(), "risk_since": {}},
            "generated": (SAT_2326 - timedelta(hours=1)).isoformat()}
    w = watch(pool, previous=N.PreviousWatch.from_record(prev))
    assert [r.key for r in w.risk_alerts] == ["kclead:wk10"] and w.risk[0].first_seen == SAT_2326
    later = SAT_2326 + timedelta(hours=2)
    rec2 = {"radar": {"pool": [{"id": "kcback"}]}, "watch": w.record(),
            "generated": (later - timedelta(hours=1)).isoformat()}
    w2 = watch(pool, previous=N.PreviousWatch.from_record(rec2), now=later)
    assert w2.risk[0].first_seen == SAT_2326, "carried, not re-fired"
    legacy = {"radar": {"pool": [{"id": "kcback"}]}, "watch": {"pool_since": {}},
              "generated": (SAT_2326 - timedelta(hours=1)).isoformat()}
    assert not watch(pool, previous=N.PreviousWatch.from_record(legacy)).risk_alerts


def test_the_week10_case_alerts_at_sat_0141_and_the_later_dnp_is_carried_not_re_fired():
    pool = [P("kcback", "KC")]
    fri = {"radar": {"pool": [{"id": "kcback"}]},
           "watch": watch(pool, now=FRI_2132, injury_rows={}).record(),
           "generated": FRI_2132.isoformat()}
    assert fri["watch"]["risk"] == [], "Fri 21:32: nothing"
    sat = watch(pool, now=SAT_0141, injury_rows={}, sleeper=TAGGED, tags_fresh=True,
                previous=N.PreviousWatch.from_record(fri))
    assert [r.key for r in sat.risk_alerts] == ["kclead:wk10"]
    assert sat.risk[0].risk.kind == "QUESTIONABLE" and sat.risk[0].first_seen == SAT_0141
    assert sat.record()["risk"][0]["kind"] == "QUESTIONABLE" and sat.record()["risk_tags_fresh"]
    rec = {"radar": {"pool": [{"id": "kcback"}]}, "watch": sat.record(),
           "generated": SAT_0141.isoformat()}
    # the next build, same inputs: carried, not re-fired
    later = SAT_0141 + timedelta(hours=6)
    again = watch(pool, now=later, injury_rows={}, sleeper=TAGGED, tags_fresh=True,
                  previous=N.PreviousWatch.from_record(rec))
    assert again.risk[0].first_seen == SAT_0141
    # ... and when the report's DNP arrives, the row says so, same first sighting
    rec2 = {"radar": {"pool": [{"id": "kcback"}]}, "watch": again.record(),
            "generated": later.isoformat()}
    dnp = watch(pool, now=later + timedelta(hours=6), sleeper=TAGGED, tags_fresh=True,
                previous=N.PreviousWatch.from_record(rec2))
    assert dnp.risk[0].risk.kind == "DNP" and dnp.risk[0].first_seen == SAT_0141


def test_after_kickoff_the_row_is_gone():
    assert watch([P("kcback", "KC")], now=KICK + timedelta(minutes=5)).risk == ()


# ------------------------------------------------------------ 6. the page

def test_the_page_renders_it_as_research_and_says_unknown_without_a_report():
    prev = {"radar": {"pool": [{"id": "kcback"}]}, "watch": {"risk_since": {}},
            "generated": (SAT_2326 - timedelta(hours=1)).isoformat()}
    d = SimpleNamespace(watch=watch([P("kcback", "KC")], previous=N.PreviousWatch.from_record(prev)))
    page = _risk_html(d)
    assert "KC Second Back" in page and "KC Lead Back" in page and "25%" in page
    assert "Not a move" in page and "FREE AGENT" in page
    assert "KC Second Back" in _risk_alert(d) and "#risk" in _risk_alert(d)
    blind = SimpleNamespace(watch=watch([P("kcback", "KC")], injury_rows={}))
    assert blind.watch.risk_known and _risk_alert(blind) == ""
    none = SimpleNamespace(watch=N.build_watch(
        [], sleeper_players=SLEEPER, market_of=lambda p: None, prior={}, prior_season=2025,
        drops={}, drops_note="", now=SAT_2326, best=lineup(), slots=SLOTS,
        usage_frame=frame(), through_week=THROUGH, gsis_of=GSIS.get))
    assert not none.watch.risk_known and "UNKNOWN" in _risk_html(none)
    assert "not fresh" in _risk_html(blind), "says only the report was read"


def test_a_questionable_row_reads_questionable_not_out_on_the_page():
    prev = {"radar": {"pool": [{"id": "kcback"}]}, "watch": {"risk_since": {}},
            "generated": (SAT_0141 - timedelta(hours=1)).isoformat()}
    d = SimpleNamespace(watch=watch([P("kcback", "KC")], now=SAT_0141, injury_rows={},
                                    sleeper=TAGGED, tags_fresh=True,
                                    previous=N.PreviousWatch.from_record(prev)))
    page, alert = _risk_html(d), _risk_alert(d)
    assert "QUESTIONABLE — NOT OUT" in page and "DID NOT PRACTISE</span> <b>" not in page
    assert "KC Lead Back (KC, Questionable)" in alert and "not out" in alert
    assert "did not practise)" not in alert and "three in five" not in alert
    dnp = SimpleNamespace(watch=watch([P("kcback", "KC")], previous=N.PreviousWatch.from_record(
        {**prev, "generated": (SAT_2326 - timedelta(hours=1)).isoformat()})))
    assert "three in five" in _risk_alert(dnp) and "most such backs play" not in _risk_alert(dnp)
