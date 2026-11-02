"""Weekly streaming plans (`gridiron.streaming`; roadmap TE-09, K-13, DEF-13)
and their section in scripts/weekly/ros_rankings.py. Synthetic data only:
fictional names, ids and teams (the DEF rows use the ROS table's own
`DEF:<team>` keys). Pins: a proven bye is a bye and an unknown week never
is; an OUT week gives nothing; a defense below zero is shown, never called
out; a known game covers a week before an unknown one; a free agent can
matter only in my bye week; hold vs stream adds up and counts a move each
time the name changes; an empty pool, an empty position and an unresolved
row are said, not hidden; team defenses keep the table's ids; a free
agent's flags stay with him; every number is the ROS table's own; and the
wording keeps the plan a plan."""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pandas as pd
import pytest

from gridiron import ros as R
from gridiron import streaming as S
from gridiron.ids import Crosswalk

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _line(games: dict[int, tuple[str, float]]) -> str:
    """A ROS line in the table's own spelling (`gridiron.ros`)."""
    return " ".join(f"{w}:{o}:{p:g}" for w, (o, p) in sorted(games.items()))


def _row(gid, pos, held, games, *, team="AAA", byes="", name=None, rank=1, **extra):
    return {"gsis_id": gid, "position": pos, "team": team, "held_by": held,
            "name": name or f"Fixture {gid}", "pos_rank": rank, "weeks": _line(games),
            "byes": byes, "injury": "", "depth_rank": None, "reserve_since": None,
            "sleeper_id": "", **extra}


def _md(plan) -> str:
    return "\n".join(S.markdown(plan))


def _ids(options) -> list[str]:
    return [o.line.gsis_id for o in options]


def test_a_proven_bye_is_a_bye_and_an_unknown_week_never_is():
    table = [
        _row("te1", "TE", "MINE", {9: ("BBB", 9.0), 10: ("CCC", 9.0), 12: ("DDD", 9.0)},
             byes="11"),
        _row("k1", "K", "MINE", {9: ("DDD", 8.0), 10: ("?", 8.0), 11: ("AAA", 8.0),
                                 12: ("BBB", 8.0)}, team="CCC")]
    plan = S.build(table)
    assert plan.weeks == (9, 10, 11)                 # the table's first week and the next two
    te, k, defense = plan.positions
    by = {wp.week: wp for wp in te.weeks}
    assert by[11].status == S.BYE and by[11].need and by[11].mine_points == 0.0
    assert by[9].status == S.GAME and not by[9].need and by[9].best.opponent == "BBB"
    kby = {wp.week: wp for wp in k.weeks}
    assert kby[10].status == S.UNKNOWN and kby[10].need and kby[10].best.opponent == "?"
    assert kby[10].mine_points == 8.0                # counted at the plain rate, as ROS does
    assert k.rostered[0].byes == frozenset()         # ...and never a bye
    assert (te.needs, k.needs) == ((11,), (10,))
    assert (te.totals.mine, k.totals.mine) == (18.0, 24.0)
    md = _md(plan)
    assert "| 11 **NEED** | Fixture te1 (AAA) BYE |" in md
    assert "| 10 **NEED** | Fixture k1 (CCC) 8.0 v ? (UNKNOWN) |" in md
    assert defense.rows == 0 and "No DEF in this table: nothing to plan." in md


def _schedule(last=17):
    """A toy league in season 2020's shape (16 games in 17 weeks): AAA and
    CCC sit out week 6, and their whole seasons are present, so the bye is
    PROVEN (`gridiron.lineup.proven_byes`); cut the frame short and it is not."""
    rows = []
    for w in range(1, last + 1):
        pairs = [("AAA", "BBB"), ("CCC", "DDD")] if w % 2 else [("AAA", "CCC"), ("BBB", "DDD")]
        for home, away in pairs:
            if w == 6 and "CCC" in (home, away):
                continue
            played = w < 4
            rows.append({"season": 2020, "week": w, "game_type": "REG",
                         "home_team": home, "away_team": away,
                         "home_score": 21.0 if played else None,
                         "away_score": 17.0 if played else None, "roof": "outdoors"})
    return pd.DataFrame(rows)


def _box():
    rows = []
    for w in (1, 2, 3, 4):
        for gid, team, opp, pos, pts in (("te_a", "AAA", "BBB", "TE", 9.0),
                                         ("te_b", "BBB", "AAA", "TE", 7.0),
                                         ("k_c", "CCC", "DDD", "K", 8.0)):
            rows.append({"gsis_id": gid, "week": w, "team": team, "opponent_team": opp,
                         "position": pos, "league_points": pts,
                         "targets": 6.0 if pos == "TE" else 0.0, "carries": 0.0,
                         "receiving_points": pts if pos == "TE" else 0.0,
                         "rushing_points": 0.0, "passing_points": 0.0,
                         "offense_pct": 0.8, "target_share": 0.15, "air_yards_share": 0.1})
    return pd.DataFrame(rows)


def _ros_table(schedule):
    table, _ = R.build_table(weeks=_box(), schedule=schedule, injuries=None, inputs=None,
                             week=5, weights={"choice": {"TE": "ppg", "K": "ppg"}})
    held = {"te_a": "MINE", "te_b": "FA", "k_c": "MINE"}
    return table.assign(held_by=[held.get(g, "rostered") for g in table["gsis_id"]],
                        name=[f"Fixture {g}" for g in table["gsis_id"]],
                        injury="", sleeper_id="")


def test_the_plan_reads_the_ros_tables_own_lines_and_changes_no_number():
    table = _ros_table(_schedule())
    plan = S.build(table)
    assert plan.weeks == (5, 6, 7)
    te = plan.positions[0]
    by = {wp.week: wp for wp in te.weeks}
    assert by[6].status == S.BYE and by[6].need          # AAA's bye, proven by the count
    assert _ids(by[6].free_agents) == ["te_b"] and by[6].free_agents[0].opponent == "DDD"
    # the free agent beats mine only in my bye week
    assert [wp.week for wp in te.weeks if wp.beats_mine] == [6]
    # every number is the table's own: nothing re-projected
    rows = table.set_index("gsis_id")
    for gid in ("te_a", "te_b"):
        line = {w: p for w, _, p in S.parse_weeks(rows.loc[gid, "weeks"])}
        for wp in te.weeks:
            for o in wp.mine + wp.free_agents:
                if o.line.gsis_id == gid and o.status != S.BYE:
                    assert o.points == line[o.week]
    b = {w: p for w, _, p in S.parse_weeks(rows.loc["te_b", "weeks"])}
    t = te.totals
    assert t.hold.gsis_id == "te_b" and t.hold_total == pytest.approx(b[5] + b[6] + b[7])
    assert t.cover == pytest.approx(t.mine + b[6]) and t.cover_adds == 1
    k = plan.positions[1]
    assert k.weeks[1].status == S.BYE and k.pool == 0       # CCC's kicker, the same proof
    # cut the frame short: the count proves nothing, so week 6 is UNKNOWN, not
    # a bye, and a week the frame does not reach is UNKNOWN too
    cut = S.build(_ros_table(_schedule(last=8)), weeks=(6, 9)).positions[0]
    assert [wp.status for wp in cut.weeks] == [S.UNKNOWN, S.UNKNOWN]
    assert all(wp.need and wp.best.opponent == "?" and wp.mine_points > 0 for wp in cut.weeks)


def test_an_out_week_gives_nothing_and_lists_no_free_agent_for_it():
    table = [
        _row("te_out", "TE", "MINE", {9: ("BBB", 0.0), 10: ("CCC", 9.5), 11: ("DDD", 9.5)},
             injury="OUT wk9"),
        _row("fa_out", "TE", "FA", {9: ("CCC", 0.0), 10: ("DDD", 6.0), 11: ("BBB", 6.0)},
             team="DDD", rank=2),
        _row("fa_ok", "TE", "FA", {9: ("AAA", 5.0), 10: ("BBB", 5.0), 11: ("CCC", 5.0)},
             team="EEE", rank=3)]
    te = S.build(table, positions=("TE",)).positions[0]
    w9, w10 = te.weeks[0], te.weeks[1]
    assert w9.status == S.OUT and w9.need and w9.best.opponent == "BBB"
    assert w9.mine_points == 0.0
    assert _ids(w9.free_agents) == ["fa_ok"]             # his own OUT week is no option
    assert w9.margin(w9.free_agents[0]) == 5.0           # anyone beats a week of nothing
    assert w10.status == S.GAME and _ids(w10.free_agents) == ["fa_out", "fa_ok"]
    assert te.totals.mine == 19.0
    assert te.rostered[0].flags == ("OUT WK9",)
    assert "| 9 **NEED** | Fixture te_out (AAA; OUT WK9) OUT (v BBB) |" in _md(
        S.build(table, positions=("TE",)))


def test_a_defense_below_zero_is_shown_as_is_and_never_called_out():
    """A team defense's line is not clipped: a week under zero is his own
    number, shown as the table has it; it gives nothing, so it is a NEED."""
    table = [
        _row("DEF:AAA", "DEF", "MINE", {9: ("BBB", 6.0), 10: ("CCC", -1.5), 11: ("DDD", 0.0)},
             team="AAA", name="AAA DST"),
        _row("DEF:EEE", "DEF", "FA", {9: ("FFF", 5.0), 10: ("GGG", 4.0), 11: ("HHH", 3.0)},
             team="EEE", name="EEE DST", rank=2)]
    d = S.build(table, positions=("DEF",)).positions[0]
    w9, w10, w11 = d.weeks
    assert (w9.status, w10.status, w11.status) == (S.GAME, S.BELOW, S.OUT)
    assert d.needs == (10, 11) and w10.best.points == -1.5 and w10.mine_points == 0.0
    assert w10.margin(w10.free_agents[0]) == 4.0
    t = d.totals
    assert (t.mine, t.cover, t.cover_adds) == (6.0, 13.0, 1)
    md = _md(S.build(table, positions=("DEF",)))
    assert "| 10 **NEED** | AAA DST -1.5 v CCC (BELOW 0) | **EEE DST 4.0 v GGG** (+4.0) |" in md
    assert "| 11 **NEED** | AAA DST OUT (v DDD) |" in md
    assert "mine 6.0 (wk 10 BELOW 0; wk 11 OUT)" in md


def test_a_known_game_is_my_best_before_an_unknown_week():
    """A `?` week may yet be his bye: a known game covers the week first,
    and a week no line covers shows no number."""
    table = [
        _row("te_k", "TE", "MINE", {9: ("BBB", 6.0), 10: ("CCC", 6.0), 11: ("DDD", 6.0)},
             rank=2),
        _row("te_q", "TE", "MINE", {9: ("?", 9.0), 10: ("DDD", 9.0), 11: ("BBB", 9.0)},
             team="EEE", rank=1),
        _row("te_n", "TE", "MINE", {10: ("AAA", 4.0), 11: ("AAA", 4.0)}, team="FFF", rank=3)]
    te = S.build(table, positions=("TE",)).positions[0]
    w9, w10 = te.weeks[0], te.weeks[1]
    assert _ids(w9.mine) == ["te_k", "te_q", "te_n"] and not w9.need
    assert w9.mine_points == 6.0 and _ids(w10.mine)[0] == "te_q"     # known, more points
    assert te.needs == () and te.totals.mine == 24.0
    md = _md(S.build(table, positions=("TE",)))
    assert ("| 9 | Fixture te_k (AAA) 6.0 v BBB; Fixture te_q (EEE) 9.0 v ? (UNKNOWN); "
            "Fixture te_n (FFF) UNKNOWN (no number that week) |") in md
    # alone, the unknown week is my best and a NEED, counted at his plain rate
    alone = S.build(table[1:2], positions=("TE",)).positions[0]
    assert alone.needs == (9,) and alone.weeks[0].mine_points == 9.0


def test_a_free_agent_can_beat_mine_only_in_my_bye_week():
    table = [
        _row("te_m", "TE", "MINE", {9: ("BBB", 9.0), 10: ("CCC", 9.0)}, byes="11",
             name="Mine Tight"),
        _row("fa_a", "TE", "FA", {9: ("AAA", 7.0), 10: ("DDD", 7.0), 11: ("CCC", 7.0)},
             team="BBB", rank=2, name="Free Alpha"),
        _row("fa_b", "TE", "FA", {9: ("CCC", 5.0), 10: ("BBB", 8.0), 11: ("AAA", 6.0)},
             team="DDD", rank=3, name="Free Bravo")]
    plan = S.build(table, positions=("TE",))
    te = plan.positions[0]
    assert {wp.week: _ids(wp.beats_mine) for wp in te.weeks} == \
        {9: [], 10: [], 11: ["fa_a", "fa_b"]}
    assert te.needs == (11,)
    t = te.totals
    assert (t.mine, t.hold.gsis_id, t.hold_total) == (18.0, "fa_a", 21.0)
    assert [p.gsis_id for p in t.stream_picks] == ["fa_a", "fa_b", "fa_a"]
    assert (t.stream, t.stream_adds) == (22.0, 3)
    assert [p and p.gsis_id for p in t.cover_picks] == [None, None, "fa_a"]
    assert (t.cover, t.cover_adds) == (25.0, 1)
    md = _md(plan)
    assert "| 11 **NEED** | Mine Tight (AAA) BYE | **Free Alpha (BBB) 7.0 v CCC** (+7.0) |" in md
    assert "| 9 | Mine Tight (AAA) 9.0 v BBB | Free Alpha (BBB) 7.0 v AAA |" in md   # not bold
    assert ("Hold vs stream, weeks 9-11 (points): mine 18.0 (wk 11 BYE) · hold one FA "
            "throughout, Free Alpha (BBB): 21.0 (1 add) · stream each week's best FA: "
            "22.0 (3 adds) · mine, plus the week's best FA where mine gives 0: 25.0 "
            "(1 add).") in md


def test_hold_vs_stream_adds_up_and_counts_a_move_each_time_the_name_changes():
    table = [
        _row("te_m", "TE", "MINE", {9: ("BBB", 8.0), 10: ("CCC", 8.0), 11: ("DDD", 8.0)}),
        _row("fa_s", "TE", "FA", {9: ("AAA", 6.0), 10: ("BBB", 6.0), 11: ("CCC", 6.0)},
             rank=3),
        _row("fa_p", "TE", "FA", {9: ("CCC", 10.0), 11: ("DDD", 0.0)}, byes="10", rank=2),
        _row("fa_l", "TE", "FA", {9: ("DDD", 2.0), 10: ("AAA", 7.0), 11: ("BBB", 7.5)},
             rank=4)]
    te = S.build(table, positions=("TE",)).positions[0]
    t = te.totals
    assert t.mine == 24.0
    # the steady one is the best HOLD although he is never a week's best
    assert (t.hold.gsis_id, t.hold_total) == ("fa_s", 18.0)      # fa_p 10.0, fa_l 16.5
    assert [p.gsis_id for p in t.stream_picks] == ["fa_p", "fa_l", "fa_l"]
    assert t.stream == pytest.approx(24.5) and t.stream_adds == 2
    assert (t.cover, t.cover_adds, t.cover_picks) == (24.0, 0, (None, None, None))
    assert _ids(te.weeks[0].beats_mine) == ["fa_p"]
    a, b = S.Line.from_row(table[1]), S.Line.from_row(table[2])
    assert S.adds([a, None, a]) == 1 and S.adds([a, b, a]) == 3 and S.adds([None]) == 0


def test_an_empty_free_agent_pool_is_said_not_hidden():
    table = [
        _row("k_m", "K", "MINE", {9: ("BBB", 8.0), 10: ("CCC", 7.0), 11: ("DDD", 9.0)}),
        # held elsewhere, or not resolved to a Sleeper id: never a free agent
        _row("k_x", "K", "Rival Manager", {9: ("AAA", 12.0), 10: ("AAA", 12.0)}, rank=2),
        _row("k_u", "K", "unresolved", {9: ("AAA", 11.0), 10: ("AAA", 11.0)}, rank=3)]
    plan = S.build(table, positions=("K",))
    k = plan.positions[0]
    assert k.pool == 0 and all(wp.free_agents == () for wp in k.weeks)
    assert k.totals.hold is None and (k.totals.stream, k.totals.stream_adds) == (0.0, 0)
    assert k.totals.cover == k.totals.mine == 24.0
    md = _md(plan)
    assert "No free-agent K in this table." in md
    assert "nothing to hold or stream" in md and "Rival" not in md
    # a row with no Sleeper id could be anyone's, mine included: said, never listed
    assert k.unresolved == 1 and "Fixture k_u" not in md
    assert "1 row at K with no holder known (no Sleeper id): NOT KNOWN, so in neither list." \
        in md


def _defenses_for_hire():
    return [_row("DEF:KC", "DEF", "FA", {9: ("LV", 7.0), 10: ("DEN", 5.0), 11: ("LAC", 6.0)},
                 team="KC", name="KC DST", rank=1),
            _row("DEF:SF", "DEF", "FA", {9: ("SEA", 6.0), 10: ("ARI", 8.0), 11: ("LA", 4.0)},
                 team="SF", name="SF DST", rank=2)]


def test_no_rostered_player_at_the_position_makes_every_week_a_need():
    table = [_row("te_m", "TE", "MINE", {9: ("BBB", 9.0), 10: ("CCC", 9.0), 11: ("DDD", 9.0)}),
             *_defenses_for_hire()]
    plan = S.build(table, positions=("DEF",))
    d = plan.positions[0]
    assert d.rostered == () and all(wp.status == S.NONE and wp.need for wp in d.weeks)
    assert d.totals.mine == 0.0 and d.totals.cover == d.totals.stream == 21.0
    assert d.totals.cover_adds == d.totals.stream_adds == 3
    assert d.later == ()
    md = _md(plan)
    # the table holds only the players it can project: "none of mine in it",
    # never "I hold none"
    assert ("No DEF of mine in this table (it holds only the players it can project): "
            "every week is a NEED.") in md
    assert "mine 0.0 (no DEF of mine in the table)" in md
    # with nobody of mine to beat, every free agent beats a week of nothing
    assert "| 9 **NEED** | — | **KC DST 7.0 v LV** (+7.0) | **SF DST 6.0 v SEA** (+6.0) |" in md
    assert "NOT KNOWN" not in md


def test_a_table_with_no_mine_row_at_all_says_not_known_never_empty():
    """The owner's roster not found in the snapshot (a wrong `--owner`): no
    row is MINE anywhere, so "no rostered player" would be a false claim."""
    plan = S.build(_defenses_for_hire(), positions=("DEF",))
    assert plan.owner_rows == 0
    md = _md(plan)
    assert "**No row in this table is MINE**" in md and "never empty" in md
    assert "What I hold at DEF is NOT KNOWN" in md and "mine NOT KNOWN" in md
    assert "every week is a NEED" not in md and "DEF of mine" not in md
    assert "KC DST 7.0 v LV" in md                       # the free-agent lists stand


def test_team_defenses_keep_the_ros_tables_def_ids():
    """`DEF:<team>` keys, the nflverse team, and the Sleeper id through
    `ros.ownership` (Sleeper's LAR is nflverse's LA), exactly as the table."""
    table = pd.DataFrame([
        {"gsis_id": "DEF:LA", "position": "DEF", "team": "LA", "pos_rank": 2, "byes": "",
         "weeks": _line({9: ("KC", 6.0), 10: ("SF", 5.5), 11: ("NYJ", 7.0)})},
        {"gsis_id": "DEF:KC", "position": "DEF", "team": "KC", "pos_rank": 1, "byes": "",
         "weeks": _line({9: ("LA", 8.0), 10: ("DEN", -1.0), 11: ("LV", 9.0)})},
        {"gsis_id": "DEF:NYJ", "position": "DEF", "team": "NYJ", "pos_rank": 3, "byes": "",
         "weeks": _line({9: ("MIA", 9.5), 10: ("BUF", 9.5), 11: ("LA", 9.5)})}])
    players = {"LAR": {"position": "DEF", "team": "LAR"}, "KC": {"position": "DEF", "team": "KC"},
               "NYJ": {"position": "DEF", "team": "NYJ"}}
    snap = {"users": [{"user_id": "u1", "display_name": "Me"},
                      {"user_id": "u2", "display_name": "Rival Manager"}],
            "rosters": [{"owner_id": "u1", "roster_id": 1, "players": ["LAR"]},
                        {"owner_id": "u2", "roster_id": 2, "players": ["NYJ"]}]}
    owned = R.ownership(table, snapshot=snap, players=players, crosswalk=Crosswalk({}, {}),
                        owner_id="u1", manager_names=True)
    assert owned.set_index("gsis_id").loc["DEF:LA", "sleeper_id"] == "LAR"
    plan = S.build(owned, positions=("DST",))            # the alias plans as DEF
    d = plan.positions[0]
    assert d.position == "DEF"
    mine = d.rostered[0]                                 # MINE through Sleeper's LAR
    assert (mine.gsis_id, mine.team, mine.name, mine.held_by) == ("DEF:LA", "LA", "LA DST",
                                                                  "MINE")
    listed = {o.line.gsis_id for wp in d.weeks for o in wp.free_agents}
    assert listed == {"DEF:KC"}                          # NYJ is another manager's
    w10 = d.weeks[1]
    kc = S.Line.from_row(owned.loc[owned["gsis_id"] == "DEF:KC"].iloc[0].to_dict())
    assert kc.option(10).status == S.BELOW               # KC's unclipped -1.0 week...
    assert w10.free_agents == ()                         # ...is no option
    assert d.totals.hold_total == 17.0                   # ...and counts 0, as an empty slot
    md = _md(plan)
    assert "| 9 | LA DST 6.0 v KC | **KC DST 8.0 v LA** (+2.0) |" in md   # the table's names
    # a defense row without a name or team is still named the table's way
    bare = S.Line.from_row({"gsis_id": "DEF:NYJ", "position": "DEF", "held_by": "FA"})
    assert (bare.team, bare.name) == ("NYJ", "NYJ DST")


def test_a_free_agents_flags_stay_with_him():
    table = [
        _row("te_m", "TE", "MINE", {9: ("BBB", 9.0), 10: ("CCC", 9.0), 11: ("DDD", 9.0)}),
        _row("fa_ir", "TE", "FA", {9: ("AAA", 8.0), 10: ("BBB", 8.0), 11: ("CCC", 8.0)},
             team="EEE", injury="IR", rank=2),
        _row("fa_d2", "TE", "FA", {9: ("AAA", 7.0), 10: ("BBB", 7.0), 11: ("CCC", 7.0)},
             team="FFF", depth_rank=2, rank=3),
        _row("fa_rs", "TE", "FA", {9: ("AAA", 0.0), 10: ("BBB", 0.0), 11: ("CCC", 6.0)},
             team="GGG", reserve_since=7, rank=4)]
    plan = S.build(table, positions=("TE",))
    te = plan.positions[0]
    flags = {o.line.gsis_id: o.line.flags for wp in te.weeks for o in wp.free_agents}
    assert flags["fa_ir"] == ("IR",) and flags["fa_d2"] == ("depth 2",)
    assert flags["fa_rs"] == (f"IR since wk7: {R.IR_MIN_GAMES} games 0",)
    # a flag is shown, never a filter: the IR-tagged one is still listed; the
    # reserve-list stint's zero weeks are no option, his first week back is
    assert _ids(te.weeks[0].free_agents) == ["fa_ir", "fa_d2"]
    assert _ids(te.weeks[2].free_agents) == ["fa_ir", "fa_d2", "fa_rs"]
    md = _md(plan)
    assert "Fixture fa_ir (EEE; IR) 8.0 v AAA" in md
    assert "Fixture fa_d2 (FFF; depth 2) 7.0 v AAA" in md
    assert f"Fixture fa_rs (GGG; IR since wk7: {R.IR_MIN_GAMES} games 0) 6.0 v CCC" in md
    # the rankings' own flag text is the same implementation
    rr = _load("stream_ros_flags", "scripts/weekly/ros_rankings.py")
    assert rr._flag(table[3]) == f" ({S.row_flags(table[3])[0]})"


def test_the_window_is_the_tables_first_week_and_the_next_two_or_the_weeks_asked():
    rows = [_row("te_m", "TE", "MINE", {w: ("BBB", 9.0) for w in range(15, 18)})]
    assert S.build(rows).weeks == (15, 16, 17)
    late = [_row("te_m", "TE", "MINE", {16: ("BBB", 9.0), 17: ("CCC", 9.0)})]
    assert S.build(late).weeks == (16, 17)                # never past the table's last week
    assert S.build(rows, span=1).weeks == (15,)
    asked = S.build(rows, weeks=(17, 14, 16, 18))
    assert asked.weeks == (16, 17) and asked.skipped == (14, 18)
    assert "so not planned: week(s) 14, 18." in _md(asked)
    nothing = S.build(rows, weeks=(18,))
    assert nothing.weeks == () and "Nothing to plan: week(s) 18 are not in this table" in _md(
        nothing)
    assert "this table has no per-week lines" in _md(S.build([{**rows[0], "weeks": None}]))
    with pytest.raises(ValueError):
        S.build(rows, top=0)
    # RosLine's own tuples read the same as the table's string
    line = R.Schedule(_schedule(), 5).project("TE", "AAA", 9.0, adjust=False)
    assert line.byes == (6,)
    assert S.parse_weeks(line.weeks) == S.parse_weeks(" ".join(
        f"{v}:{o}:{p:g}" for v, o, p in line.weeks))
    assert S.parse_byes(line.byes) == S.parse_byes("6") == S.parse_byes(6.0) == {6}
    assert S.parse_byes("6,14") == {6, 14} and S.parse_byes("") == S.parse_byes(None) == set()


def test_a_need_after_the_window_is_named():
    """The week the owner's tight end's team is off, seen from three weeks out."""
    games = {w: ("BBB", 9.0) for w in (9, 10, 11, 12, 13, 15)}
    te = S.build([_row("te_m", "TE", "MINE", games, byes="14")],
                 positions=("TE",)).positions[0]
    assert te.weeks[-1].week == 11 and te.needs == () and te.later == ((14, S.BYE),)
    assert "Later weeks where my best TE is not a known game: week 14 BYE." in _md(
        S.build([_row("te_m", "TE", "MINE", games, byes="14")], positions=("TE",)))
    planned = S.build([_row("te_m", "TE", "MINE", games, byes="14")], weeks=(14,))
    assert planned.positions[0].needs == (14,) and planned.positions[0].later == ()


def test_the_wording_keeps_the_plan_a_plan():
    md = _md(S.build([_row("te_m", "TE", "MINE", {9: ("BBB", 9.0)})]))
    for phrase in ("Points, not ΔP(win); planning, not a move.",
                   "A pickup is a move only if it improves THIS week's lineup, and the board "
                   "decides that (`gridiron.waivers`",
                   "\"FA\" means on no roster in the league snapshot, which may still be on "
                   "waivers (claims only) — check the board's waiver clock before acting",
                   "UNKNOWN = no game row and no proven bye", "never a bye"):
        assert phrase in md
    for verb in ("add now", "pick up now", "must add"):
        assert verb not in md.lower()


def test_the_rankings_carry_the_streaming_plan(tmp_path):
    scn = _load("stream_scn", "scripts/weekly/dashboard_scenarios.py")
    rr = _load("stream_ros_cli", "scripts/weekly/ros_rankings.py")
    root = tmp_path / "complete"
    scn.build_scenario(root, "complete", now=scn.NOW)
    out = tmp_path / "out"
    assert rr.main(["--cache-root", str(root), "--owner", "fixture_owner", "--week", "3",
                    "--out", str(out), "--stream", "3-4"]) == 0
    md = (out / "ros_week03.md").read_text("utf-8")
    assert "## Streaming plan — TE, K, DEF, weeks 3-4" in md
    sec = md.split("## Streaming plan")[1].split("\n## ")[0]
    assert "### TE" in sec and "### K" in sec and "### DEF" in sec
    assert "| MINE |" not in sec and "Other weeks: `--stream 12-14`" in sec
    assert md.index("## Streaming plan") < md.index("\n## TE")      # before the long tables
    assert (rr.stream_weeks("12-14"), rr.stream_weeks("12,14"), rr.stream_weeks("14")) == \
        ((12, 13, 14), (12, 14), (14,))
    for bad in ("14-12", "soon", ""):
        with pytest.raises(argparse.ArgumentTypeError):
            rr.stream_weeks(bad)
    # a plan that cannot be built is said in its section; the rankings still go out
    broken = pd.DataFrame([_row("te_m", "TE", "MINE", {9: ("BBB", 9.0)}, weeks="9:BBB")])
    lines = rr.streaming_section(broken)
    assert lines[0] == "## Streaming plan" and lines[2].startswith("Not built: ValueError")
