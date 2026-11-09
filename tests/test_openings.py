"""Free agents named on USAGE alone (gridiron.openings, wired into
gridiron.notable's watch and the board). Invented players only.

Pins the two 2026 week-9 misses as cases:
  1. a receiver already playing a starter's snaps with a starter's targets is
     named (VOLUME), whatever any projection ranks him; unknown snaps, too few
     games or stale games never are;
  2. a back behind a role vacated this week is named (OPENING): the lead back
     released (Sleeper lists no team), traded (another team) or designated
     Out/IR/Doubtful — never Questionable (rule #11), never work from weeks
     ago, never a sliver of work; the pool players highest on Sleeper's depth
     chart are named, never one who will not play himself;
  3. a row alerts only when it is NEW against a comparable previous record
     that carried the list (rule #8), and its first-seen time carries record
     to record; a legacy record alerts nothing;
  4. the page and the review render the list, and say UNKNOWN without box
     scores.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
from test_notable import NOW, SLOTS, lineup

from gridiron import notable as N
from gridiron import openings as O
from gridiron.dashboard import _work_alert, _work_html
from gridiron.lineup import Player
from gridiron.projection import Projection, abstain

ROOT = Path(__file__).resolve().parents[1]
THROUGH = 8


def P(sid, pos, team, mean=None, *, withheld=False, name=None):
    proj = abstain("no admissible box scores") if mean is None else Projection(mean, 3.0)
    if withheld:
        proj = Projection(5.0, 3.0).withheld("designated Out: projected 0")
    return Player(sid, name or f"Invented {sid}", pos, team, proj, "BENCH", False, "", "",
                  (), f"00-{sid}")


def row(gid, week, team, pos, snap, targets=0, carries=0, attempts=0):
    return {"gsis_id": gid, "week": week, "team": team, "position": pos,
            "offense_pct": snap, "targets": targets, "carries": carries,
            "attempts": attempts}


def frame():
    rows = []
    for wk in (5, 6, 7, 8):
        # the volume receiver: 80%+ of snaps, 7-9 targets
        rows.append(row("00-vol", wk, "CLE", "WR", 0.82, targets=6 + wk % 3 + 1))
        # a part-timer: plenty of snaps, two targets
        rows.append(row("00-pt", wk, "CLE", "WR", 0.70, targets=2))
        # the lead back who was just released, and the depth chart behind him
        rows.append(row("00-lead", wk, "NOO", "RB", 0.55, targets=2, carries=16))
        rows.append(row("00-back3", wk, "NOO", "RB", 0.10, carries=2))
        # a back ruled Questionable: still on the field
        rows.append(row("00-q", wk, "QQQ", "RB", 0.60, carries=14))
    rows.append(row("00-old", 4, "OLD", "RB", 0.70, carries=18))     # last played week 4
    rows.append(row("00-blank", 6, "CLE", "WR", None, targets=9))     # snaps unknown
    rows.append(row("00-blank", 7, "CLE", "WR", None, targets=9))
    rows.append(row("00-blank", 8, "CLE", "WR", None, targets=9))
    rows.append(row("00-late", 9, "CLE", "WR", 0.95, targets=12))     # after the boundary
    return pd.DataFrame(rows)


SLEEPER = {
    "lead": {"full_name": "Lead Back", "position": "RB", "team": None},
    "back2": {"full_name": "Second Back", "position": "RB", "team": "NOO",
              "depth_chart_order": 2},
    "back3": {"full_name": "Third Back", "position": "RB", "team": "NOO",
              "depth_chart_order": 3},
    "back4": {"full_name": "Fourth Back", "position": "RB", "team": "NOO"},
    "hurt": {"full_name": "Hurt Back", "position": "RB", "team": "NOO",
             "depth_chart_order": 1, "injury_status": "Out"},
    "q": {"full_name": "Quentin Questionable", "position": "RB", "team": "QQQ",
          "injury_status": "Questionable"},
    "qb2": {"full_name": "Quinn Backup", "position": "RB", "team": "QQQ",
            "depth_chart_order": 2},
    "old": {"full_name": "Old Starter", "position": "RB", "team": None},
    "vol": {"full_name": "Volume Receiver", "position": "WR", "team": "CLE"},
}
GSIS = {sid: f"00-{sid}" for sid in SLEEPER}


def lines():
    return O.lines_by_player(frame(), through_week=THROUGH)


def test_a_starters_workload_is_named_whatever_the_projection_says():
    ls = lines()
    hit = O.volume_reason("WR", ls["00-vol"], THROUGH)
    assert hit and "82% of snaps" in hit[0] and hit[1] >= 6
    assert O.volume_reason("WR", ls["00-pt"], THROUGH) is None      # snaps, no ball
    assert O.volume_reason("WR", ls["00-blank"], THROUGH) is None   # unknown is not a starter
    assert O.volume_reason("RB", ls["00-old"], THROUGH) is None     # one stale game
    assert O.volume_reason("K", ls["00-vol"], THROUGH) is None
    assert "00-late" not in ls, "a week after the evidence boundary is never read"


def test_a_released_or_ruled_out_starter_leaves_a_vacancy_but_questionable_does_not():
    vac = O.find_vacancies(SLEEPER, lines(), through_week=THROUGH, gsis_of=GSIS.get)
    gone = vac[("NOO", "RB")]
    assert [v.sleeper_id for v in gone] == ["lead"]
    assert "no longer on the team" in gone[0].why and gone[0].opps >= 8
    assert ("QQQ", "RB") not in vac, "Questionable is not out (rule #11)"
    assert not any(v.sleeper_id == "old" for vs in vac.values() for v in vs), "stale work"
    traded = {**SLEEPER, "lead": {**SLEEPER["lead"], "team": "KC"}}
    v2 = O.find_vacancies(traded, lines(), through_week=THROUGH, gsis_of=GSIS.get)
    assert "now with KC" in v2[("NOO", "RB")][0].why
    out = {**SLEEPER, "lead": {**SLEEPER["lead"], "team": "NOO", "injury_status": "Doubtful"}}
    v3 = O.find_vacancies(out, lines(), through_week=THROUGH, gsis_of=GSIS.get)
    assert "Doubtful" in v3[("NOO", "RB")][0].why
    assert O.find_vacancies(SLEEPER, lines(), through_week=None, gsis_of=GSIS.get) == {}


def test_the_backs_behind_the_opening_are_named_by_depth_chart_never_one_who_sits():
    pool = [P("back4", "RB", "NOO"), P("back3", "RB", "NOO"), P("back2", "RB", "NOO"),
            P("vol", "WR", "CLE")]
    vac = O.find_vacancies(SLEEPER, lines(), through_week=THROUGH, gsis_of=GSIS.get)
    got = O.opening_reasons(pool, SLEEPER, vac, lines())
    assert set(got) == {"back2", "back3"}                         # OPENING_TAKE, by depth
    assert "#2 on Sleeper's NOO RB depth chart" in got["back2"][0]
    assert "Lead Back" in got["back2"][0]
    got2 = O.opening_reasons(pool, SLEEPER, vac, lines(),
                             sits=lambda p: p.sleeper_id == "back2")
    assert set(got2) == {"back3", "back4"}
    assert "not on Sleeper's depth chart" in got2["back4"][0]


def test_openings_rank_by_roles_vacated_not_raw_volume_so_qbs_do_not_crowd_the_list():
    sleeper = {"qb1": {"full_name": "Gone Passer", "position": "QB", "team": None},
               "qb2": {"full_name": "Backup Passer", "position": "QB", "team": "QBT",
                       "depth_chart_order": 2},
               **SLEEPER}
    f = pd.concat([frame(), pd.DataFrame([row("00-qb1", wk, "QBT", "QB", 1.0, attempts=24)
                                          for wk in (7, 8)])])
    ls = O.lines_by_player(f, through_week=THROUGH)
    gsis = {**GSIS, "qb1": "00-qb1", "qb2": "00-qb2"}
    vac = O.find_vacancies(sleeper, ls, through_week=THROUGH, gsis_of=gsis.get)
    got = O.opening_reasons([P("qb2", "QB", "QBT"), P("back2", "RB", "NOO")], sleeper, vac, ls)
    # 24 attempts is 1.2 QB roles; 18 touches is 2.25 RB roles
    assert got["back2"][1] > got["qb2"][1]


def watch(pool, *, previous=None, now=NOW, usage=True):
    return N.build_watch(
        pool, sleeper_players=SLEEPER, market_of=lambda p: None, prior={},
        prior_season=2025, drops={}, drops_note="", now=now, best=lineup(), slots=SLOTS,
        out_designations={"OUT", "IR"}, previous=previous,
        usage_frame=frame() if usage else None, through_week=THROUGH, gsis_of=GSIS.get)


POOL = [P("back2", "RB", "NOO"), P("vol", "WR", "CLE", 6.0), P("pt", "WR", "CLE")]


def test_the_watch_carries_both_misses_openings_first():
    w = watch(POOL)
    assert [(r.player.sleeper_id, r.kind) for r in w.work] == [("back2", "opening"),
                                                                ("vol", "volume")]
    assert not w.work_alerts, "no comparable previous record: nothing is NEW"
    rec = w.record()
    assert rec["work_total"] == 2 and rec["work"][1]["ours"] == 6.0
    assert "below your weakest eligible starter" in rec["work"][1]["versus"]
    # a player who will not play this week is never named
    out = watch([P("back2", "RB", "NOO", withheld=True), P("vol", "WR", "CLE", 6.0)])
    assert [r.player.sleeper_id for r in out.work] == ["vol"]


def test_a_new_row_alerts_for_48_hours_and_a_legacy_record_alerts_nothing():
    first = watch(POOL)
    rec = {"radar": {"pool": [{"id": "x"}]}, "watch": {**first.record(), "work_since": {}},
           "generated": (NOW - timedelta(hours=1)).isoformat()}
    w = watch(POOL, previous=N.PreviousWatch.from_record(rec))
    assert {r.key for r in w.work_alerts} == {"back2:opening", "vol:volume"}
    assert all(r.first_seen == NOW for r in w.work)
    later = NOW + timedelta(hours=N.ALERT_HOURS - 1)
    rec2 = {"radar": {"pool": [{"id": "x"}]}, "watch": w.record(),
            "generated": (later - timedelta(hours=1)).isoformat()}
    w2 = watch(POOL, previous=N.PreviousWatch.from_record(rec2), now=later)
    assert len(w2.work_alerts) == 2 and w2.work[0].first_seen == NOW
    gone = NOW + timedelta(hours=N.ALERT_HOURS + 1)
    rec3 = {"radar": {"pool": [{"id": "x"}]}, "watch": w2.record(),
            "generated": (gone - timedelta(hours=1)).isoformat()}
    w3 = watch(POOL, previous=N.PreviousWatch.from_record(rec3), now=gone)
    assert len(w3.work) == 2 and not w3.work_alerts, "listed, off the top"
    legacy = {"radar": {"pool": [{"id": "x"}]}, "watch": {"pool_since": {}},
              "generated": (NOW - timedelta(hours=1)).isoformat()}
    assert not watch(POOL, previous=N.PreviousWatch.from_record(legacy)).work_alerts


def test_the_page_and_the_review_render_the_list_or_say_unknown():
    w = watch(POOL, previous=N.PreviousWatch.from_record(
        {"radar": {"pool": [{"id": "x"}]}, "watch": {"work_since": {}},
         "generated": (NOW - timedelta(hours=1)).isoformat()}))
    d = SimpleNamespace(watch=w)
    page = _work_html(d)
    assert "OPENING" in page and "Invented back2" in page and "VOLUME" in page
    assert "Work just opened up" in _work_alert(d) and "#work" in _work_alert(d)
    blind = SimpleNamespace(watch=watch(POOL, usage=False))
    assert _work_alert(blind) == ""
    none = SimpleNamespace(watch=N.build_watch(
        POOL, sleeper_players=SLEEPER, market_of=lambda p: None, prior={}, prior_season=2025,
        drops={}, drops_note="", now=NOW, best=lineup(), slots=SLOTS,
        usage_frame=frame().iloc[0:0], through_week=THROUGH, gsis_of=GSIS.get))
    assert "UNKNOWN" in _work_html(none)
    assert any("no box scores" in n for n in none.watch.notes)

    spec = importlib.util.spec_from_file_location("weekly_review",
                                                  ROOT / "scripts/weekly/weekly_review.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["weekly_review"] = mod
    spec.loader.exec_module(mod)
    text = "\n".join(mod.notable_lines(w.record()))
    assert "Role openings and starter volume" in text and "NEW Invented back2" in text
    assert "Not in this record" in "\n".join(mod.notable_lines({"notable": []}))
