"""Free agents the radar cannot rank are still named (gridiron.notable).

Pins, with invented players:
  1. last season's starters are ranked by league points per game within
     position, scored by the one scoring implementation (rule #2), and a
     player under MIN_PRIOR_GAMES is never ranked;
  2. league drops: the latest COMPLETE drop per id, timestamps from Sleeper's
     milliseconds; a missing or failed transactions block says drops are
     UNKNOWN instead of returning "no drops";
  3. the waiver timing the league's settings imply: the first Wednesday
     ~03:00 Eastern at least two days after the drop (2026 week 7: a Friday
     03:00 UTC drop was claimed Wednesday 07:17 UTC);
  4. a player with no number of ours who was a starter last season, or a
     recent league drop whom Sleeper ranks as a starter, is notable; a
     recent drop worth nothing this week is not;
  5. the market list carries every ABSTAINED player with a Sleeper number,
     highest first, and never a withheld (Out / bye) one;
  6. NEW marks only a player absent from the previous record's pool, and
     nothing is NEW without a previous record.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from gridiron import notable as N
from gridiron.lineup import Player, slot_order
from gridiron.projection import Projection, abstain

UTC = timezone.utc
NOW = datetime(2026, 10, 18, 12, 0, tzinfo=UTC)          # a Sunday
SLOTS = slot_order(None)


def P(sid, pos, mean=None, *, gid=None, withheld=False, name=None):
    if mean is None:
        proj = abstain("no admissible box scores through week 6")
    else:
        proj = Projection(mean, max(mean * 0.5, 1.5))
        if withheld:
            proj = proj.withheld("designated Out: projected 0")
    return Player(sid, name or f"Invented {sid}", pos, "AAA", proj, "BENCH", False, "", "",
                  (), gid if gid is not None else f"00-{sid}")


def lineup():
    """A best lineup whose weakest RB-eligible starter projects 9.0."""
    by = {"QB": P("q1", "QB", 20), "RB": [P("r1", "RB", 15), P("r2", "RB", 11)],
          "WR": [P("w1", "WR", 14), P("w2", "WR", 12)], "TE": P("t1", "TE", 8),
          "FLEX": P("f1", "WR", 9), "K": P("k1", "K", 8), "DEF": None}
    best = []
    for s in SLOTS:
        if s in ("RB", "WR"):
            best.append(by[s].pop(0))
        else:
            best.append(by.get(s))
    return best


def prior_frame():
    rows = []
    for wk in range(1, 9):
        rows.append({"player_id": "00-a", "position": "RB", "team": "AAA", "week": wk,
                     "rushing_yards": 120, "receptions": 3, "receiving_yards": 30})
        rows.append({"player_id": "00-b", "position": "RB", "team": "BBB", "week": wk,
                     "rushing_yards": 40, "receptions": 1, "receiving_yards": 5})
    for wk in range(1, 4):                       # 3 games: never ranked
        rows.append({"player_id": "00-c", "position": "RB", "team": "CCC", "week": wk,
                     "rushing_yards": 200, "receptions": 8, "receiving_yards": 90})
    return pd.DataFrame(rows)


def ms(t: datetime) -> int:
    return int(t.timestamp() * 1000)


# ---------------------------------------------------------------- 1. prior
def test_prior_lines_rank_within_position_and_skip_thin_seasons():
    lines = N.prior_lines(prior_frame())
    assert lines["00-a"].rank == 1 and lines["00-b"].rank == 2
    assert lines["00-c"].rank is None and lines["00-c"].games == 3
    assert lines["00-a"].ppg > lines["00-b"].ppg > 0


def test_prior_lines_absent_frame_is_unknown_not_zero():
    assert N.prior_lines(None) == {}
    assert N.prior_lines(pd.DataFrame(columns=["player_id", "week"])) == {}


# ---------------------------------------------------------------- 2. drops
def test_league_drops_keep_the_latest_complete_drop():
    t0, t1 = NOW - timedelta(days=4), NOW - timedelta(days=2)
    block = {"weeks": [6, 7], "error": "", "rows": [
        {"type": "free_agent", "status": "complete", "drops": {"501": 5},
         "status_updated": ms(t0)},
        {"type": "waiver", "status": "complete", "drops": {"501": 8},
         "status_updated": ms(t1)},
        {"type": "waiver", "status": "failed", "drops": {"502": 3},
         "status_updated": ms(t1)},
        {"type": "free_agent", "status": "complete", "adds": {"503": 4}, "drops": None,
         "created": ms(t1)}]}
    drops, note = N.league_drops(block)
    assert note == ""
    assert set(drops) == {"501"}
    assert drops["501"].roster_id == 8 and drops["501"].at == t1.replace(microsecond=0)


def test_missing_or_failed_transactions_are_unknown_not_none():
    for block in (None, {"weeks": [7], "rows": [], "error": "HTTPError: 503"}):
        drops, note = N.league_drops(block)
        assert drops == {} and "UNKNOWN" in note


# ---------------------------------------------------------------- 3. waivers
def test_waiver_run_is_the_first_wednesday_three_am_eastern_after_the_clear():
    # the 2026 week-7 case: dropped Fri 16 Oct 03:00 UTC, claimed Wed 21 Oct
    run = N.waiver_run_after(datetime(2026, 10, 16, 3, 0, tzinfo=UTC))
    assert run == datetime(2026, 10, 21, 7, 0, tzinfo=UTC)
    assert run.weekday() == 2
    # dropped Tuesday: two days later is Thursday, so it waits for next Wednesday
    run = N.waiver_run_after(datetime(2026, 10, 20, 12, 0, tzinfo=UTC))
    assert run == datetime(2026, 10, 28, 7, 0, tzinfo=UTC)
    # Eastern standard time after 1 Nov: 03:00 EST is 08:00 UTC
    run = N.waiver_run_after(datetime(2026, 11, 6, 3, 0, tzinfo=UTC))
    assert run == datetime(2026, 11, 11, 8, 0, tzinfo=UTC)


# ---------------------------------------------------------------- 4/5/6. watch
def watch(pool, *, market=None, drops=None, prev=None, ranks=None, prior=None):
    market = market or {}
    return N.build_watch(
        pool, sleeper_players={}, market_of=lambda p: market.get(p.sleeper_id),
        prior=N.prior_lines(prior_frame()) if prior is None else prior, prior_season=2025,
        ranks=ranks, drops=drops or {}, drops_note="", now=NOW, best=lineup(), slots=SLOTS,
        reserve={"00-a"}, previous_pool_ids=prev)


def test_a_returning_starter_with_no_number_of_ours_is_named():
    returner = P("a", "RB", gid="00-a", name="Ivor Returner")
    w = watch([returner, P("b", "RB", gid="00-b")], market={"a": 11.7})
    # both were top-24 RBs in this two-player season; the better one first
    assert [n.player.sleeper_id for n in w.notable] == ["a", "b"]
    n = w.notable[0]
    assert n.ours is None and "no admissible box scores" in n.ours_note
    assert n.market == 11.7 and "RB1" in n.why[0]
    assert "reserve list" in n.status
    assert "open him in Sleeper" in n.waiver


def test_a_recent_league_drop_is_notable_when_sleeper_ranks_him_a_starter():
    d = N.Drop("x", 5, NOW - timedelta(days=3), "free_agent")
    pool = [P("x", "RB", gid="00-x")]
    w = watch(pool, market={"x": 7.5}, drops={"x": d}, ranks={"x": 18})
    assert len(w.notable) == 1
    assert "roster #5" in w.notable[0].why[0] and "RB18" in w.notable[0].why[0]
    assert "claim run at" in w.notable[0].waiver
    # same drop, ranked RB40 by Sleeper and below the weakest starter: not notable
    assert watch(pool, market={"x": 7.5}, drops={"x": d}, ranks={"x": 40}).notable == ()


def test_a_drop_reaching_the_weakest_eligible_starter_is_notable():
    d = N.Drop("y", 2, NOW - timedelta(days=1), "waiver")
    w = watch([P("y", "WR", 9.5, gid="00-y")], drops={"y": d})
    assert len(w.notable) == 1 and "reaches your weakest eligible starter" in w.notable[0].why[0]
    old = N.Drop("y", 2, NOW - timedelta(days=N.DROP_WINDOW_DAYS + 1), "waiver")
    assert watch([P("y", "WR", 9.5, gid="00-y")], drops={"y": old}).notable == ()


def test_a_waiver_run_already_past_says_free_agent_not_on_waivers():
    d = N.Drop("y", 2, NOW - timedelta(days=8), "waiver")
    w = watch([P("y", "WR", 9.5, gid="00-y")], drops={"y": d})
    assert "already past" in w.notable[0].waiver and "FREE AGENT" in w.notable[0].waiver


def test_market_list_is_abstained_players_with_a_sleeper_number_highest_first():
    pool = [P("m1", "WR", gid="00-m1"), P("m2", "RB", gid="00-m2"),
            P("m3", "TE", 6.0, gid="00-m3"),                    # we project him: not listed
            P("m4", "RB", 6.0, gid="00-m4", withheld=True),     # Out: known not to play
            P("m5", "QB", gid="00-m5")]                          # no Sleeper number
    w = watch(pool, market={"m1": 4.2, "m2": 12.9, "m3": 7.0, "m4": 9.9})
    assert [m.player.sleeper_id for m in w.market] == ["m2", "m1"]
    assert w.market[0].gain == round(12.9 - 9.0, 2)
    assert all("no admissible box scores" in m.why_unprojected for m in w.market)


def test_new_needs_a_previous_pool_and_absence_from_it():
    returner = P("a", "RB", gid="00-a")
    assert not watch([returner]).notable[0].new
    assert watch([returner], prev=["zz"]).notable[0].new
    assert not watch([returner], prev=["a"]).notable[0].new


def test_no_prior_season_is_said_not_silently_skipped():
    w = watch([P("a", "RB", gid="00-a")], prior={})
    assert w.notable == ()
    assert any("cannot be checked" in n for n in w.notes)


def test_market_ranks_refuse_a_thin_feed():
    thin = {str(i): {"points": float(i), "position": "RB"} for i in range(5)}
    assert N.market_ranks(thin) == {}
    full = {str(i): {"points": float(i), "position": "RB"}
            for i in range(N.FEED_MIN_PER_POSITION)}
    ranks = N.market_ranks(full)
    assert ranks[str(N.FEED_MIN_PER_POSITION - 1)] == 1 and ranks["0"] == N.FEED_MIN_PER_POSITION


def test_a_fullback_last_season_is_not_a_starter_running_back():
    frame = pd.DataFrame([{"player_id": "00-fb", "position": "FB", "team": "AAA", "week": w,
                           "rushing_yards": 5} for w in range(1, 9)])
    line = N.prior_lines(frame)["00-fb"]
    assert line.rank == 1 and not line.starter
    w = watch([P("fb", "RB", gid="00-fb")], prior=N.prior_lines(frame))
    assert w.notable == ()


def test_the_owners_own_drop_says_you():
    d = N.Drop("y", 3, NOW - timedelta(days=1), "free_agent")
    w = N.build_watch([P("y", "WR", 9.5, gid="00-y")], sleeper_players={},
                      market_of=lambda p: None, prior={}, prior_season=2025, drops={"y": d},
                      drops_note="", now=NOW, best=lineup(), slots=SLOTS, my_roster_id=3)
    assert "dropped by YOU (roster #3)" in w.notable[0].why[0]


def test_the_alert_is_a_transition_not_a_standing_state():
    stash = P("a", "RB", gid="00-a")                 # a starter last season, in the pool
    # first build after the list existed: no first-seen known, no drop -> listed, no alert
    w = watch([stash], prev=["a"])
    assert w.notable and not w.alerts and w.notable[0].first_seen is None
    # entered the pool this build -> NEW and alert; the time carries forward
    w = watch([stash], prev=["zz"])
    assert w.alerts and w.notable[0].new and w.notable[0].first_seen == NOW
    later = NOW + timedelta(hours=N.ALERT_HOURS - 1)
    w2 = N.build_watch([stash], sleeper_players={}, market_of=lambda p: None,
                       prior=N.prior_lines(prior_frame()), prior_season=2025, drops={},
                       drops_note="", now=later, best=lineup(), slots=SLOTS,
                       previous_pool_ids=["a"], previous_first_seen={"a": NOW.isoformat()})
    assert w2.alerts and not w2.notable[0].new and w2.notable[0].first_seen == NOW
    w3 = N.build_watch([stash], sleeper_players={}, market_of=lambda p: None,
                       prior=N.prior_lines(prior_frame()), prior_season=2025, drops={},
                       drops_note="", now=NOW + timedelta(hours=N.ALERT_HOURS + 1),
                       best=lineup(), slots=SLOTS, previous_pool_ids=["a"],
                       previous_first_seen={"a": NOW.isoformat()})
    assert w3.notable and not w3.alerts, "after the window he stays listed, off the top"


def test_an_open_waiver_window_keeps_the_alert_up():
    d = N.Drop("a", 5, NOW - timedelta(days=3), "free_agent")    # run: Wed 21 Oct
    w = watch([P("a", "RB", gid="00-a")], drops={"a": d}, prev=["a"])
    assert w.alerts and w.notable[0].first_seen is None


def test_malformed_optional_inputs_never_raise():
    old_schema = prior_frame().rename(columns={"team": "recent_team"})
    assert N.prior_lines(old_schema)["00-a"].team == "AAA"
    assert N.prior_lines(prior_frame().drop(columns=["position"])) == {}
    drops, note = N.league_drops({"rows": [{"status": "complete", "drops": ["501"],
                                            "created": 1}, "junk"], "error": ""})
    assert drops == {} and note == ""
