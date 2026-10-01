"""The advanced-stats projection (`gridiron.models.advanced`). Synthetic data
only. Pins: features come from earlier weeks only; each input is read the
way it was trained; the shipped coefficients are the gated ones; missing
inputs keep the baseline; and the dashboard refines before withholding."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gridiron.models import advanced as A
from gridiron.models import validated_signals as V

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _hist():
    rows = []
    for w in (1, 2, 3, 4):
        rows += [
            {"gsis_id": "wr1", "week": w, "team": "NYJ", "opponent_team": "MIA",
             "position": "WR", "league_points": 10.0 + w, "offense_pct": 0.9,
             "target_share": 0.25, "air_yards_share": 0.3, "carries": 0.0,
             "targets": 8.0},
            {"gsis_id": "wr2", "week": w, "team": "NYJ", "opponent_team": "MIA",
             "position": "WR", "league_points": 6.0, "offense_pct": 0.7,
             "target_share": 0.15, "air_yards_share": 0.2, "carries": 0.0,
             "targets": 4.0},
            {"gsis_id": "rb1", "week": w, "team": "BUF", "opponent_team": "NE",
             "position": "RB", "league_points": 15.0, "offense_pct": 0.6,
             "target_share": 0.1, "air_yards_share": 0.0, "carries": 15.0,
             "targets": 3.0}]
    weeks = pd.DataFrame(rows)
    xfp = pd.DataFrame([{"gsis_id": r["gsis_id"], "week": r["week"], "xfp": 9.0}
                        for r in rows])
    return A.history_frame(weeks, xfp, pd.DataFrame(columns=["gsis_id", "week"]))


def test_features_use_only_earlier_weeks():
    h = _hist()
    before = A.features_as_of(h, 3)
    later = h.copy()
    later.loc[later["week"] >= 3, ["league_points", "offense_pct", "xfp"]] = 999.0
    after = A.features_as_of(later, 3)
    pd.testing.assert_frame_equal(before, after)
    wr1 = before.set_index("gsis_id").loc["wr1"]
    assert wr1["xfp_l3"] == 9.0 and wr1["fpoe_season"] == pytest.approx(2.5)


def test_expected_points_are_scored_with_the_leagues_rules():
    ff = pd.DataFrame([{"player_id": "g1", "week": 2, "receptions_exp": 4,
                        "rec_yards_gained_exp": 50, "rec_touchdown_exp": 0.5}])
    assert A.xfp_from_ff_opportunity(ff)["xfp"].tolist() == [4 * 0.5 + 5 + 3]


def test_a_teammate_ruled_out_hands_his_volume_to_the_active_ones():
    practice = A.practice_from_injuries(pd.DataFrame([
        {"week": 5, "gsis_id": "wr2", "team": "NYJ", "position": "WR",
         "report_status": "Out", "practice_status": "Did Not Participate In Practice"},
        {"week": 5, "gsis_id": "wr2", "team": "NYJ", "position": "WR",
         "report_status": "Out", "practice_status": None}]))
    f = A.features_as_of(_hist(), 5, practice=practice).set_index("gsis_id")
    assert f.loc["wr2", "vacated_pickup"] == 0.0
    assert f.loc["wr1", "vacated_pickup"] == pytest.approx(4.0)   # all of wr2's 4
    assert f.loc["rb1", "vacated_pickup"] == 0.0                   # other team
    assert f.loc["wr2", "practice_dnp"] == 1.0


def _role_hist():
    """rb1 starts weeks 1-3, then vanishes (an IR stash is never on the
    weekly report); rb2 carries the team's week-4 game."""
    rows = []
    for w in (1, 2, 3):
        rows += [{"gsis_id": "rb1", "week": w, "team": "MIA", "opponent_team": "NYJ",
                  "position": "RB", "league_points": 15.0, "offense_pct": 0.7,
                  "target_share": 0.1, "air_yards_share": 0.0, "carries": 15.0, "targets": 4.0},
                 {"gsis_id": "rb2", "week": w, "team": "MIA", "opponent_team": "NYJ",
                  "position": "RB", "league_points": 4.0, "offense_pct": 0.3,
                  "target_share": 0.05, "air_yards_share": 0.0, "carries": 5.0, "targets": 1.0}]
    rows.append({"gsis_id": "rb2", "week": 4, "team": "MIA", "opponent_team": "BUF",
                 "position": "RB", "league_points": 14.0, "offense_pct": 0.8,
                 "target_share": 0.15, "air_yards_share": 0.0, "carries": 18.0, "targets": 5.0})
    xfp = pd.DataFrame([{"gsis_id": r["gsis_id"], "week": r["week"], "xfp": r["league_points"]}
                        for r in rows])
    return A.history_frame(pd.DataFrame(rows), xfp, pd.DataFrame(columns=["gsis_id", "week"]))


def test_a_starter_missing_from_the_box_score_hands_his_role_to_who_played():
    f = A.features_as_of(_role_hist(), 5).set_index("gsis_id")
    # the trailing average still calls rb2 a backup; the role block does not
    assert f.loc["rb2", "opp_l3"] == pytest.approx((6 + 6 + 23) / 3)
    assert f.loc["rb2", "opp_last"] == 23.0 and f.loc["rb2", "opp_share_last"] == 1.0
    assert f.loc["rb2", "opp_jump"] == pytest.approx(17.0)
    assert f.loc["rb2", "snap_jump"] == pytest.approx(0.5)
    assert f.loc["rb2", "absent_pickup"] == pytest.approx(19.0)     # rb1's 19 per game
    assert np.isnan(f.loc["rb2", "vacated_pickup"])                  # no report this week: unknown
    empty_report = A.practice_from_injuries(pd.DataFrame([
        {"week": 5, "gsis_id": "wr9", "team": "NYJ", "position": "WR",
         "report_status": "Questionable", "practice_status": None}]))
    g = A.features_as_of(_role_hist(), 5, practice=empty_report).set_index("gsis_id")
    assert g.loc["rb2", "vacated_pickup"] == 0.0                     # a report exists; rb1 is not on it
    assert f.loc["rb1", "opp_share_last"] == 0.0 and f.loc["rb1", "absent_pickup"] == 0.0
    # before the injury nothing fires
    before = A.features_as_of(_role_hist(), 4).set_index("gsis_id")
    assert before["absent_pickup"].eq(0.0).all() and before["opp_jump"].eq(0.0).all()
    assert before.loc["rb1", "opp_share_last"] == pytest.approx(19 / 25)


def test_a_questionable_teammate_is_not_counted_as_missing():
    practice = A.practice_from_injuries(pd.DataFrame([
        {"week": 5, "gsis_id": "rb1", "team": "MIA", "position": "RB",
         "report_status": "Questionable",
         "practice_status": "Limited Participation in Practice"}]))
    f = A.features_as_of(_role_hist(), 5, practice=practice).set_index("gsis_id")
    assert f.loc["rb2", "absent_pickup"] == 0.0                       # rule #11: he may play
    assert f.loc["rb2", "opp_share_last"] == 1.0                      # what he DID do stands
    ruled_out = A.practice_from_injuries(pd.DataFrame([
        {"week": 5, "gsis_id": "rb1", "team": "MIA", "position": "RB",
         "report_status": "Out", "practice_status": "Did Not Participate In Practice"}]))
    g = A.features_as_of(_role_hist(), 5, practice=ruled_out).set_index("gsis_id")
    assert g.loc["rb2", "vacated_pickup"] > 0 and g.loc["rb2", "absent_pickup"] == 0.0


def test_a_teammate_on_a_reserve_list_is_the_confirmed_version(tmp_path):
    rosters = pd.DataFrame([
        {"week": 4, "gsis_id": "rb1", "position": "RB", "status": "ACT", "game_type": "REG"},
        {"week": 5, "gsis_id": "rb1", "position": "RB", "status": "RES", "game_type": "REG"},
        {"week": 5, "gsis_id": "rb2", "position": "RB", "status": "ACT", "game_type": "REG"},
        {"week": 5, "gsis_id": "lb9", "position": "LB", "status": "RES", "game_type": "REG"},
        {"week": 5, "gsis_id": "wr7", "position": "WR", "status": "INA", "game_type": "REG"}])
    reserve = A.reserve_from_rosters(rosters)
    assert reserve.to_dict("records") == [{"week": 5, "gsis_id": "rb1"}]   # INA never counts
    assert A.reserve_ids(reserve, 4) is None                     # nothing known that early
    assert A.reserve_ids(reserve, 5) == {"rb1"} == A.reserve_ids(reserve, 7)   # last known stands
    f = A.features_as_of(_role_hist(), 5, reserve=reserve).set_index("gsis_id")
    assert f.loc["rb2", "reserve_pickup"] == pytest.approx(19.0)
    assert f.loc["rb1", "reserve_pickup"] == 0.0
    unknown = A.features_as_of(_role_hist(), 5).set_index("gsis_id")
    assert np.isnan(unknown.loc["rb2", "reserve_pickup"])         # no rosters: unknown, not 0
    # the exponentially weighted averages lean on the last game
    assert 11.7 < f.loc["rb2", "opp_ewm"] < 23.0 and f.loc["rb2", "opp_ewm"] > f.loc["rb2", "opp_l3"]
    # the pull step saves it beside the other inputs and the context reads it
    A.write_inputs(tmp_path, 2026, {"xfp": pd.DataFrame(columns=["gsis_id", "week", "xfp"]),
                                    "ngs": pd.DataFrame(columns=["gsis_id", "week"]),
                                    "depth": pd.DataFrame(columns=["week", "gsis_id", "depth_rank"]),
                                    "reserve": reserve}, datetime(2026, 10, 1, tzinfo=UTC))
    assert A.load_inputs(tmp_path, 2026)["reserve"]["gsis_id"].tolist() == ["rb1"]
    fetched = A.fetch_inputs(2026, loaders={"rosters": lambda: rosters})
    assert fetched["reserve"]["gsis_id"].tolist() == ["rb1"] and len(fetched["defense"]) == 0


def test_expansion_inputs_are_built_from_their_sources():
    ff = pd.DataFrame([{"player_id": "g1", "week": 2, "receptions_exp": 4, "rec_yards_gained_exp": 50,
                        "rec_touchdown_exp": 0.5, "rush_touchdown_exp": 0.25,
                        "total_fantasy_points_exp": 12.0, "total_fantasy_points_exp_team": 48.0}])
    x = A.xfp_from_ff_opportunity(ff).iloc[0]
    assert x["xfp"] == 4 * 0.5 + 5 + 3 + 1.5 and x["xtd"] == 0.75 and x["xfp_share"] == 0.25
    off = A.offense_history(pd.DataFrame([
        {"team": "NYJ", "week": 1, "season_type": "REG", "attempts": 30, "carries": 30,
         "passing_epa": 2.0, "rushing_epa": -1.0},
        {"team": "NYJ", "week": 19, "season_type": "POST", "attempts": 50, "carries": 10}]))
    assert off.to_dict("records") == [{"team": "NYJ", "week": 1, "plays": 60.0, "pass_rate": 0.5,
                                       "off_epa": 1.0}]
    pfr = A.pfr_from_advstats({
        "rec": pd.DataFrame([{"pfr_player_id": "AbcDe00", "week": 1, "game_type": "REG",
                              "receiving_drop_pct": 10.0}]),
        "rush": pd.DataFrame([{"pfr_player_id": "AbcDe00", "week": 1, "game_type": "REG",
                               "rushing_yards_before_contact_avg": 2.5,
                               "rushing_yards_after_contact_avg": 1.5}]),
        "pass": None})
    assert pfr.iloc[0].to_dict() == {"pfr_id": "AbcDe00", "week": 1, "pfr_drop_pct": 10.0,
                                     "pfr_ybc": 2.5, "pfr_yac": 1.5}
    ctx = A.schedule_context(pd.DataFrame([
        {"week": 1, "game_type": "REG", "home_team": "NYJ", "away_team": "MIA", "roof": "dome",
         "spread_line": 3.0, "wind": 12, "temp": 40},
        {"week": 1, "game_type": "REG", "home_team": "BUF", "away_team": "NE", "roof": "outdoors",
         "spread_line": -2.0, "wind": 12, "temp": 40}]), 1)
    assert ctx["NYJ"]["wind"] == 0.0 and ctx["NYJ"]["temp"] == 70.0 and ctx["MIA"]["spread"] == -3.0
    assert ctx["BUF"]["wind"] == 12.0 and ctx["BUF"]["temp"] == 40.0 and ctx["NE"]["spread"] == 2.0


def test_the_feature_frame_carries_every_expansion_column_and_reads_only_earlier_weeks():
    h = _hist()
    h["pfr_id"] = "P1"
    pfr = pd.DataFrame([{"pfr_id": "P1", "week": w, "pfr_drop_pct": 5.0 * w} for w in (1, 2, 3, 4)])
    weeks = h.rename(columns={"xfp": "_x"})
    hist = A.history_frame(weeks.assign(receiving_epa=1.0), h[["gsis_id", "week", "xfp"]]
                           .assign(xtd=0.5, xfp_share=0.3), pd.DataFrame(columns=["gsis_id", "week"]),
                           pfr)
    off = pd.DataFrame([{"team": "NYJ", "week": w, "plays": 60 + w, "pass_rate": 0.6, "off_epa": 1.0}
                        for w in (1, 2, 3, 4)])
    f = A.features_as_of(hist, 3, offense=off).set_index("gsis_id")
    assert set(A.EXPANSION_FEATURES) <= set(f.columns)
    wr1 = f.loc["wr1"]
    assert wr1["pfr_drop_pct"] == 7.5 and wr1["epa_pg"] == 1.0          # weeks 1-2 only
    assert wr1["team_plays_l3"] == 61.5 and wr1["xtd_l3"] == 0.5 and wr1["xfp_share_l3"] == 0.3
    assert np.isnan(wr1["spread"]) and not np.isnan(wr1["team_epa_l3"])


def test_routes_run_come_from_participation_joined_to_dropbacks():
    part = pd.DataFrame([
        {"nflverse_game_id": "G1", "play_id": 1, "offense_players": "wr1;wr2;qb1"},
        {"nflverse_game_id": "G1", "play_id": 2, "offense_players": "wr1;qb1"},
        {"nflverse_game_id": "G1", "play_id": 3, "offense_players": "wr1;wr2;qb1"},   # a run
        {"nflverse_game_id": "G2", "play_id": 9, "offense_players": "wr1"}])          # postseason
    pbp = pd.DataFrame([
        {"game_id": "G1", "play_id": 1, "week": 3, "posteam": "NYJ", "qb_dropback": 1, "season_type": "REG"},
        {"game_id": "G1", "play_id": 2, "week": 3, "posteam": "NYJ", "qb_dropback": 1, "season_type": "REG"},
        {"game_id": "G1", "play_id": 3, "week": 3, "posteam": "NYJ", "qb_dropback": 0, "season_type": "REG"},
        {"game_id": "G2", "play_id": 9, "week": 19, "posteam": "NYJ", "qb_dropback": 1, "season_type": "POST"}])
    r = A.routes_from_participation(part, pbp).set_index("gsis_id")
    assert r.loc["wr1"].to_dict() == {"week": 3, "routes": 2, "team_routes": 2, "route_share": 1.0}
    assert r.loc["wr2"]["routes"] == 1 and r.loc["wr2"]["route_share"] == 0.5
    assert "19" not in set(r["week"].astype(str))
    assert A.routes_from_participation(None, pbp).empty
    h = _hist()
    routes = pd.DataFrame([{"gsis_id": "wr1", "week": w, "routes": 30 + w, "team_routes": 40,
                            "route_share": (30 + w) / 40} for w in (1, 2, 3, 4)])
    hist = A.history_frame(h.rename(columns={"xfp": "_x"}), h[["gsis_id", "week", "xfp"]],
                           pd.DataFrame(columns=["gsis_id", "week"]), None, routes)
    f = A.features_as_of(hist, 4).set_index("gsis_id")
    assert f.loc["wr1", "routes_l3"] == 32.0 and f.loc["wr1", "routes_last"] == 33.0
    assert f.loc["wr1", "route_share_l3"] == pytest.approx(0.8)
    assert f.loc["wr1", "tprr_season"] == pytest.approx(24 / 96)         # 8 targets x 3 / routes
    assert np.isnan(f.loc["wr2", "routes_l3"])                            # no participation: unknown


def test_an_optional_input_fails_on_its_own_and_a_core_one_fails_the_refresh():
    heard = []

    def boom():
        raise ValueError("Season must be between 2016 and 2025")
    good = lambda: pd.DataFrame([{"player_id": "g1", "week": 1, "receptions_exp": 2}])  # noqa: E731
    got = A.fetch_inputs(2026, loaders={"ff_opportunity": good, "participation": boom, "pbp": boom,
                                        "rosters": boom, "depth_charts": lambda: None,
                                        "schedules": lambda: None}, log=heard.append)
    assert got["xfp"]["xfp"].tolist() == [1.0]                 # the good input survived
    assert got["routes"].empty and got["reserve"].empty       # the bad ones are unknown, not 0
    assert len(heard) == 3 and all("unavailable" in h for h in heard)
    with pytest.raises(ValueError):                           # a core input down: no refresh
        A.fetch_inputs(2026, loaders={"ff_opportunity": boom}, log=heard.append)
    # the live default never asks for the expansion inputs at all
    import inspect
    assert "participation" in A.EXPANSION_LOADERS and "pbp" in A.EXPANSION_LOADERS
    assert inspect.signature(A.fetch_inputs).parameters["expansion"].default is False


def test_depth_rank_from_both_nflverse_layouts():
    weekly = pd.DataFrame([
        {"week": 3.0, "formation": "Offense", "depth_position": "WR", "depth_team": "2",
         "gsis_id": "a"},
        {"week": 3.0, "formation": "Offense", "depth_position": "WR", "depth_team": "1",
         "gsis_id": "a"},
        {"week": None, "formation": "Offense", "depth_position": "WR", "depth_team": "1",
         "gsis_id": "b"}])
    d = A.depth_from_charts(weekly, None)
    assert d.to_dict("records") == [{"week": 3, "gsis_id": "a", "depth_rank": 1.0}]
    sched = pd.DataFrame([{"week": 4, "game_type": "REG", "gameday": "2026-10-01"}])
    stamped = pd.DataFrame([
        {"dt": "2026-09-29T08:00:00Z", "team": "NYJ", "gsis_id": "a", "pos_abb": "WR", "pos_rank": 2},
        {"dt": "2026-09-30T08:00:00Z", "team": "NYJ", "gsis_id": "a", "pos_abb": "WR", "pos_rank": 1},
        {"dt": "2026-10-02T08:00:00Z", "team": "NYJ", "gsis_id": "a", "pos_abb": "WR", "pos_rank": 3}])
    d = A.depth_from_charts(stamped, sched)
    assert d.to_dict("records") == [{"week": 4, "gsis_id": "a", "depth_rank": 1}]


def test_ridge_round_trips_and_fills_missing_inputs_like_training():
    df = pd.DataFrame({"x": [1.0, 2.0, 3.0, np.nan], "y": [2.0, 4.0, 6.0, 5.0]})
    m = A.Ridge.fit(df, df["y"], ["x"], lam=0.0001)
    again = A.Ridge.from_json(json.loads(json.dumps(m.to_json())))
    assert again.predict_row({"x": 2.0}) == pytest.approx(m.predict_row({"x": 2.0}))
    assert again.predict_row({"x": None}) == pytest.approx(again.predict_row({"x": 2.0}))


def test_the_shipped_model_is_the_gated_one():
    model = A.AdvancedModel.load()
    assert model is not None and set(model.adv) == {*A.POSITIONS, "K", "DEF"} == set(model.stack)
    assert set(model.adv["K"].cols) <= set(A.KICKER_FEATURES) | {"baseline"}
    assert "baseline" in model.adv["K"].cols                  # measured against it
    assert set(model.adv["DEF"].cols) <= set(A.DEFENSE_FEATURES)
    assert model.meta["resid_sd"]["DEF"] > 0 and model.meta["resid_sd"]["K"] > 0
    kev, dev = model.meta["k_def_evidence"]["K"], model.meta["k_def_evidence"]["DEF"]
    assert kev["systems"]["adv"]["pairwise"] > kev["systems"]["baseline"]["pairwise"]
    assert dev["systems"]["adv"]["pairwise"] > dev["systems"]["naive"]["pairwise"]
    allowed = set(A.FEATURE_COLUMNS) | {"baseline", "ppg_to_date"}
    for pos in A.POSITIONS:
        assert set(model.adv[pos].cols) <= allowed
        assert "baseline" in model.adv[pos].cols            # measured against it
        assert set(model.stack[pos].cols) <= allowed | {"sleeper", "adv"}
    form = model.meta.get("stack_form") or {}
    if form.get("kind") == "mix_calibrated":
        # the contender: ours + Sleeper mixed and calibrated, one composed ridge
        se = model.meta["stack_evidence"]
        assert se["cv"][se["winner"]]["mae"] < se["cv"]["sleeper"]["mae"]
        assert se["mae_better_folds"] == len(se["folds"]) and se["ordering_ahead_folds"] >= 6
        assert se["bar_cleared"] is False                           # 2020 is behind: say so
        for p in A.POSITIONS:
            assert {"adv", "sleeper", "baseline"} <= set(model.stack[p].cols)
            assert all(sd == 1.0 and mu == 0.0 for sd, mu in zip(model.stack[p].sd, model.stack[p].mu))
        assert all(form["calibration"][p][1] > 0 for p in A.POSITIONS)   # monotone
    elif form.get("kind") == "two_stage_calibrated":
        # the live contender: ours + Sleeper, calibrated; never worse than
        # Sleeper on ordering in any fold (a tie = under one pair in ten
        # thousand; 2025 is behind by 2 of 60,813), better on error in every fold
        se = model.meta["stack_evidence"]
        if se.get("bar_cleared", True):
            for t, f in se["folds"].items():
                assert f["two_stage_cal"]["pairwise"] >= f["sleeper"]["pairwise"] - 1e-4
                assert f["two_stage_cal"]["mae"] < f["sleeper"]["mae"]
        else:
            # saved as a contender with the bar recorded as NOT cleared: it
            # must still beat Sleeper on error on the mean, and say so
            assert se["cv"]["two_stage_cal"]["mae"] < se["cv"]["sleeper"]["mae"]
            assert se["winners"] == [] or "two_stage_cal" not in se["winners"]
        assert all(model.stack[p].cols == ("adv", "sleeper", "baseline") for p in A.POSITIONS)
        assert all(form["calibration"][p][1] > 0 for p in A.POSITIONS)   # monotone
    ev = model.meta["evidence"]
    assert ev["adv_pairwise"] > ev["baseline_pairwise"] and ev["adv_mae"] < ev["baseline_mae"]
    # the role-change block ships only with its own cross-validated win
    role = model.meta["role_change_evidence"]
    used = {c for pos in A.POSITIONS for c in model.adv[pos].cols} & set(A.ROLE_FEATURES)
    assert used and used == set(V.VALIDATED["role_change_v1"]["role_features"]) \
        if "role_features" in V.VALIDATED["role_change_v1"] else used
    cv = role["cv"]["systems"]
    win = cv[f"adv{role['winner']}"]
    assert win["pairwise"] > cv["advshipped"]["pairwise"] and win["mae"] < cv["advshipped"]["mae"]
    assert role["winner"] in role["eligible"] and "role_change_v1" in V.FEATS
    assert A.NAME in V.FEATS and A.NAME in V.VALIDATED
    assert (ROOT / V.VALIDATED[A.NAME]["evidence"]).is_file()
    assert (ROOT / V.VALIDATED[A.NAME]["script"]).is_file()


def test_missing_inputs_keep_the_baseline_and_say_why():
    model = A.AdvancedModel.load()
    ctx = A.build_context(weeks=None, inputs=None, injuries=None, schedule=None,
                          week=5, model=model)
    assert not ctx.active and "baseline" in ctx.status and "not available" in ctx.status
    early = A.build_context(weeks=None, inputs={}, injuries=None, schedule=None,
                            week=2, model=model)
    assert not early.active and "week 4" in early.status
    assert ctx.refine("wr1", "WR", 10.0, 9.0) is None


def test_an_active_context_refines_and_never_goes_negative():
    model = A.AdvancedModel.load()
    weeks = _hist()
    inputs = {"xfp": weeks[["gsis_id", "week", "xfp"]],
              "ngs": pd.DataFrame(columns=["gsis_id", "week"]),
              "depth": pd.DataFrame(columns=["week", "gsis_id", "depth_rank"]),
              "meta": {"fetched_at": "2026-10-01T00:00:00+00:00"}}
    ctx = A.build_context(weeks=weeks, inputs=inputs, injuries=None, schedule=None,
                          week=5, model=model)
    assert ctx.active and ctx.status.startswith(A.NAME)
    mean, used = ctx.refine("wr1", "WR", 12.0, 12.5)
    assert mean >= 0 and used["baseline"] == 12.0
    assert ctx.refine("nobody", "WR", 12.0, 12.0) is None
    assert ctx.refine("wr1", "K", 7.0, 7.0) is None
    assert ctx.stacked("wr1", "WR", 12.0, 12.5, None) is None
    assert ctx.stacked("wr1", "WR", 12.0, 12.5, 11.0) >= 0


def test_the_pull_step_is_best_effort(tmp_path, capsys):
    pw = _load("adv_pull_week", "scripts/ingest/pull_week.py")
    from gridiron.ingest import Manifest
    m = Manifest(tmp_path, {}, 2026)
    now = datetime(2026, 10, 1, tzinfo=UTC)

    def boom():
        raise RuntimeError("nflverse down")
    pw.pull_model_inputs(m, 2026, now, False, loaders={"ff_opportunity": boom})
    assert "FAILED" in capsys.readouterr().err and A.load_inputs(tmp_path, 2026) is None
    loaders = {"ff_opportunity": lambda: pd.DataFrame([{"player_id": "g1", "week": 1,
                                                        "receptions_exp": 2}]),
               "depth_charts": lambda: None, "schedules": lambda: None}
    pw.pull_model_inputs(m, 2026, now, False, loaders=loaders)
    got = A.load_inputs(tmp_path, 2026)
    assert got["xfp"]["xfp"].tolist() == [1.0] and got["meta"]["season"] == 2026
    assert A.inputs_fresh(tmp_path, 2026, now + timedelta(hours=1))
    assert A.load_inputs(tmp_path, 2025) is None
    assert m.entries == {}                                    # outside the manifest


def test_the_board_refines_before_withholding(tmp_path, monkeypatch):
    """End to end on the synthetic cache with the model forced on (the
    scenario is week 3, below the shipped model's week-4 minimum)."""
    scn = _load("adv_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("adv_dash", "scripts/weekly/dashboard.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    directory = root / "season2026"
    xfp = pd.DataFrame([{"gsis_id": "", "week": 1, "xfp": 0.0}])
    A.write_inputs(directory, 2026, {"xfp": xfp, "ngs": pd.DataFrame({"gsis_id": [], "week": []}),
                                     "depth": pd.DataFrame({"week": [], "gsis_id": [],
                                                            "depth_rank": []})},
                   scn.NOW)
    real = A.AdvancedModel.load()
    forced = A.AdvancedModel(real.adv, real.stack, {**real.meta, "min_week": 1})
    monkeypatch.setattr(A.AdvancedModel, "load", classmethod(lambda cls, *a, **k: forced))
    out = tmp_path / "out"
    cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch"),
              "--now", scn.NOW.isoformat(), "--no-archive"])
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    assert rec["projection_model"].startswith(A.NAME)
    base = rec["contenders"]["baseline_v1"]
    refined = [p for p in rec["roster"] if "baseline ->" in (p["explain"] or "")]
    assert refined, "no roster projection was refined"
    assert any(abs(p["projected"] - base[p["sleeper_id"]]) > 1e-6 for p in refined
               if not p["withheld"])
    for p in rec["roster"]:
        if p["withheld"]:
            assert p["projected"] == 0.0                     # withholding still wins
