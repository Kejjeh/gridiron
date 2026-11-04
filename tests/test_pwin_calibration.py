"""P(win) calibration: the tested spread factor, its label, the league-matchup
log on the board, and the weekly grade of it. Synthetic numbers only."""
from __future__ import annotations

import importlib.util
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from gridiron import dashboard as D
from gridiron import grading
from gridiron import winprob as W
from gridiron.lineup import Player
from gridiron.projection import Projection, abstain

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _cal_file(tmp_path, **over):
    blob = {"kind": "sd_scale", "k": 1.2, "bar_cleared": True, "doc": "docs/research/PWIN_CALIBRATION.md",
            "synthetic": {"n_fit": 1000, "fit_seasons": [2019, 2022], "n_test": 800,
                          "test_seasons": [2023, 2025], "test_brier": 0.231, "test_brier_raw": 0.235},
            "league_history": {"n": 150, "seasons": [2023, 2025], "brier": 0.22, "brier_raw": 0.23}}
    blob.update(over)
    p = tmp_path / "cal.json"
    p.write_text(json.dumps(blob), encoding="utf-8")
    return p


def test_the_factor_widens_the_margin_spread_and_nothing_else():
    c = W.Calibration(k=1.2, validated=True)
    assert c.win_prob(110, 20, 100, 21) == pytest.approx(W.matchup_win_prob(110, 24, 100, 25.2))
    assert 0.5 < c.win_prob(110, 20, 100, 21) < W.matchup_win_prob(110, 20, 100, 21)
    assert c.win_prob(100, 20, 100, 21) == pytest.approx(0.5)            # even stays even
    assert c.leverage(110, 20, 100, 21) == pytest.approx(W.leverage_per_point(110, 24, 100, 25.2))
    assert W.RAW.win_prob(110, 20, 100, 21) == W.matchup_win_prob(110, 20, 100, 21)


def test_only_a_file_that_cleared_the_bar_is_used(tmp_path):
    good = W.load_calibration(_cal_file(tmp_path))
    assert good.validated and good.k == 1.2
    assert good.label.startswith("CALIBRATED") and "1.20" in good.label and "150" in good.label
    assert good.short == "calibrated, SD x1.20"
    assert W.load_calibration(tmp_path / "absent.json") is W.RAW
    assert "UNCALIBRATED" in W.RAW.label
    not_cleared = W.load_calibration(_cal_file(tmp_path, bar_cleared=False))
    assert not not_cleared.validated and "did not beat" in not_cleared.label
    for bad in ({"k": 7.0}, {"k": "x"}, {"kind": "isotonic"}):
        c = W.load_calibration(_cal_file(tmp_path, **bad))
        assert not c.validated and c.label.startswith("UNCALIBRATED")
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert not W.load_calibration(broken).validated


def _raw_tested_file(tmp_path, **over):
    lg = {"n": 157, "seasons": [2023, 2025], "fav_stated": 0.62, "fav_won": 0.66}
    syn = {"n_fit": 1000, "fit_seasons": [2019, 2022], "n_test": 800, "test_seasons": [2023, 2025],
           "test_logloss": 0.667, "test_logloss_raw": 0.666, "test_brier": 0.237,
           "test_brier_raw": 0.2367}
    blob = {"k": 1.0, "k_tested": 1.03, "bar_cleared": False, "raw_tested": True,
            "synthetic": syn, "league_history": lg}
    return _cal_file(tmp_path, **(blob | over))


def test_a_test_that_found_no_factor_labels_the_raw_formula_tested(tmp_path):
    c = W.load_calibration(_raw_tested_file(tmp_path))
    assert c.validated and c.k == 1.0 and not c.widens
    assert c.badge == "TESTED" and c.short == "tested, no correction"
    assert c.label.startswith("TESTED") and "no correction" in c.label
    assert "157 real matchups" in c.label and "given 62%" in c.label and "won 66%" in c.label
    assert "sampling noise ±4 points" in c.label          # sqrt(.66 * .34 / 157)
    assert "0.6660 as is vs 0.6670 with the factor" in c.label
    # the raw closed form, exactly
    assert c.win_prob(110, 20, 100, 21) == W.matchup_win_prob(110, 20, 100, 21)
    assert c.record()["badge"] == "TESTED" and c.record()["short"] == "tested, no correction"
    # "no correction" with a factor other than 1 is a broken file, not a test result
    odd = W.load_calibration(_raw_tested_file(tmp_path, k=1.1))
    assert not odd.validated and "k = 1.1" in odd.label
    # a cleared factor still wins over raw_tested
    both = W.load_calibration(_cal_file(tmp_path, raw_tested=True))
    assert both.widens and both.badge == "CALIBRATED"
    assert W.RAW.badge == "UNCALIBRATED" and not W.RAW.widens


def test_the_shipped_calibration_is_evidence_backed_when_present():
    shipped = ROOT / "src/gridiron/models/pwin_calibration.json"
    if not shipped.exists():
        pytest.skip("no calibration shipped")
    c = W.load_calibration(shipped)
    blob = json.loads(shipped.read_text(encoding="utf-8"))
    assert c.validated == bool(blob.get("bar_cleared") or blob.get("raw_tested"))
    syn, lg = blob["synthetic"], blob.get("league_history") or {}
    if c.validated:
        assert set(syn["fit_seasons"]).isdisjoint(syn["test_seasons"])
    if c.widens:
        # rule #5: better than the raw formula out of sample, on both scores
        assert syn["test_brier"] < syn["test_brier_raw"]
        assert syn["test_logloss"] < syn["test_logloss_raw"]
    elif c.validated:
        # tested, no factor: the factor did not beat raw on both held-out
        # scores, and the raw spread held on its own (the saver's rule)
        assert c.k == 1.0 and not blob["bar_cleared"]
        assert not (syn["test_brier"] < syn["test_brier_raw"]
                    and syn["test_logloss"] < syn["test_logloss_raw"])
        assert 0.9 <= syn["test_margin_z_sd"] <= 1.1 and 0.9 <= lg["margin_z_sd"] <= 1.1
        pooled = lg["loso_pooled"]
        assert pooled["logloss_raw"] <= pooled["logloss_k"] and pooled["brier_raw"] <= pooled["brier_k"]
    assert (ROOT / blob["doc"]).is_file()
    for script in blob.get("scripts", []):
        assert (ROOT / script).is_file()


# ---------------------------------------------------------------- the board

def _p(sid, pos, mean, sd, team="AAA", gid=None):
    proj = Projection(mean, sd, (), (), {}) if mean is not None else abstain("no evidence")
    return Player(sid, f"Player {sid}", pos, team, proj, "START", False, "", "", (),
                  gid if gid is not None else f"g{sid}", True, None)


SLOTS = ("QB", "RB", "WR", "DEF")


def test_a_calibrated_matchup_keeps_the_raw_number_and_says_which_it_is(tmp_path):
    cal = W.load_calibration(_cal_file(tmp_path))
    mine = (_p("1", "QB", 20, 8), _p("2", "RB", 15, 6), _p("3", "WR", 12, 5), _p("PIT", "DEF", 8, 6))
    theirs = (_p("4", "QB", 18, 8), _p("5", "RB", 12, 6), _p("6", "WR", 11, 5), _p("KC", "DEF", 7, 6))
    m = D._matchup(9, mine, theirs, cal)
    assert m.pwin_raw == round(W.matchup_win_prob(m.my_mean, m.my_sd, m.opp_mean, m.opp_sd), 4)
    assert m.pwin == round(cal.win_prob(m.my_mean, m.my_sd, m.opp_mean, m.opp_sd), 4)
    assert 0.5 < m.pwin < m.pwin_raw
    assert m.label.startswith("CALIBRATED") and "widened x1.20" in m.pwin_reason
    raw = D._matchup(9, mine, theirs)
    assert raw.pwin == raw.pwin_raw and raw.label.startswith("UNCALIBRATED")
    tested = D._matchup(9, mine, theirs, W.load_calibration(_raw_tested_file(tmp_path)))
    assert tested.pwin == tested.pwin_raw == raw.pwin and tested.label.startswith("TESTED")
    assert "no spread correction needed" in tested.pwin_reason and "widened" not in tested.pwin_reason


def test_every_league_matchup_is_recorded_with_keys_the_grader_can_score(tmp_path):
    cal = W.load_calibration(_cal_file(tmp_path))
    players = {"1": _p("1", "QB", 20, 8), "2": _p("2", "RB", 15, 6), "3": _p("3", "WR", 12, 5),
               "4": _p("4", "QB", 18, 8), "5": _p("5", "RB", 12, 6), "6": _p("6", "WR", 11, 5),
               "7": _p("7", "QB", 16, 8), "8": _p("8", "RB", None, None),
               "9": _p("9", "QB", 17, 8), "10": _p("10", "RB", 9, 6),
               "PIT": _p("PIT", "DEF", 8, 6, team="PIT", gid="")}
    make = lambda sid, lineup: players[sid]                      # noqa: E731
    matchups = [
        {"roster_id": 2, "matchup_id": 1, "starters": ["4", "5", "6", "0"]},
        {"roster_id": 1, "matchup_id": 1, "starters": ["1", "2", "3", "PIT"]},
        {"roster_id": 3, "matchup_id": 2, "starters": ["7", "8"]},      # an unprojected RB
        {"roster_id": 4, "matchup_id": 2, "starters": ["9", "10"]},
        {"roster_id": 5, "matchup_id": 3, "starters": ["1"]},           # no opponent row
    ]
    mine_view = D._matchup(2, tuple(players[s] for s in ("1", "2", "3", "PIT")),
                           (players["4"], players["5"], players["6"], None), cal)
    out = D._league_matchups(matchups, [], SLOTS, make, cal, mine_id=1, mine_view=mine_view)
    assert [x["matchup_id"] for x in out] == [1, 2]                    # the lone row is skipped
    a = out[0]
    assert a["roster_ids"] == [1, 2]                                  # sides ordered by roster id
    assert a["pwin"] == mine_view.pwin and a["pwin_raw"] == mine_view.pwin_raw
    assert a["means"] == [round(mine_view.my_mean, 3), round(mine_view.opp_mean, 3)]
    keys = [s["key"] for s in a["starters"][0]]
    assert keys == ["g1", "g2", "g3", "DEF:PIT"] and len(a["starters"][1]) == 3   # empty slot dropped
    b = out[1]
    assert b["pwin"] is None and "ABSTAINED" in b["reason"]           # rule #11: no number, said why
    # the owner's view is flipped, never recomputed, when the owner is the second side
    flipped = D._league_matchups([{"roster_id": 9, "matchup_id": 7, "starters": ["4"]},
                                  {"roster_id": 1, "matchup_id": 7, "starters": ["1"]}],
                                 [], SLOTS, make, cal, mine_id=9, mine_view=mine_view)[0]
    assert flipped["roster_ids"] == [1, 9]
    assert flipped["pwin"] == round(1 - mine_view.pwin, 4)
    assert flipped["means"] == [round(mine_view.opp_mean, 3), round(mine_view.my_mean, 3)]


# ---------------------------------------------------------------- the grade

def _gw():
    spec = importlib.util.spec_from_file_location("gw_pwin", ROOT / "scripts/weekly/grade_week.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["gw_pwin"] = mod
    spec.loader.exec_module(mod)
    return mod


def _side(*keys):
    return [{"id": k, "key": k, "position": "WR", "projected": 10.0} for k in keys]


def test_the_grader_scores_every_recorded_matchup_against_actual_points():
    gw = _gw()
    archive = {"league_matchups": [
        {"pwin": 0.7, "pwin_raw": 0.8, "starters": [_side("a1", "a2"), _side("b1", "b2")]},   # side 0 wins
        {"pwin": 0.6, "pwin_raw": 0.65, "starters": [_side("c1"), _side("d1")]},               # side 0 loses
        {"pwin": None, "pwin_raw": None, "starters": [_side("e1"), _side("f1")]},              # abstained
        {"pwin": 0.5, "pwin_raw": 0.5, "starters": [_side("g1"), [{"id": "x", "key": ""}]]},  # unresolved
        {"pwin": 0.55, "pwin_raw": 0.6, "starters": [_side("h1"), _side("i1")]},               # tie
    ]}
    actuals = {"a1": 20.0, "a2": 5.0, "b1": 10.0, "c1": 3.0, "d1": 9.0, "h1": 7.0, "i1": 7.0}
    out = gw.league_pwin(archive, actuals)            # b2 has no stat line: scored 0
    assert out["pwin_n"] == 2 and out["skipped"] == 3
    assert out["pwin_brier"] == round(((0.7 - 1) ** 2 + (0.6 - 0) ** 2) / 2, 4)
    assert out["pwin_raw_brier"] == round(((0.8 - 1) ** 2 + (0.65 - 0) ** 2) / 2, 4)
    assert out["pwin_logloss"] == round(-(math.log(0.7) + math.log(0.4)) / 2, 4)
    assert out["pwin_fav_won"] == 1 and out["pwin_fav_stated"] == round((0.7 + 0.6) / 2, 4)
    assert gw.league_pwin({"roster": []}, actuals) is None
    row = gw.aggregate(__import__("gridiron.decisions", fromlist=["x"]).grade_archive({"week": 6}, {}),
                       2026, Path("week06_20261018T120000Z.json"),
                       datetime(2026, 10, 18, 12, tzinfo=UTC), None, None, out)
    assert row["pwin_n"] == 2 and row["pwin_brier"] == out["pwin_brier"]
    assert all(f in gw.FIELDS for f in gw.PWIN_FIELDS)


def test_the_report_card_weights_each_week_by_its_matchups():
    rows = [{"season": "2026", "week": "6", "archive": "a", "pwin_n": "6", "pwin_brier": "0.20",
             "pwin_logloss": "0.60", "pwin_raw_brier": "0.22", "pwin_raw_logloss": "0.65",
             "pwin_fav_stated": "0.60", "pwin_fav_won": "4"},
            {"season": "2026", "week": "7", "archive": "b", "pwin_n": "2", "pwin_brier": "0.28",
             "pwin_logloss": "0.70", "pwin_raw_brier": "0.30", "pwin_raw_logloss": "0.80",
             "pwin_fav_stated": "0.70", "pwin_fav_won": "1"},
            {"season": "2026", "week": "5", "archive": "c", "pwin_n": ""}]
    t = grading.pwin_totals(rows)
    assert t["n"] == 8 and t["fav_won"] == 5
    assert t["brier"] == round((0.20 * 6 + 0.28 * 2) / 8, 4)
    assert t["fav_stated"] == round((0.60 * 6 + 0.70 * 2) / 8, 4)
    assert grading.pwin_totals([{"pwin_n": ""}]) == {}
