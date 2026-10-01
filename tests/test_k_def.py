"""Kickers and team defenses in the advanced model. Synthetic data only.
Pins: DEF scoring maps nflverse team columns onto the league's Sleeper
weights; Sleeper's K/DEF projections are scored with league rules; every
K/DEF feature comes from earlier weeks only; a kicker is refined only from
a kicker row; a defense is projected by team (its stable id); and free-agent
defenses enter the pool only when they can be projected."""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from gridiron import shadow as SH
from gridiron.models import advanced as A
from gridiron.models import validated_signals as V
from gridiron.scoring import DEFENSE_UNSCORED, defense_points, points_allowed_tier
from gridiron.waivers import available_ids

ROOT = Path(__file__).resolve().parents[1]
TEAMS = ("AAA", "BBB", "CCC", "DDD")


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_kicker_and_defense_models_are_registered_with_their_evidence():
    for name in ("advanced_v1_k", "advanced_v1_def"):
        assert name in V.FEATS and V.VALIDATED[name]["pairwise_delta"] > 0
        assert (ROOT / V.VALIDATED[name]["evidence"]).is_file()
        assert (ROOT / V.VALIDATED[name]["script"]).is_file()


# ------------------------------------------------------------------ scoring

@pytest.mark.parametrize("pa,tier", [(0, "pts_allow_0"), (6, "pts_allow_1_6"),
                                     (7, "pts_allow_7_13"), (20, "pts_allow_14_20"),
                                     (27, "pts_allow_21_27"), (34, "pts_allow_28_34"),
                                     (35, "pts_allow_35p"), (61, "pts_allow_35p")])
def test_points_allowed_tiers(pa, tier):
    assert points_allowed_tier(pa) == tier


def test_defense_points_map_nflverse_columns_onto_league_weights():
    week = {"def_sacks": 3, "def_interceptions": 1, "fumble_recovery_opp": 1,
            "def_fumbles_forced": 2, "def_tds": 1, "fumble_recovery_tds": 1,
            "def_safeties": 1, "def_punt_blocks": 1, "def_fg_blocks": 0,
            "def_pat_blocks": 1, "special_teams_tds": 1, "passing_yards": 999}
    # 3 sacks + 2 int + 2 fum_rec + 2 ff + 12 def_td + 2 safe + 4 blk + 6 st_td
    assert defense_points(week, None) == 33.0
    assert defense_points(week, 10) == 33.0 + 4.0            # 7-13 tier
    assert defense_points(week, float("nan")) == 33.0         # never guesses a tier
    assert defense_points({}, 40) == -4.0
    assert defense_points({"def_sacks": 2}, 0, weights={"sack": 0.5}) == 1.0
    assert "st_ff" in DEFENSE_UNSCORED


def test_sleeper_kicker_and_defense_lines_are_scored_with_league_rules():
    k = {"fgm_30_39": 1, "fgm_40_49": 1, "fgm_50p": 0.5, "xpm": 3,
         "fgmiss_40_49": 0.2, "xpmiss": 0.1, "pts_half_ppr": 99}
    assert SH.score_kicker_projection(k) == round(3 + 4 + 2.5 + 3 - 0.2 - 0.1, 2)
    assert SH.score_kicker_projection({"adp_dd_ppr": 3}) is None
    d = {"sack": 2.5, "int": 1, "pts_allow_7_13": 0.5, "pts_allow_35p": 0.1,
         "yds_allow": 300}
    assert SH.score_defense_projection(d) == round(2.5 + 2 + 2 - 0.4, 2)
    assert SH.score_defense_projection({"yds_allow": 300}) is None
    fetch_calls = []

    def fetch(url):
        fetch_calls.append(url)
        pos = url.split("position%5B%5D=")[1]
        return {"K": [{"player_id": "77", "stats": k}],
                "DEF": [{"player_id": "AAA", "stats": d}]}.get(pos, [])
    blob = SH.fetch_sleeper(2026, 5, fetch=fetch)
    assert blob["players"]["77"]["position"] == "K"
    assert blob["players"]["AAA"]["points"] == SH.score_defense_projection(d)


def test_free_agent_defenses_enter_the_pool_only_when_projectable():
    players = {"AAA": {"position": "DEF", "team": "AAA"},
               "BBB": {"position": "DEF", "team": "BBB"},
               "10": {"position": "WR", "team": "AAA", "active": True},
               "11": {"position": "DEF", "team": "CCC"}}
    rosters = [{"players": ["BBB"]}]
    assert "AAA" not in available_ids(players, rosters)
    with_def = available_ids(players, rosters, include_defense=True)
    assert "AAA" in with_def and "BBB" not in with_def   # held by a roster
    assert "11" not in with_def                          # a numeric id is never a DST


# ----------------------------------------------------------------- features

def _schedule(weeks=(1, 2, 3, 4, 5)):
    rows = []
    for w in weeks:
        pairs = (("AAA", "BBB"), ("CCC", "DDD")) if w % 2 else (("AAA", "CCC"), ("BBB", "DDD"))
        for home, away in pairs:
            rows.append({"week": w, "game_type": "REG", "home_team": home, "away_team": away,
                         "home_score": 24.0 if w < 5 else np.nan,
                         "away_score": 10.0 if w < 5 else np.nan,
                         "spread_line": 3.0, "total_line": 44.0, "roof": "dome",
                         "gameday": f"2026-10-{w:02d}", "gametime": "13:00"})
    return pd.DataFrame(rows)


def _team_stats(sched):
    rows = []
    for r in sched.loc[sched["week"] < 5].to_dict("records"):
        for team, opp in ((r["home_team"], r["away_team"]), (r["away_team"], r["home_team"])):
            rows.append({"season_type": "REG", "week": r["week"], "team": team,
                         "opponent_team": opp, "def_sacks": 3 if team == "AAA" else 1,
                         "def_interceptions": 1, "fumble_recovery_opp": 0,
                         "passing_interceptions": 1, "sacks_suffered": 2,
                         "fg_att": 2, "pat_att": 3})
    return pd.DataFrame(rows)


def test_defense_history_scores_every_team_week():
    sched = _schedule()
    dh = A.defense_history(_team_stats(sched), sched)
    aaa1 = dh.loc[(dh["team"] == "AAA") & (dh["week"] == 1)].iloc[0]
    assert aaa1["points_allowed"] == 10.0
    assert aaa1["dst_points"] == 3 + 2 + 4.0                  # sacks + int + 7-13 tier
    bbb1 = dh.loc[(dh["team"] == "BBB") & (dh["week"] == 1)].iloc[0]
    assert bbb1["dst_points"] == 1 + 2 + 0.0                  # allowed 24 -> 21-27 tier
    assert A.defense_history(None, sched).empty


def test_defense_and_kicker_features_use_only_earlier_weeks():
    sched = _schedule()
    dh = A.defense_history(_team_stats(sched), sched)
    before = A.defense_features_as_of(dh, 3, sched)
    later = dh.copy()
    later.loc[later["week"] >= 3, ["dst_points", "sacks", "fga"]] = 999.0
    pd.testing.assert_frame_equal(before, A.defense_features_as_of(later, 3, sched))
    aaa = before.set_index("team").loc["AAA"]
    assert aaa["sacks_pg"] == 3.0 and aaa["home"] == 1.0 and aaa["spread"] == 3.0
    assert aaa["opp_sacks_allowed_pg"] == 2.0                 # opponent CCC's history
    kweeks = pd.DataFrame([{"gsis_id": "k1", "week": w, "team": "AAA",
                            "league_points": float(w)} for w in (1, 2, 3, 4)])
    kf = A.kicker_features_as_of(kweeks, dh, 3, sched).set_index("gsis_id").loc["k1"]
    assert kf["k_ppg_season"] == 1.5 and kf["team_fga_pg"] == 2.0 and kf["dome"] == 1.0
    moved = A.kicker_features_as_of(kweeks, dh, 3, sched, teams={"k1": "BBB"})
    assert moved.iloc[0]["team"] == "BBB"                     # current team wins


def _context(model, week=5):
    sched = _schedule()
    dh = A.defense_history(_team_stats(sched), sched)
    weeks = pd.DataFrame(
        [{"gsis_id": "k1", "week": w, "team": "AAA", "opponent_team": "BBB",
          "position": "K", "league_points": 8.0} for w in (1, 2, 3, 4)]
        + [{"gsis_id": "wr1", "week": w, "team": "AAA", "opponent_team": "BBB",
            "position": "WR", "league_points": 10.0, "offense_pct": 0.9,
            "target_share": 0.25, "air_yards_share": 0.3, "carries": 0.0,
            "targets": 8.0} for w in (1, 2, 3, 4)])
    inputs = {"xfp": pd.DataFrame([{"gsis_id": "wr1", "week": w, "xfp": 9.0}
                                   for w in (1, 2, 3, 4)]),
              "ngs": pd.DataFrame(columns=["gsis_id", "week"]),
              "depth": pd.DataFrame(columns=["week", "gsis_id", "depth_rank"]),
              "defense": dh, "meta": {"fetched_at": "2026-10-05T00:00:00+00:00"}}
    return A.build_context(weeks=weeks, inputs=inputs, injuries=None, schedule=sched,
                           week=week, model=model)


def test_a_context_projects_kickers_and_defenses_from_their_own_rows():
    model = A.AdvancedModel.load()
    ctx = _context(model)
    assert ctx.active and set(ctx.defense) == set(TEAMS)
    mean, used = ctx.refine("k1", "K", 8.0, 8.0)
    assert mean >= 0 and used["k_ppg_season"] == 8.0
    assert ctx.refine("k1", "WR", 8.0, 8.0) is None          # a kicker row is not a WR row
    assert ctx.refine("wr1", "K", 8.0, 8.0) is None           # and vice versa
    assert ctx.refine("wr1", "WR", 10.0, 10.0) is not None
    dmean, dsd = ctx.defense_projection("AAA")
    assert dsd == model.meta["resid_sd"]["DEF"] > 0
    assert dmean > ctx.defense_projection("DDD")[0]           # more sacks, fewer points allowed
    assert ctx.defense_projection("ZZZ") is None              # no row: unknown, never zero
    assert ctx.stacked_defense("AAA", None) is None
    assert ctx.stacked_defense("AAA", 9.0) is not None
    no_def = A.build_context(weeks=None, inputs=None, injuries=None, schedule=None,
                             week=5, model=model)
    assert no_def.defense_projection("AAA") is None


def test_saved_defense_inputs_round_trip(tmp_path):
    sched = _schedule()
    dh = A.defense_history(_team_stats(sched), sched)
    empty = pd.DataFrame({"gsis_id": [], "week": []})
    A.write_inputs(tmp_path, 2026, {"xfp": empty, "ngs": empty,
                                    "depth": pd.DataFrame({"week": [], "gsis_id": [],
                                                           "depth_rank": []}),
                                    "defense": dh}, pd.Timestamp("2026-10-05", tz="UTC"))
    got = A.load_inputs(tmp_path, 2026)
    assert len(got["defense"]) == len(dh)


# -------------------------------------------------------------- the board

def test_the_board_projects_the_owners_defense_and_kicker(tmp_path, monkeypatch):
    """The synthetic league's owner starts a kicker and SEA's defense. With
    defense inputs present, the DST gets a labelled advanced projection
    instead of abstaining; without them it still abstains."""
    scn = _load("kdef_scn", "scripts/weekly/dashboard_scenarios.py")
    cli = _load("kdef_dash", "scripts/weekly/dashboard.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    directory = root / "season2026"
    sched = pd.read_parquet(directory / "schedules.parquet") \
        if (directory / "schedules.parquet").exists() else None
    if sched is None:
        sched = next(pd.read_csv(p) for p in directory.glob("schedules*.csv"))
    played = sched.loc[sched["week"] < 3]
    stats = []
    for r in played.to_dict("records"):
        for team, opp in ((r["home_team"], r["away_team"]), (r["away_team"], r["home_team"])):
            stats.append({"season_type": "REG", "week": int(r["week"]), "team": team,
                          "opponent_team": opp, "def_sacks": 2, "def_interceptions": 1,
                          "fg_att": 2, "pat_att": 3})
    scored = played.assign(home_score=20.0, away_score=17.0)
    dh = A.defense_history(pd.DataFrame(stats), scored)
    empty = pd.DataFrame({"gsis_id": [], "week": []})
    A.write_inputs(directory, 2026, {"xfp": pd.DataFrame([{"gsis_id": "", "week": 1,
                                                           "xfp": 0.0}]),
                                     "ngs": empty,
                                     "depth": pd.DataFrame({"week": [], "gsis_id": [],
                                                            "depth_rank": []}),
                                     "defense": dh}, scn.NOW)
    real = A.AdvancedModel.load()
    forced = A.AdvancedModel(real.adv, real.stack, {**real.meta, "min_week": 1})
    monkeypatch.setattr(A.AdvancedModel, "load", classmethod(lambda cls, *a, **k: forced))
    out = tmp_path / "out"
    cli.main(["--cache-root", str(root), "--owner", "fixture_owner", "--write",
              "--out-dir", str(out), "--archive-root", str(tmp_path / "arch"),
              "--now", scn.NOW.isoformat(), "--no-archive"])
    rec = json.loads((out / "dashboard_latest.json").read_text("utf-8"))
    dst = next(p for p in rec["roster"] if p["position"] == "DEF")
    assert dst["projected"] is not None and dst["projected"] > 0
    assert "advanced_v1" in " ".join(dst["reasons"] or []) + (dst["explain"] or "") \
        + str(dst.get("label") or "")
