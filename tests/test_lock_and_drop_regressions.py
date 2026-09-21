"""Regressions for the source-review findings against the dashboard at f82623e.

Each test below reproduces a defect that was executed and confirmed on that
commit before the fix, and then pins the corrected behaviour. The original
observations, in the reviewer's words:

  1. `kickoff_index` with `gameday=2026-09-20`, `gametime=None` fabricated a
     17:00 UTC kickoff and reported the team UNLOCKED at 16:00.
  2. `kickoff_index` with a malformed `gameday` returned `{}`, and
     `lock_state("A", {}, ...)` returned `(False, "no game this week (bye or
     unknown team)")` — an unreadable schedule read as a league-wide bye.
  3. The dashboard used `kickoffs is not None` as full lock certainty, so an
     index that proved nothing still authorised every swap on the board.
  4. An IR player carrying `Projection(0, 0)` was admitted to
     `droppable_players` and ranked FIRST, ahead of a healthy backup.
  5. The stale scenario emitted 120h-old league/players/injuries/stats,
     `degraded=True`, `lineup_abstained=""`, and still produced TWO lineup
     alternatives and THREE waiver upgrades as live advice.

The thread joining all five: a missing fact was being substituted with a
convenient default — a time, a bye, a certainty, a zero, a permission. Each
test asserts the honest state is now preserved instead.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from gridiron.freshness import SourceFreshness, Status
from gridiron.gating import box_score_blockers, build_gate
from gridiron.lineup import (
    OPEN, UNKNOWN, Player, kickoff_index, lock_state, plan_lineup, slot_order,
)
from gridiron.projection import Projection, abstain
from gridiron.waivers import build_board, droppable_players, protected_players

UTC = timezone.utc
SLOTS = slot_order(None)
NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)


def P(sid, pos, mean, lineup="BENCH", *, locked=False, lock_known=True,
      unprojected=False, withheld=""):
    proj = abstain("no admissible box scores") if unprojected \
        else Projection(mean, max(mean * 0.5, 1.5))
    if withheld:
        proj = proj.withheld(withheld)
    return Player(sid, f"p{sid}", pos, "T", proj, lineup, locked,
                  "LOCKED" if locked else "", "", (), "g" + sid, lock_known)


# ------------------------------------------------------- 1. fabricated times

def test_a_game_with_no_kickoff_time_is_unknown_not_one_oclock():
    """Finding 1. `gametime=None` used to become 13:00 ET = 17:00 UTC, and the
    team then read as UNLOCKED at 16:00 UTC. Every 9:30 am London kickoff and
    every flexed night game makes that guess wrong, and wrong in the unsafe
    direction: it authorises a move Sleeper has already refused."""
    sched = pd.DataFrame([{"week": 3, "gameday": "2026-09-20", "gametime": None,
                           "home_team": "A", "away_team": "B"}])
    idx = kickoff_index(sched, 3)
    assert idx is not None
    assert idx.kickoffs == {}, "no kickoff time may be invented"
    assert idx.time_unknown == frozenset({"A", "B"})
    assert not idx.complete
    lock = lock_state("A", idx, NOW)
    assert lock.state == UNKNOWN and not lock.movable
    assert "no usable kickoff time" in lock.note
    # the fact that the team PLAYS is still carried: it is not reported as a bye
    assert "bye" not in lock.note.lower()


def test_an_unreadable_kickoff_date_does_not_silently_drop_the_game():
    sched = pd.DataFrame([{"week": 3, "gameday": "not-a-date", "gametime": "13:00",
                           "home_team": "A", "away_team": "B"}])
    idx = kickoff_index(sched, 3)
    assert idx.time_unknown == frozenset({"A", "B"}) and idx.problems
    assert lock_state("B", idx, NOW).state == UNKNOWN


# ------------------------------------------- 2/3. empty index, false certainty

def test_a_week_that_could_not_be_read_is_never_a_league_wide_bye():
    """Findings 2 and 3. The old index returned `{}`, which is
    indistinguishable from "all 32 teams are on bye", and the caller read
    `{} is not None` as proof that every lock state was known."""
    sched = pd.DataFrame([
        {"week": 3, "gameday": "bad", "gametime": "13:00", "home_team": "A", "away_team": "B"},
        {"week": 3, "gameday": "bad", "gametime": "13:00", "home_team": "C", "away_team": "D"},
    ])
    idx = kickoff_index(sched, 3)
    assert idx is not None and not idx.kickoffs
    for team in ("A", "B", "C", "D"):
        lock = lock_state(team, idx, NOW)
        assert lock.state == UNKNOWN, f"{team} read as {lock.state}"
        assert not lock.movable
    # and the dashboard's certainty test now requires at least one timed game
    assert not bool(idx.kickoffs)


def test_a_partially_readable_week_freezes_only_the_players_it_cannot_time():
    """A half-broken schedule is neither total ignorance nor full certainty.
    The games that parsed are still optimised; the rest are frozen by name."""
    sched = pd.DataFrame([
        {"week": 3, "gameday": "2026-09-20", "gametime": "13:00", "home_team": "TA", "away_team": "TB"},
        {"week": 3, "gameday": "2026-09-20", "gametime": None, "home_team": "TC", "away_team": "TD"},
    ])
    idx = kickoff_index(sched, 3)
    assert not idx.complete
    assert lock_state("TA", idx, datetime(2026, 9, 20, 12, 0, tzinfo=UTC)).state == OPEN
    assert lock_state("TC", idx, datetime(2026, 9, 20, 12, 0, tzinfo=UTC)).state == UNKNOWN
    # a team with NO row in this incomplete week cannot be called a bye either
    assert lock_state("TZ", idx, NOW).state == UNKNOWN


def test_an_unknown_lock_freezes_the_starter_and_bars_the_bench_player():
    roster = [
        P("q1", "QB", 20, "START"), P("r1", "RB", 15, "START"), P("r2", "RB", 10, "START"),
        P("w1", "WR", 14, "START"), P("w2", "WR", 9, "START"), P("t1", "TE", 7, "START"),
        P("f1", "WR", 8, "START"), P("f2", "RB", 6, "START", lock_known=False),
        P("k1", "K", 8, "START"), P("d1", "DST", 0, "START", unprojected=True),
        P("r3", "RB", 30, lock_known=False),        # would be the best add, unknown lock
        P("r4", "RB", 12),
    ]
    starters = ["q1", "r1", "r2", "w1", "w2", "t1", "f1", "f2", "k1", "d1"]
    plan = plan_lineup(roster, starters, SLOTS)
    ids = [p.sleeper_id for p in plan.best if p]
    assert "r3" not in ids, "a player whose lock state is unknown entered the lineup"
    assert "f2" in ids, "an unknown-lock starter must stay where he is"
    assert all(a.bench.sleeper_id != "r3" for a in plan.alternatives)
    assert all(not (a.starter and a.starter.sleeper_id == "f2") for a in plan.alternatives)
    frozen = {p.sleeper_id for p, _ in plan.frozen}
    assert {"f2", "r3"} <= frozen


# ------------------------------------------------------ 4. drop ranking

def test_a_player_who_is_out_this_week_is_not_the_cheapest_thing_to_drop():
    """Finding 4. An IR player with `Projection(0, 0)` was admitted to
    `droppable_players` and sorted FIRST. Ranking by this week's points makes
    every temporarily absent player look free to cut."""
    roster = [
        P("stud", "RB", 18, "START"),
        P("ir", "RB", 0, "IR", withheld="designated IR: projected 0"),
        P("bye", "WR", 0, withheld="BYE week: projected 0"),
        P("out", "WR", 0, withheld="designated Out: projected 0"),
        P("scrub", "TE", 4),
    ]
    drops = [p.sleeper_id for p in droppable_players(roster)]
    for sid in ("ir", "bye", "out"):
        assert sid not in drops, f"{sid} is protected, not droppable"
    assert drops[0] == "scrub", "the cheapest drop is the healthy low projection"
    reasons = {p.sleeper_id: r for p, r in protected_players(roster)}
    assert set(reasons) == {"ir", "bye", "out"}
    for sid in ("bye", "out"):
        assert "not because he is worth 0" in reasons[sid]
        assert "rest-of-season" in reasons[sid]
    assert "roster spot" in reasons["ir"]


def test_a_bare_ir_projection_without_a_withholding_marker_is_still_protected():
    """The reviewer's exact case: `Projection(0, 0)` on an IR player, with no
    `withheld()` call behind it, so nothing in the projection says 'absent'."""
    roster = [P("stud", "RB", 18, "START"), P("scrub", "TE", 4),
              P("ir", "RB", 0, "IR")]
    assert [p.sleeper_id for p in droppable_players(roster)] == ["scrub", "stud"]
    assert any(p.sleeper_id == "ir" for p, _ in protected_players(roster))


def test_a_board_with_nothing_left_to_drop_abstains_and_says_how_many():
    roster = [P("ir", "RB", 0, "IR", withheld="designated IR: projected 0"),
              P("bye", "WR", 0, withheld="BYE week: projected 0")]
    board = build_board(roster, [P("fa", "RB", 30)], ["ir", "bye"], ("RB", "WR"),
                        locks_known=True)
    assert "protected" in board.abstained and board.upgrades == ()
    assert len(board.protected) == 2


def test_an_unrostered_player_is_never_promised_as_addable():
    from gridiron.waivers import eligibility
    e = eligibility(snapshot_as_of="2026-09-20 12:00 UTC")
    assert e.state == "UNVERIFIED"
    assert any("on no roster in the league snapshot" in b for b in e.basis)
    assert any("ON WAIVERS, not a free agent" in b for b in e.basis)
    text = e.describe().lower()
    assert "add now" not in text and "addable" not in text


# ------------------------------------------------- 5. freshness gates actions

def _src(name, status, reason="pulled 120h ago"):
    return SourceFreshness(name, status, None, 1, 3, reason)


def test_stale_league_and_injury_inputs_withhold_lineup_and_waiver_actions():
    """Finding 5, at the unit level: the freshness labels were decorative."""
    fresh = [_src(n, Status.FRESH, "current") for n in
             ("sleeper_league", "sleeper_players", "injuries", "schedules")]
    gate = build_gate(fresh)
    assert gate.allows("lineup") and gate.allows("waiver") and not gate.withheld

    stale = [_src("sleeper_league", Status.STALE), _src("sleeper_players", Status.STALE),
             _src("injuries", Status.STALE), _src("schedules", Status.FRESH, "current")]
    gate = build_gate(stale)
    assert not gate.allows("lineup") and not gate.allows("waiver")
    assert not gate.allows("matchup")
    assert "sleeper_league is STALE" in gate.why("lineup")
    assert set(gate.withheld) == {"lineup", "waiver", "matchup"}
    assert gate.gate("lineup").verify()


def test_box_score_staleness_does_not_withhold_anything():
    """weekly_stats is stale for most of every week by construction. Gating on
    it would withhold every action every Wednesday and train the reader to
    ignore the gate."""
    sources = [_src(n, Status.FRESH, "current") for n in
               ("sleeper_league", "sleeper_players", "injuries", "schedules")]
    sources += [_src("weekly_stats", Status.STALE), _src("snap_counts", Status.MISSING)]
    gate = build_gate(sources)
    assert gate.withheld == ()


def test_an_unreadable_schedule_blocks_actions_even_though_the_file_is_fresh():
    sources = [_src(n, Status.FRESH, "current") for n in
               ("sleeper_league", "sleeper_players", "injuries", "schedules")]
    gate = build_gate(sources, extra={"lineup": [("schedules", "week 3 did not parse")],
                                      "waiver": [("schedules", "week 3 did not parse")]})
    assert not gate.allows("lineup") and "did not parse" in gate.why("lineup")
    assert gate.allows("matchup")


def test_a_withheld_gate_still_names_what_to_verify():
    gate = build_gate([_src("sleeper_league", Status.STALE),
                       _src("sleeper_players", Status.FRESH, "current"),
                       _src("injuries", Status.FRESH, "current"),
                       _src("schedules", Status.FRESH, "current")])
    g = gate.gate("lineup")
    assert not g.allowed
    assert any("roster and starting lineup" in v for v in g.verify())
    assert "WITHHELD" in g.banner() and "LAST KNOWN" in g.banner()
    assert gate.record()["lineup"]["allowed"] is False


# ==========================================================================
# Second review pass, against the dashboard at 47708e3. The reviewer passed
# all 433 tests and then probed the parts the tests did not reach:
#
#   1. A frame holding BUF/MIA in week 2 and only NYJ/NE in week 3 made BUF
#      OPEN/BYE in week 3. Parsing every row that arrived is not evidence
#      that every row arrived, and the truncated pull is indistinguishable
#      from a clean one by parse success alone.
#   2. Two week rows giving one team two different kickoffs resolved
#      silently to whichever was read last.
#   3. A row naming one team was accepted as a timed game.
#   4. Box scores were exempt from gating unconditionally, so a cache nobody
#      had refreshed since September would keep endorsing actions forever.
# ==========================================================================

def _sched(rows):
    return pd.DataFrame(rows)


def _game(week, home, away, day, time_="13:00"):
    return {"week": week, "home_team": home, "away_team": away,
            "gameday": day, "gametime": time_}


def test_a_week_that_simply_stops_early_is_not_a_league_of_byes():
    """The first reviewer frame. Every row parses; nothing is damaged; and
    the week is still missing half the teams the frame knows about."""
    sched = _sched([_game(2, "BUF", "MIA", "2026-09-13"),
                    _game(3, "NYJ", "NE", "2026-09-20")])
    idx = kickoff_index(sched, 3)

    assert idx.rows_intact, "nothing about the rows that arrived is wrong"
    assert not idx.complete, "and that is not the same as the week being whole"
    assert idx.declared_bye == frozenset()

    lock = lock_state("BUF", idx, NOW)
    assert lock.state == UNKNOWN and not lock.movable
    assert "does not declare a bye" in lock.note
    # the teams that DID arrive are unaffected
    assert lock_state("NYJ", idx, NOW).state in (OPEN, "LOCKED")


def test_a_bracketed_gap_is_not_a_bye_either():
    """The second reviewer frame, and the reason bracketing was removed.

    BUF plays in week 2 AND week 4, so the week-3 gap is bracketed on both
    sides by the frame's own rows — the shape the previous rule accepted as
    proof of a bye. It proves nothing: this is exactly what deleting one
    week-3 row looks like, and the surviving rows cannot tell the two apart.
    """
    sched = _sched([_game(2, "BUF", "MIA", "2026-09-13"),
                    _game(2, "NYJ", "NE", "2026-09-13"),
                    _game(3, "NYJ", "NE", "2026-09-20"),
                    _game(4, "BUF", "MIA", "2026-09-27"),
                    _game(4, "NYJ", "NE", "2026-09-27")])
    idx = kickoff_index(sched, 3)

    assert idx.rows_intact and idx.extends_past
    assert idx.declared_bye == frozenset()
    for team in ("BUF", "MIA"):
        lock = lock_state(team, idx, NOW)
        assert lock.state == UNKNOWN and not lock.movable, team
        assert "look identical from here" in lock.note


def _full_season(weeks=6, teams=("AAA", "BBB", "CCC", "DDD", "EEE", "FFF")):
    """Every team playing every week: a frame with no gaps at all."""
    rows = []
    for week in range(1, weeks + 1):
        day = f"2026-09-{5 + week * 2:02d}"
        for i in range(0, len(teams), 2):
            rows.append(_game(week, teams[i], teams[i + 1], day))
    return rows


def test_deleting_one_real_game_from_a_full_season_frame_is_not_a_bye():
    """The case the goal names: a complete frame, minus one genuine row.

    Nothing else about the frame changes. Every remaining row parses, the
    frame still runs to week 6, and the two teams whose game was removed are
    bracketed by games before and after. The deletion is invisible to every
    property of the surviving rows — which is why no property of the
    surviving rows is allowed to decide it.
    """
    full = _full_season()
    idx_full = kickoff_index(_sched(full), 3)
    assert lock_state("AAA", idx_full, NOW).state in (OPEN, "LOCKED")

    cut = [r for r in full
           if not (r["week"] == 3 and r["home_team"] == "AAA")]
    assert len(cut) == len(full) - 1, "exactly one real game removed"
    idx = kickoff_index(_sched(cut), 3)

    # Indistinguishable from the complete frame by every damage signal.
    assert idx.rows_intact and idx.extends_past and idx.problems == ()
    assert idx.declared_bye == frozenset()
    for team in ("AAA", "BBB"):
        lock = lock_state(team, idx, NOW)
        assert lock.state == UNKNOWN and not lock.movable, team
    # and the teams whose rows survived are untouched
    assert lock_state("CCC", idx, NOW).state in (OPEN, "LOCKED")


def test_a_schedule_that_declares_a_bye_is_believed():
    """Removing the inference must not make a real bye unrenderable forever:
    a source that STATES the bye is taken at its word."""
    rows = _full_season()
    rows = [r for r in rows if not (r["week"] == 3 and r["home_team"] == "AAA")]
    rows.append({"week": 3, "home_team": "AAA", "away_team": "",
                 "gameday": "", "gametime": "", "game_type": "BYE"})
    rows.append({"week": 3, "home_team": "BBB", "away_team": "",
                 "gameday": "", "gametime": "", "game_type": "BYE"})
    idx = kickoff_index(_sched(rows), 3)

    assert idx.declared_bye == frozenset({"AAA", "BBB"})
    assert idx.rows_intact and idx.partial_rows == 0
    lock = lock_state("AAA", idx, NOW)
    assert lock.state == OPEN and lock.movable and "BYE" in lock.note


def test_a_declared_bye_that_contradicts_a_real_game_loses():
    """A schedule saying both things about one team has not established the
    bye. The timed game is evidence; the declaration beside it is dropped."""
    rows = _full_season()
    rows.append({"week": 3, "home_team": "AAA", "away_team": "",
                 "gameday": "", "gametime": "", "game_type": "BYE"})
    idx = kickoff_index(_sched(rows), 3)

    assert idx.declared_bye == frozenset()
    assert any("declares a week-3 bye and also carries" in p for p in idx.problems)
    assert lock_state("AAA", idx, NOW).state in (OPEN, "LOCKED")


def test_two_rows_giving_one_team_two_kickoffs_establish_neither():
    sched = _sched([_game(3, "BUF", "MIA", "2026-09-20", "13:00"),
                    _game(3, "BUF", "NYJ", "2026-09-20", "20:00")])
    idx = kickoff_index(sched, 3)

    assert "BUF" in idx.conflicting
    assert "BUF" not in idx.kickoffs, "neither time is used"
    assert "BUF" in idx.time_unknown
    assert not idx.rows_intact
    assert any("different kickoffs" in p for p in idx.problems)
    assert lock_state("BUF", idx, NOW).state == UNKNOWN
    # the uncontradicted teams keep their times
    assert idx.kickoffs.get("MIA") is not None


def test_a_row_naming_one_team_is_half_a_game_and_times_nobody():
    sched = _sched([{"week": 3, "home_team": "BUF", "away_team": "",
                     "gameday": "2026-09-20", "gametime": "13:00"}])
    idx = kickoff_index(sched, 3)

    assert idx.partial_rows == 1 and not idx.rows_intact
    assert dict(idx.kickoffs) == {}, "a damaged row's kickoff is not trusted"
    assert "BUF" in idx.time_unknown
    assert any("names only one team" in p for p in idx.problems)
    assert lock_state("BUF", idx, NOW).state == UNKNOWN


def test_a_row_listing_one_team_on_both_sides_is_not_a_game():
    sched = _sched([_game(3, "BUF", "BUF", "2026-09-20"),
                    _game(3, "NYJ", "NE", "2026-09-20")])
    idx = kickoff_index(sched, 3)
    assert idx.dropped_rows == 1 and not idx.rows_intact
    assert "BUF" not in idx.kickoffs
    assert lock_state("BUF", idx, NOW).state == UNKNOWN


# ------------------------------------------------- box scores: coverage, not age

def _box(name, status, through, weeks, failed=False):
    return SourceFreshness(name, status, None, 10, through, "pulled 120h ago",
                           tuple(weeks), failed)


def test_box_scores_four_days_old_but_caught_up_still_withhold_nothing():
    """Wednesday. Sunday's box scores are the newest there are. That is the
    publication cadence (rule #8), not a stale cache."""
    blockers = box_score_blockers(
        [_box("weekly_stats", Status.STALE, 2, [1, 2]),
         _box("snap_counts", Status.STALE, 1, [1])],
        evidence_boundary=2)
    assert blockers == {}


def test_box_scores_months_behind_the_evidence_boundary_do_withhold():
    """The exemption used to be unconditional, so a cache nobody refreshed
    since September kept endorsing actions indefinitely."""
    blockers = box_score_blockers(
        [_box("weekly_stats", Status.STALE, 2, [1, 2])], evidence_boundary=9)
    assert set(blockers) == {"lineup", "waiver", "matchup"}
    why = blockers["lineup"][0][1]
    assert "covers only through week 2" in why and "7 weeks behind" in why


def test_a_failed_box_score_refresh_withholds_even_when_coverage_looks_right():
    blockers = box_score_blockers(
        [_box("weekly_stats", Status.STALE, 2, [1, 2], failed=True)],
        evidence_boundary=2)
    assert "the latest refresh FAILED" in blockers["lineup"][0][1]


def test_a_hole_inside_the_covered_weeks_withholds():
    blockers = box_score_blockers(
        [_box("weekly_stats", Status.STALE, 5, [1, 2, 4, 5])], evidence_boundary=5)
    assert "missing wk3" in blockers["lineup"][0][1]


def test_a_box_score_gate_names_what_to_re_run():
    sources = [_src(n, Status.FRESH, "current") for n in
               ("sleeper_league", "sleeper_players", "injuries", "schedules")]
    gate = build_gate(sources, extra=box_score_blockers(
        [_box("weekly_stats", Status.STALE, 1, [1])], evidence_boundary=9))
    assert not gate.allows("lineup")
    assert any("pull_week.py" in v for v in gate.gate("lineup").verify())
