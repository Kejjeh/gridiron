"""Scoring against real nflverse columns, pinned to two external ground truths.

The bug this file exists to prevent: `gridiron.scoring` originally read the
column names `interceptions`, `fumbles_lost` and `two_point_conversions`,
none of which exist in the nflverse weekly frame any more. Missing columns
score as ZERO, so every interception, lost fumble and two-point conversion
silently vanished — a wrong number that looks exactly like a right one.

Ground truth 1 (public, in the fixture): nflverse ships `fantasy_points`
(standard) and `fantasy_points_ppr` beside each row. Only the reception
weight differs between them, so half-PPR is their exact midpoint.

Ground truth 2 (public, in the fixture): Sleeper's own scored points for
each kicker in week 1 of this league's scoring.

Fixtures are read with the stdlib `csv` module, not pandas, and this file
deliberately uses NO shared conftest fixture: the scoring implementation is
stdlib-pure and its test suite is too, so `python -m pytest
tests/test_scoring_nflverse.py` runs against this repo's declared
dependencies alone.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

from gridiron.league_config import DEFAULT_SCORING, KICKING_SCORING, ScoringRules
from gridiron.scoring import fantasy_points, kicker_points, scoring_inputs

FIXTURES = Path(__file__).resolve().parent / "fixtures"

#: nflverse standard scoring uses -2 per interception; the league uses -1.
NFLVERSE_STANDARD = ScoringRules(interception=-2.0, reception=0.5)

#: Spellings an nflverse CSV export uses for "no observation here".
_MISSING = {"", "NA", "NAN", "NONE", "NULL", "<NA>"}


def parse_cell(raw: str) -> object:
    """One CSV cell -> float, str, or None for a MISSING observation.

    An empty cell becomes None, never 0.0. `gridiron.scoring` is the single
    place allowed to decide that an absent stat scores zero (`_num` maps both
    None and NaN to 0.0); collapsing blanks here would move that decision
    into the test harness and make a column that stopped being populated
    indistinguishable from one that is genuinely all zeros. It also keeps
    this loader's semantics identical to `pandas.read_csv`, which turns the
    same blank into NaN.
    """
    raw = raw.strip()
    if raw.upper() in _MISSING:
        return None
    try:
        return float(raw)
    except ValueError:
        return raw  # ids, names, team codes


def read_fixture(name: str) -> list[dict[str, object]]:
    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        return [{k: parse_cell(v) for k, v in row.items()}
                for row in csv.DictReader(fh)]


def columns(name: str) -> set[str]:
    with (FIXTURES / name).open(newline="", encoding="utf-8") as fh:
        return set(csv.DictReader(fh).fieldnames or ())


def column_total(rows: list[dict[str, object]], *names: str) -> float:
    """Sum named columns, counting a MISSING cell as absent, not as zero.

    KeyError on an unknown column is deliberate: a renamed column must fail
    loudly here rather than quietly total zero — that is the exact failure
    mode this whole file exists to catch.
    """
    return sum(float(v) for row in rows for name in names
               if (v := row[name]) is not None)


OFFENSE = read_fixture("weekly_offense_wk1.csv")
KICKERS = read_fixture("weekly_kickers_wk1.csv")


# --- the loader's own contract ----------------------------------------------

def test_an_empty_cell_loads_as_missing_not_as_zero(tmp_path):
    """The distinction this port had to preserve: pandas reads a blank as
    NaN, and NaN is not 0.0 until `scoring._num` says so."""
    path = tmp_path / "blanks.csv"
    path.write_text("player_id,receptions,receiving_yards\nX,,40\n", encoding="utf-8")
    with path.open(newline="", encoding="utf-8") as fh:
        row = {k: parse_cell(v) for k, v in next(csv.DictReader(fh)).items()}
    assert row["receptions"] is None, "a blank cell must not become 0.0"
    assert row["receiving_yards"] == 40.0
    assert row["player_id"] == "X"
    # and the single place that IS allowed to decide a blank scores zero:
    assert fantasy_points(row, DEFAULT_SCORING) == pytest.approx(4.0)


def test_the_loader_reads_na_spellings_as_missing():
    assert parse_cell("NA") is None and parse_cell("  ") is None
    assert parse_cell("<NA>") is None and parse_cell("0") == 0.0


# --- ground truth 1: nflverse's own scored columns ---------------------------

def test_half_ppr_is_the_exact_midpoint_of_nflverse_standard_and_ppr():
    assert len(OFFENSE) >= 10, "fixture shrank — this test would go vacuous"
    for row in OFFENSE:
        mid = (row["fantasy_points"] + row["fantasy_points_ppr"]) / 2
        assert fantasy_points(row, NFLVERSE_STANDARD) == pytest.approx(mid, abs=1e-9), (
            f"{row['player_display_name']} scores "
            f"{fantasy_points(row, NFLVERSE_STANDARD)} vs nflverse {mid}"
        )


def test_the_parity_fixture_actually_exercises_the_renamed_columns():
    """A parity test over rows with no INT, no fumble and no 2pt would pass
    with the old broken key names. Fail loudly if the fixture drifts there."""
    fumbles = column_total(OFFENSE, "sack_fumbles_lost", "rushing_fumbles_lost",
                           "receiving_fumbles_lost")
    twopt = column_total(OFFENSE, "passing_2pt_conversions",
                         "rushing_2pt_conversions", "receiving_2pt_conversions")
    ints = column_total(OFFENSE, "passing_interceptions")
    assert fumbles > 0 and twopt > 0 and ints > 0, (
        f"fixture is vacuous for the renamed columns: "
        f"fumbles={fumbles} twopt={twopt} ints={ints}")


# --- ground truth 2: Sleeper's own scored kicker points ----------------------

def test_kicker_points_match_sleeper_actuals():
    assert len(KICKERS) >= 10
    for row in KICKERS:
        got = kicker_points(row, KICKING_SCORING)
        assert got == pytest.approx(row["sleeper_points"], abs=1e-9), (
            f"{row['player_display_name']}: ours {got} vs Sleeper "
            f"{row['sleeper_points']}")


def test_kicker_fixture_covers_a_miss_and_a_long_make():
    assert column_total(KICKERS, "fg_missed") > 0
    assert column_total(KICKERS, "fg_made_50_59") > 0


# --- the rename itself -------------------------------------------------------

def test_legacy_column_names_still_score():
    """Pre-rewrite frames (and the 2023-25 cache) keep working."""
    legacy = {"passing_yards": 300, "passing_tds": 2, "interceptions": 1,
              "fumbles_lost": 1, "two_point_conversions": 1}
    assert fantasy_points(legacy, DEFAULT_SCORING) == pytest.approx(
        300 * 0.04 + 2 * 4 - 1 - 2 + 2)


def test_a_frame_carrying_both_spellings_does_not_double_count():
    both = {"passing_interceptions": 2, "interceptions": 2}
    assert fantasy_points(both, DEFAULT_SCORING) == pytest.approx(-2.0)
    both_f = {"sack_fumbles_lost": 1, "rushing_fumbles_lost": 0,
              "receiving_fumbles_lost": 0, "fumbles_lost": 5}
    assert fantasy_points(both_f, DEFAULT_SCORING) == pytest.approx(-2.0)


def test_fumbles_lost_total_is_not_used():
    """It counts return fumbles, which nflverse and Sleeper both exclude from
    the player's offensive score."""
    assert fantasy_points({"fumbles_lost_total": 3}, DEFAULT_SCORING) == 0.0


def test_nan_and_none_score_as_zero_not_as_a_crash():
    assert fantasy_points({"passing_yards": float("nan"), "rushing_tds": None,
                           "receptions": 3}) == pytest.approx(1.5)
    assert kicker_points({"pat_made": float("nan"), "fg_made_40_49": 1}) == 4.0


def test_scoring_inputs_lists_every_column_read():
    cols = scoring_inputs()
    assert "passing_interceptions" in cols and "fg_made_50_59" in cols
    assert "interceptions" not in cols, "legacy aliases are not ingest contracts"
    present = columns("weekly_offense_wk1.csv") | columns("weekly_kickers_wk1.csv")
    assert cols <= present, (
        "the fixtures no longer carry every column scoring reads: "
        f"{sorted(cols - present)}")


def test_reception_weight_is_the_verified_league_value():
    assert DEFAULT_SCORING.reception == 0.5      # half-PPR, verified on Sleeper
    assert DEFAULT_SCORING.interception == -1.0  # Sleeper default, not ESPN's -2
    assert not math.isnan(DEFAULT_SCORING.pass_yd)
