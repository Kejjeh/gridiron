"""Pre-flight for a set of add/drop moves: the checks a human forgets.

2026 week 9, one advice session, five misses that a table would have caught:

  * dropping a receiver who had just scored 18.5 on 9 targets, because his
    rest-of-season rank (WR124) came from the games he MISSED, not his role;
  * not seeing the owner's own back go Out with a knee injury in that
    afternoon's game, while the owner's other back was on bye next week;
  * recommending a receiver whose team was on bye the following week;
  * weighing receivers without their quarterbacks (one team on its third QB
    in five weeks, another with a QB at -10 EPA);
  * naming a player wrong (the wrong first name for a rostered receiver), and
    suggesting a back his team had already released.

So every card here is built by id (rule #3) from the published record, the
schedule, nflverse box scores and Sleeper's own week stats, and every flag is
a FACT with its source — never a verdict. The decision stays with the owner
and the skill that reads this (`.claude/skills/move-plan`).

Inputs, all passed in (this module never opens a socket):
  * `record` — a published board record (roster, radar pool, usage, ros);
  * `live` — `live_rows(...)` of Sleeper's week stats: points as the
    platform scores them (Game Day's rule: platform actuals only) and the
    injury tag and team Sleeper attaches to each row, so an in-game injury is
    visible without the once-a-day player map;
  * `byes` — `bye_weeks(schedule, ...)`; a team absent from a week that has
    rows is on bye only when the week is complete (rule: absence is never a
    bye unless proven);
  * `qbs` — `team_qbs(player_stats, ...)`: who threw for each team lately.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from gridiron.ids import nflverse_team, normalize_id
from gridiron.notable import STARTER_RANK
from gridiron.openings import VACATING_DESIGNATIONS

#: A drop candidate who scored at least this many platform points in the
#: latest week is flagged "look before dropping" (a quarterback's ordinary
#: week is a skill player's big one).
HOT_POINTS = 12.0
HOT_POINTS_BY_POSITION = {"QB": 20.0}
#: A drop candidate with fewer usage lines than this in the record's window,
#: who just had this many opportunities, is flagged as a returning player
#: whose rest-of-season rank undercounts his role.
RETURN_MAX_GAMES = 2
RETURN_MIN_OPPS = 6.0
#: Quarterback context window (games with a pass attempt line).
QB_GAMES = 3
#: Slots each position must fill next week, read from the record's slots.
FLEX_POSITIONS = ("RB", "WR", "TE")


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


# --------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class LiveRow:
    points: float | None
    opportunities: float | None
    line: str
    tag: str
    team: str            # nflverse spelling, "" when Sleeper lists none
    news: datetime | None


def _line(st: Mapping[str, object]) -> str:
    bits = []
    if _num(st.get("pass_att")):
        bits.append(f"{int(_num(st.get('pass_yd')) or 0)} pass yds, "
                    f"{int(_num(st.get('pass_td')) or 0)} TD, "
                    f"{int(_num(st.get('pass_int')) or 0)} INT")
    if _num(st.get("rush_att")):
        bits.append(f"{int(_num(st['rush_att']))} carries for "
                    f"{int(_num(st.get('rush_yd')) or 0)} yds"
                    + (f", {int(_num(st['rush_td']))} TD" if _num(st.get("rush_td")) else ""))
    if _num(st.get("rec_tgt")):
        bits.append(f"{int(_num(st.get('rec')) or 0)}/{int(_num(st['rec_tgt']))} targets for "
                    f"{int(_num(st.get('rec_yd')) or 0)} yds"
                    + (f", {int(_num(st['rec_td']))} TD" if _num(st.get("rec_td")) else ""))
    if _num(st.get("fga")) or _num(st.get("xpa")) or _num(st.get("xpm")):
        bits.append(f"FG {int(_num(st.get('fgm')) or 0)}/{int(_num(st.get('fga')) or 0)}, "
                    f"XP {int(_num(st.get('xpm')) or 0)}")
    return "; ".join(bits)


def live_rows(rows: Iterable[Mapping[str, object]]) -> dict[str, LiveRow]:
    """Sleeper's week-stats rows (api.sleeper.com/stats/nfl/<season>/<week>),
    keyed by Sleeper id. A row with no stats still carries the tag and team."""
    out: dict[str, LiveRow] = {}
    for r in rows or ():
        if not isinstance(r, Mapping):
            continue
        sid = normalize_id(r.get("player_id"))
        if not sid:
            continue
        st = r.get("stats") if isinstance(r.get("stats"), Mapping) else {}
        meta = r.get("player") if isinstance(r.get("player"), Mapping) else {}
        opps = None
        if st:
            pos = str(meta.get("position") or "").upper()
            opps = sum(_num(st.get(k)) or 0.0 for k in
                       (("pass_att", "rush_att") if pos == "QB" else ("rec_tgt", "rush_att")))
        news = _num(meta.get("news_updated"))
        team = meta.get("team") if "team" in meta else r.get("team")
        out[sid] = LiveRow(
            points=_num(st.get("pts_half_ppr")) if st else None,
            opportunities=opps, line=_line(st) if st else "",
            tag=str(meta.get("injury_status") or ""),
            team=nflverse_team(team) if team else "",
            news=(datetime.fromtimestamp(news / 1000, tz=UTC) if news else None))
    return out


def bye_weeks(schedule: pd.DataFrame | None, weeks: Iterable[int]) -> dict[int, frozenset[str]]:
    """week -> teams on bye, for regular-season weeks whose schedule rows are
    present. A team is on bye only when the season's teams are all known and
    it has no game that week (absence is proven by count, never assumed)."""
    out: dict[int, frozenset[str]] = {}
    if schedule is None or len(schedule) == 0:
        return out
    s = schedule
    if "game_type" in s:
        s = s.loc[s["game_type"] == "REG"]
    teams = set(s["home_team"]) | set(s["away_team"])
    for w in weeks:
        rows = s.loc[s["week"] == int(w)]
        if len(rows) == 0:
            continue
        playing = set(rows["home_team"]) | set(rows["away_team"])
        out[int(w)] = frozenset(teams - playing)
    return out


@dataclass(frozen=True)
class QBLine:
    team: str
    games: tuple[tuple[int, str, float, float | None], ...]   # (week, name, attempts, epa)

    @property
    def starters(self) -> list[str]:
        return [g[1] for g in self.games]

    @property
    def changed(self) -> bool:
        return len(set(self.starters)) > 1

    @property
    def epa(self) -> float | None:
        vals = [g[3] for g in self.games if g[3] is not None]
        return sum(vals) / len(vals) if vals else None

    def describe(self) -> str:
        who = " → ".join(dict.fromkeys(self.starters))
        e = self.epa
        return (f"{self.team} QB {who} over weeks {'/'.join(str(g[0]) for g in self.games)}"
                + (f", passing EPA {e:+.1f} a game" if e is not None else ""))


def team_qbs(player_stats: pd.DataFrame | None, *, through_week: int,
             games: int = QB_GAMES) -> dict[str, QBLine]:
    """team -> who threw the most passes in each of its last `games` games
    (nflverse weekly player stats), with his passing EPA."""
    out: dict[str, QBLine] = {}
    if player_stats is None or len(player_stats) == 0:
        return out
    q = player_stats.loc[(player_stats["position"] == "QB")
                         & (pd.to_numeric(player_stats["week"]) <= int(through_week))]
    q = q.loc[pd.to_numeric(q["attempts"], errors="coerce").fillna(0) > 0]
    for team, rows in q.groupby("team"):
        top = (rows.sort_values("attempts", ascending=False)
               .drop_duplicates(subset=["week"]).sort_values("week").tail(games))
        out[str(team)] = QBLine(str(team), tuple(
            (int(r["week"]), str(r.get("player_display_name") or r.get("player_name") or ""),
             float(r["attempts"]), _num(r.get("passing_epa")))
            for _, r in top.iterrows()))
    return out


# --------------------------------------------------------------------------- cards


@dataclass(frozen=True)
class Index:
    """Who the record knows, by Sleeper id: identity, who holds him, usage and
    rest-of-season numbers. Built only from the record (rule #3)."""

    who: Mapping[str, dict]

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> Index:
        who: dict[str, dict] = {}
        ros = (record.get("ros") or {}).get("players") or []
        for p in ros:
            sid = normalize_id(p.get("sleeper_id"))
            if sid:
                who[sid] = {"name": p.get("name"), "position": p.get("position"),
                            "team": p.get("team"), "held": p.get("held_by") or "unknown",
                            "ros_rank": p.get("pos_rank"), "ros": p.get("ros"),
                            "byes": p.get("byes")}
        for p in (record.get("radar") or {}).get("pool") or []:
            sid = normalize_id(p.get("id"))
            if sid:
                who.setdefault(sid, {}).update({"name": p.get("name"), "position":
                                                p.get("position"), "team": p.get("team"),
                                                "held": "available"})
        for p in record.get("roster") or []:
            sid = normalize_id(p.get("sleeper_id"))
            if sid:
                who.setdefault(sid, {}).update({
                    "name": p.get("name"), "position": p.get("position"),
                    "team": p.get("team"), "held": "mine", "lineup": p.get("lineup"),
                    "availability": p.get("availability") or "",
                    "withheld": bool(p.get("withheld"))})
        usage = (record.get("usage") or {}).get("players") or {}
        for sid, line in usage.items():
            who.setdefault(normalize_id(sid), {})["usage"] = line
        return cls(who)

    def get(self, sid: str) -> dict:
        return self.who.get(normalize_id(sid), {})


@dataclass(frozen=True)
class Card:
    sleeper_id: str
    name: str
    position: str
    team: str
    held: str
    tag: str
    tag_source: str
    live: LiveRow | None
    trend: str
    games_in_window: int
    ros_rank: object
    byes: tuple[int, ...]            # of the weeks checked
    qb: QBLine | None
    flags: tuple[str, ...] = field(default=())

    def with_flags(self, flags: Sequence[str]) -> Card:
        return Card(**{**self.__dict__, "flags": tuple(self.flags) + tuple(flags)})


def card(sid: str, ix: Index, *, live: Mapping[str, LiveRow], byes: Mapping[int, frozenset],
         qbs: Mapping[str, QBLine]) -> Card:
    """One player's facts. The tag prefers Sleeper's week-stats row (current to
    the minute) over the record's injury report line (as of its build)."""
    sid = normalize_id(sid)
    info = ix.get(sid)
    lv = live.get(sid)
    team = (lv.team if lv is not None and lv.team else nflverse_team(info.get("team") or ""))
    if lv is not None and lv.tag:
        tag, src = lv.tag, "Sleeper, " + (lv.news.strftime("%a %H:%M UTC") if lv.news
                                           else "this week's stats feed")
    elif lv is not None:
        tag, src = "", "Sleeper (no tag)"
    else:
        tag, src = "", "record: " + (info.get("availability") or "no injury line")[:80]
    u = info.get("usage") or {}
    pos = str(info.get("position") or "").upper()
    flags = []
    if lv is not None and lv.team == "" and info:
        flags.append("Sleeper lists NO NFL team (released?)")
    if tag.upper() in VACATING_DESIGNATIONS:
        flags.append(f"{tag} per {src}: will not play")
    elif tag:
        flags.append(f"{tag} per {src} (not out, rule #11)")
    bye_hits = tuple(w for w, teams in sorted(byes.items()) if team and team in teams)
    for w in bye_hits:
        flags.append(f"BYE in week {w}")
    qb = qbs.get(team) if pos in ("WR", "TE") else None
    if qb is not None:
        if qb.changed:
            flags.append(f"QB changed: {qb.describe()}")
        elif qb.epa is not None and qb.epa < 0:
            flags.append(f"weak QB: {qb.describe()}")
    if u.get("trend") == "FALLING":
        flags.append(f"usage FALLING ({u.get('trend_why')})")
    return Card(sid, str(info.get("name") or f"sleeper id {sid}"), pos, team,
                str(info.get("held") or "unknown"), tag, src, lv,
                str(u.get("trend") or "NO LINE"), len(u.get("weeks") or []),
                info.get("ros_rank"), bye_hits, qb, tuple(flags))


def drop_check(c: Card) -> Card:
    """Facts that say "look before dropping"."""
    flags = []
    if c.held != "mine":
        flags.append(f"not on your roster ({c.held})")
    cut = STARTER_RANK.get(c.position)
    rank = _num(c.ros_rank)
    if cut and rank is not None and rank <= cut:
        flags.append(f"rest-of-season rank {c.position}{int(rank)} is a starter's "
                     f"(top {cut}): look before dropping")
    lv = c.live
    hot = HOT_POINTS_BY_POSITION.get(c.position, HOT_POINTS)
    if lv is not None and lv.points is not None and lv.points >= hot:
        flags.append(f"scored {lv.points:.1f} this week ({lv.line}): look before dropping")
    if (lv is not None and (lv.opportunities or 0) >= RETURN_MIN_OPPS
            and c.games_in_window <= RETURN_MAX_GAMES):
        flags.append(f"only {c.games_in_window} game(s) in the usage window but "
                     f"{lv.opportunities:.0f} opportunities this week: a returning player; "
                     f"the rest-of-season rank ({c.position}{c.ros_rank}) undercounts his role")
    return c.with_flags(flags)


def add_check(c: Card) -> Card:
    """Facts that say "this add may not help"."""
    flags = []
    if c.held != "available":
        flags.append(f"NOT AVAILABLE: {c.held}")
    return c.with_flags(flags)


# --------------------------------------------------------------------------- roster + plan


def roster_cards(ix: Index, **kw) -> list[Card]:
    return [card(sid, ix, **kw) for sid, info in ix.who.items() if info.get("held") == "mine"]


def _need(slots: Sequence[str]) -> dict[str, int]:
    need: dict[str, int] = {}
    for s in slots:
        key = "DEF" if s in ("DST", "DEF") else s
        need[key] = need.get(key, 0) + 1
    return need


def plan_check(roster: Sequence[Card], adds: Sequence[Card], drops: Sequence[Card], *,
               slots: Sequence[str], week: int) -> list[str]:
    """Whether the roster after the moves can field a legal lineup in `week`
    (byes and will-not-play tags), and whether the moves balance."""
    out = []
    if len(adds) != len(drops):
        out.append(f"{len(adds)} add(s) but {len(drops)} drop(s): every claim needs a "
                   f"roster spot")
    dropped = {c.sleeper_id for c in drops}
    after = [c for c in roster if c.sleeper_id not in dropped] + list(adds)

    def ready(c: Card) -> bool:
        return week not in c.byes and c.tag.upper() not in VACATING_DESIGNATIONS

    count: dict[str, int] = {}
    for c in after:
        if ready(c):
            key = "DEF" if c.position in ("DST", "DEF") else c.position
            count[key] = count.get(key, 0) + 1
    need = _need(slots)
    spare = 0
    for pos, n in need.items():
        if pos == "FLEX":
            continue
        have = count.get(pos, 0)
        if have < n:
            out.append(f"week {week}: {have} {pos} able to play for {n} {pos} slot(s)")
        elif pos in FLEX_POSITIONS:
            spare += have - n
    if need.get("FLEX") and spare < need["FLEX"]:
        out.append(f"week {week}: {spare} RB/WR/TE left for {need['FLEX']} FLEX slot(s)")
    return out


def leaders(ix: Index, live: Mapping[str, LiveRow], *, top: int = 10,
            positions: Sequence[str] = ("QB", "RB", "WR", "TE")) -> dict[str, list[tuple]]:
    """position -> the available pool's best platform scores this week:
    (points, name, team, opportunities, line, sleeper id)."""
    out: dict[str, list[tuple]] = {p: [] for p in positions}
    for sid, info in ix.who.items():
        lv = live.get(sid)
        pos = str(info.get("position") or "").upper()
        if info.get("held") != "available" or lv is None or lv.points is None \
                or pos not in out:
            continue
        out[pos].append((lv.points, str(info.get("name")), lv.team or info.get("team"),
                         lv.opportunities, lv.line, sid))
    return {p: sorted(rows, key=lambda r: -r[0])[:top] for p, rows in out.items()}
