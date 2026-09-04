"""Contract pins on league constants — and the unverified-settings tripwire."""
from __future__ import annotations

from gridiron import league_config as lc


def test_season_year_is_current():
    assert lc.SEASON_YEAR == 2026


def test_flex_eligibility_is_a_subset_of_roster_positions():
    assert set(lc.FLEX_ELIGIBLE) <= set(lc.ROSTER_SLOTS)


def test_starting_slots_are_positive_ints():
    assert all(isinstance(n, int) and n > 0 for n in lc.ROSTER_SLOTS.values())


def test_scoring_rules_are_frozen():
    import dataclasses
    assert dataclasses.is_dataclass(lc.ScoringRules)
    try:
        lc.DEFAULT_SCORING.reception = 99.0
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("ScoringRules must be frozen (rule #2)")


def test_unverified_settings_flag_exists():
    """Rule #1: engines must be able to check this flag. The flag existing
    (either state) is the contract; flipping it to True requires pulling the
    real settings from the platform in the same commit."""
    assert isinstance(lc.SETTINGS_VERIFIED, bool)
