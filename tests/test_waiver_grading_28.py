"""How a week's advice is graded (owner decision 2026-11-18, Kejjeh/gridiron#28:
"Yes, all of them" to A + B + C and item 2).

Pins:
  A. a waiver pair is graded as the add against the starter he DISPLACES,
     signed by the board's `lineup_gain`, never against the drop; the key
     keeps the drop (the move the owner confirms); an add into an empty slot
     is graded against 0; an archive with no `displaces_id` is ungradeable;
  B. one counted comparison per (kind, slot) a week: the largest claimed
     gain, or the closest start/sit; the rest stay in the table, uncounted;
  C. the ledger splits the direction rate into start/sit and waiver columns,
     and the Report card shows them apart, with older pooled weeks labelled;
  2. a team-defense pair is graded from `DEF:<team>` actuals.
The week-9 shape is the issue's own numbers: a bench WR drop at 18.5, a
starting K at 16.0, seven K adds at 0-13, two WR adds at 2.6 and 4.0 against
a starter at 12.7, start/sit 2 of 3.
Invented ids.
"""
from __future__ import annotations

import csv
import importlib.util
import sys
from pathlib import Path

from gridiron import grading
from gridiron.dashboard import _report_html
from gridiron.decisions import EMPTY_SLOT, actual_key, grade_archive

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GW = _load("grade_week_28", "scripts/weekly/grade_week.py")


def p(sid, proj, pos="WR", team="KC"):
    return {"sleeper_id": sid, "gsis_id": f"g{sid}", "projected": proj, "position": pos,
            "team": team, "withheld": False}


def week9():
    """Start/sit 2 of 3; seven K adds over the starting K; two WR adds over a
    WR starter; the same bench WR is the drop in all nine."""
    roster = [p("k0", 9.0, "K"), p("wr0", 11.0), p("bench", 7.0),
              p("s1", 10.0), p("b1", 12.0), p("s2", 8.0), p("b2", 7.0), p("s3", 6.0),
              p("b3", 5.0)]
    alts = [{"slot": "RB", "starter_id": "s1", "bench_id": "b1"},
            {"slot": "TE", "starter_id": "s2", "bench_id": "b2"},
            {"slot": "QB", "starter_id": "s3", "bench_id": "b3"}]
    ups = [{"slot": "K", "drop_id": "bench", "displaces_id": "k0", "lineup_gain": g,
            "add": p(f"ka{i}", 9.0 + g, "K")}
           for i, g in enumerate((3.0, 2.5, 2.0, 1.5, 1.0, 0.8, 0.5))]
    ups += [{"slot": "WR", "drop_id": "bench", "displaces_id": "wr0", "lineup_gain": g,
             "add": p(f"wa{i}", 11.0 + g)} for i, g in enumerate((1.2, 0.6))]
    actuals = {"gk0": 16.0, "gwr0": 12.7, "gbench": 18.5,
               # start/sit: RB bench was right (b1 20 > s1 4), TE held was right
               # (s2 9 > b2 2), QB bench was projected worse and won (wrong)
               "gs1": 4.0, "gb1": 20.0, "gs2": 9.0, "gb2": 2.0, "gs3": 3.0, "gb3": 14.0,
               **{f"gka{i}": v for i, v in enumerate((0.0, 4.0, 13.0, 7.0, 9.0, 2.0, 5.0))},
               "gwa0": 2.6, "gwa1": 4.0}
    return ({"season": 2026, "week": 9, "roster": roster, "alternatives": alts,
             "upgrades": ups, "gate": {"lineup": {"allowed": True},
                                       "waiver": {"allowed": True}}}, actuals)


# ------------------------------------------------------------ A
def test_a_waiver_add_is_graded_against_the_starter_it_displaces_not_the_drop():
    arc, act = week9()
    g = grade_archive(arc, act)
    k = next(c for c in g.comparisons if c.kind == "waiver" and c.alternative_id == "ka0")
    assert k.held_id == "k0" and k.held_actual == 16.0        # the starting K, not the WR drop
    assert k.projected_edge == -3.0                          # the board's claim: +3 for the add
    assert k.realized_edge == 16.0 - 0.0                     # the starter won
    assert k.key == "waiver:K:bench:ka0"                     # the move the owner confirms


def test_an_add_into_an_empty_slot_is_graded_against_zero():
    arc = {"week": 9, "roster": [p("bench", 3.0)], "upgrades": [
        {"slot": "WR", "drop_id": "bench", "displaces_id": None, "lineup_gain": 6.0,
         "add": p("fa", 6.0)}]}
    [c] = grade_archive(arc, {"gfa": 4.0, "gbench": 30.0}).comparisons
    assert c.held_id == EMPTY_SLOT and c.held_actual == 0.0
    assert (c.projected_edge, c.realized_edge, c.status) == (-6.0, -4.0, "graded")


def test_an_archive_without_a_displaced_starter_is_ungradeable_never_the_drop():
    arc = {"week": 9, "roster": [p("bench", 3.0)], "upgrades": [
        {"slot": "WR", "drop_id": "bench", "add": p("fa", 6.0)}]}
    g = grade_archive(arc, {"gfa": 4.0, "gbench": 30.0})
    [c] = g.comparisons
    assert c.status == "ungradeable" and "displaced starter" in c.reason
    assert g.ungradeable == 1 and GW.direction(g) == (0, 0)


def test_a_displaced_starter_missing_from_the_roster_is_ungradeable():
    arc = {"week": 9, "roster": [p("bench", 3.0)], "upgrades": [
        {"slot": "WR", "drop_id": "bench", "displaces_id": "ghost", "lineup_gain": 1.0,
         "add": p("fa", 6.0)}]}
    [c] = grade_archive(arc, {"gfa": 4.0, "gbench": 30.0, "gghost": 1.0}).comparisons
    assert c.status == "ungradeable" and "not on the archived roster" in c.reason


# ------------------------------------------------------------ B
def test_one_counted_comparison_per_slot_and_the_rest_are_kept():
    arc, act = week9()
    g = grade_archive(arc, act)
    counted = [c for c in g.comparisons if c.primary]
    assert {(c.kind, c.slot) for c in counted} == {
        ("start_sit", "RB"), ("start_sit", "TE"), ("start_sit", "QB"),
        ("waiver", "K"), ("waiver", "WR")}
    assert next(c for c in counted if c.slot == "K").alternative_id == "ka0"   # +3.0, the top
    assert next(c for c in counted if c.slot == "WR").alternative_id == "wa0"  # +1.2
    assert len(g.comparisons) == 12                          # nothing dropped from the table


def test_the_week9_shape_comes_out_2_of_5_not_2_of_12():
    arc, act = week9()
    g = grade_archive(arc, act)
    assert GW.direction(g) == (2, 5)
    assert GW.direction(g, "start_sit") == (2, 3)
    assert GW.direction(g, "waiver") == (0, 2)
    # under A alone, every waiver pair still loses: no verdict flips
    assert all(c.realized_edge > 0 for c in g.comparisons if c.kind == "waiver")


def test_an_ungradeable_top_pick_leaves_the_slot_uncounted():
    arc, act = week9()
    act = {k: v for k, v in act.items() if k != "gka0"}      # the top K add has no line
    g = grade_archive(arc, act)
    assert GW.direction(g, "waiver") == (0, 1)              # WR only; K is not promoted


def test_start_sit_counts_the_closest_swap_in_a_slot():
    arc = {"week": 9, "roster": [p("s", 10.0), p("b1", 4.0), p("b2", 9.5)],
           "alternatives": [{"slot": "FLEX", "starter_id": "s", "bench_id": "b1"},
                            {"slot": "FLEX", "starter_id": "s", "bench_id": "b2"}]}
    g = grade_archive(arc, {"gs": 5.0, "gb1": 1.0, "gb2": 8.0})
    [top] = [c for c in g.comparisons if c.primary]
    assert top.alternative_id == "b2" and GW.direction(g) == (0, 1)


# ------------------------------------------------------------ C
def test_the_ledger_carries_split_columns_and_an_old_row_keeps_blanks(tmp_path):
    arc, act = week9()
    g = grade_archive(arc, act)
    ledger = tmp_path / "season2026.csv"
    old = {f: "" for f in GW.FIELDS if not f.startswith(("ss_", "wv_"))}
    old.update(season="2026", week="8", archive="old.json", graded="9", direction_n="9",
               direction_agree="2", scorable="0", agree="0", ungradeable="0",
               projection_mae="", projection_n="0")
    with ledger.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(old))
        w.writeheader()
        w.writerow(old)
    from datetime import datetime, timezone
    rows = GW.upsert(ledger, GW.aggregate(g, 2026, Path("w9.json"),
                                          datetime(2026, 11, 8, 17, tzinfo=timezone.utc)))
    new = next(r for r in rows if r["week"] == 9)
    assert (new["ss_direction_agree"], new["ss_direction_n"]) == (2, 3)
    assert (new["wv_direction_agree"], new["wv_direction_n"]) == (0, 2)
    assert (new["direction_agree"], new["direction_n"]) == (2, 5)
    legacy = next(r for r in rows if r["week"] == "8")
    assert legacy["ss_direction_n"] == "" and legacy["wv_direction_n"] == ""


def _row(week, d_agree, d_n, ss=None, wv=None):
    r = {f: "" for f in GW.FIELDS}
    r.update(season="2026", week=str(week), archive=f"w{week}.json",
             archive_built=f"2026-11-0{week % 9 + 1}T17:00:00+00:00", graded=str(d_n),
             ungradeable="0", agree="0", scorable="0", direction_agree=str(d_agree),
             direction_n=str(d_n), projection_n="0")
    if ss is not None:
        r.update(ss_direction_agree=str(ss[0]), ss_direction_n=str(ss[1]),
                 wv_direction_agree=str(wv[0]), wv_direction_n=str(wv[1]))
    return r


def test_the_report_card_shows_start_sit_and_waiver_apart_and_labels_pooled_weeks():
    card = grading.report_card([_row(7, 8, 24), _row(9, 2, 5, ss=(2, 3), wv=(0, 2)),
                                _row(10, 4, 4, ss=(4, 4), wv=(0, 0))], 2026)
    t = card.totals()
    assert (t["ss_agree"], t["ss_n"], t["wv_agree"], t["wv_n"]) == (6, 7, 0, 2)
    assert (t["pooled_agree"], t["pooled_n"], t["pooled_weeks"]) == (8, 24, (7,))
    html = "".join(_report_html(card))
    assert "start/sit direction" in html and "6 of 7 calls" in html
    assert "waiver direction" in html and "0 of 2 calls" in html
    assert "displaced" in html                               # what the waiver rate measures
    assert "week 7" in html and "pooled" in html and "8 of 24" in html


def test_a_ledger_with_only_pooled_weeks_reads_as_before():
    card = grading.report_card([_row(7, 8, 24)], 2026)
    html = "".join(_report_html(card))
    assert "projection direction" in html and "8 of 24 calls" in html
    assert "waiver direction" not in html


# ------------------------------------------------------------ 2
def test_a_team_defense_pair_is_graded_from_its_team_key():
    arc = {"week": 9, "roster": [p("DEF-KC", 7.0, "DEF", "KC"), p("bench", 3.0)],
           "upgrades": [{"slot": "DEF", "drop_id": "bench", "displaces_id": "DEF-KC",
                         "lineup_gain": 2.0,
                         "add": {"sleeper_id": "BUF", "position": "DEF", "team": "BUF",
                                 "projected": 9.0}}]}
    assert actual_key({"position": "DEF", "team": "BUF"}) == "DEF:BUF"
    [c] = grade_archive(arc, {"DEF:KC": 11.0, "DEF:BUF": 4.0}).comparisons
    assert (c.status, c.realized_edge) == ("graded", 7.0)
    assert GW.actual_key is actual_key                       # one implementation


# ------------------------------------------------------------ review follow-ups
def test_a_tied_start_sit_never_takes_the_slot_from_a_real_call():
    arc = {"week": 9, "roster": [p("s", 10.0), p("b0", 10.0), p("b1", 8.0)],
           "alternatives": [{"slot": "WR", "starter_id": "s", "bench_id": "b0"},
                            {"slot": "WR", "starter_id": "s", "bench_id": "b1"}]}
    g = grade_archive(arc, {"gs": 5.0, "gb0": 1.0, "gb1": 8.0})
    [top] = [c for c in g.comparisons if c.primary]
    assert top.alternative_id == "b1" and GW.direction(g) == (0, 1)


def test_a_no_drop_add_is_keyed_nodrop_and_its_acted_key_matches():
    from gridiron.decisions import ACTED
    arc = {"week": 9, "roster": [p("s", 5.0)], "upgrades": [
        {"slot": "WR", "drop_id": None, "displaces_id": "s", "lineup_gain": 2.0,
         "add": p("fa", 7.0)}]}
    [c] = grade_archive(arc, {"gs": 3.0, "gfa": 9.0},
                        observed={"waiver:WR:nodrop:fa": ACTED}).comparisons
    assert c.key == "waiver:WR:nodrop:fa" and c.owner_action == ACTED


def test_the_season_line_never_adds_pooled_weeks_to_split_ones():
    line = GW.direction_line([_row(8, 2, 12), _row(9, 2, 5, ss=(2, 3), wv=(0, 2))])
    assert "start/sit 2/3" in line and "waiver vs the displaced starter 0/2" in line
    assert "2/12" in line and "before #28" in line and "4/17" not in line
    assert GW.direction_line([_row(8, 2, 12)]).startswith("projection direction 2/12")
