"""The page serves the stack where Sleeper's pre-kickoff number is in
(docs/research/STACK_SERVING.md). Synthetic players only."""
from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import dashboard as D
from gridiron.lineup import Player
from gridiron.models import advanced as A
from gridiron.models import validated_signals as V
from gridiron.projection import Projection

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
NOW = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
KICK = datetime(2026, 10, 18, 17, 0, tzinfo=UTC)


def _shadow(fetched: datetime, **points):
    return {"fetched_at": fetched.isoformat(), "fetched_at_dt": fetched,
            "players": {sid: {"points": v} for sid, v in points.items()}}


def test_shadow_number_applies_the_graders_pre_kickoff_rule_and_an_age_limit():
    fresh = _shadow(NOW - timedelta(hours=1), **{"11": 9.5, "12": None})
    assert D.shadow_number(fresh, "11", KICK, NOW) == 9.5
    assert D.shadow_number(fresh, "12", KICK, NOW) is None          # no number
    assert D.shadow_number(fresh, "99", KICK, NOW) is None          # not covered
    assert D.shadow_number(fresh, "11", None, NOW) is None          # kickoff untimed
    assert D.shadow_number(None, "11", KICK, NOW) is None
    # fetched after the player's kickoff: the grader would not score it, the page must not serve it
    late = _shadow(KICK + timedelta(minutes=5), **{"11": 9.5})
    assert D.shadow_number(late, "11", KICK, KICK + timedelta(minutes=10)) is None
    # fetched too long ago: Sleeper has moved on, the page keeps advanced_v1
    stale = _shadow(NOW - timedelta(hours=A.STACK_SHADOW_MAX_AGE_HOURS + 1), **{"11": 9.5})
    assert D.shadow_number(stale, "11", KICK, NOW) is None
    assert D.shadow_number(_shadow(NOW - timedelta(hours=A.STACK_SHADOW_MAX_AGE_HOURS - 1),
                                   **{"11": 9.5}), "11", KICK, NOW) == 9.5


def _player(sid, pos, mean, inputs, gid=None):
    proj = Projection(mean, 4.0, (), (), inputs, D.ADVANCED_LABEL)
    return Player(sid, f"Player {sid}", pos, "AAA", proj, "START", False, "", "", (),
                  gid or f"g{sid}", True, KICK)


def test_the_record_and_contenders_carry_the_advanced_mean_beside_the_stacked_number():
    stacked = _player("1", "WR", 10.4, {"baseline_mean": 8.0, "advanced": A.NAME, "adv_mean": 9.0,
                                        "sleeper_mean": 11.0, "system": A.STACK_NAME})
    adv_only = _player("2", "RB", 7.0, {"baseline_mean": 6.0, "advanced": A.NAME, "adv_mean": 7.0,
                                        "system": A.NAME})
    kicker = _player("3", "K", 8.0, {"baseline_mean": 8.5, "advanced": A.NAME, "adv_mean": 8.0,
                                     "system": A.NAME})
    plain = _player("4", "TE", 5.0, {})
    cont = D.contenders_block(None, {"players": {}}, (stacked, adv_only, kicker, plain))
    assert cont["baseline_v1"] == {"1": 8.0, "2": 6.0, "3": 8.5, "4": 5.0}
    assert cont["adv_v1"] == {"1": 9.0, "2": 7.0}             # skill positions only; the TE never ran it
    assert cont["stack"] == {}                                # no model given, nothing stacked here
    # the explain string shows all three steps for a stacked player, two for the rest
    assert "8.00 baseline -> 9.00 with advanced stats -> 10.40 stacked with Sleeper's 11.00" \
        in stacked.projection.explain()
    assert "6.00 baseline -> 7.00 with advanced stats" in adv_only.projection.explain()
    assert "stacked" not in adv_only.projection.explain()
    # the status clause counts the served and says why the others were not
    note = D.stack_serving_note((stacked, adv_only, kicker, plain),
                                _shadow(NOW - timedelta(hours=2)), NOW)
    assert note.startswith(f"; {A.STACK_NAME} served for 1 of 2 advanced-projected skill players")
    assert "fetched 2.0 h ago" in note
    assert "over the" in D.stack_serving_note((adv_only,),
                                              _shadow(NOW - timedelta(hours=48)), NOW)
    assert "no shadow projections" in D.stack_serving_note((adv_only,), None, NOW)
    assert D.stack_serving_note((plain,), None, NOW) == ""


def test_the_shootout_grades_advanced_alone_when_the_record_carries_it():
    spec = importlib.util.spec_from_file_location("gw_stack", ROOT / "scripts/weekly/grade_week.py")
    gw = importlib.util.module_from_spec(spec)
    sys.modules["gw_stack"] = gw
    spec.loader.exec_module(gw)

    def p(sid, proj):
        return {"sleeper_id": sid, "id": sid, "gsis_id": f"g{sid}", "position": "WR",
                "projected": proj, "withheld": False}
    archive = {"roster": [p("1", 12.0), p("2", 8.0)],
               "shadow": {"players": {"1": {"points": 9.0, "pre_kickoff": True},
                                      "2": {"points": 11.0, "pre_kickoff": True}}},
               "contenders": {"baseline_v1": {"1": 10.0, "2": 11.0},
                              "adv_v1": {"1": 11.5, "2": 8.5}, "stack": {"1": 12.0, "2": 8.0}}}
    out = gw.shootout(archive, {"g1": 5.0, "g2": 15.0})
    assert out["systems"] == ["page", "baseline_v1", "adv_v1", "sleeper", "blend", "stack"]
    assert out["adv_v1_pairwise"] == 0.0 and out["baseline_v1_pairwise"] == 1.0
    assert "adv_v1_pairwise" in gw.FIELDS and "adv_v1_mae" in gw.FIELDS
    # an older record without adv_v1 grades exactly as before
    del archive["contenders"]["adv_v1"]
    assert gw.shootout(archive, {"g1": 5.0, "g2": 15.0})["systems"] == \
        ["page", "baseline_v1", "sleeper", "blend", "stack"]


def test_stack_serving_is_registered_under_rule_5_with_its_evidence():
    assert "stack_v1" in V.FEATS and "stack_v1" in V.VALIDATED
    ev = V.VALIDATED["stack_v1"]
    assert (ROOT / ev["evidence"]).is_file() and (ROOT / ev["script"]).is_file()
    assert ev["pairwise_delta"] > 0 and ev["mae_delta"] < 0
    model = A.AdvancedModel.load()
    se = model.meta["serving_evidence"]
    assert se["bars"]["stack_vs_adv"]["both_every_fold"] is True
    # the number the page SERVES is the mean-scale form; it clears the bar on its own
    assert se["bars"]["stack_mean_vs_adv"]["both_every_fold"] is True
    assert model.meta["evidence"]["stack_mean_mae"] < model.meta["evidence"]["adv_mae"]
    fa = se["feed_absence"]
    assert fa["min_per_position"] == A.FEED_MIN_PER_POSITION
    assert fa["live_2026_weeks_4_5"]["did_not_play"] / fa["live_2026_weeks_4_5"]["missing_projected_5_plus"] > 0.9
    assert (ROOT / fa["script"]).is_file()
    assert all(d > 0 for pos in ("QB", "RB", "WR", "TE")
               for d in se["by_position_stack_minus_adv"][pos])
    e = model.meta["evidence"]
    assert e["stack_pairwise"] > e["adv_pairwise"] > e["baseline_pairwise"]
    assert e["stack_mae"] < e["adv_mae"] < e["baseline_mae"]


def test_the_served_stack_is_the_composed_ridge_pushed_back_through_its_median_line():
    model = A.AdvancedModel.load()
    pos = "WR"
    cols = model.stack[pos].cols
    a, b = model.meta["stack_form"]["calibration"][pos]
    feats = {c: model.stack[pos].mu[i] for i, c in enumerate(cols) if c not in ("adv", "sleeper",
                                                                               "baseline", "ppg_to_date")}
    ctx = A.AdvancedContext(model, {"g1": dict(feats), "g2": {**feats, "xfp_l3": feats["xfp_l3"] + 4}},
                            6, "test")
    rows = [("g1", 8.0, 9.0), ("g2", 8.0, 9.0), ("g1", 12.0, 14.0)]
    for gid, base, slp in rows:
        mean = ctx.stacked_mean(gid, pos, base, base, slp)
        med = ctx.stacked(gid, pos, base, base, slp)
        full = {**ctx.features[gid], "baseline": base, "sleeper": slp, "ppg_to_date": base}
        full["adv"] = model.predict(pos, full)
        raw = model.stack[pos].predict_row(full)
        assert abs(med - max(0.0, raw)) < 1e-3                  # the shipped (median) stack
        assert abs(mean - max(0.0, (raw - a) / b)) < 1e-3       # the served (mean) stack
        assert mean > med                                       # a median sits below the mean
    # monotone within a position: the two scales order players identically
    m = [ctx.stacked_mean(g, pos, 8.0, 8.0, 9.0) for g in ("g1", "g2")]
    c = [ctx.stacked(g, pos, 8.0, 8.0, 9.0) for g in ("g1", "g2")]
    assert (m[0] < m[1]) == (c[0] < c[1])
    assert ctx.stacked_mean("g1", "K", 8.0, 8.0, 9.0) is None    # kickers keep advanced_v1
    assert ctx.stacked_mean("g1", pos, 8.0, 8.0, None) is None


def _feed(fetched, n_per_pos=25, extra=()):
    players = {f"{pos}{i}": {"points": 5.0, "position": pos}
               for pos in ("QB", "RB", "WR", "TE") for i in range(n_per_pos)}
    players.update({sid: {"points": 7.0, "position": pos} for sid, pos in extra})
    return {"fetched_at_dt": fetched, "players": players}


def test_a_complete_fresh_feed_that_omits_a_skill_player_abstains_him_without_a_number():
    feed = _feed(NOW - timedelta(hours=1), extra=[("11", "WR")])
    why = D.feed_absence(feed, "99", "WR", KICK, NOW)
    assert why and D.FEED_ABSENT in why and "verify his status" in why
    assert D.feed_absence(feed, "11", "WR", KICK, NOW) is None              # listed
    assert D.feed_absence(feed, "99", "K", KICK, NOW) is None               # kickers: not judged
    assert D.feed_absence(feed, "99", "DEF", KICK, NOW) is None
    assert D.feed_absence(feed, "99", "WR", None, NOW) is None              # untimed kickoff
    assert D.feed_absence(None, "99", "WR", KICK, NOW) is None
    # a thin position is a failed fetch, not news: nobody there is judged by it
    thin = _feed(NOW - timedelta(hours=1), n_per_pos=A.FEED_MIN_PER_POSITION - 1)
    assert D.feed_absence(thin, "99", "WR", KICK, NOW) is None
    # stale, or fetched after his kickoff: the feed says nothing about this game
    stale = _feed(NOW - timedelta(hours=A.STACK_SHADOW_MAX_AGE_HOURS + 1))
    assert D.feed_absence(stale, "99", "WR", KICK, NOW) is None
    late = _feed(KICK + timedelta(minutes=1))
    assert D.feed_absence(late, "99", "WR", KICK, KICK + timedelta(minutes=2)) is None
    # the status clause counts them
    ab = Player("99", "Player 99", "WR", "AAA", __import__("gridiron.projection", fromlist=["x"])
                .abstain(why), "BENCH", False, "", "", (), "g99", True, KICK)
    st = _player("1", "WR", 10.4, {"baseline_mean": 8.0, "advanced": A.NAME, "adv_mean": 9.0,
                                   "sleeper_mean": 11.0, "system": A.STACK_NAME})
    note = D.stack_serving_note((st, ab), feed, NOW)
    assert note.endswith(f"1 skill player(s) not projected: {D.FEED_ABSENT}")
