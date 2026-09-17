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


# --- end to end against the SAVED league fixture, never the live league ------

def _saved_league() -> dict:
    """The committed synthetic snapshot (`tests/fixtures/sleeper_league.json`):
    two teams, invented user ids, invented league name. Read-only, offline,
    and deliberately NOT the owner's league."""
    import json

    payload = json.loads(
        (ROOT / "tests" / "fixtures" / "sleeper_league.json").read_text(
            encoding="utf-8"))
    return payload["league"]


def test_the_checker_runs_end_to_end_off_a_saved_league_with_no_network():
    """`SleeperReadOnly` takes an injected `fetch`, so the whole verification
    path — transport, league read, comparison — runs against a file."""
    from gridiron.sleeper import SleeperReadOnly

    urls: list[str] = []

    def fetch(url: str):
        urls.append(url)
        return _saved_league()

    league = SleeperReadOnly("TESTLEAGUE", fetch=fetch).league()
    rows = V.compare(league)

    assert urls == ["https://api.sleeper.app/v1/league/TESTLEAGUE"], (
        "the settings check must issue exactly one GET and no other call")
    assert len(rows) >= 25, f"the comparison went vacuous: {len(rows)} rows"


def test_a_different_league_is_reported_as_drift_not_quietly_accepted():
    """The saved fixture is a 2-team league called 'Fixture League'. Running
    the checker against it MUST light up — a checker that shrugs at the wrong
    league would shrug at a mid-season settings edit too."""
    drift = [r for r in V.compare(_saved_league()) if not r["match"]]
    fields = {r["field"] for r in drift}
    assert "NUM_TEAMS" in fields, f"12-team vs 2-team went undetected: {fields}"
    assert "LEAGUE_NAME" in fields


def test_a_league_payload_with_no_scoring_block_is_drift_not_agreement():
    """Absence of evidence is not evidence of a match. The saved fixture
    carries the league SHAPE and no `scoring_settings`/`settings` blocks, so
    every scoring weight and every settings constant must come back
    live=None and match=False rather than quietly passing."""
    league = _saved_league()
    assert "scoring_settings" not in league and "settings" not in league

    rows = {r["field"]: r for r in V.compare(league)}
    for field in ("ScoringRules.reception (rec)", "ScoringRules.interception (pass_int)",
                  "WAIVER_BUDGET", "PLAYOFF_START_WEEK"):
        assert rows[field]["live"] is None, field
        assert rows[field]["match"] is False, (
            f"{field} was absent from the payload and still reported a match")


def test_weights_the_payload_omits_are_counted_not_silently_dropped():
    """Kicking/defense weights Sleeper does not return cannot be compared —
    but a check that silently covers less than it did last week is exactly
    what rule #1 is about, so they are reported."""
    unchecked = V.unchecked_weights(_saved_league())
    assert unchecked, "the fixture has no scoring block; nothing was reported"
    assert any(u.startswith("KICKING_SCORING[") for u in unchecked)
    assert V.unchecked_weights(matching_league()) == [], (
        "a full payload must leave nothing unchecked")


def test_the_roster_shape_in_the_saved_fixture_round_trips():
    """The synthetic fixture was built with this league's roster shape, so it
    is the one block that legitimately matches: proof the vocabulary mapping
    (DST->DEF, BENCH->BN) is right, on a payload nobody hand-fed to it."""
    league = _saved_league()
    assert league["roster_positions"] == V.expected_roster_positions()


def test_the_checker_never_reads_a_credential():
    """Sleeper's read API needs no auth. A cookie or token in the settings
    path would mean the verification route had grown a private surface."""
    import re

    text = (ROOT / "scripts" / "verify_league_settings.py").read_text(
        encoding="utf-8")
    assert not re.search(r"espn_s2|swid|cookie|api_key|token|password", text, re.I)
