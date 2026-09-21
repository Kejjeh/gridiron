"""The Game Day model: platform actuals, observed game status, legality now,
like-for-like diffs, and the pregame join — each on synthetic inputs.

Every test builds through `build_gameday` (the production assembly) from a
small invented league; nothing reads the real cache or the network.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from gridiron import gameday as gd
from gridiron.decisions import archive_path, write_archive
from gridiron.freshness import SourceFreshness, Status

ROOT = Path(__file__).resolve().parent.parent
UTC = timezone.utc
#: Sunday of fixture week 3, 17:00 ET: early games over, late games on,
#: night games ahead.
NOW = datetime(2026, 9, 27, 21, 0, tzinfo=UTC)
LEAGUE = "TESTLEAGUE"

PLAYERS = {
    "1": {"full_name": "Early Quarterback", "position": "QB", "team": "BAL"},
    "2": {"full_name": "Late Back", "position": "RB", "team": "SEA"},
    "3": {"full_name": "Night Receiver", "position": "WR", "team": "KC"},
    "4": {"full_name": "Early Kicker", "position": "K", "team": "PIT"},
    "5": {"full_name": "Bench Night Wideout", "position": "WR", "team": "LAR"},
    "6": {"full_name": "Bench Early End", "position": "TE", "team": "PHI"},
    "7": {"full_name": "Their Quarterback", "position": "QB", "team": "CAR"},
    "8": {"full_name": "Their Night Back", "position": "RB", "team": "IND"},
    "9": {"full_name": "Their Kicker", "position": "K", "team": "LAR"},
    "10": {"full_name": "Night End", "position": "TE", "team": "KC"},
}
SLOTS = ["QB", "RB", "WR", "K", "DEF"]


def _scn():
    spec = importlib.util.spec_from_file_location(
        "weekly_dashboard_scenarios", ROOT / "scripts/weekly/dashboard_scenarios.py")
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


@pytest.fixture(scope="module")
def schedule() -> pd.DataFrame:
    return _scn()._week3_schedule(pd.read_csv(ROOT / "tests/fixtures/schedules_wk1_2.csv"))


def feed(status: dict[str, str], *, fresh: bool = True, as_of: datetime | None = None,
         present: bool = True) -> gd.GameFeed:
    """A feed whose statuses are ASSIGNED by team pair, never by the clock."""
    if not present:
        return gd.GameFeed.from_blob(None, week=3, freshness=None, now=NOW)
    games = [("NO", "BAL"), ("PIT", "NE"), ("CAR", "ATL"), ("PHI", "TEN"), ("SEA", "ARI"),
             ("IND", "KC"), ("NYG", "LAR")]
    rows = [{"week": 3, "home": h, "away": a, "status": status.get(h, status.get(a, "pre_game")),
             "date": "2026-09-27", "game_id": f"g{i}"} for i, (a, h) in enumerate(games)]
    stamp = as_of or NOW - timedelta(minutes=3)
    fr = SourceFreshness("game_status", Status.FRESH if fresh else Status.STALE, stamp, len(rows),
                         3, "pulled just now" if fresh else "pulled 3h ago, over the limit")
    return gd.GameFeed.from_blob({"as_of": stamp.isoformat(), "games": rows}, week=3,
                                 freshness=fr, now=NOW)


def snapshot(*, my_starters=("1", "2", "3", "4", "SEA"), my_points=(14.0, 0.0, 0.0, 7.0, -1.0),
             opp_starters=("7", "8", "9", "0", "IND"), opp_points=(20.14, 0.0, 0.0, 0.0, 0.0),
             my_total=None, opp_total=None, custom=None, matchups=None, week=3,
             my_players=("1", "2", "3", "4", "SEA", "5", "6"), state_week=None,
             slots=SLOTS, reserve=(), taxi=()) -> dict:
    my_sp = list(my_points)
    opp_sp = list(opp_points)
    rows = matchups if matchups is not None else [
        {"roster_id": 1, "matchup_id": 1, "starters": list(my_starters), "players": list(my_players),
         "starters_points": my_sp, "players_points": {"5": 9.5, "6": 3.0},
         "points": round(sum(v for v in my_sp if v is not None), 2) if my_total is None else my_total,
         "custom_points": None},
        {"roster_id": 2, "matchup_id": 1, "starters": list(opp_starters), "players": list(opp_starters),
         "starters_points": opp_sp, "players_points": {},
         "points": round(sum(opp_sp), 2) if opp_total is None else opp_total,
         "custom_points": custom}]
    return {"as_of": (NOW - timedelta(minutes=2)).isoformat(), "league_id": LEAGUE, "week": week,
            "state": {"season": 2026, "week": state_week or week, "season_type": "regular"},
            "league": {"league_id": LEAGUE, "roster_positions": list(slots)},
            "users": [{"user_id": "U1", "display_name": "fixture_owner"}],
            "rosters": [{"roster_id": 1, "owner_id": "U1", "starters": list(my_starters),
                         "players": list(my_players), "reserve": list(reserve),
                         "taxi": list(taxi)},
                        {"roster_id": 2, "owner_id": "U2", "starters": list(opp_starters),
                         "players": list(opp_starters), "reserve": []}],
            "matchups": rows}


def sources(*, league_fresh=True, players_fresh=True, schedule_fresh=True
            ) -> tuple[SourceFreshness, ...]:
    return (SourceFreshness("sleeper_league", Status.FRESH if league_fresh else Status.STALE,
                            NOW - timedelta(minutes=2), 2, 3,
                            "pulled 0h ago" if league_fresh else "pulled 9h ago"),
            SourceFreshness("sleeper_players", Status.FRESH if players_fresh else Status.STALE,
                            NOW - timedelta(hours=1), 9, None,
                            "pulled 1h ago" if players_fresh else "pulled 30h ago"),
            SourceFreshness("schedules", Status.FRESH if schedule_fresh else Status.STALE,
                            NOW - timedelta(hours=5), 48, 3,
                            "pulled 5h ago" if schedule_fresh else "pulled 9 days ago"))


def build(schedule, *, snap=None, fd=None, now=NOW, archive_root=None, previous=None,
          players=PLAYERS, players_fresh=True, srcs=None, week=3):
    return gd.build_gameday(
        season=2026, week=week, league_id=LEAGUE, owner_id="U1",
        snapshot=snap or snapshot(), sleeper_players=players,
        players_as_of=NOW - timedelta(hours=1), players_fresh=players_fresh,
        schedule=schedule, feed=fd if fd is not None else feed({"BAL": "complete", "NE": "complete",
                                                                "ATL": "complete", "TEN": "complete",
                                                                "ARI": "in_game", "KC": "pre_game",
                                                                "LAR": "pre_game"}),
        sources=srcs or sources(), now=now, archive_root=archive_root, previous=previous)


def by_slot(side: gd.SideView) -> dict[str, gd.StarterView]:
    return {s.slot: s for s in side.starters}


def write_record(root: Path, *, generated: datetime, actions: list[dict], week=3,
                 league=LEAGUE, roster=1, roster_rows=None, matchup=None, tagged=True) -> Path:
    rec = {"generated": generated.isoformat(timespec="seconds"), "season": 2026, "week": week,
           "roster": roster_rows or [], "actions": actions, "withheld_actions": [],
           "matchup": matchup, "sources": [], "current_lineup": []}
    if tagged:
        rec["league_id"] = league
        rec["my_roster_id"] = roster
    path = archive_path(2026, week, generated, root, rec)
    write_archive(rec, path)
    return path


# ---------------------------------------------------------------- statuses

def test_a_kickoff_that_has_passed_is_never_a_final_by_itself(schedule):
    """13:00 ET games kicked off 4h before NOW. Without a feed they are
    UNKNOWN; the lock column, which the schedule DOES prove, is LOCKED."""
    d = build(schedule, fd=feed({}, present=False))
    qb = by_slot(d.score.mine)["QB"]
    assert qb.state == gd.UNKNOWN and qb.lock == "LOCKED"
    assert "kickoff time alone" in qb.game.note
    assert any("every game is UNKNOWN" in n for n in d.notes)


def test_observed_statuses_map_and_unknown_words_pass_through(schedule):
    fd = feed({"BAL": "complete", "ARI": "in_game", "KC": "pre_game", "NE": "suspended",
               "ATL": "canceled", "TEN": "postponed"})
    d = build(schedule, fd=fd)
    me = by_slot(d.score.mine)
    assert me["QB"].state == gd.FINAL and me["RB"].state == gd.PLAYING
    assert me["WR"].state == gd.NOT_STARTED and me["K"].state == gd.SUSPENDED
    them = by_slot(d.score.opp)
    assert them["QB"].state == gd.CANCELED
    # A word the page does not know is UNKNOWN with the word shown, not mapped.
    bench = {b.sleeper_id: b for b in d.score.mine.bench}
    assert bench["6"].state == gd.UNKNOWN and '"postponed"' in bench["6"].game.note


def test_a_game_in_progress_four_hours_after_kickoff_is_still_playing(schedule):
    """Overtime and delays exist; elapsed time proves nothing."""
    late = NOW + timedelta(hours=3)                # 20:00 ET: 13:00 games are 7h old
    d = build(schedule, fd=feed({"BAL": "in_game", "ARI": "in_game", "KC": "in_game"},
                                as_of=late - timedelta(minutes=2)), now=late)
    assert by_slot(d.score.mine)["QB"].state == gd.PLAYING


def test_a_stale_feed_keeps_finals_and_demotes_everything_else(schedule):
    d = build(schedule, fd=feed({"BAL": "complete", "ARI": "in_game", "KC": "pre_game"},
                                fresh=False, as_of=NOW - timedelta(hours=3)))
    me = by_slot(d.score.mine)
    assert me["QB"].state == gd.FINAL and me["QB"].game.current
    assert me["RB"].state == gd.UNKNOWN and "too old" in me["RB"].game.note
    assert me["WR"].state == gd.UNKNOWN
    assert any("not current" in n for n in d.notes)


def test_a_team_missing_from_the_feed_is_unknown_not_a_bye(schedule):
    fd = feed({"BAL": "complete"})
    rows = tuple(r for r in fd.rows if "PIT" not in (r["home"], r["away"]))
    fd = gd.GameFeed(fd.present, fd.as_of, fd.fresh, fd.reason, 3, rows)
    d = build(schedule, fd=fd)
    k = by_slot(d.score.mine)["K"]
    assert k.state == gd.UNKNOWN and "missing row is not a bye" in k.game.note


def test_feed_and_schedule_disagreements_are_stated_and_the_safer_side_locks(schedule):
    # 13:00 game the feed still calls pre_game: NOT STARTED, with the conflict named.
    d = build(schedule, fd=feed({"BAL": "pre_game", "KC": "in_game"}))
    qb = by_slot(d.score.mine)["QB"]
    assert qb.state == gd.NOT_STARTED and "kickoff has passed" in qb.game.note
    # A night game the feed says is under way: locked although the schedule
    # kickoff is still ahead.
    wr = by_slot(d.score.mine)["WR"]
    assert wr.state == gd.PLAYING and wr.lock == "LOCKED" and "observed status wins" in wr.lock_note


def test_team_aliases_are_normalised_through_the_one_table(schedule):
    """The bench back is on LAR in Sleeper's spelling; the schedule and the
    feed spell the Rams LA. One alias table joins them."""
    d = build(schedule)
    b = {x.sleeper_id: x for x in d.score.mine.bench}["5"]
    assert b.team == "LA" and b.state == gd.NOT_STARTED and b.lock == "OPEN"


# ---------------------------------------------------------------- actuals

def test_platform_values_are_taken_as_sent_including_zero_negative_and_kicker(schedule):
    d = build(schedule)
    me = by_slot(d.score.mine)
    assert me["K"].points == 7.0 and me["DST"].points == -1.0
    assert me["RB"].points == 0.0 and "placeholder" not in me["RB"].points_note   # PLAYING zero
    assert me["WR"].points == 0.0 and "placeholder" in me["WR"].points_note       # NOT STARTED zero
    assert d.score.mine.platform_points == 20.0 and d.score.margin == pytest.approx(-0.14)
    assert "add up" in d.score.mine.reconciliation


def test_a_value_the_platform_did_not_send_is_unknown_not_zero(schedule):
    snap = snapshot(my_points=(14.0, 0.0, 0.0, 7.0), my_total=20.0)   # DST value not sent
    d = build(schedule, snap=snap)
    dst = by_slot(d.score.mine)["DST"]
    assert dst.points is None and "UNKNOWN, not 0" in dst.points_note
    assert d.score.mine.unknown_points == 1
    assert "cannot be reconciled" in d.score.mine.reconciliation
    assert d.score.mine.platform_points == 20.0          # the total is never edited


def test_an_unexplained_difference_is_disclosed_and_the_total_kept(schedule):
    d = build(schedule, snap=snapshot(my_total=25.0))
    assert d.score.mine.platform_points == 25.0
    assert "unexplained difference of +5.00" in d.score.mine.reconciliation


def test_a_commissioner_override_is_the_total_and_is_named(schedule):
    d = build(schedule, snap=snapshot(custom=50.0))
    assert d.score.opp.platform_points == 50.0 and d.score.opp.override == 50.0
    assert "OVERRIDE" in d.score.opp.reconciliation and "20.14" in d.score.opp.reconciliation
    assert d.score.margin == pytest.approx(-30.0)


def test_an_empty_slot_scores_nothing_and_is_named(schedule):
    d = build(schedule)
    empty = by_slot(d.score.opp)["K"]
    assert empty.empty and empty.state == gd.EMPTY and empty.points is None
    assert "1 empty slot" in d.score.opp.exposure()


def test_opponent_is_found_by_matchup_id_and_never_chosen_arbitrarily(schedule):
    base = snapshot()
    rows = base["matchups"]
    # no other roster in the matchup
    alone = dict(base, matchups=[rows[0]])
    assert "no other roster" in build(schedule, snap=alone).score.opp_reason
    # a third roster in the same matchup: unsupported, not "the first one"
    three = dict(base, matchups=rows + [dict(rows[1], roster_id=3)])
    d = build(schedule, snap=three)
    assert d.score.opp is None and "2 other rosters" in d.score.opp_reason
    # null matchup id
    null = dict(base, matchups=[dict(rows[0], matchup_id=None), rows[1]])
    assert "matchup_id is null" in build(schedule, snap=null).score.opp_reason


def test_a_lead_is_never_called_safe_while_they_have_players_left(schedule):
    d = build(schedule, snap=snapshot(my_points=(30.0, 10.0, 5.0, 7.0, 3.0)))
    assert d.score.lead() == "ahead by 34.86"
    s = d.score.settled()
    assert s.startswith("not settled") and "RB, K" in s and "DST" in s
    assert "safe" not in d.to_html().lower()


def test_everything_final_on_both_sides_says_the_result_stands(schedule):
    fd = feed({t: "complete" for t in ("BAL", "NE", "ATL", "TEN", "ARI", "KC", "LAR")})
    d = build(schedule, fd=fd)
    assert "result stands" in d.score.settled()


def test_an_unknown_status_anywhere_stops_the_page_calling_anything_settled(schedule):
    d = build(schedule, fd=feed({}, present=False))
    assert "UNKNOWN" in d.score.settled()


def test_exposure_is_text_that_names_positions(schedule):
    d = build(schedule)
    assert d.score.opp.exposure() == "3 yet to play (RB, K, DST); 1 final; 1 empty slot"
    assert d.score.mine.exposure() == "1 yet to play (WR); 2 playing (RB, DST); 2 final"


# ---------------------------------------------------------------- actions

def action(*, generated=None, deadline=None, status="ACTIONABLE", ids=("5", "3"), kind="swap",
           slot=None, title="WR: start Bench Night Wideout over Night Receiver") -> dict:
    dl = (deadline or NOW + timedelta(hours=3)).isoformat()
    raw = {"kind": kind, "status": status, "title": title, "body": "x", "deadline": dl,
           "deadline_note": "act before kickoff", "backup": "none", "player_ids": list(ids)}
    if slot is not None:
        raw["slot"] = slot
    return raw


def _actions(schedule, tmp_path, *, generated=None, deadline=None, status="ACTIONABLE",
             ids=("5", "3"), kind="swap", slot=None, **kw):
    """One archived board with one action — a WR on the bench (night game)
    into the WR slot held by a WR whose game is also tonight: legal on every
    gate when nothing else is wrong."""
    gen = generated or NOW - timedelta(days=1)
    root = tmp_path / f"ledger{len(list(tmp_path.glob('ledger*')))}"   # one ledger per case
    write_record(root, generated=gen, actions=[
        action(deadline=deadline, status=status, ids=ids, kind=kind, slot=slot)])
    return build(schedule, archive_root=root, **kw)


def test_a_legal_pregame_swap_is_still_available_before_both_kickoffs(schedule, tmp_path):
    d = _actions(schedule, tmp_path)
    a = d.actions[0]
    assert a.available and a.eligible and "proven unlocked" in a.why
    assert "eligible for WR" in a.why and "all current" in a.why
    assert d.capacity.open_starters == 1 and d.capacity.open_bench == 1


# --- the archive's ACTIONABLE is history; every gate is re-run NOW

@pytest.mark.parametrize("kw, name", [
    ({"srcs": sources(league_fresh=False)}, "sleeper_league"),
    ({"srcs": sources(players_fresh=False)}, "sleeper_players"),
    ({"srcs": sources(schedule_fresh=False)}, "schedules"),
    ({"players_fresh": False}, "sleeper_players"),
])
def test_a_stale_input_withholds_the_advice_while_the_score_still_shows(schedule, tmp_path, kw, name):
    d = _actions(schedule, tmp_path, **kw)
    a = d.actions[0]
    assert not a.available and a.eligible and name in a.why and "not current" in a.why
    assert "score above stands on its own" in a.why
    assert d.score.mine.platform_points == 20.0 and d.score.lead() == "behind by 0.14"


def test_a_player_the_platform_lists_out_is_never_offered(schedule, tmp_path):
    for word in ("Out", "IR", "PUP", "Sus", "NA", "COV", "DNR"):
        d = _actions(schedule, tmp_path,
                     players={**PLAYERS, "5": {**PLAYERS["5"], "injury_status": word}})
        a = d.actions[0]
        assert not a.available and word in a.why and "not offered as a start" in a.why, word
        assert d.score.mine.platform_points == 20.0


def test_questionable_and_doubtful_are_eligible_and_said_so(schedule, tmp_path):
    """Rule #11: Questionable is not out. The card carries the word; the
    owner decides."""
    for word in ("Questionable", "Doubtful"):
        d = _actions(schedule, tmp_path,
                     players={**PLAYERS, "5": {**PLAYERS["5"], "injury_status": word}})
        a = d.actions[0]
        assert a.available and f"listed {word}" in a.why and "not out (rule #11)" in a.why


def test_the_incoming_player_must_be_eligible_for_the_actual_destination_slot(schedule, tmp_path):
    # an RB into the WR slot: never legal, whatever the board once said
    rb = {**PLAYERS, "5": {**PLAYERS["5"], "position": "RB"}}
    d = _actions(schedule, tmp_path, players=rb)
    assert not d.actions[0].available and "not eligible for the WR slot" in d.actions[0].why
    # the platform lists him at two positions: the list decides, not the tag
    multi = {**PLAYERS, "5": {**PLAYERS["5"], "position": "RB", "fantasy_positions": ["RB", "WR"]}}
    d = _actions(schedule, tmp_path, players=multi)
    assert d.actions[0].available and "(RB/WR) is eligible for WR" in d.actions[0].why
    # a position the dump does not carry cannot be shown eligible
    d = _actions(schedule, tmp_path, players={**PLAYERS, "5": {"full_name": "No Tag", "team": "LAR"}})
    assert not d.actions[0].available and "position is not known" in d.actions[0].why
    # an id the dump lacks altogether is unknown on every axis, lock first
    d = _actions(schedule, tmp_path, players={k: v for k, v in PLAYERS.items() if k != "5"})
    assert not d.actions[0].available and "UNKNOWN" in d.actions[0].why


def test_a_flex_slot_takes_any_flex_position_and_nothing_else(schedule, tmp_path):
    slots = ["QB", "RB", "WR", "FLEX", "K", "DEF"]
    snap = snapshot(slots=slots, my_starters=("1", "2", "3", "10", "4", "SEA"),
                    my_points=(14.0, 0.0, 0.0, 0.0, 7.0, -1.0),
                    my_players=("1", "2", "3", "10", "4", "SEA", "5", "6"))
    # WR 5 into the FLEX held by TE 10 (both night games): eligible
    wr = _actions(schedule, tmp_path, ids=("5", "10"), snap=snap)
    assert wr.actions[0].available and "eligible for FLEX" in wr.actions[0].why
    # a kicker into the same FLEX: not eligible
    k = _actions(schedule, tmp_path, ids=("5", "10"), snap=snap,
                 players={**PLAYERS, "5": {**PLAYERS["5"], "position": "K"}})
    assert not k.actions[0].available and "not eligible for the FLEX slot" in k.actions[0].why


def test_a_player_on_ir_or_taxi_is_a_roster_move_not_a_lineup_change(schedule, tmp_path):
    ir = _actions(schedule, tmp_path, snap=snapshot(reserve=("5",)))
    assert not ir.actions[0].available and "is on IR" in ir.actions[0].why
    taxi = _actions(schedule, tmp_path, snap=snapshot(taxi=("5",)))
    assert not taxi.actions[0].available and "is on TAXI" in taxi.actions[0].why
    assert taxi.capacity.open_bench == 0             # a taxi player is not bench capacity


def test_an_empty_slot_move_needs_the_slot_and_the_slot_must_still_be_empty(schedule, tmp_path):
    empty = snapshot(my_starters=("1", "2", "0", "4", "SEA"), my_points=(14.0, 0.0, 0.0, 7.0, -1.0))
    named = _actions(schedule, tmp_path, kind="empty_slot", ids=("5",), slot="WR", snap=empty)
    assert named.actions[0].available and "eligible for WR" in named.actions[0].why
    # only one slot is empty: no guess is needed
    unnamed = _actions(schedule, tmp_path, kind="empty_slot", ids=("5",), snap=empty)
    assert unnamed.actions[0].available
    # the named slot is filled again: nothing to do
    filled = _actions(schedule, tmp_path, kind="empty_slot", ids=("5",), slot="WR")
    assert not filled.actions[0].available and "no longer empty" in filled.actions[0].why
    # two empty slots and no name: not guessed
    two = snapshot(my_starters=("1", "0", "0", "4", "SEA"), my_points=(14.0, 0.0, 0.0, 7.0, -1.0))
    d = _actions(schedule, tmp_path, kind="empty_slot", ids=("5",), snap=two)
    assert not d.actions[0].available and "a slot is not guessed" in d.actions[0].why


def test_an_inactive_starter_replacement_is_judged_like_a_swap(schedule, tmp_path):
    d = _actions(schedule, tmp_path, kind="inactive_starter", ids=("5", "3"))
    assert d.actions[0].available and "eligible for WR" in d.actions[0].why
    none = _actions(schedule, tmp_path, kind="inactive_starter", ids=("3",))
    assert not none.actions[0].available and "nothing to move in" in none.actions[0].why


def test_a_withheld_pregame_action_never_becomes_advice_during_games(schedule, tmp_path):
    d = _actions(schedule, tmp_path, status="WITHHELD")
    assert not d.actions[0].available and "WITHHELD" in d.actions[0].why
    # the score still displays on its own freshness
    assert d.score.mine.platform_points == 20.0


def test_a_locked_or_unknown_player_is_never_offered(schedule, tmp_path):
    locked = _actions(schedule, tmp_path, ids=("6", "1"))       # both 13:00 ET: LOCKED
    assert not locked.actions[0].available and "LOCKED" in locked.actions[0].why
    unknown = _actions(schedule, tmp_path, fd=feed({}, present=False),
                       snap=snapshot(), players={**PLAYERS, "5": {**PLAYERS["5"], "team": ""}})
    assert not unknown.actions[0].available and "UNKNOWN" in unknown.actions[0].why


def test_the_feed_can_lock_a_player_the_schedule_still_calls_open(schedule, tmp_path):
    d = _actions(schedule, tmp_path, fd=feed({"KC": "in_game", "LAR": "pre_game"}))
    assert not d.actions[0].available and "LOCKED" in d.actions[0].why


def test_an_action_whose_lineup_already_moved_is_an_observation_not_a_decision(schedule, tmp_path):
    d = _actions(schedule, tmp_path, snap=snapshot(my_starters=("1", "2", "5", "4", "SEA")))
    assert not d.actions[0].available and "not proof" in d.actions[0].why


def test_a_record_written_after_its_own_deadline_is_not_decision_time_evidence(schedule, tmp_path):
    d = _actions(schedule, tmp_path, generated=NOW - timedelta(minutes=5),
                 deadline=NOW - timedelta(hours=1))
    assert not d.actions[0].available and not d.actions[0].eligible


def test_a_passed_deadline_and_a_pickup_are_never_available(schedule, tmp_path):
    passed = _actions(schedule, tmp_path, deadline=NOW - timedelta(minutes=1))
    assert "deadline passed" in passed.actions[0].why
    pickup = _actions(schedule, tmp_path, kind="acquire")
    assert not pickup.actions[0].available and "not a game-day move" in pickup.actions[0].why


def test_no_legal_move_left_is_said_plainly(schedule, tmp_path):
    fd = feed({t: "complete" for t in ("BAL", "NE", "ATL", "TEN", "ARI", "KC", "LAR")})
    late = datetime(2026, 9, 29, 6, 0, tzinfo=UTC)          # Tuesday
    d = build(schedule, fd=fd, now=late)
    assert d.capacity.sentence().startswith("No legal lineup change remains")


def test_an_unconfirmed_game_status_is_never_legal_advice(schedule, tmp_path):
    """No feed, or a stale one: the schedule alone says 'not started', and a
    kickoff time is not proof. The move is withheld and says why; the score
    still shows on its own."""
    d = _actions(schedule, tmp_path, fd=feed({}, present=False))
    assert not d.actions[0].available and "UNCONFIRMED" in d.actions[0].why
    assert d.score.mine.platform_points == 20.0
    stale = _actions(schedule, tmp_path, fd=feed({"KC": "pre_game", "LAR": "pre_game"}, fresh=False))
    assert not stale.actions[0].available and "UNCONFIRMED" in stale.actions[0].why


def test_a_fresh_feed_with_an_unknown_status_word_is_not_permission(schedule, tmp_path):
    """The feed is current and names the game, but with a word this page does
    not know. Freshness proved the feed, not the game: only an observed
    pre-game status endorses a move. The score still shows."""
    d = _actions(schedule, tmp_path, fd=feed({"KC": "unrecognized_live_status", "LAR": "pre_game"}))
    a = d.actions[0]
    assert not a.available and "UNKNOWN" in a.why and "unrecognized_live_status" in a.why
    assert d.score.mine.platform_points == 20.0
    # the unknown word is on the outgoing side too when it is the incoming's game
    d2 = _actions(schedule, tmp_path, fd=feed({"LAR": "unrecognized_live_status", "KC": "pre_game"}))
    assert not d2.actions[0].available and "UNKNOWN" in d2.actions[0].why


@pytest.mark.parametrize("team, side", [("LAR", "incoming"), ("KC", "outgoing")])
def test_a_canceled_game_on_either_side_withholds_the_move(schedule, tmp_path, team, side):
    """Starting a player whose game is canceled is never endorsed; and a swap
    the board priced assuming both games would be played is not re-modelled
    when one of them is canceled. Both sides are withheld, and say so."""
    d = _actions(schedule, tmp_path, fd=feed({team: "canceled"}))
    a = d.actions[0]
    assert not a.available and "CANCELED" in a.why, side
    assert d.score.mine.platform_points == 20.0


# ------------------------------------------------------------ pregame join

def test_no_record_means_a_clear_absence_and_nothing_reconstructed(schedule, tmp_path):
    d = build(schedule, archive_root=tmp_path / "empty")
    assert not d.pregame.found and d.pregame.note.startswith("no pregame record for week 3")
    assert d.actions == () and "no advice to re-check" in d.to_html()


def test_the_record_must_match_season_week_league_and_roster(schedule, tmp_path):
    root = tmp_path / "ledger"
    write_record(root, generated=NOW - timedelta(days=1), actions=[], week=2)
    write_record(root, generated=NOW - timedelta(days=1, hours=1), actions=[], league="OTHER")
    write_record(root, generated=NOW - timedelta(days=1, hours=2), actions=[], roster=9)
    assert not build(schedule, archive_root=root).pregame.found
    good = write_record(root, generated=NOW - timedelta(days=1, hours=3), actions=[])
    d = build(schedule, archive_root=root)
    assert d.pregame.path == good and d.pregame.tagged


def test_an_untagged_record_is_context_only_never_personalised_advice(schedule, tmp_path):
    root = tmp_path / "ledger"
    write_record(root, generated=NOW - timedelta(days=1), actions=[action()], tagged=False)
    d = build(schedule, archive_root=root)
    assert not d.pregame.found and d.actions == ()
    assert len(d.pregame.context) == 1 and "historical context only" in d.pregame.context[0]
    assert "no valid league_id tag" in d.pregame.context[0]
    # ...and the same for one that carries a different league or roster, untagged or not
    foreign = tmp_path / "foreign"
    write_record(foreign, generated=NOW - timedelta(days=1), actions=[action()],
                 league="DIFFERENT_LEAGUE", roster=99, tagged=False)
    assert not build(schedule, archive_root=foreign).pregame.found
    assert gd.find_pregame_record(foreign, season=2026, week=3, league_id=LEAGUE,
                                  my_roster_id=1, now=NOW)[0] is None


def test_a_record_from_the_future_is_refused_and_said_so(schedule, tmp_path):
    root = tmp_path / "ledger"
    write_record(root, generated=NOW + timedelta(hours=1), actions=[action()])
    d = build(schedule, archive_root=root)
    assert not d.pregame.found and d.actions == ()
    assert any("record from the future" in c for c in d.pregame.context)
    # a few minutes of device-clock drift is not 'the future'
    close = tmp_path / "close"
    write_record(close, generated=NOW + timedelta(minutes=2), actions=[action()])
    assert build(schedule, archive_root=close).pregame.found


def test_a_record_whose_bytes_no_longer_match_its_digest_is_refused(schedule, tmp_path):
    root = tmp_path / "ledger"
    path = write_record(root, generated=NOW - timedelta(days=1), actions=[action()])
    blob = json.loads(path.read_text("utf-8"))
    blob["actions"][0]["player_ids"] = ["6", "3"]           # an edit after the fact
    path.write_text(json.dumps(blob, indent=1), "utf-8")
    d = build(schedule, archive_root=root)
    assert not d.pregame.found and d.actions == ()
    assert any("no longer match the digest" in c for c in d.pregame.context)


def test_a_malformed_identity_is_refused_safely(schedule, tmp_path):
    root = tmp_path / "ledger"
    for bad_league, bad_roster in (({"id": LEAGUE}, 1), (LEAGUE, "one"), (LEAGUE, True),
                                   (["TESTLEAGUE"], 1), (LEAGUE, 1.5)):
        rec_root = tmp_path / f"bad{len(list(tmp_path.iterdir()))}"
        path = write_record(rec_root, generated=NOW - timedelta(days=1), actions=[action()])
        blob = json.loads(path.read_text("utf-8"))
        blob["league_id"], blob["my_roster_id"] = bad_league, bad_roster
        path.unlink()
        # re-archive with a matching digest so only the identity is at fault
        body = {k: v for k, v in blob.items() if k != "archive_version"}
        new = archive_path(2026, 3, NOW - timedelta(days=1), rec_root, body)
        write_archive(body, new)
        d = build(schedule, archive_root=rec_root)
        assert not d.pregame.found and d.actions == (), (bad_league, bad_roster)
        assert any("malformed" in c for c in d.pregame.context), (bad_league, bad_roster)
    assert not build(schedule, archive_root=root).pregame.found


def test_a_newer_postgame_board_does_not_erase_the_pregame_evidence(schedule, tmp_path):
    """Saturday's board endorsed the swap before its deadline. A Sunday-night
    render (after the deadline) is a newer record but not decision-time
    evidence for that move: the Saturday provenance stands, the Sunday board
    is listed, and the move is judged on today's gates (deadline passed)."""
    root = tmp_path / "ledger"
    sat = NOW - timedelta(days=1)
    deadline = NOW - timedelta(hours=1)
    write_record(root, generated=sat, actions=[action(deadline=deadline)])
    sun = write_record(root, generated=NOW - timedelta(minutes=10), actions=[action(deadline=deadline)])
    d = build(schedule, archive_root=root)
    (a,) = d.actions
    assert a.generated == sat and a.eligible and not a.available and "deadline passed" in a.why
    assert len(d.pregame.records) == 2 and "newest board written before its own deadline" in d.pregame.note
    assert d.pregame.path != sun


def test_a_later_decision_time_board_that_dropped_a_move_supersedes_it(schedule, tmp_path):
    root = tmp_path / "ledger"
    write_record(root, generated=NOW - timedelta(days=1), actions=[action()])
    write_record(root, generated=NOW - timedelta(hours=2), actions=[])     # still before the deadline
    d = build(schedule, archive_root=root)
    (a,) = d.actions
    assert not a.available and "superseded" in a.why and a.superseded
    assert a.generated == NOW - timedelta(days=1)


def test_a_designation_that_moved_after_the_record_is_reported_without_blame(schedule, tmp_path):
    root = tmp_path / "ledger"
    write_record(root, generated=NOW - timedelta(days=1), actions=[], roster_rows=[
        {"sleeper_id": "1", "availability": "Questionable (sleeper)"}])
    players = {**PLAYERS, "1": {**PLAYERS["1"], "injury_status": "Out"}}
    d = build(schedule, archive_root=root, players=players)
    (line,) = d.pregame.designation_changes
    assert "Questionable" in line and "Out" in line and "cannot show what was knowable" in line
    html = d.to_html()
    assert "should have" not in html and "blame" not in html


def test_outcomes_are_descriptive_and_unproven_until_both_final(schedule, tmp_path):
    d = _actions(schedule, tmp_path)
    (line,) = d.pregame.outcomes
    assert "UNPROVEN" in line and "not a record of a decision" in line
    fd = feed({t: "complete" for t in ("BAL", "NE", "ATL", "TEN", "ARI", "KC", "LAR")})
    d2 = _actions(schedule, tmp_path, fd=fd)
    (line2,) = d2.pregame.outcomes
    assert "both final" in line2 and "descriptive" in line2


def test_the_pregame_projection_is_shown_as_pregame_and_never_added_to_points(schedule, tmp_path):
    root = tmp_path / "ledger"
    write_record(root, generated=NOW - timedelta(days=1), actions=[],
                 matchup={"opponent_roster_id": 2, "my_mean": 120.0, "opp_mean": 90.0, "pwin": 0.83})
    d = build(schedule, archive_root=root)
    assert "UNCALIBRATED, pregame" in d.pregame.projection
    assert "never added" in d.pregame.projection
    assert d.score.mine.platform_points == 20.0


# ------------------------------------------------------------------ diffs

def test_a_first_visit_says_so(schedule):
    d = build(schedule)
    assert d.changes.items == () and "no earlier" in d.changes.note


def test_a_lowered_total_is_named_a_correction_and_a_lineup_change_is_listed(schedule):
    first = build(schedule).record()
    snap = snapshot(my_starters=("1", "5", "3", "4", "SEA"), my_points=(14.0, 9.5, 0.0, 7.0, -1.0),
                    opp_points=(18.0, 0.0, 0.0, 0.0, 0.0))
    d = build(schedule, snap=snap, previous=first)
    kinds = {(k, s) for k, s, _ in d.changes.items}
    assert ("score CORRECTION (lowered)", "Roster #2") in kinds
    assert ("lineup", "You") in kinds and ("score", "You") in kinds


def test_another_week_or_roster_is_never_compared(schedule):
    prev = dict(build(schedule).record(), week=2)
    d = build(schedule, previous=prev)
    assert d.changes.items == () and "nothing is compared across that boundary" in d.changes.note


def test_a_feed_that_vanished_is_a_change(schedule):
    prev = build(schedule).record()
    d = build(schedule, fd=feed({}, present=False), previous=prev)
    assert ("source", "game status feed", "NO LONGER available") in d.changes.items


# ------------------------------------------------------------------- page

def test_upstream_names_cannot_inject_markup_or_script(schedule):
    evil = {**PLAYERS, "1": {"full_name": "<img src=x onerror=alert(1)>", "position": "QB",
                             "team": "BAL</script><script>alert(2)</script>"}}
    html = build(schedule, players=evil).to_html()
    assert "<img" not in html and "<script>alert" not in html and "</script><script" not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html          # shown as text
    blob = html.split('id="gd-data">')[1].split("</script>")[0]
    assert "<" not in blob and ">" not in blob and "&" not in blob
    assert json.loads(blob)["names"]["1"]["name"] == "<img src=x onerror=alert(1)>"


def test_the_page_may_only_talk_to_sleeper(schedule):
    html = build(schedule).to_html()
    assert "connect-src https://api.sleeper.app;" in html
    assert "default-src 'none'" in html and "form-action 'none'" in html
    assert "nonce-" in html


def test_no_live_win_probability_and_no_projection_arithmetic_on_the_page(schedule, tmp_path):
    root = tmp_path / "ledger"
    write_record(root, generated=NOW - timedelta(days=1), actions=[],
                 matchup={"opponent_roster_id": 2, "my_mean": 120.0, "opp_mean": 90.0, "pwin": 0.83})
    html = build(schedule, archive_root=root).to_html()
    score = html.split('id="gd-score"')[1].split("</div>\n")[0]
    assert "%" not in score and "P(win)" not in score and "win" not in score.lower()
    assert "remaining projection" not in html and "projected remaining" not in html


def test_the_record_is_the_page(schedule):
    d = build(schedule)
    rec = d.record()
    assert rec["score"]["mine"]["platform_points"] == 20.0
    assert rec["score"]["mine"]["exposure"] == d.score.mine.exposure()
    assert rec["source_kind"] == gd.SOURCE_KIND and rec["league_id"] == LEAGUE
    assert [s["state"] for s in rec["score"]["opp"]["starters"]] == \
        [gd.FINAL, gd.NOT_STARTED, gd.NOT_STARTED, gd.EMPTY, gd.NOT_STARTED]


def test_a_stale_league_snapshot_is_said_to_be_the_last_score_seen(schedule):
    d = build(schedule, srcs=sources(league_fresh=False))
    assert any("last one seen" in n for n in d.notes)
    assert d.score.mine.platform_points == 20.0       # still shown, dated


def test_a_week_the_platform_has_left_is_flagged_and_not_compared(schedule):
    d = build(schedule, snap=snapshot(state_week=4))
    assert d.state_week == 4 and any("week 4" in n for n in d.notes)
