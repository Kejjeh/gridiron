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


# ---------------------------------------------------------------- as-of line
# A report about week N must read exactly what week N could have read. The
# cache is append-only and grows past the week you are rendering, so the
# boundary has to come from the report week and the phase — never from
# max(weeks present), which is whatever the last pull happened to fetch.

SUN_NIGHT = datetime(2026, 9, 20, 23, 0, tzinfo=UTC)   # week-2 slate done
WK2_KICKOFFS = [datetime(2026, 9, 20, 17, 0, tzinfo=UTC)]


def test_a_pregame_report_may_not_read_its_own_week():
    """Before the games, week N has no box scores worth the name. The
    boundary is N-1 and a partial week-N row must not sneak in."""
    ctx = WeekContext.build(season=2026, report_week=2, stats_weeks=[1, 2],
                            kickoffs=[THU + timedelta(days=3)], now=THU)
    assert ctx.phase is Phase.PREGAME
    assert ctx.evidence_boundary == 1
    assert ctx.stats_through == 1
    assert ctx.withheld_weeks == (2,)


def test_a_completed_week_is_its_own_evidence():
    ctx = WeekContext.build(season=2026, report_week=2, stats_weeks=[1, 2],
                            kickoffs=WK2_KICKOFFS, now=SUN_NIGHT)
    assert ctx.phase is Phase.COMPLETE
    assert ctx.evidence_boundary == 2
    assert ctx.stats_through == 2
    assert ctx.withheld_weeks == ()
    assert not ctx.is_historical


def test_a_week_after_the_boundary_is_withheld_not_read():
    """The leak this exists to stop: re-rendering week 2 in week 6."""
    ctx = WeekContext.build(season=2026, report_week=2,
                            stats_weeks=[1, 2, 3, 4, 5],
                            kickoffs=WK2_KICKOFFS, now=SUN_NIGHT)
    assert ctx.stats_through == 2
    assert ctx.withheld_weeks == (3, 4, 5)
    assert ctx.is_historical
    blob = " ".join(ctx.notes)
    assert "WITHHELD" in blob and "3, 4, 5" in blob


def test_a_historical_re_render_matches_what_that_week_saw():
    """The property that makes a backtest honest: adding later weeks to the
    cache changes nothing about an earlier week's report."""
    at_the_time = WeekContext.build(season=2026, report_week=2,
                                    stats_weeks=[1, 2], kickoffs=WK2_KICKOFFS,
                                    now=SUN_NIGHT)
    much_later = WeekContext.build(season=2026, report_week=2,
                                   stats_weeks=list(range(1, 19)),
                                   kickoffs=WK2_KICKOFFS, now=SUN_NIGHT)
    assert much_later.stats_through == at_the_time.stats_through
    assert much_later.stats_lag_weeks == at_the_time.stats_lag_weeks
    assert much_later.headline() == at_the_time.headline()


def test_the_boundary_does_not_move_when_the_cache_runs_dry():
    """A gap in the cache is a lag, not a new boundary. Week 4 pregame with
    box scores stopping at week 1 is two weeks behind and says so."""
    ctx = WeekContext.build(season=2026, report_week=4, stats_weeks=[1],
                            kickoffs=[THU + timedelta(days=3)], now=THU)
    assert ctx.evidence_boundary == 3
    assert ctx.stats_through == 1
    assert ctx.stats_lag_weeks == 3 and ctx.rolled_over
    assert any("rolled over" in n for n in ctx.notes)


def test_withholding_everything_leaves_usage_blank_not_zero():
    """Week 1 pregame: there is no admissible week at all. That is 'no
    evidence', which the report must not render as a row of zeros."""
    ctx = WeekContext.build(season=2026, report_week=1, stats_weeks=[1],
                            kickoffs=[THU + timedelta(days=3)], now=THU)
    assert ctx.stats_through is None
    assert ctx.withheld_weeks == (1,)
    assert any("blank, not zero" in n for n in ctx.notes)


def test_a_historical_report_admits_its_designations_are_after_the_fact():
    """Injury status and market lines come from the latest pull, not from an
    archive. A re-rendered week cannot claim to know what was known then."""
    ctx = WeekContext.build(season=2026, report_week=2, stats_weeks=[1, 2, 7],
                            kickoffs=WK2_KICKOFFS, now=SUN_NIGHT)
    assert any("not what was known before kickoff" in n for n in ctx.notes)


# ------------------------------------------------------------ covered weeks
# "covers through week 5" is the LAST week, not the whole set. A hole inside
# the range is a data defect that staleness checks cannot see.

def test_a_contiguous_range_reads_as_a_range():
    f = assess("weekly_stats", now=THU, as_of=THU, rows=900,
               covers_through_week=5, covered_weeks=[1, 2, 3, 4, 5])
    assert f.week_gaps == ()
    assert f.coverage() == "wk1-5"
    assert "covers wk1-5" in f.line()


def test_a_hole_inside_the_covered_range_is_named():
    """The failure this catches: week 3 never landed, every season total is
    short by a week, and 'covers wk5' says nothing is wrong."""
    f = assess("weekly_stats", now=THU, as_of=THU, rows=700,
               covers_through_week=5, covered_weeks=[1, 2, 4, 5])
    assert f.status is Status.FRESH, "a gap is a defect, not staleness"
    assert f.week_gaps == (3,)
    assert f.coverage() == "wk1-5 (no wk3)"


def test_a_gap_is_its_own_degradation_line():
    ctx = WeekContext.build(season=2026, report_week=6, stats_weeks=[1, 2, 4, 5],
                            kickoffs=[THU + timedelta(days=3)], now=THU)
    gappy = assess("weekly_stats", now=THU, as_of=THU, rows=700,
                   covers_through_week=5, covered_weeks=[1, 2, 4, 5])
    notes = degradations([gappy], ctx)
    gap_note = next(n for n in notes if "GAP" in n)
    assert "week(s) 3" in gap_note and "short by those weeks" in gap_note


def test_a_single_covered_week_is_not_a_range():
    f = assess("injuries", now=THU, as_of=THU, rows=300,
               covers_through_week=2, covered_weeks=[2])
    assert f.coverage() == "wk2" and f.week_gaps == ()


def test_a_source_with_no_week_column_still_renders_its_line():
    """The crosswalk has no weeks at all. It must not print a fake range."""
    f = assess("crosswalk", now=THU, as_of=THU, rows=12000)
    assert f.coverage() == "—" and f.week_gaps == ()
