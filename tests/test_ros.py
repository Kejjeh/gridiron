"""Rest-of-season rankings (`gridiron.ros`, scripts/weekly/ros_rankings.py).
Synthetic data only. Pins: team strength reads only earlier FINAL scores and
is shrunk; a team absent from a week is on bye; a future week is estimated
the same way as the next one; an Out week is zero for that week only; the
schedule nudge moves the way the weekly model's coefficient says; ownership
joins through ids and never publishes other managers' names."""
from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pandas as pd
import pytest

from gridiron import ros as R
from gridiron.ids import Crosswalk
from gridiron.models import advanced as A

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _schedule(last=17, bye=("CCC", 6)):
    rows = []
    for w in range(1, last + 1):
        pairs = [("AAA", "BBB"), ("CCC", "DDD")] if w % 2 else [("AAA", "CCC"), ("BBB", "DDD")]
        for home, away in pairs:
            if bye and w == bye[1] and bye[0] in (home, away):
                continue
            played = w < 4
            rows.append({"week": w, "game_type": "REG", "home_team": home, "away_team": away,
                         "home_score": (30.0 if home == "AAA" else 20.0) if played else None,
                         "away_score": 14.0 if played else None,
                         "roof": "dome" if home == "AAA" else "outdoors"})
    return pd.DataFrame(rows)


def test_team_strength_reads_only_earlier_final_scores_and_shrinks():
    s = R.team_strength(_schedule(), 4)
    # 6 games before week 4; AAA is at home in all three and scored 30 each
    assert s.league == pytest.approx((30 + 14 + 20 + 14) * 3 / 12)
    raw = 30 - s.league
    assert s.offense["AAA"] == pytest.approx(raw * 3 / (3 + R.SHRINK_GAMES))
    assert R.team_strength(_schedule(), 1).offense == {}          # nothing before week 1
    assert R.team_strength(None, 5).league == 22.0


def test_a_week_is_estimated_never_read_from_a_posted_line_and_absence_is_a_bye():
    sched = _schedule()
    sched["spread_line"] = 99.0                                  # must be ignored
    st = R.team_strength(sched, 4)
    ctx = R.week_context(sched, 5, st)
    a = ctx["AAA"]
    assert a["opponent"] == "BBB" and a["home"] == 1.0 and a["dome"] == 1.0
    assert a["spread"] == pytest.approx(a["implied"] - a["opp_implied"])
    assert a["implied"] - ctx["BBB"]["implied"] == pytest.approx(
        st.offense["AAA"] + st.defense["BBB"] - st.offense["BBB"] - st.defense["AAA"]
        + 2 * R.HOME_EDGE)
    assert "CCC" not in R.week_context(sched, 6, st)             # CCC's bye


def test_flat_ros_counts_games_skips_byes_and_zeroes_only_an_out_week():
    S = R.Schedule(_schedule(), 4)
    line = S.project("WR", "CCC", 10.0, adjust=False)
    assert line.byes == (6,) and line.games == 13                # weeks 4-17 less the bye
    assert line.ros == pytest.approx(130.0)
    assert line.playoff == pytest.approx(30.0)                   # weeks 15-17
    out = S.project("WR", "CCC", 10.0, adjust=False, skip=(4,))
    assert out.ros == pytest.approx(120.0) and out.games == 12   # week 4 only
    assert S.project("RB", "AAA", -3.0, adjust=False).ros == 0.0  # clipped
    assert S.project("DEF", "AAA", -1.0, adjust=False, clip=False).ros < 0


def test_a_week_the_schedule_does_not_cover_is_never_a_bye():
    S = R.Schedule(_schedule(last=8), 4)
    assert S.uncovered == tuple(range(9, 18))
    line = S.project("WR", "CCC", 10.0, adjust=False)
    assert line.byes == (6,)                                     # only the declared one
    assert line.games == 13 and line.ros == pytest.approx(130.0)
    assert dict((v, o) for v, o, _ in line.weeks)[12] == "?"
    assert R.team_strength(_schedule().drop(columns=["home_score"]), 4).offense == {}


def test_the_schedule_nudge_follows_the_weekly_models_coefficient():
    sched = _schedule()
    hist = pd.DataFrame([{"week": w, "opponent_team": opp, "position": "WR",
                          "league_points": pts}
                         for w in (1, 2, 3)
                         for opp, pts in (("AAA", 5.0), ("BBB", 15.0), ("CCC", 10.0),
                                          ("DDD", 10.0))])
    S = R.Schedule(sched, 4, hist)
    assert S.allowed[("BBB", "WR")] == pytest.approx(5.0)       # 15 vs league 10
    ridge = A.Ridge(("baseline", "def_allowed"), (10.0, 0.0), (10.0, 0.0), (1.0, 2.0),
                    (10.0, 1.0, 1.0))                            # +0.5 pt per pt allowed
    assert R.slopes(ridge, "WR") == {"implied": 0.0, "def_allowed": 0.5}
    flat = S.project("WR", "AAA", 10.0, adjust=False)
    nudged = S.project("WR", "AAA", 10.0, ridge=ridge, adjust=True)
    by_week = {v: p for v, _, p in nudged.weeks}
    # week 4 is the reference; AAA meets soft BBB in odd weeks, CCC in even
    assert by_week[4] == pytest.approx(10.0)
    assert by_week[5] - by_week[4] == pytest.approx(0.5 * (5.0 - 0.0))
    assert nudged.ros != flat.ros
    none = S.project("WR", "AAA", 10.0, ridge=None, adjust=True)
    assert none.ros == flat.ros                                  # no model, no nudge


def test_rank_is_within_position():
    t = pd.DataFrame({"position": ["WR", "WR", "RB"], "ros": [5.0, 9.0, 1.0]})
    r = R.rank(t)
    assert r.loc[r["ros"] == 9.0, "pos_rank"].item() == 1
    assert r.loc[r["position"] == "RB", "pos_rank"].item() == 1


def test_ownership_joins_through_ids_and_hides_other_managers_by_default():
    table = pd.DataFrame([
        {"gsis_id": "g1", "position": "WR", "team": "AAA", "ros": 1.0},
        {"gsis_id": "g2", "position": "RB", "team": "BBB", "ros": 1.0},
        {"gsis_id": "g3", "position": "TE", "team": "BBB", "ros": 1.0},
        {"gsis_id": "DEF:LA", "position": "DEF", "team": "LA", "ros": 1.0},
        {"gsis_id": "g9", "position": "QB", "team": "CCC", "ros": 1.0}])
    players = {"11": {"full_name": "Pat Example", "injury_status": "Questionable"},
               "12": {"full_name": "Sam Sample"}, "13": {"full_name": "Lee Fixture"},
               "LAR": {"position": "DEF", "team": "LAR"}}
    cw = Crosswalk({"11": "g1", "12": "g2", "13": "g3"}, {})
    snap = {"users": [{"user_id": "u1", "display_name": "Me"},
                      {"user_id": "u2", "display_name": "Rival"}],
            "rosters": [{"owner_id": "u1", "roster_id": 1, "players": ["11", "LAR"]},
                        {"owner_id": "u2", "roster_id": 2, "players": ["12"]}]}
    out = R.ownership(table, snapshot=snap, players=players, crosswalk=cw,
                      owner_id="u1").set_index("gsis_id")
    assert out.loc["g1", "held_by"] == "MINE" and out.loc["g1", "name"] == "Pat Example"
    assert out.loc["g1", "injury"] == "Questionable"
    assert out.loc["g2", "held_by"] == "rostered"                # no manager name
    assert out.loc["g3", "held_by"] == "FA"
    assert out.loc["DEF:LA", "held_by"] == "MINE" and out.loc["DEF:LA", "sleeper_id"] == "LAR"
    assert out.loc["g9", "held_by"] == "unresolved"              # never name-matched
    named = R.ownership(table, snapshot=snap, players=players, crosswalk=cw,
                        owner_id="u1", manager_names=True).set_index("gsis_id")
    assert named.loc["g2", "held_by"] == "Rival"


def test_an_out_report_zeroes_the_week_and_the_map_only_flags():
    rr = _load("ros_cli", "scripts/weekly/ros_rankings.py")
    inj = pd.DataFrame([{"week": 4, "gsis_id": "g1", "report_status": "Out"},
                        {"week": 3, "gsis_id": "g2", "report_status": "Out"},
                        {"week": 4, "gsis_id": "g3", "report_status": "Questionable"}])
    players = {"14": {"injury_status": "IR"}}
    out, flags = rr.out_for_week(inj, players, Crosswalk({"14": "g4"}, {}), 4)
    assert out == {"g1"}                                         # Questionable is not out
    assert flags["g4"] == "IR" and "g4" not in out               # flagged, not zeroed
    assert flags["g1"] == "OUT wk4"


def _weeks():
    rows = []
    for w in (1, 2, 3, 4):
        for gid, team, opp, pos, pts in (("wr1", "AAA", "BBB", "WR", 12.0),
                                         ("wr2", "CCC", "DDD", "WR", 6.0),
                                         ("k1", "AAA", "BBB", "K", 8.0)):
            rows.append({"gsis_id": gid, "week": w, "team": team, "opponent_team": opp,
                         "position": pos, "league_points": pts, "targets": 8.0 if pos == "WR" else 0.0,
                         "carries": 0.0, "receiving_points": pts if pos == "WR" else 0.0,
                         "rushing_points": 0.0, "passing_points": 0.0,
                         "offense_pct": 0.8, "target_share": 0.2, "air_yards_share": 0.2})
    return pd.DataFrame(rows)


def test_the_table_ranks_by_the_chosen_method_and_zeroes_only_the_out_week():
    sched = _schedule()
    weeks = _weeks()
    common = dict(weeks=weeks, schedule=sched, injuries=None, inputs=None, week=5)
    by_ppg, status = R.build_table(**common, weights={"choice": {"WR": "ppg", "K": "ppg"}})
    assert "baseline" in status                                  # no model inputs
    wr = by_ppg.loc[by_ppg["position"] == "WR"].set_index("gsis_id")
    assert wr.loc["wr1", "pos_rank"] == 1 and wr.loc["wr1", "method"] == "ppg"
    assert wr.loc["wr1", "rate"] == pytest.approx(12.0)
    assert wr.loc["wr1", "ros"] == pytest.approx(12.0 * wr.loc["wr1", "games_left"])
    assert wr.loc["wr2", "byes"] == "6"                          # CCC's bye
    out, _ = R.build_table(**common, weights={"choice": {"WR": "ppg"}}, out={"wr1"})
    o = out.set_index("gsis_id").loc["wr1"]
    assert o["ros"] == pytest.approx(wr.loc["wr1", "ros"] - 12.0)   # week 5 only
    assert o["weeks"].startswith("5:BBB:0 ")
    shipped, _ = R.build_table(**common)                         # the shipped choice
    assert set(shipped["method"]) <= {"ppg", "base", "adv", "adv_sched", "ros_model"}
    empty, why = R.build_table(weeks=None, schedule=sched, injuries=None, inputs=None, week=5)
    assert empty.empty and "no box scores" in why


def test_the_shipped_ros_weights_are_the_cross_validated_ones():
    w = R.load_weights()
    assert w is not None and set(w["choice"]) == {"QB", "RB", "WR", "TE", "K", "DEF"}
    cv = w["evidence"]["cv"]
    for pos, method in w["choice"].items():
        best = max(cv[pos], key=lambda s: cv[pos][s]["spearman"])
        assert method == best                                    # chosen by the CV mean
        assert cv[pos][method]["spearman"] >= cv[pos]["ppg"]["spearman"]
    assert (ROOT / w["evidence"]["doc"]).is_file()
    for pos, blob in w["stack"].items():
        assert set(blob["cols"]) <= {"ppg", "base", "adv", "sched", "games_before"}


def test_the_script_writes_rankings_with_my_roster_from_the_cache(tmp_path):
    scn = _load("ros_scn", "scripts/weekly/dashboard_scenarios.py")
    rr = _load("ros_cli2", "scripts/weekly/ros_rankings.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    out = tmp_path / "out"
    rc = rr.main(["--cache-root", str(root), "--owner", "fixture_owner", "--week", "3",
                  "--out", str(out)])
    assert rc == 0
    md = (out / "ros_week03.md").read_text("utf-8")
    assert "## My roster" in md and "## WR" in md and "| MINE |" in md
    csv = pd.read_csv(out / "ros_week03.csv")
    assert {"gsis_id", "sleeper_id", "held_by", "ros", "playoff", "pos_rank"} <= set(csv.columns)
    assert (csv["held_by"] == "MINE").any()
    assert not math.isnan(csv["ros"].max())


def test_the_board_record_carries_ros_without_other_managers_names(tmp_path):
    import json
    scn = _load("ros_scn2", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("ros_dash", "scripts/weekly/dashboard.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    out = tmp_path / "out"
    cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch"),
              "--now", scn.NOW.isoformat(), "--no-archive"])
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    block = rec["ros"]
    assert block["players"], block["status"]
    held = {p["held_by"] for p in block["players"]}
    assert "MINE" in held and held <= {"MINE", "FA", "rostered", "unresolved"}
    assert "counted as played, NOT as byes" in block["status"]   # the fixture's short schedule
    mine = [p for p in block["players"] if p["held_by"] == "MINE"]
    assert all(p["ros"] >= 0 for p in mine if p["position"] != "DEF")
