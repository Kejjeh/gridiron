"""Rule #8: the week has a shape, and stale data says so.

These tests pin the three ways a weekly report can lie about time:
  1. calling a source fresh because the FILE is new when the PULL is old,
  2. printing week N-1 usage under a week N heading after the week rolls over,
  3. treating "we have no data" and "the data says zero" as the same thing.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from gridiron.freshness import (CADENCES, Cadence, DayShape, Phase, Status,
                                WeekContext, age_hours, assess, day_shape,
                                degradations, phase_for)

UTC = timezone.utc
# 2026-09-17 is a Thursday; 13:00 UTC is 09:00 ET.
THU = datetime(2026, 9, 17, 13, 0, tzinfo=UTC)
TUE = datetime(2026, 9, 15, 13, 0, tzinfo=UTC)
WED = datetime(2026, 9, 16, 13, 0, tzinfo=UTC)
FRI = datetime(2026, 9, 18, 13, 0, tzinfo=UTC)
SUN = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize("now,shape", [
    (TUE, DayShape.PLANNING),
    (WED, DayShape.WAIVER_DAY),
    (THU, DayShape.GAMEDAY),
    (FRI, DayShape.DESIGNATION_DAY),
    (SUN, DayShape.GAMEDAY),
])
def test_the_week_has_a_shape_in_eastern_time(now, shape):
    assert day_shape(now) is shape


def test_an_et_midnight_boundary_is_not_read_as_the_next_day():
    """23:30 ET Tuesday is 03:30 UTC Wednesday. Waiver day starts in ET."""
    late_tuesday = datetime(2026, 9, 16, 3, 30, tzinfo=UTC)
    assert day_shape(late_tuesday) is DayShape.PLANNING


def test_injuries_get_a_tighter_limit_on_a_gameday():
    cad = CADENCES["injuries"]
    assert cad.limit_for(TUE) == cad.max_age_hours
    assert cad.limit_for(SUN) == cad.gameday_max_age_hours
    assert cad.gameday_max_age_hours < cad.max_age_hours


def test_a_fresh_pull_is_fresh():
    f = assess("weekly_stats", now=THU, as_of=THU - timedelta(hours=2), rows=100,
               covers_through_week=1)
    assert f.status is Status.FRESH and f.usable


def test_an_old_pull_is_stale_even_though_the_rows_are_there():
    f = assess("injuries", now=SUN, as_of=SUN - timedelta(hours=40), rows=300,
               covers_through_week=2, required_week=2)
    assert f.status is Status.STALE
    assert f.usable, "stale data is shown, labelled — not withheld"
    assert "over the" in f.reason


def test_a_source_that_never_pulled_is_missing_not_empty():
    f = assess("snap_counts", now=THU, as_of=None, rows=0)
    assert f.status is Status.MISSING and not f.usable
    assert f.reason == "not pulled"


def test_a_pull_that_returned_nothing_is_missing_not_zero():
    f = assess("snap_counts", now=THU, as_of=THU, rows=0)
    assert f.status is Status.MISSING and f.reason == "pulled but empty"


def test_a_forward_looking_source_that_lags_the_report_week_is_stale():
    """The injury table was pulled a minute ago but only has week 1 in it."""
    f = assess("injuries", now=THU, as_of=THU, rows=300, covers_through_week=1,
               required_week=2)
    assert f.status is Status.STALE
    assert "only covers week 1" in f.reason


def test_a_backward_looking_source_may_legitimately_lag():
    """Box scores for week 2 cannot exist before week 2 is played."""
    f = assess("weekly_stats", now=THU, as_of=THU, rows=1000,
               covers_through_week=1, required_week=2)
    assert f.status is Status.FRESH


def test_age_hours_treats_a_naive_stamp_as_utc():
    assert age_hours(datetime(2026, 9, 17, 11, 0), THU) == pytest.approx(2.0)
    assert age_hours(None, THU) is None


def test_phase_tracks_the_slate():
    ks = [datetime(2026, 9, 17, 20, 15, tzinfo=UTC),
          datetime(2026, 9, 20, 17, 0, tzinfo=UTC)]
    assert phase_for(ks, THU) is Phase.PREGAME
    assert phase_for(ks, datetime(2026, 9, 18, 2, 0, tzinfo=UTC)) is Phase.IN_PROGRESS
    assert phase_for(ks, datetime(2026, 9, 21, 6, 0, tzinfo=UTC)) is Phase.COMPLETE
    assert phase_for([], THU) is Phase.PREGAME, "no schedule == not started"


class TestWeekRollover:
    """The single most common in-season lie: last week's usage under this
    week's heading."""

    def _ctx(self, report_week, stats_weeks, kickoffs=(), now=THU):
        return WeekContext.build(season=2026, report_week=report_week,
                                 stats_weeks=stats_weeks, kickoffs=kickoffs,
                                 now=now)

    def test_a_one_week_lag_before_kickoff_is_the_healthy_state(self):
        ctx = self._ctx(2, [1], [datetime(2026, 9, 18, 0, 15, tzinfo=UTC)])
        assert ctx.phase is Phase.PREGAME
        assert ctx.stats_lag_weeks == 1 and ctx.expected_lag == 1
        assert not ctx.rolled_over and ctx.notes == ()
        assert "through week 1" in ctx.headline()

    def test_the_week_ticking_over_without_new_box_scores_is_flagged(self):
        ctx = self._ctx(3, [1])
        assert ctx.stats_lag_weeks == 2 and ctx.rolled_over
        assert any("rolled over to 3" in n for n in ctx.notes)
        assert "lag 2w" in ctx.headline()

    def test_a_completed_week_should_have_its_own_box_scores(self):
        past = [datetime(2026, 9, 10, 0, 15, tzinfo=UTC)]
        ctx = self._ctx(2, [1], past, now=THU)
        assert ctx.phase is Phase.COMPLETE and ctx.expected_lag == 0
        assert ctx.rolled_over, "week 2 is final but week 2 box scores are absent"

    def test_no_box_scores_at_all_is_blank_not_zero(self):
        ctx = self._ctx(1, [])
        assert ctx.stats_through is None and ctx.stats_lag_weeks is None
        assert any("blank, not zero" in n for n in ctx.notes)
        assert "NO box scores" in ctx.headline()

    def test_the_headline_always_names_season_week_and_phase(self):
        head = self._ctx(2, [1]).headline()
        assert "2026" in head and "week 2" in head and "pregame" in head


def test_degradations_collects_every_reason_in_one_block():
    ctx = WeekContext.build(season=2026, report_week=3, stats_weeks=[1],
                            kickoffs=[], now=THU)
    sources = [
        assess("injuries", now=THU, as_of=None, rows=0),
        assess("snap_counts", now=THU, as_of=THU - timedelta(days=9), rows=50),
        assess("weekly_stats", now=THU, as_of=THU, rows=10),
    ]
    notes = degradations(sources, ctx)
    assert any("rolled over" in n for n in notes)
    assert any(n.startswith("injuries: MISSING") for n in notes)
    assert any(n.startswith("snap_counts: STALE") for n in notes)
    assert not any("weekly_stats" in n for n in notes), "fresh sources are quiet"


def test_an_unknown_source_gets_a_conservative_default_cadence():
    f = assess("something_new", now=THU, as_of=THU - timedelta(hours=100), rows=5)
    assert f.status is Status.STALE


def test_a_custom_cadence_can_be_passed_in():
    cad = Cadence("x", max_age_hours=1.0)
    f = assess("x", now=THU, as_of=THU - timedelta(hours=2), rows=1, cadence=cad)
    assert f.status is Status.STALE
