"""Usage: volume and efficiency stay apart, joins stay id-anchored, and a
missing measurement stays missing."""
from __future__ import annotations

import pandas as pd
import pytest

from gridiron.ids import Crosswalk, normalize_id
from gridiron.usage import (EFFICIENCY_COLUMNS, VOLUME_COLUMNS, attach_snaps,
                            last_n_weeks, player_weeks, score_frame,
                            season_to_date, weeks_present)


def test_volume_and_efficiency_are_disjoint_vocabularies():
    """Rule #6 is a modelling claim, so the code must not let the two blur."""
    assert not set(VOLUME_COLUMNS) & set(EFFICIENCY_COLUMNS)
    assert "targets" in VOLUME_COLUMNS and "yards_per_target" in EFFICIENCY_COLUMNS


def test_score_frame_uses_the_one_scoring_implementation(weekly_offense):
    scored = score_frame(weekly_offense)
    mid = (scored.fantasy_points + scored.fantasy_points_ppr) / 2
    # DEFAULT_SCORING uses int -1 where nflverse standard uses -2.
    adj = mid + scored.passing_interceptions.fillna(0) * 1.0
    assert (scored.league_points - adj).abs().max() == pytest.approx(0, abs=1e-9)
    assert "league_points" in scored.columns
    assert "league_points" not in weekly_offense.columns, "must not mutate input"


def test_kickers_are_routed_to_kicker_scoring(weekly_kickers):
    scored = score_frame(weekly_kickers)
    assert (scored.league_points - scored.sleeper_points).abs().max() < 1e-9


def test_snaps_join_through_pfr_ids_not_names(weekly_offense, snaps_wk1,
                                              crosswalk):
    joined = attach_snaps(score_frame(weekly_offense), snaps_wk1, crosswalk)
    assert joined["offense_pct"].notna().mean() >= 0.8
    # Break the id edge but keep the names identical: the join must collapse.
    blind = Crosswalk({}, {})
    unjoined = attach_snaps(score_frame(weekly_offense), snaps_wk1, blind)
    assert unjoined["offense_pct"].notna().sum() == 0, (
        "snaps resolved without an id edge — something is matching on name")


def test_a_player_with_no_snap_row_is_blank_not_zero(weekly_offense, crosswalk):
    empty = pd.DataFrame(columns=["pfr_player_id", "season", "week",
                                  "offense_snaps", "offense_pct"])
    joined = attach_snaps(score_frame(weekly_offense), empty, crosswalk)
    assert joined["offense_pct"].isna().all(), (
        "'no snap row' and 'played no snaps' are different facts")


def test_missing_snaps_frame_is_handled(weekly_offense, crosswalk):
    joined = attach_snaps(score_frame(weekly_offense), None, crosswalk)
    assert "offense_pct" in joined.columns and joined["offense_pct"].isna().all()


def test_duplicate_snap_rows_cannot_duplicate_a_player_week(weekly_offense,
                                                            snaps_wk1, crosswalk):
    doubled = pd.concat([snaps_wk1, snaps_wk1], ignore_index=True)
    joined = attach_snaps(score_frame(weekly_offense), doubled, crosswalk)
    assert len(joined) == len(weekly_offense)


def test_season_to_date_aggregates_and_derives(weekly_offense, snaps_wk1,
                                               crosswalk):
    std = season_to_date(player_weeks(weekly_offense, snaps_wk1, crosswalk))
    assert len(std) == weekly_offense.player_id.nunique()
    assert {"gsis_id", "games", "points", "ppg", "opportunities"} <= set(std.columns)
    row = std.iloc[0]
    assert row.ppg == pytest.approx(row.points / row.games)
    assert row.opportunities == pytest.approx(row.carries + row.targets)
    assert std.points.is_monotonic_decreasing


def test_efficiency_is_blank_when_the_denominator_is_zero(weekly_offense,
                                                          snaps_wk1, crosswalk):
    std = season_to_date(player_weeks(weekly_offense, snaps_wk1, crosswalk))
    no_targets = std.loc[std.targets == 0]
    assert len(no_targets), "fixture has no zero-target player to check"
    assert no_targets.yards_per_target.isna().all(), (
        "0 targets must give a blank yards-per-target, never 0.0")


class TestChronologicalBoundary:
    """No future week may leak into a through-week aggregate. This is the
    boundary every backtest and every in-season 'as of week N' read leans on.
    """

    def _frame(self):
        rows = []
        for week, yards in ((1, 100.0), (2, 200.0), (3, 400.0)):
            rows.append(dict(player_id="00-0000001",
                             player_display_name="Test Back", position="RB",
                             team="AAA", season=2026, week=week,
                             rushing_yards=yards, carries=10.0, targets=0.0,
                             receptions=0.0, receiving_yards=0.0,
                             rushing_tds=0.0, receiving_tds=0.0))
        return pd.DataFrame(rows)

    def _pw(self):
        return player_weeks(self._frame(), None, Crosswalk({}, {}))

    def test_through_week_truncates_the_future(self):
        std = season_to_date(self._pw(), through_week=2)
        assert std.iloc[0].games == 2
        assert std.iloc[0].points == pytest.approx(30.0)  # (100+200) * 0.1

    def test_through_week_none_reads_everything(self):
        assert season_to_date(self._pw()).iloc[0].games == 3

    def test_a_trailing_window_cannot_see_past_its_right_edge(self):
        window = last_n_weeks(self._pw(), through_week=2, n=2)
        assert window.iloc[0].games == 2
        assert window.iloc[0].points == pytest.approx(30.0)

    def test_a_trailing_window_drops_weeks_before_its_left_edge(self):
        window = last_n_weeks(self._pw(), through_week=3, n=1)
        assert window.iloc[0].games == 1
        assert window.iloc[0].points == pytest.approx(40.0)


def test_an_empty_frame_aggregates_to_an_empty_frame():
    out = season_to_date(pd.DataFrame(columns=["gsis_id", "week"]))
    assert len(out) == 0 and "gsis_id" in out.columns


def test_weeks_present_reports_coverage(weekly_offense):
    assert weeks_present(weekly_offense) == (1,)
    assert weeks_present(None) == () and weeks_present(pd.DataFrame()) == ()


def test_gsis_ids_are_normalised_on_the_way_in(weekly_offense, crosswalk):
    dirty = weekly_offense.copy()
    dirty["player_id"] = [" " + str(p) for p in dirty["player_id"]]
    joined = attach_snaps(score_frame(dirty), None, crosswalk)
    assert all(g == normalize_id(g) for g in joined["gsis_id"])
