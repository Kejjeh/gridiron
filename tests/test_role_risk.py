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


def frame(**override):
    """`override`: gsis id -> {week: {column: value}} edits to the rows."""
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
    for r in rows:
        r.update((override.get(r["gsis_id"]) or {}).get(r["week"]) or {})
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


def risks(injury_rows, sleeper=None, *, tags_fresh=False, usage=None, plays=None):
    return O.role_risks(sleeper or SLEEPER,
                        O.lines_by_player(frame() if usage is None else usage,
                                          through_week=THROUGH),
                        injury_rows, week=WEEK, through_week=THROUGH, gsis_of=GSIS.get,
                        tags_fresh=tags_fresh, plays=plays)


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

def P(sid, team, mean=6.4, kickoff=KICK):
    return Player(sid, SLEEPER[sid]["full_name"], "RB", team, Projection(mean, 3.0), "BENCH",
                  False, "", "", (), f"00-{sid}", kickoff=kickoff)


def watch(pool, *, now=SAT_2326, previous=None, injury_rows=None, holders=None,
          roster=(), drops=None, sleeper=None, tags_fresh=False, report_week=WEEK,
          kicks=(LAST_KICK, KICK, NEXT_KICK), team_plays=None, unknown_report=False):
    return N.build_watch(
        pool, sleeper_players=sleeper or SLEEPER, tags_fresh=tags_fresh, team_plays=team_plays, market_of=lambda p: None, prior={}, prior_season=2025,
        drops=drops or {}, drops_note="", now=now, best=lineup(), slots=SLOTS, roster=roster,
        kickoffs_of=lambda p: kicks,
        slate_ends=(LAST_KICK + timedelta(hours=27), KICK + timedelta(hours=27)),
        previous=previous, usage_frame=frame(), through_week=THROUGH, gsis_of=GSIS.get,
        injury_rows=None if unknown_report else (report() if injury_rows is None
                                                     else injury_rows),
        report_week=report_week,
        holders=holders or {}, my_roster_id=3)


def test_the_row_says_who_holds_the_backup_and_his_waiver_phase():
    w = watch([P("kcback", "KC"), P("kcthird", "KC")])
    (r,) = w.risk
    assert r.holder == "pool" and r.clock.phase == "FREE_AGENT"
    assert "FREE AGENT" in r.waiver and "Sun 15 Nov 18:00 UTC" in r.waiver
    # the row never leaks into the other lists or into a number: the same
    # build without any risk input is identical outside the risk fields
    plain = watch([P("kcback", "KC"), P("kcthird", "KC")], injury_rows={})
    assert plain.risk == ()
    strip = ("risk", "risk_total", "risk_known", "risk_since", "risk_tags_fresh",
             "risk_tag_since")
    assert {k: v for k, v in w.record().items() if k not in strip} == \
        {k: v for k, v in plain.record().items() if k not in strip}, "a separate list"
    mine = watch([], roster=[P("kcback", "KC")], holders={"kcback": 3})
    assert mine.risk[0].holder == "yours" and "your roster" in mine.risk[0].waiver
    theirs = watch([], holders={"kcback": 7})
    assert theirs.risk[0].holder == "other" and "roster #7" in theirs.risk[0].waiver
    rec = w.record()
    assert rec["risk_total"] == 1 and rec["risk"][0]["backup"]["id"] == "kcback"
    assert rec["risk"][0]["waiver_phase"] == "FREE_AGENT"
    assert set(rec["risk"][0]) == {
        "key", "week", "team", "position", "starter", "starter_opps", "kind", "signal",
        "backup", "runner_up", "committee", "unknown_snaps", "holder", "waiver",
        "waiver_phase", "alert", "first_seen"}, "no number, no move, no claim field"


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


def _rec(w, at):
    return {"radar": {"pool": [{"id": "kcback"}]}, "watch": w.record(),
            "generated": at.isoformat()}


def test_an_unreadable_report_build_mid_run_does_not_re_fire_the_row():
    pool = [P("kcback", "KC")]
    t0, t1 = SAT_2326 - timedelta(hours=2), SAT_2326
    b0 = watch(pool, now=t0, injury_rows={})
    b1 = watch(pool, now=t1, previous=N.PreviousWatch.from_record(_rec(b0, t0)))
    assert b1.risk[0].first_seen == t1 and b1.risk_alerts
    t2 = t1 + timedelta(hours=3)            # injury source not FRESH: UNKNOWN
    b2 = watch(pool, now=t2, unknown_report=True,
               previous=N.PreviousWatch.from_record(_rec(b1, t1)))
    assert not b2.risk_known and b2.risk == ()
    t3 = t2 + timedelta(hours=3)
    b3 = watch(pool, now=t3, previous=N.PreviousWatch.from_record(_rec(b2, t2)))
    assert b3.risk[0].first_seen == t1, "carried through the unreadable build, not NEW at t3"


def test_a_stale_tag_build_mid_run_does_not_re_fire_newly_questionable():
    pool = [P("kcback", "KC")]
    fri = watch(pool, now=FRI_2132, injury_rows={})
    b1 = watch(pool, now=SAT_0141, injury_rows={}, sleeper=TAGGED, tags_fresh=True,
               previous=N.PreviousWatch.from_record(_rec(fri, FRI_2132)))
    assert b1.risk[0].first_seen == SAT_0141
    t2 = SAT_0141 + timedelta(hours=6)      # the player pull went stale: tags unread
    b2 = watch(pool, now=t2, injury_rows={}, sleeper=TAGGED, tags_fresh=False,
               previous=N.PreviousWatch.from_record(_rec(b1, SAT_0141)))
    assert b2.risk == ()
    t3 = t2 + timedelta(hours=6)
    b3 = watch(pool, now=t3, injury_rows={}, sleeper=TAGGED, tags_fresh=True,
               previous=N.PreviousWatch.from_record(_rec(b2, t2)))
    assert b3.risk[0].first_seen == SAT_0141, "the tag never went away"


def test_a_tag_carried_into_a_new_week_is_not_newly_questionable():
    wk10 = watch([P("kcback", "KC")], now=SAT_0141, injury_rows={}, sleeper=TAGGED,
                 tags_fresh=True,
                 previous=N.PreviousWatch.from_record(
                     _rec(watch([P("kcback", "KC")], now=FRI_2132, injury_rows={}), FRI_2132)))
    assert wk10.risk_alerts
    tue = datetime(2026, 11, 17, 8, 0, tzinfo=UTC)
    nxt = NEXT_KICK
    later = (KICK, nxt, nxt + timedelta(days=7))
    # the same tag, still on Sleeper on Tuesday of week 11 (no week-11 report row)
    prev = {**_rec(wk10, SAT_0141), "generated": (tue - timedelta(hours=1)).isoformat()}
    w11 = watch([P("kcback", "KC", kickoff=nxt)], now=tue, injury_rows={}, sleeper=TAGGED,
                tags_fresh=True, report_week=WEEK + 1, kicks=later,
                previous=N.PreviousWatch.from_record(prev))
    (r,) = w11.risk
    assert r.key == "kclead:wk11" and r.risk.kind == "QUESTIONABLE"
    assert not r.alert and r.first_seen == SAT_0141, "the week changed, the tag did not"
    # a fresh pull clears the tag; when it comes back, that IS new
    cleared = watch([P("kcback", "KC", kickoff=nxt)], now=tue + timedelta(hours=6),
                    injury_rows={}, sleeper=SLEEPER, tags_fresh=True, report_week=WEEK + 1,
                    kicks=later, previous=N.PreviousWatch.from_record(_rec(w11, tue)))
    assert cleared.risk == ()
    back = tue + timedelta(hours=12)
    again = watch([P("kcback", "KC", kickoff=nxt)], now=back, injury_rows={}, sleeper=TAGGED,
                  tags_fresh=True, report_week=WEEK + 1, kicks=later,
                  previous=N.PreviousWatch.from_record(_rec(cleared, tue + timedelta(hours=6))))
    assert again.risk[0].alert and again.risk[0].first_seen == back


def test_a_team_on_bye_has_no_role_at_risk():
    # finding 3: a Questionable tag held through a bye week never fires
    assert risks({}, sleeper=TAGGED, tags_fresh=True, plays=lambda t: t != "KC") == []
    assert risks({}, sleeper=TAGGED, tags_fresh=True, plays=lambda t: None), \
        "an unknown schedule is not a bye"
    w = watch([P("kcback", "KC", kickoff=None)], injury_rows={}, sleeper=TAGGED,
              tags_fresh=True, team_plays=lambda t: t != "KC")
    assert w.risk == ()


def test_unknown_backup_snaps_say_unknown_not_absent_or_committee():
    # finding 5: the second back's week-8 snap count is missing
    usage = frame(**{"00-kcback": {8: {"offense_pct": None}}})
    (r,) = risks(report(), usage=usage)
    assert r.unknown_snaps == ("KC Second Back",)
    assert "UNKNOWN" in r.committee and "KC Second Back" in r.committee
    assert "may be split" not in r.committee and "a committee:" not in r.committee
    # nobody known behind him at all: the row says the snaps are unknown
    only = {k: v for k, v in SLEEPER.items() if k != "kcthird"}
    w = N.build_watch([], sleeper_players=only, market_of=lambda p: None, prior={},
                      prior_season=2025, drops={}, drops_note="", now=SAT_2326, best=lineup(),
                      slots=SLOTS, usage_frame=usage, through_week=THROUGH, gsis_of=GSIS.get,
                      injury_rows=report(), report_week=WEEK,
                      team_kickoff=lambda t: KICK)
    (row_,) = w.risk
    assert row_.risk.backup is None and "UNKNOWN" in row_.waiver
    assert "has played recently" not in row_.waiver


def test_a_backup_whose_last_line_is_for_another_team_is_not_named():
    # finding 6: he just joined KC; his snaps are NYJ's
    usage = frame(**{"00-kcback": {wk: {"team": "NYJ"} for wk in (6, 7, 8, 9)}})
    (r,) = risks(report(), usage=usage)
    assert r.backup.sleeper_id == "kcthird"


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
