"""Shared block helpers (scripts/research/blocks/_*.py) on synthetic frames:
no research cache, no network. These pin the 2026-11-02 corrections the
near-miss bug hunt led to: a player missing from his team's roster is away in
every season, only injury lists count as injuries, and the observed weather
reanalysis never stands in for a forecast."""
from __future__ import annotations

import importlib
import sys
import types

import numpy as np
import pandas as pd

from gridiron.paths import REPO_ROOT

BLOCKS = REPO_ROOT / "scripts" / "research" / "blocks"
if str(BLOCKS) not in sys.path:
    sys.path.insert(0, str(BLOCKS))

P2A = importlib.import_module("_p2a_events_offense")
P2B = importlib.import_module("_p2b_events_ramps_defense")
P5 = importlib.import_module("_p5_schedule_weather")

A, B, C = "00-0000001", "00-0000002", "00-0000003"


def _ctx(rosters: pd.DataFrame):
    """A Context stand-in that serves only the weekly rosters."""
    return types.SimpleNamespace(rosters_weekly=lambda seasons: rosters.loc[
        rosters["season"].isin(list(seasons))].copy())


def test_roster_away_flags_a_player_missing_from_his_teams_roster():
    ro = pd.DataFrame([
        # week 5: A active, B on reserve, C cut (2019 style keeps the row)
        (2021, 5, "LV", A, "ACT"), (2021, 5, "LV", B, "RES"), (2021, 5, "LV", C, "CUT"),
        # week 6: A active; B and C have no row anywhere (2020+ style release)
        (2021, 6, "LV", A, "ACT"),
        # week 7: LV has no roster; C is listed on another team
        (2021, 7, "KC", C, "ACT"),
    ], columns=["season", "week", "team", "gsis_id", "status"])
    rows = pd.DataFrame([
        (2021, 5, "LV", A), (2021, 5, "LV", B), (2021, 5, "LV", C),
        (2021, 6, "LV", A), (2021, 6, "LV", B), (2021, 6, "LV", C),
        (2021, 7, "LV", A), (2021, 7, "LV", C),
    ], columns=["season", "week", "team", "gsis_id"])
    away = P2A.roster_away(_ctx(ro), rows)
    assert away.tolist() == [
        False, True, True,      # on the roster: the status decides
        False, True, True,      # LV has a roster in week 6 and neither is on it: away
        False, True,            # no LV roster: unknown for A, C listed elsewhere: away
    ]


def test_injury_list_counts_injury_lists_only():
    cases = [
        ("RES", "R01", True), ("RES", "R48", True), ("RES", "R47", True),
        ("RES", "R04", True), ("RES", "R05", True), ("RES", "", True),
        ("PUP", "", True), ("PUP", "A02", True), ("RSN", "", True), ("RSN", "A03", True),
        ("RES", "R59", False), ("RES", "R62", False), ("RES", "R40", False),
        ("RES", "R30", False), ("RES", "R33", False), ("RES", "R27", False),
        ("RES", "R02", False), ("RES", "R03", False), ("RES", "A01", False),
        ("RSR", "", False), ("SUS", "", False), ("NWT", "", False), ("ACT", "A01", False),
        ("CUT", "W03", False),
    ]
    status = pd.Series([c[0] for c in cases])
    code = pd.Series([c[1] for c in cases])
    got = P2B.injury_list(status, code).tolist()
    assert got == [c[2] for c in cases]


def test_hurt_ignores_covid_and_suspension_reserve():
    ro = pd.DataFrame({
        "season": [2021] * 4, "week": [5, 5, 5, 5], "team": ["NO"] * 4,
        "gsis_id": [A, B, C, "00-0000004"], "status": ["RES"] * 4,
        "status_description_abbr": ["R01", "R59", "R40", None], "position": ["RB"] * 4})
    inj = pd.DataFrame({"season": pd.Series([], dtype=int), "week": pd.Series([], dtype=int),
                        "gsis_id": pd.Series([], dtype=object),
                        "team": pd.Series([], dtype=object),
                        "report_status": pd.Series([], dtype=object),
                        "report_primary_injury": pd.Series([], dtype=object),
                        "practice_primary_injury": pd.Series([], dtype=object)})
    ctx = types.SimpleNamespace(rosters_weekly=lambda seasons: ro.copy(),
                                injuries=lambda seasons: inj.copy(), cutoff=None, _store={})
    hurt = P2B.hurt(ctx, [2021])
    assert sorted(hurt["gsis_id"]) == [A, "00-0000004"]


def test_weather_never_uses_the_observed_reanalysis(monkeypatch):
    def values(kind, venue, season, kick):
        if venue == "has-forecast":
            return (12.0, 1.0)
        if venue == "archive-only" and kind == "archive":
            return (15.0, 2.0)
        return None
    monkeypatch.setattr(P5, "_values", values)
    kick = pd.Timestamp("2019-10-06 13:30", tz="UTC")
    g = pd.DataFrame({"game_id": ["g1", "g2", "g3", "g4"], "indoor": [0.0, 0.0, 1.0, 0.0],
                      "venue": ["has-forecast", "archive-only", "dome", "nothing"],
                      "season": [2019] * 4, "kick_utc": [kick] * 4})
    w = P5.weather(g).set_index("game_id")
    assert w.loc["g1", "wx_source"] == "forecast" and w.loc["g1", "wind_kick"] == 12.0
    assert w.loc["g2", "wx_source"] == "observed, not used"
    assert np.isnan(w.loc["g2", "wind_kick"]) and np.isnan(w.loc["g2", "precip_kick"])
    assert w.loc["g3", "wx_source"] == "indoors" and w.loc["g3", "wind_kick"] == 0.0
    assert w.loc["g4", "wx_source"] == "not cached" and np.isnan(w.loc["g4", "wind_kick"])
