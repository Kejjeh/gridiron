"""The chronological evaluation cannot see the week it predicts."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from gridiron.evaluate import PREDICTORS, chronological_evaluation
from gridiron.ids import Crosswalk
from gridiron.usage import player_weeks

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _weeks(n: int, *, scale: float = 1.0) -> pd.DataFrame:
    cw = Crosswalk.from_csv(FIXTURES / "crosswalk_small.csv")
    w1 = pd.concat([pd.read_csv(FIXTURES / "weekly_offense_wk1.csv"),
                    pd.read_csv(FIXTURES / "weekly_kickers_wk1.csv")], ignore_index=True)
    parts = []
    for k in range(1, n + 1):
        wk = w1.copy()
        wk["week"] = k
        f = 1.0 + 0.1 * k * scale
        for c in ("targets", "receptions", "receiving_yards", "carries", "rushing_yards",
                  "passing_yards"):
            wk[c] = wk[c] * f
        parts.append(wk)
    return player_weeks(pd.concat(parts, ignore_index=True),
                        pd.read_csv(FIXTURES / "snaps_wk1.csv"), cw)


@pytest.fixture(scope="module")
def schedule():
    return pd.read_csv(FIXTURES / "schedules_wk1_2.csv")


def test_one_week_cannot_be_evaluated_and_says_so(schedule):
    rep = chronological_evaluation(_weeks(1), schedule)
    assert rep.n == 0 and "NOT EVALUATED" in rep.verdict()
    assert not rep.pwin_calibrated


def test_each_week_is_predicted_only_from_the_weeks_before_it(schedule):
    base = chronological_evaluation(_weeks(3), schedule)
    assert base.weeks_evaluated == (2, 3)
    # Blow up week 3: week-2 predictions must not move at all.
    loud = _weeks(3)
    mask = loud["week"] == 3
    for c in ("targets", "receptions", "receiving_yards", "carries", "rushing_yards",
              "passing_yards", "league_points"):
        loud.loc[mask, c] = loud.loc[mask, c] * 10
    after = chronological_evaluation(loud, schedule)
    a = base.rows.loc[base.rows["week"] == 2].set_index("gsis_id")["baseline"]
    b = after.rows.loc[after.rows["week"] == 2].set_index("gsis_id")["baseline"]
    assert a.equals(b)
    # ...and week-3 predictions are also unchanged, since only week-3 ACTUALS moved
    a3 = base.rows.loc[base.rows["week"] == 3].set_index("gsis_id")["baseline"]
    b3 = after.rows.loc[after.rows["week"] == 3].set_index("gsis_id")["baseline"]
    assert a3.equals(b3)
    # but the SCORE for week 3 did change, because the actuals did
    assert after.score("baseline").mae != base.score("baseline").mae


def test_through_week_caps_the_evaluation_at_the_evidence_boundary(schedule):
    rep = chronological_evaluation(_weeks(3), schedule, through_week=2)
    assert rep.weeks_evaluated == (2,)


def test_all_three_predictors_are_scored_on_the_same_rows(schedule):
    rep = chronological_evaluation(_weeks(3), schedule)
    ns = {s.name: s.n for s in rep.scores}
    assert set(ns) == set(PREDICTORS) and len(set(ns.values())) == 1
    assert rep.n_calibration == rep.n
    assert 0.0 <= rep.within_1sd <= 1.0
    assert rep.baseline_beats_ppg in (True, False)
    assert "UNVALIDATED" in rep.verdict()
    assert set(rep.by_position) <= {"QB", "RB", "WR", "TE", "K"}


def test_an_empty_frame_is_a_verdict_not_a_crash(schedule):
    rep = chronological_evaluation(pd.DataFrame(), schedule)
    assert rep.n == 0 and rep.notes
