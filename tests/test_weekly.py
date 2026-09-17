"""The weekly report's honesty invariants.

Every test here corresponds to a specific way a fantasy tool lies:
telling you a player is healthy because he isn't on a report you never
loaded; printing 0.0 where it means "no data"; showing Wednesday's
designation on Sunday; quietly inventing a market line; or ranking a lineup
off a model that does not exist.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from gridiron.freshness import Status, WeekContext, assess
from gridiron.ids import Crosswalk
from gridiron.usage import player_weeks, season_to_date
from gridiron.weekly import (PROJECTION_STATUS, AvailabilityNote, availability,
                             build_report, injury_index, markdown_table,
                             schedule_index)

UTC = timezone.utc
NOW = datetime(2026, 9, 17, 13, 0, tzinfo=UTC)
WEEK = 2


def _sources(now=NOW, *, injuries_as_of=NOW, injury_weeks=2, rows=300):
    return [
        assess("sleeper_league", now=now, as_of=now, rows=12,
               covers_through_week=WEEK, required_week=WEEK),
        assess("injuries", now=now, as_of=injuries_as_of, rows=rows,
               covers_through_week=injury_weeks, required_week=WEEK),
        assess("schedules", now=now, as_of=now, rows=272,
               covers_through_week=18, required_week=WEEK),
        assess("weekly_stats", now=now, as_of=now, rows=1000,
               covers_through_week=1),
        assess("snap_counts", now=now, as_of=now, rows=1000,
               covers_through_week=1),
        assess("crosswalk", now=now, as_of=now, rows=12000),
    ]


@pytest.fixture
def ctx():
    return WeekContext.build(season=2026, report_week=WEEK, stats_weeks=[1],
                             kickoffs=[datetime(2026, 9, 18, 0, 15, tzinfo=UTC)],
                             now=NOW)


@pytest.fixture
def std(weekly_offense, weekly_kickers, snaps_wk1, crosswalk):
    frame = pd.concat([weekly_offense, weekly_kickers], ignore_index=True)
    return season_to_date(player_weeks(frame, snaps_wk1, crosswalk),
                          through_week=1)


def _build(ctx, std, crosswalk, league_snapshot, sleeper_players, schedules,
           injuries, sources=None):
    return build_report(
        context=ctx, sources=sources or _sources(),
        roster=league_snapshot["rosters"][0],
        sleeper_players=sleeper_players, crosswalk=crosswalk, std=std,
        schedule=schedules, injuries=injuries)


@pytest.fixture
def report(ctx, std, crosswalk, league_snapshot, sleeper_players, schedules,
           injuries):
    return _build(ctx, std, crosswalk, league_snapshot, sleeper_players,
                  schedules, injuries)


# --- what the report is, and is not ------------------------------------

def test_the_header_states_season_week_phase_and_generation_time(report):
    text = report.to_markdown()
    assert "2026 week 2 (pregame)" in text
    assert "usage/box scores through week 1" in text
    assert report.context.now.isoformat(timespec="seconds") in text


def test_it_refuses_to_recommend_a_lineup(report):
    text = report.to_markdown()
    assert PROJECTION_STATUS in text
    banned = {"projection", "projected", "rank", "recommend", "start_score"}
    assert not (banned & {c.lower() for c in report.rows.columns})


def test_the_freshness_block_precedes_any_number(report):
    text = report.to_markdown()
    assert text.index("## Input freshness") < text.index("## Roster")
    for name in ("injuries", "weekly_stats", "snap_counts", "crosswalk"):
        assert name in text


def test_every_roster_spot_appears_exactly_once(report, league_snapshot):
    roster = league_snapshot["rosters"][0]
    assert len(report.rows) == len(roster["players"])
    assert report.rows.sleeper_id.is_unique


def test_lineup_grouping_reflects_starters_bench_and_ir(report, league_snapshot):
    roster = league_snapshot["rosters"][0]
    starters = set(roster["starters"])
    got = dict(zip(report.rows.sleeper_id, report.rows.lineup))
    assert {k for k, v in got.items() if v == "START"} == starters
    assert {k for k, v in got.items() if v == "IR"} == set(roster["reserve"])
    assert list(report.rows.lineup) == sorted(
        report.rows.lineup, key={"START": 0, "BENCH": 1, "IR": 2}.get)


def test_team_defenses_are_carried_not_dropped(report):
    dst = report.rows.loc[report.rows.sleeper_id == "SEA"]
    assert len(dst) == 1
    assert dst.iloc[0]["availability"] == "n/a (team defense)"
    assert pd.isna(dst.iloc[0]["pts"]), "no DST scoring implementation yet"


# --- invariant 1: evidence of absence vs absence of evidence ------------

class TestAvailability:
    def test_a_live_sleeper_designation_is_reported_verbatim(self):
        note = availability("g1", {"injury_status": "Questionable",
                                   "injury_body_part": "Ankle"}, {},
                            report_week=2, covers_report_week=True)
        assert "Questionable" in note.describe() and "Ankle" in note.describe()
        assert note.current

    def test_absence_from_a_loaded_report_is_evidence(self):
        note = availability("g1", {}, {}, report_week=2, covers_report_week=True)
        assert note.describe() == "no designation on the wk2 report"

    def test_absence_of_a_report_is_not_evidence(self):
        note = availability("g1", {}, {}, report_week=2, covers_report_week=False)
        assert "no wk2 injury report loaded" in note.describe()
        assert "UNRESOLVED, not healthy" in note.describe()
        assert not note.current

    def test_last_weeks_designation_is_labelled_with_its_week(self):
        note = availability("g1", {}, {"g1": {"week": 1, "report_status": "Out",
                                              "practice_status": "", "body_part": "Knee"}},
                            report_week=2, covers_report_week=True)
        described = note.describe()
        assert "wk1 report: Out" in described
        assert "NO wk2 designation yet" in described
        assert not note.current, "a week-old designation is not current"

    def test_this_weeks_designation_is_marked_current(self):
        note = availability("g1", {}, {"g1": {"week": 2, "report_status": "Out",
                                              "practice_status": "", "body_part": ""}},
                            report_week=2, covers_report_week=True)
        assert "wk2 report" in note.describe() and note.current

    def test_a_practice_only_row_still_surfaces(self):
        note = availability("g1", {}, {"g1": {
            "week": 2, "report_status": "",
            "practice_status": "Did Not Participate In Practice",
            "body_part": "Not injury related - resting player"}},
            report_week=2, covers_report_week=True)
        assert "wk2 practice: Did Not Participate" in note.describe()

    def test_there_is_no_boolean_that_makes_the_wrong_call_easy(self):
        """Rule #11: 'Questionable' is not 'out'; 'on roster' is not
        'startable'. A convenience accessor is the whole problem."""
        banned = {"is_startable", "startable", "is_out", "is_healthy", "playable"}
        assert not banned & set(dir(AvailabilityNote))
        import gridiron.weekly as W
        assert not banned & set(vars(W))


def test_a_stale_injury_pull_removes_the_evidence_of_absence(
        ctx, std, crosswalk, league_snapshot, sleeper_players, schedules,
        injuries):
    stale = _sources(injuries_as_of=NOW - timedelta(days=4))
    report = _build(ctx, std, crosswalk, league_snapshot, sleeper_players,
                    schedules, injuries, sources=stale)
    assert report.degraded
    assert any("injuries: STALE" in n for n in report.notes)
    assert any("no wk2 injury report loaded" in a
               for a in report.rows.availability)


def test_a_missing_injury_table_still_renders_a_report(
        ctx, std, crosswalk, league_snapshot, sleeper_players, schedules):
    gone = _sources(injuries_as_of=None, rows=0)
    report = _build(ctx, std, crosswalk, league_snapshot, sleeper_players,
                    schedules, pd.DataFrame(), sources=gone)
    text = report.to_markdown()
    assert report.degraded
    assert any("injuries: MISSING" in n for n in report.notes)
    assert "## Roster" in text and len(report.rows) > 0
    # Sleeper's own live injury_status is a separate source; where it exists
    # the report still shows it. Everyone else falls back to UNRESOLVED.
    no_sleeper_flag = [
        sid for sid in report.rows.sleeper_id
        if not (sleeper_players.get(sid) or {}).get("injury_status")]
    shown = dict(zip(report.rows.sleeper_id, report.rows.availability))
    assert any("UNRESOLVED, not healthy" in shown[s] for s in no_sleeper_flag)
    assert all("no designation on the wk2 report" not in shown[s]
               for s in no_sleeper_flag), "absence without a report is not evidence"


# --- invariant 2: blank is blank ---------------------------------------

def test_a_player_with_no_box_score_row_is_blank_not_zero(
        ctx, crosswalk, league_snapshot, sleeper_players, schedules, injuries):
    empty = pd.DataFrame(columns=["gsis_id"])
    report = _build(ctx, empty, crosswalk, league_snapshot, sleeper_players,
                    schedules, injuries)
    for col in ("g", "pts", "ppg", "snap%", "tgt"):
        assert report.rows[col].isna().all(), f"{col} invented a zero"


def test_blank_cells_render_as_blank_never_as_zero(report):
    table = markdown_table(pd.DataFrame([{"a": None, "b": 0.0}]), ["a", "b"])
    assert "| a | b |" in table.replace("  ", " ")
    body = table.splitlines()[-1]
    assert body.split("|")[1].strip() == ""
    assert body.split("|")[2].strip() == "0"


def test_an_unresolved_roster_id_is_listed_and_left_blank(
        ctx, std, league_snapshot, sleeper_players, schedules, injuries):
    blind = Crosswalk({}, {})
    report = _build(ctx, std, blind, league_snapshot, sleeper_players,
                    schedules, injuries)
    assert len(report.unresolved_ids) == len(league_snapshot["rosters"][0]["players"]) - 1
    text = report.to_markdown()
    assert "## Unresolved player ids" in text
    assert "never name-matched" in text
    assert report.rows["pts"].isna().all()


# --- invariant 3: the market line is never invented ---------------------

class TestScheduleContext:
    def test_implied_totals_split_the_posted_total(self, schedules):
        index = schedule_index(schedules, WEEK)
        assert index
        row = schedules.loc[schedules.week == WEEK].iloc[0]
        home, away = index[row.home_team], index[row.away_team]
        assert home.implied_total + away.implied_total == pytest.approx(row.total_line)
        assert home.home and not away.home

    def test_a_game_with_no_posted_line_leaves_the_cell_blank(self, schedules):
        no_line = schedules.copy()
        no_line["total_line"] = None
        index = schedule_index(no_line, WEEK)
        assert all(g.implied_total is None for g in index.values())
        assert all(not g.line_known for g in index.values())

    def test_a_team_absent_from_the_week_is_on_bye(self, schedules):
        index = schedule_index(schedules, WEEK)
        assert "NOTATEAM" not in index

    def test_an_empty_schedule_yields_no_context(self):
        assert schedule_index(pd.DataFrame(), WEEK) == {}
        assert schedule_index(None, WEEK) == {}


def test_a_bye_week_player_is_marked_bye_with_no_implied_total(
        ctx, std, crosswalk, league_snapshot, sleeper_players, injuries,
        schedules):
    """Week 2 rows exist and are current; a team missing from them is on bye."""
    one_game = schedules.loc[(schedules.week == 1) | (schedules.index ==
                             schedules.loc[schedules.week == 2].index[0])]
    report = _build(ctx, std, crosswalk, league_snapshot, sleeper_players,
                    one_game, injuries)
    playing = set(one_game.loc[one_game.week == 2, "home_team"]) | \
        set(one_game.loc[one_game.week == 2, "away_team"])
    byes = report.rows.loc[~report.rows.nfl.isin(playing)]
    assert len(byes) and (byes.opp == "BYE").all()
    assert byes["implied"].isna().all()


def test_a_missing_schedule_says_unknown_not_bye(
        ctx, std, crosswalk, league_snapshot, sleeper_players, injuries,
        schedules):
    """A team absent from a schedule we never loaded is not on a bye."""
    only_week_1 = schedules.loc[schedules.week == 1]
    report = _build(ctx, std, crosswalk, league_snapshot, sleeper_players,
                    only_week_1, injuries)
    assert (report.rows.opp == "?").all()
    assert not (report.rows.opp == "BYE").any()
    assert any("NOT a bye" in n for n in report.notes)


# --- degradation plumbing ----------------------------------------------

def test_a_fully_current_report_says_so(report):
    assert not report.degraded
    assert "All inputs current." in report.to_markdown()


def test_the_week_rollover_note_reaches_the_report(
        std, crosswalk, league_snapshot, sleeper_players, schedules, injuries):
    behind = WeekContext.build(season=2026, report_week=4, stats_weeks=[1],
                               kickoffs=[], now=NOW)
    report = _build(behind, std, crosswalk, league_snapshot, sleeper_players,
                    schedules, injuries)
    assert report.degraded
    assert any("rolled over to 4" in n for n in report.notes)
    assert "## Degraded inputs" in report.to_markdown()


def test_an_incomplete_lineup_is_called_out(
        ctx, std, crosswalk, league_snapshot, sleeper_players, schedules,
        injuries):
    short = dict(league_snapshot["rosters"][0])
    short["starters"] = short["starters"][:-2]
    report = build_report(context=ctx, sources=_sources(), roster=short,
                          sleeper_players=sleeper_players, crosswalk=crosswalk,
                          std=std, schedule=schedules, injuries=injuries)
    assert any("lineup slots" in n for n in report.notes)


def test_a_season_mismatch_is_called_out(
        std, crosswalk, league_snapshot, sleeper_players, schedules, injuries):
    wrong = WeekContext.build(season=2019, report_week=WEEK, stats_weeks=[1],
                              kickoffs=[], now=NOW)
    report = _build(wrong, std, crosswalk, league_snapshot, sleeper_players,
                    schedules, injuries)
    assert any("SEASON_YEAR" in n for n in report.notes)


def test_injury_index_never_reads_a_later_week(injuries):
    index = injury_index(injuries, week=1)
    assert all(v["week"] <= 1 for v in index.values())
    assert injury_index(pd.DataFrame(), week=2) == {}


def test_an_empty_roster_renders_without_crashing(ctx, std, crosswalk,
                                                  sleeper_players, schedules,
                                                  injuries):
    report = build_report(context=ctx, sources=_sources(),
                          roster={"players": [], "starters": []},
                          sleeper_players=sleeper_players, crosswalk=crosswalk,
                          std=std, schedule=schedules, injuries=injuries)
    assert "_no roster rows_" in report.to_markdown()


def test_degraded_is_true_whenever_any_source_is_not_fresh(ctx, std, crosswalk,
                                                           league_snapshot,
                                                           sleeper_players,
                                                           schedules, injuries):
    sources = _sources()
    sources[4] = assess("snap_counts", now=NOW, as_of=None, rows=0)
    report = _build(ctx, std, crosswalk, league_snapshot, sleeper_players,
                    schedules, injuries, sources=sources)
    assert report.degraded
    assert any(s.status is Status.MISSING for s in report.sources)


def test_the_league_name_can_be_withheld(report):
    assert "# Weekly report\n" in report.to_markdown(include_names=False)
