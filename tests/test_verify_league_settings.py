"""The settings re-verification check: it detects drift and never hides it."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from gridiron import league_config as lc

ROOT = Path(__file__).resolve().parent.parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "verify_league_settings", ROOT / "scripts" / "verify_league_settings.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


V = _load()


def matching_league() -> dict:
    """A live-shaped league object built from the verified constants."""
    scoring = {key: float(getattr(lc.DEFAULT_SCORING, field))
               for key, field in V.SCORING_MAP.items()}
    scoring.update({k: float(v) for k, v in lc.KICKING_SCORING.items()})
    scoring.update({k: float(v) for k, v in lc.DEFENSE_SCORING.items()})
    settings = {key: int(getattr(lc, const))
                for key, const in V.SETTINGS_MAP.items()}
    return {
        "league_id": lc.SLEEPER_LEAGUE_ID,
        "name": lc.LEAGUE_NAME,
        "season": str(lc.SEASON_YEAR),
        "status": "in_season",
        "total_rosters": lc.NUM_TEAMS,
        "roster_positions": V.expected_roster_positions(),
        "scoring_settings": scoring,
        "settings": settings,
    }


def test_a_matching_league_reports_no_drift():
    rows = V.compare(matching_league())
    drift = [r for r in rows if not r["match"]]
    assert not drift, drift
    assert len(rows) >= 40, "the check went vacuous"


@pytest.mark.parametrize("mutate,expected_field", [
    (lambda lg: lg["scoring_settings"].__setitem__("rec", 1.0), "reception"),
    (lambda lg: lg["scoring_settings"].__setitem__("pass_int", -2.0),
     "interception"),
    (lambda lg: lg.__setitem__("total_rosters", 10), "NUM_TEAMS"),
    (lambda lg: lg["settings"].__setitem__("playoff_week_start", 14),
     "PLAYOFF_START_WEEK"),
    (lambda lg: lg["settings"].__setitem__("waiver_budget", 50),
     "WAIVER_BUDGET"),
    (lambda lg: lg["roster_positions"].append("WR"), "ROSTER_SLOTS"),
    (lambda lg: lg["scoring_settings"].__setitem__("fgm_50p", 6.0),
     "KICKING_SCORING[fgm_50p]"),
])
def test_every_kind_of_drift_is_caught(mutate, expected_field):
    league = matching_league()
    mutate(league)
    drift = [r for r in V.compare(league) if not r["match"]]
    assert any(expected_field in r["field"] for r in drift), (
        f"{expected_field} drift went undetected; got {[r['field'] for r in drift]}")


def test_the_roster_slot_vocabulary_round_trips():
    positions = V.expected_roster_positions()
    assert positions.count("FLEX") == lc.ROSTER_SLOTS["FLEX"]
    assert positions.count("DEF") == lc.ROSTER_SLOTS["DST"]
    assert positions.count("BN") == lc.ROSTER_SLOTS["BENCH"]
    assert len(positions) == sum(lc.ROSTER_SLOTS.values())


def test_the_script_never_writes_anything():
    """Rule #1's real teeth: a checker that can edit the thing it checks is
    one refactor away from flipping a flag to make itself pass."""
    import ast

    tree = ast.parse((ROOT / "scripts" / "verify_league_settings.py").read_text(
        encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                assert not isinstance(target, ast.Attribute), (
                    f"line {node.lineno} assigns to an attribute — this script "
                    f"must not mutate league_config")
        if isinstance(node, ast.Call):
            name = getattr(node.func, "attr", getattr(node.func, "id", ""))
            assert name not in {"write_text", "write_bytes", "open", "to_csv",
                                "unlink", "mkdir"}, (
                f"line {node.lineno} calls {name}() — the checker is read-only")
