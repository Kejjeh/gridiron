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


def test_verified_values_are_pinned_so_drift_is_a_failing_test():
    """The 2026-09-08 pull, re-verified live 2026-09-17 (55/55 constants, no
    drift) via scripts/verify_league_settings.py. If the league really changes,
    this test changes in the same commit as the constants — never the reverse.
    """
    assert lc.SETTINGS_VERIFIED is True
    assert lc.PLATFORM == "sleeper"
    assert lc.NUM_TEAMS == 12
    assert lc.DEFAULT_SCORING.reception == 0.5      # half-PPR
    assert lc.DEFAULT_SCORING.interception == -1.0  # Sleeper default
    assert lc.DEFAULT_SCORING.pass_td == 4.0
    assert lc.ROSTER_SLOTS == {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2,
                               "DST": 1, "K": 1, "BENCH": 5}
    assert lc.IR_SLOTS == 1
    assert (lc.PLAYOFF_TEAMS, lc.PLAYOFF_START_WEEK, lc.TRADE_DEADLINE_WEEK) \
        == (6, 15, 13)
    assert (lc.WAIVER_TYPE, lc.WAIVER_BUDGET, lc.WAIVER_MIN_BID) \
        == ("faab", 100, 0)
    assert (lc.WAIVER_CLEAR_WEEKDAY, lc.WAIVER_CLEAR_DAYS) == (2, 2)


def test_the_starting_lineup_is_ten_slots():
    started = sum(n for slot, n in lc.ROSTER_SLOTS.items() if slot != "BENCH")
    assert started == 10
    assert sum(lc.ROSTER_SLOTS.values()) + lc.IR_SLOTS == 16


def test_the_league_id_is_a_public_identifier_not_a_credential():
    assert lc.SLEEPER_LEAGUE_ID.isdigit()
    assert lc.MY_SLEEPER_USERNAME
