"""Free agents the radar cannot rank are still named (gridiron.notable).

Pins, with invented players:
  1. last season's starters are ranked by league points per game within
     LAST season's position, scored by the one scoring implementation
     (rule #2), and a player under MIN_PRIOR_GAMES is never ranked;
  2. league drops: the latest COMPLETE drop per id, timestamps from Sleeper's
     milliseconds; a missing or failed transactions block says drops are
     UNKNOWN instead of returning "no drops";
  3. the waiver clock, built from this league's own transactions: on waivers
     for two days after a drop, then a FREE AGENT until his kickoff, then
     locked until the Wednesday run (a Wednesday drop was an instant add on
     Sunday; every claim processed Wednesday ~07:17 UTC). Never an
     instruction to bid (rule #11);
  4. a player with no number of ours who was a starter last season, or a
     recent league drop whom Sleeper ranks as a starter or who reaches the
     weakest eligible starter (an empty slot counts as 0), is notable; a
     recent drop worth nothing this week, or designated Out, is not;
  5. the market list carries every ABSTAINED player with a Sleeper number,
     highest first, never one withheld or designated Out, and the record
     keeps every row;
  6. the alert is a transition: each change of a league drop's waiver clock
     (read off this build's data), or, with no drop behind him, entering the
     pool against a recent non-degraded record while the drops are known —
     never the owner's own drop.
"""
from __future__ import annotations

from dataclasses import replace
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
at = lambda *a: datetime(2026, *a, tzinfo=UTC)   # noqa: E731


def test_the_episode_ends_at_the_first_wednesday_run_after_the_clear():
    # the 2026 week-7 case: dropped Fri 16 Oct 03:00 UTC, claimed Wed 21 Oct 07:17
    run = N.waiver_run_after(at(10, 16, 3, 0))
    assert run == at(10, 21, 7, 20) and run.weekday() == 2
    assert N.waiver_run_after(at(10, 20, 12, 0)) == at(10, 28, 7, 20)
    # Eastern standard time after 1 Nov: 03:20 EST is 08:20 UTC
    assert N.waiver_run_after(at(11, 6, 3, 0)) == at(11, 11, 8, 20)
    assert N.last_waiver_run(at(10, 21, 7, 19)) == at(10, 14, 7, 20)
    assert N.next_waiver_run(at(10, 21, 7, 19)) == at(10, 21, 7, 20)


def test_the_waiver_clock_follows_this_leagues_transactions():
    """A Wednesday 08:13 drop was added as an instant FREE AGENT that Sunday,
    days before the next run: two days on waivers, then a free agent until
    his game, then locked until the Wednesday run, then free again."""
    d = N.Drop("x", 7, at(9, 23, 8, 13), "free_agent")
    sun, nxt = at(9, 27, 17, 0), at(10, 4, 17, 0)
    c = N.waiver_clock(d, sun, None, at(9, 24, 12, 0))
    assert c.phase == "WAIVERS" and c.until == at(9, 25, 8, 13)
    c = N.waiver_clock(d, sun, None, at(9, 26, 12, 0))
    assert c.phase == "FREE_AGENT" and "instant add" in c.text
    assert c.events == (d.at, at(9, 25, 8, 13)) and c.until == sun
    c = N.waiver_clock(d, sun, None, at(9, 28, 12, 0))
    assert c.phase == "LOCKED" and c.until == at(9, 30, 7, 20)
    assert c.events == (d.at, at(9, 25, 8, 13), sun)
    # Tuesday: the board's week has rolled to the next game; last week's game locks him
    c = N.waiver_clock(d, nxt, sun, at(9, 29, 12, 0))
    assert c.phase == "LOCKED" and c.events[-1] == sun
    # after the run he is an instant add again, and that is a new transition
    c = N.waiver_clock(d, nxt, sun, at(9, 30, 10, 0))
    assert c.phase == "FREE_AGENT" and c.events[-1] == at(9, 30, 7, 20)
    # a bye last week: never locked
    assert N.waiver_clock(d, nxt, None, at(9, 29, 12, 0)).phase == "FREE_AGENT"
    assert N.waiver_clock(d, None, None, at(9, 26, 12, 0)).phase == "UNSURE"
    for now in (at(9, 24, 12, 0), at(9, 26, 12, 0), at(9, 28, 12, 0)):
        assert "bid" not in N.waiver_clock(d, sun, None, now).text.lower()


def test_the_week_seven_player_was_an_instant_add_before_his_sunday_game():
    d = N.Drop("j", 5, at(10, 16, 3, 0), "free_agent")
    kick = at(10, 19, 0, 20)                      # DAL at GB, Sunday 20:20 ET
    assert N.waiver_clock(d, kick, None, at(10, 17, 12, 0)).phase == "WAIVERS"
    c = N.waiver_clock(d, kick, None, at(10, 18, 12, 0))
    assert c.phase == "FREE_AGENT" and c.until == kick
    c = N.waiver_clock(d, kick, None, at(10, 19, 20, 0))
    assert c.phase == "LOCKED" and c.until == at(10, 21, 7, 20)
    # dropped, freed, locked: three transitions, the last one opening the bidding window
    assert c.events == (d.at, at(10, 18, 3, 0), kick)


def test_a_game_inside_the_clear_keeps_him_on_waivers_until_the_run():
    d = N.Drop("j", 5, at(10, 17, 12, 0), "free_agent")        # clear Mon 19 12:00
    kick = at(10, 18, 17, 0)
    c = N.waiver_clock(d, kick, None, at(10, 18, 9, 0))
    assert c.phase == "WAIVERS" and c.until == at(10, 21, 7, 20) and "stays on waivers" in c.text
    c = N.waiver_clock(d, kick, None, at(10, 20, 12, 0))
    assert c.phase == "LOCKED" and c.events == (d.at,), "never a free agent in between"


# ---------------------------------------------------------------- 4/5/6. watch
def watch(pool, *, market=None, drops=None, previous=None, ranks=None, prior=None, now=NOW,
          roster=(), sleeper=None, best=None, mine=None, after_kickoff=None, drops_note=""):
    market, after_kickoff = market or {}, after_kickoff or {}
    return N.build_watch(
        pool, sleeper_players=sleeper or {}, market_of=lambda p: market.get(p.sleeper_id),
        market_any=lambda p: market.get(p.sleeper_id, after_kickoff.get(p.sleeper_id)),
        prior=N.prior_lines(prior_frame()) if prior is None else prior, prior_season=2025,
        ranks=ranks, drops=drops or {}, drops_note=drops_note, now=now,
        best=lineup() if best is None else best, slots=SLOTS, roster=roster,
        out_designations={"OUT", "IR"}, reserve={"00-a"}, reserve_week=6, my_roster_id=mine,
        previous=previous)


def test_a_returning_starter_with_no_number_of_ours_is_named():
    returner = P("a", "RB", gid="00-a", name="Ivor Returner")
    w = watch([returner, P("b", "RB", gid="00-b")], market={"a": 11.7})
    # both were top-24 RBs in this two-player season; the better one first
    assert [n.player.sleeper_id for n in w.notable] == ["a", "b"]
    n = w.notable[0]
    assert n.ours is None and "no admissible box scores" in n.ours_note
    assert n.market == 11.7 and "RB1" in n.why[0]
    assert "reserve list (RES/EXE, week-6 roster)" in n.status
    assert "Sleeper card" in n.waiver


def test_a_recent_league_drop_is_notable_when_sleeper_ranks_him_a_starter():
    d = N.Drop("x", 5, NOW - timedelta(days=3), "free_agent")
    pool = [P("x", "RB", gid="00-x")]
    w = watch(pool, market={"x": 7.5}, drops={"x": d}, ranks={"x": 18})
    assert len(w.notable) == 1
    assert "roster #5" in w.notable[0].why[0] and "RB18" in w.notable[0].why[0]
    assert "free agent since" in w.notable[0].waiver.lower()
    # same drop, ranked RB40 by Sleeper and below the weakest starter: not notable
    assert watch(pool, market={"x": 7.5}, drops={"x": d}, ranks={"x": 40}).notable == ()


def test_a_drop_stays_listed_while_his_game_locks_him():
    """After his kickoff the pre-kickoff number is gone (the feed refreshed);
    the same week's number keeps him listed through the bidding window."""
    d = N.Drop("x", 5, NOW - timedelta(days=3), "free_agent")
    x = replace(P("x", "RB", gid="00-x"), kickoff=NOW - timedelta(hours=2))
    w = watch([x], after_kickoff={"x": 7.5}, drops={"x": d}, ranks={"x": 18})
    assert len(w.notable) == 1 and w.notable[0].clock.phase == "LOCKED" and w.alerts
    assert w.market == (), "the market list keeps the pre-kickoff rule"


def test_a_drop_reaching_the_weakest_eligible_starter_is_notable():
    d = N.Drop("y", 2, NOW - timedelta(days=1), "waiver")
    w = watch([P("y", "WR", 9.5, gid="00-y")], drops={"y": d})
    assert len(w.notable) == 1 and "reaches your weakest eligible starter" in w.notable[0].why[0]
    assert w.notable[0].clock.phase == "WAIVERS"
    old = N.Drop("y", 2, NOW - timedelta(days=N.DROP_WINDOW_DAYS + 1), "waiver")
    assert watch([P("y", "WR", 9.5, gid="00-y")], drops={"y": old}).notable == ()


def test_a_player_who_will_not_play_is_never_worth_a_look_this_week():
    d = N.Drop("o", 2, NOW - timedelta(days=1), "waiver")
    withheld = P("o", "RB", 12.0, gid="00-o", withheld=True)
    assert watch([withheld], market={"o": 12.0}, drops={"o": d}, ranks={"o": 3}).notable == ()
    tagged = P("o", "RB", gid="00-o")                    # abstained, tagged Out by Sleeper
    w = watch([tagged], market={"o": 12.0}, drops={"o": d}, ranks={"o": 3},
              sleeper={"o": {"injury_status": "Out"}})
    assert w.notable == () and w.market == ()


def test_an_empty_slot_counts_as_zero_but_an_unprojected_player_is_not_empty():
    best = [None if s == "TE" else b for s, b in zip(SLOTS, lineup())]
    ir_te = replace(P("t9", "TE", 7.0), lineup="IR")
    roster = [b for b in best if b is not None] + [ir_te]
    d = N.Drop("z", 4, NOW - timedelta(days=1), "free_agent")
    w = watch([P("z", "TE", gid="00-z")], market={"z": 6.0}, drops={"z": d}, best=best,
              roster=roster)
    assert len(w.notable) == 1 and "an empty TE slot, 0.0" in w.notable[0].why[0]
    # a TE projected 0 does not "reach" an empty slot
    assert watch([P("z", "TE", gid="00-z")], market={"z": 0.0}, drops={"z": d}, best=best,
                 roster=roster).notable == ()
    # a TE on the bench the lineup could not place (no number): unknown, not empty
    bench_te = replace(P("t8", "TE"), lineup="BENCH")
    assert watch([P("z", "TE", gid="00-z")], market={"z": 6.0}, drops={"z": d}, best=best,
                 roster=roster + [bench_te]).notable == ()
    # ...but one whose game has kicked off can never move in: the slot is empty
    locked_te = replace(bench_te, locked=True, lock_known=True)
    w = watch([P("z", "TE", gid="00-z")], market={"z": 6.0}, drops={"z": d}, best=best,
              roster=roster + [locked_te])
    assert len(w.notable) == 1


def test_market_list_is_abstained_players_with_a_sleeper_number_highest_first():
    pool = [P("m1", "WR", gid="00-m1"), P("m2", "RB", gid="00-m2"),
            P("m3", "TE", 6.0, gid="00-m3"),                    # we project him: not listed
            P("m4", "RB", 6.0, gid="00-m4", withheld=True),     # Out: known not to play
            P("m5", "QB", gid="00-m5")]                          # no Sleeper number
    w = watch(pool, market={"m1": 4.2, "m2": 12.9, "m3": 7.0, "m4": 9.9})
    assert [m.player.sleeper_id for m in w.market] == ["m2", "m1"]
    assert w.market[0].gain == round(12.9 - 9.0, 2)
    assert all("no admissible box scores" in m.why_unprojected for m in w.market)
    many = [P(f"k{i}", "WR", gid=f"00-k{i}") for i in range(N.MARKET_CAP + 5)]
    w = watch(many, market={f"k{i}": 20.0 - i for i in range(N.MARKET_CAP + 5)})
    assert len(w.record()["market"]) == N.MARKET_CAP + 5, "the record keeps every row"


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


# ---------------------------------------------------------------- 6. transitions
def prev(pool=(), pool_since=None, generated=None):
    return N.PreviousWatch(pool=frozenset(pool) or None, pool_since=pool_since or {},
                           generated=NOW - timedelta(hours=1) if generated is None else generated)


def test_nothing_is_new_without_a_comparable_previous_record():
    stash = P("a", "RB", gid="00-a")                 # a starter last season, in the pool
    w = watch([stash])
    assert w.notable and not w.alerts and w.notable[0].first_seen is None
    assert not watch([stash], previous=prev(pool={"a"})).alerts
    # an empty previous pool (no player map), or one older than a day, compares nothing
    assert not watch([stash], previous=prev()).alerts
    stale = prev(pool={"zz"}, generated=NOW - N.PREVIOUS_MAX_GAP - timedelta(hours=1))
    assert not watch([stash], previous=stale).alerts


def test_an_activation_into_the_pool_is_new_and_alerted_for_48_hours():
    stash = P("a", "RB", gid="00-a")
    w = watch([stash], previous=prev(pool={"zz"}))
    assert w.alerts and w.notable[0].new and w.notable[0].first_seen == NOW
    rec = {"radar": {"pool": [{"id": "a"}]}, "watch": w.record(), "generated": NOW.isoformat()}
    later = NOW + timedelta(hours=N.ALERT_HOURS - 1)
    w2 = watch([stash], previous=N.PreviousWatch.from_record(rec), now=later)
    assert w2.alerts and not w2.notable[0].new and w2.notable[0].first_seen == NOW
    # the time carries through a build that does not list him (prior blank, say)
    gap = watch([stash], previous=N.PreviousWatch.from_record(rec), prior={},
                now=NOW + timedelta(hours=1))
    assert gap.notable == () and gap.pool_since == {"a": NOW}
    rec2 = {"radar": {"pool": [{"id": "a"}]}, "watch": gap.record(),
            "generated": (NOW + timedelta(hours=1)).isoformat()}
    back = watch([stash], previous=N.PreviousWatch.from_record(rec2),
                 now=NOW + timedelta(hours=2))
    assert back.alerts and back.notable[0].first_seen == NOW
    gone = watch([stash], now=NOW + timedelta(hours=N.ALERT_HOURS + 1),
                 previous=N.PreviousWatch.from_record(
                     {"radar": {"pool": [{"id": "a"}]}, "watch": w2.record(),
                      "generated": later.isoformat()}))
    assert gone.notable and not gone.alerts, "after the window he stays listed, off the top"


def test_an_entry_while_the_drops_are_unknown_is_not_alerted():
    stash = P("a", "RB", gid="00-a")
    w = watch([stash], previous=prev(pool={"zz"}), drops_note="drops UNKNOWN")
    assert w.notable[0].new and not w.alerts, "it could be the owner's own drop"


def test_a_league_drop_alerts_on_its_clock_and_the_owners_own_drop_never_does():
    d = N.Drop("y", 2, NOW - timedelta(hours=60), "free_agent")     # clear 12 h ago
    kick = NOW + timedelta(hours=5)
    y = replace(P("y", "WR", 9.5, gid="00-y"), kickoff=kick)
    w = watch([y], drops={"y": d}, previous=prev(pool={"y"}))
    assert w.alerts and w.notable[0].clock.phase == "FREE_AGENT"
    mine = N.Drop("y", 3, NOW - timedelta(hours=1), "free_agent")
    w = watch([y], drops={"y": mine}, previous=prev(pool={"zz"}), mine=3)
    assert w.notable and "dropped by YOU (roster #3)" in w.notable[0].why[0]
    assert not w.alerts


def test_the_lock_before_the_run_is_alerted_so_the_bidding_window_is_on_screen():
    d = N.Drop("j", 5, NOW - timedelta(days=4), "free_agent")     # clear: 2 days ago
    kick = NOW - timedelta(hours=3)                               # locked 3 h ago
    j = replace(P("j", "RB", gid="00-a"), kickoff=kick)
    w = watch([j], drops={"j": d}, previous=prev(pool={"j"}))
    assert w.notable[0].clock.phase == "LOCKED" and w.alerts


def test_malformed_optional_inputs_never_raise():
    old_schema = prior_frame().rename(columns={"team": "recent_team"})
    assert N.prior_lines(old_schema)["00-a"].team == "AAA"
    assert N.prior_lines(prior_frame().drop(columns=["position"])) == {}
    drops, note = N.league_drops({"rows": [{"status": "complete", "drops": ["501"],
                                            "created": 1}, "junk"], "error": ""})
    assert drops == {} and note == ""
    assert N.PreviousWatch.from_record({"radar": "x", "watch": {"pool_since": {"a": "bad"}},
                                        "generated": "nope"}) == N.PreviousWatch()
