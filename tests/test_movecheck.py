"""Move pre-flight (gridiron.movecheck, scripts/weekly/move_check.py) on an
invented league: no network. Pins the 2026 week-9 misses as cases:

  1. a drop who just scored big, and a returning player whose rest-of-season
     rank came from games he missed, are flagged "look before dropping";
  2. the owner's own player tagged Out DURING the week shows from Sleeper's
     week-stats row, not from a stale injury report; Questionable is not out;
  3. a bye next week (and the week after) is flagged on adds and the roster,
     only from a schedule whose week is present;
  4. a receiver's quarterback change or weak QB is a fact on his card;
  5. an add that is not in the available pool says so; a drop not on the
     roster says so; a name resolves through the record only, never guessed;
  6. the plan check counts adds against drops and next week's fillable slots;
  7. the leaders list is the available pool only;
  8. the CLI renders from a saved week-stats file and a fake nflverse.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from gridiron import movecheck as M

ROOT = Path(__file__).resolve().parents[1]


def usage(trend="STEADY", games=4, why=""):
    return {"trend": trend, "trend_why": why,
            "weeks": [{"week": w} for w in range(9 - games, 9)]}


RECORD = {
    "generated": "2026-11-09T06:00:00+00:00", "season": 2026, "week": 9,
    "slots": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DST"],
    "roster": [
        {"sleeper_id": "qb1", "name": "Mine Passer", "position": "QB", "team": "AAA"},
        {"sleeper_id": "rb1", "name": "Mine Hurt", "position": "RB", "team": "AAA",
         "availability": "no designation on the wk9 report"},
        {"sleeper_id": "rb2", "name": "Mine Byeback", "position": "RB", "team": "BYE"},
        {"sleeper_id": "rb3", "name": "Mine Steady", "position": "RB", "team": "AAA"},
        {"sleeper_id": "wr1", "name": "Mine Returner", "position": "WR", "team": "AAA"},
        {"sleeper_id": "wr2", "name": "Mine Quiet", "position": "WR", "team": "AAA"},
        {"sleeper_id": "wr3", "name": "Mine Third", "position": "WR", "team": "AAA"},
        {"sleeper_id": "te1", "name": "Mine Tight", "position": "TE", "team": "AAA"},
        {"sleeper_id": "k1", "name": "Mine Kicker", "position": "K", "team": "AAA"},
        {"sleeper_id": "AAA", "name": "AAA DST", "position": "DEF", "team": "AAA"},
    ],
    "radar": {"pool": [
        {"id": "fa1", "name": "Pool Byeweek", "position": "WR", "team": "BYE"},
        {"id": "fa2", "name": "Pool Back", "position": "RB", "team": "AAA"},
        {"id": "fa3", "name": "Pool Quiet", "position": "RB", "team": "AAA"},
    ]},
    "ros": {"players": [
        {"sleeper_id": "wr1", "name": "Mine Returner", "position": "WR", "team": "AAA",
         "held_by": "mine", "pos_rank": 124},
        {"sleeper_id": "wr2", "name": "Mine Quiet", "position": "WR", "team": "AAA",
         "held_by": "mine", "pos_rank": 60},
        {"sleeper_id": "wr3", "name": "Mine Third", "position": "WR", "team": "AAA",
         "held_by": "mine", "pos_rank": 21},
        {"sleeper_id": "x1", "name": "Other Star", "position": "WR", "team": "AAA",
         "held_by": "rostered", "pos_rank": 5},
    ]},
    "usage": {"players": {"wr1": usage(games=1), "rb3": usage("FALLING", why="opps 20 -> 8"),
                          "qb1": usage(), "wr2": usage()}},
}


def row(sid, pos, team, pts=None, tag=None, **st):
    stats = {"pts_half_ppr": pts, **st} if pts is not None else {}
    return {"player_id": sid, "team": team, "stats": stats,
            "player": {"position": pos, "team": team, "injury_status": tag,
                       "news_updated": 1794000000000}}


ROWS = [row("wr1", "WR", "AAA", 18.5, rec_tgt=9, rec=4, rec_yd=105, rec_td=1),
        row("qb1", "QB", "AAA", 12.2, pass_att=30, pass_yd=228),
        row("rb1", "RB", "AAA", 13.3, "Out", rush_att=6, rush_yd=32),
        row("rb3", "RB", "AAA", 4.0, "Questionable", rush_att=5),
        row("wr2", "WR", "AAA", 3.0, rec_tgt=2),
        row("fa2", "RB", "AAA", 11.5, rush_att=8, rush_yd=55, rush_td=1),
        row("fa3", "RB", "AAA", 2.0, rush_att=2),
        row("x1", "WR", "AAA", 30.0, rec_tgt=12)]

SCHEDULE = pd.DataFrame([
    {"game_type": "REG", "week": 10, "home_team": "AAA", "away_team": "CCC"},
    {"game_type": "REG", "week": 10, "home_team": "DDD", "away_team": "EEE"},
    {"game_type": "REG", "week": 9, "home_team": "BYE", "away_team": "DDD"},
])
QB_STATS = pd.DataFrame([
    {"position": "QB", "team": "AAA", "week": w, "attempts": 30,
     "player_display_name": name, "passing_epa": epa}
    for w, name, epa in ((7, "Old Starter", 5.0), (8, "New Starter", -8.0),
                         (9, "New Starter", -12.0))])


def env():
    return {"live": M.live_rows(ROWS), "byes": M.bye_weeks(SCHEDULE, [10, 11]),
            "qbs": M.team_qbs(QB_STATS, through_week=9)}


IX = M.Index.from_record(RECORD)


def test_a_hot_returning_drop_is_flagged_look_before_dropping():
    c = M.drop_check(M.card("wr1", IX, **env()))
    text = "; ".join(c.flags)
    assert "scored 18.5" in text and "look before dropping" in text
    assert "returning player" in text and "WR124" in text
    quiet = M.drop_check(M.card("wr2", IX, **env()))
    assert not any("look before" in f for f in quiet.flags)
    assert any("not on your roster" in f for f in M.drop_check(M.card("fa2", IX, **env())).flags)
    # a quiet week does not hide a starter's rest-of-season rank (WR21 was dropped once)
    assert not any("look before" in f for f in M.drop_check(M.card("qb1", IX, **env())).flags), \
        "12 points is a quiet week for a quarterback"
    third = M.drop_check(M.card("wr3", IX, **env()))
    assert any("WR21 is a starter's" in f for f in third.flags)


def test_an_in_week_out_tag_comes_from_the_live_row_and_questionable_is_not_out():
    hurt = M.card("rb1", IX, **env())
    assert hurt.tag == "Out" and "Sleeper" in hurt.tag_source
    assert any("will not play" in f for f in hurt.flags)
    q = M.card("rb3", IX, **env())
    assert any("not out, rule #11" in f for f in q.flags)
    assert any("FALLING" in f for f in q.flags)
    no_row = M.card("wr3", IX, **env())
    assert no_row.tag == "" and no_row.tag_source.startswith("record")


def test_byes_come_only_from_weeks_the_schedule_has():
    byes = M.bye_weeks(SCHEDULE, [10, 11])
    assert set(byes) == {10}, "week 11 has no rows: unknown, never 'no byes'"
    assert "BYE" in byes[10] and "AAA" not in byes[10]
    add = M.add_check(M.card("fa1", IX, **env()))
    assert "BYE in week 10" in add.flags


def test_a_receivers_quarterback_change_is_on_his_card():
    qbs = M.team_qbs(QB_STATS, through_week=9)
    assert qbs["AAA"].changed and qbs["AAA"].epa < 0
    c = M.card("wr2", IX, **env())
    assert any("QB changed" in f and "Old Starter → New Starter" in f for f in c.flags)
    assert M.card("rb3", IX, **env()).qb is None, "QB context is for receivers"


def test_an_add_that_is_not_available_says_so():
    assert any("NOT AVAILABLE: rostered" in f
               for f in M.add_check(M.card("x1", IX, **env())).flags)
    assert not any("NOT AVAILABLE" in f for f in M.add_check(M.card("fa2", IX, **env())).flags)


def test_the_plan_counts_spots_and_next_weeks_fillable_slots():
    e = env()
    roster = M.roster_cards(IX, **e)
    adds = [M.card("fa2", IX, **e)]
    drops = [M.card("wr3", IX, **e), M.card("k1", IX, **e)]
    plan = M.plan_check(roster, adds, drops, slots=RECORD["slots"], week=10)
    assert any("1 add(s) but 2 drop(s)" in p for p in plan)
    assert any("0 K able to play" in p for p in plan)
    # rb1 Out, rb2 on bye: two RBs left (rb3 Questionable plays, fa2 added)
    ok = M.plan_check(roster, adds, [M.card("wr3", IX, **e)], slots=RECORD["slots"], week=10)
    assert not any("RB able to play" in p for p in ok)
    assert any("0 RB/WR/TE left for 1 FLEX" in p for p in ok)
    short = M.plan_check(roster, [], [M.card("rb3", IX, **e), M.card("wr3", IX, **e)],
                         slots=RECORD["slots"], week=10)
    assert any("0 RB able to play for 2 RB" in p for p in short)


def test_leaders_are_the_available_pool_only():
    board = M.leaders(IX, M.live_rows(ROWS), top=5)
    assert [r[1] for r in board["RB"]] == ["Pool Back", "Pool Quiet"]
    assert board["WR"] == [], "a rostered player's 30 points is not a free agent"


def _cli():
    spec = importlib.util.spec_from_file_location("move_check",
                                                  ROOT / "scripts/weekly/move_check.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["move_check"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_the_cli_renders_offline_and_never_guesses_a_name(tmp_path, monkeypatch, capsys):
    rec = tmp_path / "record.json"
    rec.write_text(json.dumps(RECORD))
    live = tmp_path / "live.json"
    live.write_text(json.dumps(ROWS))
    fake = SimpleNamespace(
        load_schedules=lambda s: SimpleNamespace(to_pandas=lambda: SCHEDULE),
        load_player_stats=lambda s: SimpleNamespace(to_pandas=lambda: QB_STATS))
    monkeypatch.setitem(sys.modules, "nflreadpy", fake)
    mod = _cli()
    out = tmp_path / "moves.md"
    code = mod.main(["--record", str(rec), "--live-json", str(live),
                     "--add", "Pool Back,RB", "--add", "Nobody Atall,WR",
                     "--drop", "Mine Returner,WR", "--drop", "AAA,DEF",
                     "--leaders", "3", "--out", str(out)])
    text = capsys.readouterr().out
    assert code == 0 and out.read_text().strip() == text.strip()
    assert "UNRESOLVED 'Nobody Atall,WR'" in text
    assert "look before dropping" in text and "Out per Sleeper" in text
    assert "## Available pool" in text and "nothing here is submitted" in text
    assert "| AAA DST (DEF, AAA) | mine |" in text
