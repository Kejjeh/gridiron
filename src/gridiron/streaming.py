"""Weekly streaming plans for tight ends, kickers and team defenses —
roadmap TE-09, K-13 and DEF-13 (docs/research/MODEL_ROADMAP.md).

A product LIST, not a projection. Every number in it is one week of a
player's rest-of-season line exactly as `gridiron.ros.build_table` wrote it,
after `ros.ownership` said who holds him: nothing is re-projected,
re-weighted or guessed. For each position and each week of a short window
(by default the table's first week and the next two) it answers "who fills
this slot for me that week, and who is out there":

  * my best rostered option (`held_by == "MINE"`) with his points and
    opponent, or why there is none:
      BYE      a week the schedule PROVES off (the line's `byes`, from
               `gridiron.lineup.proven_byes`);
      OUT      his line credits nothing that week: 0 for an Out on the
               first week's injury report or the NFL minimum of a
               reserve-list stint; 0 or less for a team defense, whose line
               is not clipped;
      UNKNOWN  opponent `?`: no game row and no proven bye. Counted at his
               plain rate, as the line counts it, and NEVER a bye;
      NONE     no rostered player at the position.
    A week whose best is anything but a known game is a NEED.
  * the top free agents (`held_by == "FA"`) by that week's points, with
    opponents and the table's own flags (injury tag, `depth N`, `IR since
    wkN`). A flag goes to the reader; it never filters anyone (rule #11).
  * hold vs stream over the window: my total; the best single free agent
    held for the whole window (hold); each week's best free agent, summed
    (stream: a move every week the name changes); and mine, with the week's
    best free agent only in the weeks mine gives nothing (cover). A week
    that gives nothing counts 0, as an empty slot does.

What it is not (rules #7 and #11): points, not ΔP(win); planning, not a
move. A pickup is a move only if it improves THIS week's lineup, and the
board decides that (`gridiron.waivers`, the Free Agent Radar). `FA` means on
no roster in the league snapshot, which is not "addable now": a player
dropped in the last two days is on waivers, claims only, and every
unrostered player is locked on waivers from his game's kickoff (or the
week's last) until the weekly run (`gridiron.notable.waiver_clock`). The
board's waiver clock, and his Sleeper card, come before any claim.

Joins: none. A row is the table's row, keyed by the table's id (a gsis id,
or `DEF:<team>` for a team defense, rule #3); names are display only.
"""
from __future__ import annotations

import math
import numbers
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from gridiron.ids import normalize_id
from gridiron.ros import IR_MIN_GAMES

#: The positions planned, in the order shown.
POSITIONS: tuple[str, ...] = ("TE", "K", "DEF")
#: Weeks planned by default: the table's first week and the next two.
SPAN = 3
#: Free agents listed per week.
TOP = 5

MINE, FA = "MINE", "FA"
GAME, BYE, OUT, UNKNOWN, NONE = "GAME", "BYE", "OUT", "UNKNOWN", "NONE"
_NO_RANK = 10 ** 6

#: The section's standing words (rules #7 and #11), shown above every plan.
WORDING = (
    "Points, not ΔP(win); planning, not a move. A pickup is a move only if it "
    "improves THIS week's lineup, and the board decides that (its Free Agent Radar), "
    "not this list. \"FA\" means on no roster in the league snapshot, which may still "
    "be on waivers (claims only) — check the board's waiver clock before acting (his "
    "Sleeper card is the authority)."
)
LEGEND = (
    "Each week: my best rostered option, then the top free agents by that week's "
    "points on the ROS lines. BYE = a week the schedule proves off; UNKNOWN = no game "
    "row and no proven bye (opponent ?, counted at his plain rate, never a bye); OUT = "
    "his ROS line credits nothing that week (0; 0 or less for a defense). **NEED** = "
    "my best that week is not a known game. **Bold** = beats my best that week, by "
    "the amount shown. Flags are the ROS table's own (injury tag, depth N, IR since "
    "wkN)."
)


def _missing(v: object) -> bool:
    if v is None or v is pd.NA:
        return True
    return isinstance(v, float) and math.isnan(v)


def _position(value: object) -> str:
    p = "" if _missing(value) else str(value).strip().upper()
    return "DEF" if p in ("DEF", "DST") else p


# ------------------------------------------------------------- the table

def parse_weeks(value: object) -> tuple[tuple[int, str, float], ...]:
    """A ROS line's games as (week, opponent, points): the table's
    `"week:opponent:points ..."` string (`gridiron.ros`), or `RosLine.weeks`
    itself. A bye is not a game, so it is not in here."""
    if _missing(value):
        return ()
    if isinstance(value, str):
        out = []
        for token in value.split():
            week, opp, pts = token.split(":")
            out.append((int(week), opp, float(pts)))
        return tuple(out)
    return tuple((int(w), str(o), float(p)) for w, o, p in value)


def parse_byes(value: object) -> frozenset[int]:
    """A ROS line's PROVEN byes: the table's `"6"`, `"6,14"` or `""`, a
    number (a CSV round trip), or `RosLine.byes`."""
    if _missing(value):
        return frozenset()
    if isinstance(value, str):
        return frozenset(int(float(b)) for b in value.split(",") if b.strip())
    if isinstance(value, numbers.Real):
        return frozenset({int(value)})
    return frozenset(int(b) for b in value)


def row_flags(row: Mapping[str, object]) -> tuple[str, ...]:
    """The ROS table's own flags on a row, in the words the rankings use:
    the injury tag (the player map's, or the first week's Out from the
    injury report), `depth N` when he was not first on the depth chart
    before the week, and a reserve-list stint's NFL minimum."""
    bits = []
    st = row.get("injury")
    st = "" if _missing(st) else str(st).upper()
    if st and st != "NAN":
        bits.append(st)
    dr = row.get("depth_rank")
    if not _missing(dr) and int(dr) > 1:
        bits.append(f"depth {int(dr)}")
    rs = row.get("reserve_since")
    if not _missing(rs):
        bits.append(f"IR since wk{int(rs)}: {IR_MIN_GAMES} games 0")
    return tuple(bits)


@dataclass(frozen=True)
class Line:
    """One row of the ROS table at a planned position: who he is, who holds
    him, and his line week by week."""

    gsis_id: str                               # the table's key; DEF:<team> for a defense
    name: str
    team: str
    position: str
    held_by: str                               # MINE, FA, a manager, rostered, unresolved
    pos_rank: int | None
    flags: tuple[str, ...]
    games: tuple[tuple[int, str, float], ...]  # (week, opponent, points); byes absent
    byes: frozenset[int]                       # proven by the schedule

    @classmethod
    def from_row(cls, row: Mapping[str, object]) -> Line:
        pos = _position(row.get("position"))
        gid = normalize_id(row.get("gsis_id"))
        team = normalize_id(row.get("team"))
        if not team and gid.startswith("DEF:"):
            team = gid.split(":", 1)[1]
        name = "" if _missing(row.get("name")) else str(row.get("name")).strip()
        if not name:                           # the table's own display rule
            name = f"{team} DST" if pos == "DEF" else gid
        held = row.get("held_by")
        rank = row.get("pos_rank")
        return cls(gid, name, team, pos, "" if _missing(held) else str(held),
                   None if _missing(rank) else int(rank), row_flags(row),
                   parse_weeks(row.get("weeks")), parse_byes(row.get("byes")))

    def option(self, week: int) -> Option:
        """This player in `week`, as his line has it."""
        week = int(week)
        if week in self.byes:
            return Option(self, week, BYE, "", 0.0)
        for w, opp, pts in self.games:
            if w == week:
                if pts <= 0:
                    return Option(self, week, OUT, opp, pts)
                return Option(self, week, UNKNOWN if opp == "?" else GAME, opp, pts)
        # the line says nothing about the week (outside it): no number
        return Option(self, week, UNKNOWN, "?", 0.0)


@dataclass(frozen=True)
class Option:
    """One player in one week, as his ROS line has it."""

    line: Line
    week: int
    status: str          # GAME | BYE | OUT | UNKNOWN
    opponent: str        # "" on a bye; "?" when the schedule has no game row
    points: float        # the line's number that week; 0 on a bye

    @property
    def counts(self) -> bool:
        """A number to start that week: a known game, or a week the line
        counts as played without knowing the opponent."""
        return self.status in (GAME, UNKNOWN) and self.points > 0

    @property
    def credit(self) -> float:
        """What he adds to a total that week: his points, or 0 (an empty
        slot) when he gives nothing."""
        return self.points if self.counts else 0.0


def _rank(line: Line) -> int:
    return line.pos_rank if line.pos_rank is not None else _NO_RANK


def _order(o: Option) -> tuple:
    """Best first: a number to start, then more points, a known game before
    an unknown one, the better ROS rank, the id (a stable tie-break)."""
    return (not o.counts, -o.points, o.status != GAME, _rank(o.line), o.line.gsis_id)


# --------------------------------------------------------------- the plan

@dataclass(frozen=True)
class WeekPlan:
    """One position in one week."""

    week: int
    mine: tuple[Option, ...]          # every rostered player at the position, best first
    free_agents: tuple[Option, ...]   # the top free agents with a number, best first

    @property
    def best(self) -> Option | None:
        return self.mine[0] if self.mine else None

    @property
    def status(self) -> str:
        return self.best.status if self.best is not None else NONE

    @property
    def need(self) -> bool:
        """My best is not a known game: he gives nothing (BYE, OUT), nobody
        is rostered (NONE), or his week is UNKNOWN."""
        return self.status != GAME

    @property
    def mine_points(self) -> float:
        return self.best.credit if self.best is not None else 0.0

    def margin(self, option: Option) -> float:
        """How far `option`'s week beats my best's (a week mine gives
        nothing counts 0)."""
        return option.credit - self.mine_points

    @property
    def beats_mine(self) -> tuple[Option, ...]:
        return tuple(o for o in self.free_agents if self.margin(o) > 0)


def adds(picks: Iterable[Line | None]) -> int:
    """Pickups a weekly sequence needs: the first, then one each time the
    name changes (a week without a pickup changes nothing)."""
    n, last = 0, None
    for p in picks:
        if p is None:
            continue
        if p.gsis_id != last:
            n += 1
        last = p.gsis_id
    return n


@dataclass(frozen=True)
class HoldVsStream:
    """Ways through the window, in points (rule #7: never ΔP(win))."""

    weeks: tuple[int, ...]
    mine: float                              # my best each week, summed
    hold: Line | None                        # the free agent with the most points over the window
    hold_total: float
    stream: float                            # each week's best free agent, summed
    stream_picks: tuple[Line | None, ...]    # per week; None: no free agent with a number
    cover: float                             # mine, and the week's best FA where mine gives 0
    cover_picks: tuple[Line | None, ...]     # per week; None where mine gives points

    @property
    def stream_adds(self) -> int:
        return adds(self.stream_picks)

    @property
    def cover_adds(self) -> int:
        return adds(self.cover_picks)


@dataclass(frozen=True)
class PositionPlan:
    position: str
    weeks: tuple[WeekPlan, ...]
    totals: HoldVsStream
    rostered: tuple[Line, ...]                 # mine at the position, best ROS rank first
    pool: int                                  # free agents at the position in the table
    rows: int                                  # table rows at the position
    #: weeks after the window where my best is not a known game: (week, status)
    later: tuple[tuple[int, str], ...] = ()

    @property
    def needs(self) -> tuple[int, ...]:
        return tuple(wp.week for wp in self.weeks if wp.need)


@dataclass(frozen=True)
class StreamingPlan:
    weeks: tuple[int, ...]                     # the weeks planned
    covered: tuple[int, ...]                   # every week the table's lines cover
    skipped: tuple[int, ...]                   # asked for, but not in the table
    top: int
    positions: tuple[PositionPlan, ...]
    #: MINE rows in the whole table, every position. 0 means the owner's
    #: roster was not found, so "no rostered player" is NOT KNOWN, not empty.
    owner_rows: int = 0


def window(covered: Iterable[int], weeks: Iterable[int] | None = None,
           span: int = SPAN) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """(the weeks to plan, the asked-for weeks the table has no line for).
    Default: the table's first week and the next `span - 1` it covers."""
    have = sorted({int(w) for w in covered})
    if weeks is None:
        if not have:
            return (), ()
        return tuple(w for w in range(have[0], have[0] + int(span)) if w in have), ()
    want = sorted({int(w) for w in weeks})
    return tuple(w for w in want if w in have), tuple(w for w in want if w not in have)


def build(table: pd.DataFrame | Iterable[Mapping[str, object]], *,
          positions: Iterable[str] = POSITIONS, weeks: Iterable[int] | None = None,
          span: int = SPAN, top: int = TOP) -> StreamingPlan:
    """The streaming plan for `positions` over `weeks` (default: the
    table's first week and the next `span - 1`), from a ROS table that
    carries `held_by` (`gridiron.ros.ownership`) and its `weeks` lines."""
    if int(top) < 1 or int(span) < 1:
        raise ValueError("top and span must be at least 1")
    positions = tuple(dict.fromkeys(_position(p) for p in positions))
    rows = table.to_dict("records") if hasattr(table, "to_dict") else list(table)
    lines = [Line.from_row(r) for r in rows if _position(r.get("position")) in positions]
    covered = tuple(sorted({w for ln in lines for w, _, _ in ln.games}
                           | {b for ln in lines for b in ln.byes}))
    planned, skipped = window(covered, weeks, span)
    return StreamingPlan(planned, covered, skipped, int(top), tuple(
        _plan(p, [ln for ln in lines if ln.position == p], planned, covered, int(top))
        for p in positions), sum(1 for r in rows if r.get("held_by") == MINE))


def _plan(position: str, lines: Sequence[Line], weeks: Sequence[int],
          covered: Sequence[int], top: int) -> PositionPlan:
    mine = [ln for ln in lines if ln.held_by == MINE]
    pool = [ln for ln in lines if ln.held_by == FA]
    plans: list[WeekPlan] = []
    best_fa: list[Option | None] = []
    for w in weeks:
        fas = sorted((o for o in (ln.option(w) for ln in pool) if o.counts), key=_order)
        plans.append(WeekPlan(w, tuple(sorted((ln.option(w) for ln in mine), key=_order)),
                              tuple(fas[:top])))
        best_fa.append(fas[0] if fas else None)
    held = sorted(((sum(ln.option(w).credit for w in weeks), ln) for ln in pool),
                  key=lambda t: (-t[0], _rank(t[1]), t[1].gsis_id))
    hold, hold_total = (held[0][1], held[0][0]) if held and held[0][0] > 0 else (None, 0.0)
    gives = [wp.best is not None and wp.best.counts for wp in plans]
    totals = HoldVsStream(
        tuple(weeks), round(sum(wp.mine_points for wp in plans), 2), hold, round(hold_total, 2),
        round(sum(o.credit for o in best_fa if o is not None), 2),
        tuple(o.line if o is not None else None for o in best_fa),
        round(sum(wp.mine_points if g else (o.credit if o is not None else 0.0)
                  for wp, g, o in zip(plans, gives, best_fa)), 2),
        tuple(None if g or o is None else o.line for g, o in zip(gives, best_fa)))
    later: list[tuple[int, str]] = []
    if mine and weeks:
        for w in covered:
            if w > weeks[-1]:
                status = min((ln.option(w) for ln in mine), key=_order).status
                if status != GAME:
                    later.append((w, status))
    return PositionPlan(position, tuple(plans), totals,
                        tuple(sorted(mine, key=lambda ln: (_rank(ln), ln.gsis_id))),
                        len(pool), len(lines), tuple(later))


# ---------------------------------------------------------------- markdown

def _span(weeks: Iterable[int]) -> str:
    """4, 5, 6, 7, 9 -> "4-7, 9"."""
    ws = sorted(set(weeks))
    runs: list[list[int]] = []
    for w in ws:
        if runs and w == runs[-1][-1] + 1:
            runs[-1].append(w)
        else:
            runs.append([w])
    return ", ".join(f"{r[0]}-{r[-1]}" if len(r) > 1 else str(r[0]) for r in runs)


def _weeks_text(weeks: Sequence[int]) -> str:
    return f"week {weeks[0]}" if len(weeks) == 1 else f"weeks {_span(weeks)}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _who(line: Line) -> str:
    name = line.name.replace("|", "/")
    team_in_name = line.position == "DEF" and line.team in name.split()
    bits = ([line.team] if line.team and not team_in_name else []) + list(line.flags)
    return f"{name} ({'; '.join(bits)})" if bits else name


def _cell(o: Option) -> str:
    who = _who(o.line)
    if o.status == BYE:
        return f"{who} BYE"
    if o.status == OUT:
        return f"{who} OUT" + (f" (v {o.opponent})" if o.opponent not in ("", "?") else "")
    return f"{who} {o.points:.1f} v {o.opponent}" + (" (UNKNOWN)" if o.status == UNKNOWN else "")


def _fa_cell(wp: WeekPlan, o: Option) -> str:
    m = wp.margin(o)
    return f"**{_cell(o)}** (+{m:.1f})" if m > 0 else _cell(o)


def _totals_line(p: PositionPlan, owner_known: bool) -> str:
    t = p.totals
    gaps = "; ".join(f"wk {wp.week} {wp.status}" for wp in p.weeks if wp.need)
    mine = (f"mine {t.mine:.1f}" + (f" ({gaps})" if gaps else "") if p.rostered
            else f"mine 0.0 (no rostered {p.position})" if owner_known
            else "mine NOT KNOWN")
    head = f"Hold vs stream, {_weeks_text(t.weeks)} (points): "
    if t.hold is None:              # no free agent has a number in any of these weeks
        return head + mine + " · no free agent with a number in these weeks: nothing to " \
                             "hold or stream."
    hold = f"hold one FA throughout, {_who(t.hold)}: {t.hold_total:.1f} (1 add)"
    stream = f"stream each week's best FA: {t.stream:.1f} ({_plural(t.stream_adds, 'add')})"
    cover = (f"mine, plus the week's best FA where mine gives 0: {t.cover:.1f} "
             f"({_plural(t.cover_adds, 'add')})")
    return head + " · ".join((mine, hold, stream, cover)) + "."


def _position_lines(p: PositionPlan, top: int, owner_known: bool) -> list[str]:
    out = [f"### {p.position}", ""]
    if p.rows == 0:
        return out + [f"No {p.position} in this table: nothing to plan.", ""]
    nobody = (f"No rostered {p.position}: every week is a NEED." if owner_known else
              f"What I hold at {p.position} is NOT KNOWN (no MINE row in this table).")
    notes = ([nobody] if not p.rostered else []) \
        + ([f"No free-agent {p.position} in this table."] if p.pool == 0 else [])
    if notes:
        out += [" ".join(notes), ""]
    out += ["| week | mine (best first) | " + " | ".join(f"FA {i}" for i in range(1, top + 1))
            + " |", "|---|---|" + "---|" * top]
    for wp in p.weeks:
        fas = [_fa_cell(wp, o) for o in wp.free_agents] + ["—"] * (top - len(wp.free_agents))
        out.append(f"| {wp.week}{' **NEED**' if wp.need else ''} | "
                   f"{'; '.join(_cell(o) for o in wp.mine) or '—'} | " + " | ".join(fas) + " |")
    out += ["", _totals_line(p, owner_known), ""]
    if p.later:
        by: dict[str, list[int]] = {}
        for w, st in p.later:
            by.setdefault(st, []).append(w)
        out += [f"Later weeks where my best {p.position} is not a known game: "
                + "; ".join(f"{st} {_span(ws)}" for st, ws in by.items()) + ".", ""]
    return out


def markdown(plan: StreamingPlan) -> list[str]:
    """The plan as a markdown section. It names the owner's players, so it
    belongs in gitignored outputs only (rule #10)."""
    title = "## Streaming plan — " + ", ".join(p.position for p in plan.positions)
    out = [title + (f", {_weeks_text(plan.weeks)}" if plan.weeks else ""), "", WORDING, "",
           LEGEND, ""]
    if not plan.weeks:
        why = ("this table has no per-week lines" if not plan.covered else
               f"week(s) {_span(plan.skipped)} are not in this table "
               f"(weeks {_span(plan.covered)})")
        return out + [f"Nothing to plan: {why}.", ""]
    if plan.skipped:
        out += [f"Not in this table (weeks {_span(plan.covered)}), so not planned: "
                f"week(s) {_span(plan.skipped)}.", ""]
    if not plan.owner_rows:
        out += ["**No row in this table is MINE**, at any position: the owner's roster was "
                "not found in the league snapshot, so what I hold is NOT KNOWN here, never "
                "empty. The free-agent lists stand.", ""]
    for p in plan.positions:
        out += _position_lines(p, plan.top, plan.owner_rows > 0)
    return out
