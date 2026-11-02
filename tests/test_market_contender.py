"""The record-only `market_v1` contender: on the board and in the weekly grade.

Pins:
  * the board records market_v1 only from a derived file for its own season
    and week, only from a snapshot taken before the player's kickoff, and
    NOTHING on the page changes because of it (no projection, rank, gate or
    action reads it);
  * the grade's main shoot-out is unchanged by its presence; the market is
    scored in its own pass on the players it covers, with the page and
    Sleeper on exactly those players;
  * the ledger stays backward compatible: rows written before these columns
    existed (the committed season2026.csv) still load, upsert and add up.
"""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import sys
from datetime import timedelta
from pathlib import Path

from gridiron import grading
from gridiron import props as P
from gridiron.decisions import grade_archive

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


GW = _load("market_grade_week", "scripts/weekly/grade_week.py")


# ------------------------------------------------------------------ the board

def test_the_board_records_the_market_and_its_page_does_not_change(tmp_path, capsys):
    scn = _load("market_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("market_dash", "scripts/weekly/dashboard.py")
    root = tmp_path / "cache"
    scn.build_scenario(root, "complete", now=scn.NOW)

    def render(tag):
        out = tmp_path / f"out_{tag}"
        rc = cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                       "--out-dir", str(out), "--archive-root", str(tmp_path / f"arch_{tag}"),
                       "--now", scn.NOW.isoformat(), "--no-archive"])
        assert rc == 0
        return json.loads((out / "dashboard_latest.json").read_text("utf-8"))

    plain = render("plain")
    assert "market_v1" not in (plain["contenders"] or {})
    # the ids come from the fixture's own record: no real id is written here
    usable = [p for p in plain["roster"] if p["gsis_id"] and p["projected"] is not None
              and not p["withheld"] and p["position"] in ("QB", "RB", "WR", "TE")]
    qb = next(p for p in usable if p["position"] == "QB")
    catchers = [p for p in usable if p["position"] in ("WR", "TE")][:2]
    late = next(p for p in usable if p["position"] == "RB")
    before, after = scn.NOW - timedelta(hours=1), scn.NOW + timedelta(days=3)

    def game(taken, players):
        return {"taken_at": taken.isoformat(), "kickoff": None, "lead_minutes": None,
                "teams": [], "books": 3, "markets_returned": [], "unmatched": 0,
                "ambiguous": 0, "abstained": {}, "skipped": {}, "params_fitted": "",
                "players": players}
    blob = P.empty_week(2026, 3)
    blob["games"]["g_early"] = game(before, {
        qb["gsis_id"]: {"team": qb["team"], "basis": "exact", "abstained": {},
                        "stats": {"passing_yards": 251.0, "passing_tds": 1.7,
                                  "rushing_yards": 12.0, "scrimmage_tds": 0.15},
                        "books": {"passing_yards": 3}},
        **{c["gsis_id"]: {"team": c["team"], "basis": "reviewed", "abstained": {},
                          "stats": {"receptions": 5.2, "receiving_yards": 63.0,
                                    "scrimmage_tds": 0.38},
                          "books": {"receptions": 3, "receiving_yards": 3}} for c in catchers}})
    blob["games"]["g_late"] = game(after, {     # taken after his kickoff: never used
        late["gsis_id"]: {"team": late["team"], "basis": "exact", "abstained": {},
                          "stats": {"rushing_yards": 70.0, "scrimmage_tds": 0.5},
                          "books": {"rushing_yards": 3}}})
    path = P.derived_path(root / "props" / "derived", 2026, 3)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(blob), encoding="utf-8")
    capsys.readouterr()

    marketed = render("market")
    log = capsys.readouterr().out
    cont = marketed["contenders"]
    assert set(cont["market_v1"]) == {qb["sleeper_id"], *(c["sleeper_id"] for c in catchers)}
    assert late["sleeper_id"] not in cont["market_v1"]
    detail = cont["market_v1_detail"]
    assert detail["abstained"] == {"snapshot not taken before his kickoff": 1}
    assert detail["identity_basis"] == {"exact": 1, "reviewed": len(catchers)}
    assert all(v > 0 for v in cont["market_v1"].values())
    # the page is exactly the page it was
    for key in ("roster", "best_lineup", "current_lineup", "actions", "upgrades", "watchlist",
                "gate", "degraded", "matchup", "radar", "notes", "best_points"):
        assert plain[key] == marketed[key], f"the market changed {key}"
    for k in ("baseline_v1", "stack", "adv_v1"):
        assert plain["contenders"].get(k) == cont.get(k)
    assert "market contender (record-only): 3 player(s) from 2 game snapshot(s)" in log
    for p in plain["roster"]:
        assert p["name"] not in log, "the stdout summary named a player"


def test_a_derived_file_for_another_week_is_no_file(tmp_path):
    scn = _load("market_scn2", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("market_dash2", "scripts/weekly/dashboard.py")
    root = tmp_path / "cache"
    scn.build_scenario(root, "complete", now=scn.NOW)
    blob = P.empty_week(2026, 4)                      # the board renders week 3
    path = P.derived_path(root / "props" / "derived", 2026, 3)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(blob), encoding="utf-8")
    out = tmp_path / "out"
    assert cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
                     "--out-dir", str(out), "--now", scn.NOW.isoformat(), "--no-archive"]) == 0
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    assert "market_v1" not in (rec["contenders"] or {})


# ------------------------------------------------------------------ the grade

def _p(sid, pos, proj, gid=None):
    return {"sleeper_id": sid, "id": sid, "gsis_id": gid or f"g{sid}", "position": pos,
            "projected": proj, "withheld": False}


def _archive():
    return {
        "week": 9,
        "roster": [_p("1", "WR", 12.0), _p("2", "WR", 8.0), _p("3", "WR", 10.0),
                   _p("4", "RB", 9.0), _p("k1", "K", 9.0), _p("k2", "K", 7.0)],
        "radar": {"candidates": [_p("5", "WR", 6.0)]},
        "shadow": {"players": {s: {"points": pts, "pre_kickoff": True} for s, pts in
                               (("1", 9.0), ("2", 11.0), ("3", 10.0), ("4", 9.0), ("5", 7.0),
                                ("k1", 8.0), ("k2", 8.0))}},
        "contenders": {"baseline_v1": {"1": 10.0, "2": 11.0, "3": 9.0, "4": 9.0, "5": 6.5,
                                       "k1": 8.0, "k2": 8.5},
                       "stack": {"1": 8.0, "2": 12.0, "3": 9.5, "4": 9.0, "5": 6.0,
                                 "k1": 9.5, "k2": 7.5},
                       # the market covers three WRs and one kicker pair
                       "market_v1": {"1": 7.0, "2": 13.0, "3": 9.0, "k1": 9.0, "k2": 7.0},
                       "market_v1_detail": {"projected": 5}}}


ACTUALS = {"g1": 5.0, "g2": 15.0, "g3": 8.0, "g4": 9.0, "g5": 20.0, "gk1": 12.0, "gk2": 3.0}


def test_the_main_shoot_out_is_unchanged_by_the_market():
    with_market = _archive()
    without = _archive()
    del without["contenders"]["market_v1"]
    a, b = GW.shootout(with_market, ACTUALS), GW.shootout(without, ACTUALS)
    assert a == b and "market_v1" not in a["systems"] and a["n"] == 5
    assert GW.special_shootouts(with_market, ACTUALS) == GW.special_shootouts(without, ACTUALS)


def test_the_market_is_scored_on_the_players_it_covers_beside_the_page_and_sleeper():
    m = GW.market_shootouts(_archive(), ACTUALS)
    skill = m["skill"]
    assert skill["n"] == 3 and skill["systems"][-1] == "market_v1"
    # WR pairs among 1, 2, 3 (actuals 5, 15, 8): the market (7, 13, 9) orders all
    # three the way the points did, the page (12, 8, 10) none of them
    assert skill["pairs"] == 3 and skill["market_v1_pairwise"] == 1.0
    assert skill["page_pairwise"] == 0.0
    assert skill["market_v1_mae"] == round((2 + 2 + 1) / 3, 3)
    assert m["k"]["n"] == 2 and m["k"]["market_v1_pairwise"] == 1.0
    assert GW.market_shootouts({**_archive(), "contenders": {}}, ACTUALS) == {}
    row = GW.aggregate(grade_archive({"week": 9}, {}), 2026, Path("week09_x.json"),
                       GW._time("2026-11-08T17:00:00Z"), GW.shootout(_archive(), ACTUALS),
                       GW.special_shootouts(_archive(), ACTUALS), None, m)
    assert set(row) == set(GW.FIELDS)
    assert row["market_v1_pairwise"] == 1.0 and row["mkt_pairs"] == 3
    assert row["mkt_page_pairwise"] == 0.0 and row["mkt_n"] == 3
    assert row["k_mkt_pairs"] == 1 and row["k_mkt_market_v1_pairwise"] == 1.0
    assert row["k_pairs"] == 1                       # the K grade itself is untouched
    line = GW.shootout_line([{**{k: "" for k in GW.FIELDS}, **row}])
    assert "market_v1 (record-only) on the 3 pairs it covers in 1 week(s): market_v1 100.0%" in line
    assert "market_v1 100" not in line.split(";")[1]  # never mixed into the main clause


def test_old_ledger_rows_load_and_upsert_beside_the_new_columns(tmp_path):
    committed = ROOT / "data" / "ledger" / "grades" / "season2026.csv"
    assert grading.read_ledger(committed), "the committed ledger must still read"
    with committed.open(encoding="utf-8", newline="") as fh:
        header = next(csv.reader(fh))
    assert "market_v1_pairwise" not in header       # written before the columns existed
    ledger = tmp_path / "season2026.csv"
    shutil.copyfile(committed, ledger)
    old = grading.read_ledger(ledger)
    m = GW.market_shootouts(_archive(), ACTUALS)
    row = GW.aggregate(grade_archive({"week": 9}, {}), 2026, Path("week09_x.json"),
                       GW._time("2026-11-08T17:00:00Z"), GW.shootout(_archive(), ACTUALS),
                       GW.special_shootouts(_archive(), ACTUALS), None, m)
    rows = GW.upsert(ledger, row)
    assert len(rows) == len(old) + 1
    reread = grading.read_ledger(ledger)
    by_week = {r["week"]: r for r in reread}
    for r in old:                                  # every old value kept, new columns blank
        kept = by_week[r["week"]]
        assert all(kept[k] == v for k, v in r.items())
        assert kept["market_v1_pairwise"] == "" and kept["mkt_pairs"] == ""
    assert by_week["9"]["market_v1_pairwise"] == "1.0"
    assert grading.report_card(reread, 2026).any
    assert "market_v1 (record-only)" in GW.shootout_line(reread)
    # an old row merged from a carried ledger keeps its values too
    carried = tmp_path / "carried.csv"
    shutil.copyfile(committed, carried)
    fresh = tmp_path / "fresh.csv"
    assert GW.merge_ledger(fresh, grading.read_ledger(carried)) == len(old)
