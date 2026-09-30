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
    assert model is not None and set(model.adv) == set(A.POSITIONS) == set(model.stack)
    allowed = set(A.FEATURE_COLUMNS) | {"baseline", "ppg_to_date"}
    for pos in A.POSITIONS:
        assert set(model.adv[pos].cols) <= allowed
        assert "baseline" in model.adv[pos].cols            # measured against it
        assert set(model.stack[pos].cols) <= allowed | {"sleeper"}
    ev = model.meta["evidence"]
    assert ev["adv_pairwise"] > ev["baseline_pairwise"] and ev["adv_mae"] < ev["baseline_mae"]
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
