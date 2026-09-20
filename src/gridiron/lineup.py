"""Legal lineups, kickoff locks and start/sit alternatives.

"Legal" means what Sleeper would accept: every slot filled by a player whose
position tag is eligible for it (FLEX takes RB/WR/TE), nobody in two slots,
nobody moved once his game has kicked off, nobody promoted off the IR slot
without the transaction a human has to make in the app. The optimizer here
never proposes an illegal lineup and never proposes anything it cannot
evaluate:

  * A starter WITHOUT a projection stays where he is. "We do not know what
    he will score" is not evidence that the bench player is better, so the
    slot is frozen and listed with its reason (rule #11's spirit: no
    convenience accessor makes the wrong call easy).
  * A bench player without a projection is never proposed.
  * When the schedule is missing, the lock state is UNKNOWN and the whole
    start/sit section abstains, because a lineup change that Sleeper would
    reject is not an alternative.
  * When the schedule is only PARTLY readable, the abstention is per player:
    anyone whose kickoff could not be established is frozen and named, while
    the players whose games the schedule does time are still optimised. A
    kickoff time is never invented to fill the gap, and a team missing from
    an incomplete week is never read as a bye.

The objective is projected points, and every alternative is reported with
its Δpoints and a z-score against the two players' projection SDs, so that
a 0.4-point edge between two 8-point SDs reads as the coin flip it is. The
ΔP(win) exchange rate is applied by the caller (`gridiron.dashboard`),
which owns the matchup context and the "uncalibrated" label that goes with
it.

For this league's slot structure — dedicated QB/RB/RB/WR/WR/TE/K/DST plus
two FLEX slots whose eligibility is a superset of RB, WR and TE — the
optimal fill is top-k of each dedicated position, then the best remaining
flex-eligible players. Any lineup must hold at least the dedicated counts of
each position, so swapping its dedicated picks for the top ones never
lowers the total, and the flex slots then see the best possible remainder.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import sqrt

import pandas as pd

from gridiron.league_config import FLEX_ELIGIBLE, ROSTER_SLOTS
from gridiron.projection import Projection

#: Sleeper spells team defense "DEF"; the repo spells it "DST".
_SLOT_ALIASES = {"DEF": "DST"}
NON_LINEUP_SLOTS = {"BN", "IR", "TAXI"}
#: A z-score below this is "within noise": the edge is small against the
#: uncertainty of the two projections and the page says so.
NOISE_Z = 0.5


def slot_order(roster_positions: Sequence[str] | None) -> tuple[str, ...]:
    """The lineup slots in Sleeper's order (which is also the order of the
    roster's `starters` list). Falls back to league_config when the snapshot
    does not carry `roster_positions`."""
    if roster_positions:
        out = [_SLOT_ALIASES.get(str(s).upper(), str(s).upper())
               for s in roster_positions]
        return tuple(s for s in out if s not in NON_LINEUP_SLOTS)
    out: list[str] = []
    for slot in ("QB", "RB", "WR", "TE", "FLEX", "K", "DST"):
        out += [slot] * int(ROSTER_SLOTS.get(slot, 0))
    return tuple(out)


def eligible(slot: str, position: str) -> bool:
    pos = _SLOT_ALIASES.get(str(position or "").upper(), str(position or "").upper())
    slot = str(slot).upper()
    if slot == "FLEX":
        return pos in FLEX_ELIGIBLE
    return pos == slot


@dataclass(frozen=True)
class Player:
    """One roster spot as the optimizer sees it.

    Both ids are carried for display and for the archive; the sleeper->gsis
    resolution itself happens upstream through `gridiron.ids.Crosswalk`
    (rule #3) — nothing here maps one id onto the other.
    """

    sleeper_id: str
    name: str
    position: str
    team: str
    projection: Projection
    lineup: str                   # START | BENCH | IR
    locked: bool
    lock_note: str = ""
    availability: str = ""
    flags: tuple[str, ...] = field(default=())
    gsis_id: str = ""
    #: False when the schedule could not prove whether this player's game has
    #: started. `locked` alone cannot carry that: "not locked" would then mean
    #: both "kickoff is in the future" and "we have no idea", and only the
    #: first of those makes a lineup move legal.
    lock_known: bool = True
    #: This player's kickoff in UTC when the schedule gave one, so the action
    #: layer can state a real deadline instead of re-reading the schedule.
    kickoff: datetime | None = None

    @property
    def value(self) -> float | None:
        return self.projection.mean

    @property
    def sd(self) -> float:
        return float(self.projection.sd or 0.0)

    @property
    def projected(self) -> bool:
        return self.projection.usable

    @property
    def movable(self) -> bool:
        """Whether a lineup move involving this player can be shown LEGAL.
        Requires a proven-open kickoff, never merely the absence of a lock."""
        return self.lock_known and not self.locked

    @property
    def lock_reason(self) -> str:
        if self.locked:
            return self.lock_note or "LOCKED"
        if not self.lock_known:
            return self.lock_note or "lock state UNKNOWN"
        return ""


# --------------------------------------------------------------------------
# Kickoff locks
# --------------------------------------------------------------------------
#: The three things we can know about a player's kickoff lock. UNKNOWN is a
#: first-class state, not a flavour of OPEN: "we could not read the schedule"
#: is not evidence that a move is legal, and Sleeper will reject a move made
#: after kickoff no matter what this page believed.
LOCKED, OPEN, UNKNOWN = "LOCKED", "OPEN", "UNKNOWN"


@dataclass(frozen=True)
class Lock:
    """One player's lock state, with the sentence that explains it."""

    state: str
    note: str
    kickoff: datetime | None = None

    @property
    def locked(self) -> bool:
        return self.state == LOCKED

    @property
    def movable(self) -> bool:
        """True ONLY when the schedule proves the game has not started.
        UNKNOWN is not movable — that is the whole point of this type."""
        return self.state == OPEN

    @property
    def known(self) -> bool:
        return self.state != UNKNOWN


@dataclass(frozen=True)
class KickoffIndex:
    """What one week's schedule actually supports, including its holes.

    A plain `team -> kickoff` dict cannot express the two states that matter
    most here: a team whose game we found but could not time, and a week
    whose rows we could not fully read. Both used to collapse into "absent
    from the dict", which `lock_state` then reported as a bye — so an
    unreadable schedule authorised every swap on the board. The holes are
    fields now, and absence only means "bye" when `complete` is True.
    """

    week: int
    kickoffs: Mapping[str, datetime]
    #: Teams with a row for this week whose kickoff time could not be read.
    #: We know they play; we do not know when, so they are never movable.
    time_unknown: frozenset[str]
    #: Every team appearing anywhere in the schedule frame. A team in here
    #: with no row this week is on a BYE; a team not in here at all is a name
    #: this schedule has never heard of, which is not a bye.
    season_teams: frozenset[str]
    #: Week rows that carried no usable team names at all.
    dropped_rows: int
    problems: tuple[str, ...] = field(default=())

    @property
    def complete(self) -> bool:
        """True when every row of this week parsed into a timed game. Only
        then does 'not in the index' mean 'bye'."""
        return not self.time_unknown and self.dropped_rows == 0

    @property
    def games(self) -> int:
        return len(set(self.kickoffs)) // 2 if self.kickoffs else 0

    def summary(self) -> str:
        bits = [f"{len(self.kickoffs)} team(s) timed"]
        if self.time_unknown:
            bits.append(f"{len(self.time_unknown)} with NO kickoff time")
        if self.dropped_rows:
            bits.append(f"{self.dropped_rows} unreadable row(s)")
        return f"week {self.week} schedule: " + ", ".join(bits)


def _team(value: object) -> str:
    t = str(value or "").strip().upper()
    return "" if t in ("", "NAN", "NONE") else t


def kickoff_index(schedule: pd.DataFrame | None, week: int) -> KickoffIndex | None:
    """Read one week's kickoffs. None when there is NO schedule to read.

    Two things this deliberately does NOT do, because both were bugs:

      * It never invents a kickoff time. A row with no `gametime` used to be
        stamped 13:00 ET, which is a guess that reads as fact — and a wrong
        guess in the unsafe direction for every 9:30 am London game and
        every flexed night game, reporting a locked player as movable.
      * It never returns an empty index for a week it could not read. An
        empty dict is indistinguishable from "all 32 teams are on bye", and
        the caller treated `{} is not None` as full lock certainty.
    """
    if schedule is None or len(schedule) == 0 or "week" not in schedule.columns:
        return None
    season_teams = set()
    for col in ("home_team", "away_team"):
        if col in schedule.columns:
            season_teams |= {t for t in (_team(v) for v in schedule[col]) if t}
    wk = schedule.loc[schedule["week"] == int(week)]
    if len(wk) == 0:
        return None
    out: dict[str, datetime] = {}
    time_unknown: set[str] = set()
    problems: list[str] = []
    dropped = 0
    for row in wk.itertuples():
        teams = [t for t in (_team(getattr(row, "home_team", "")),
                             _team(getattr(row, "away_team", ""))) if t]
        day = str(getattr(row, "gameday", "") or "").strip()
        time_ = str(getattr(row, "gametime", "") or "").strip()
        if not teams:
            dropped += 1
            problems.append("a week row carried no team names")
            continue
        if not day or day.lower() == "nan" or not time_ or time_.lower() == "nan":
            time_unknown.update(teams)
            problems.append(f"{'/'.join(teams)}: no kickoff "
                            f"{'date' if not day or day.lower() == 'nan' else 'time'} "
                            f"in the schedule")
            continue
        try:
            stamp = pd.Timestamp(f"{day} {time_}", tz="America/New_York")
        except (ValueError, TypeError) as exc:
            time_unknown.update(teams)
            problems.append(f"{'/'.join(teams)}: unreadable kickoff "
                            f"'{day} {time_}' ({type(exc).__name__})")
            continue
        if pd.isna(stamp):
            time_unknown.update(teams)
            problems.append(f"{'/'.join(teams)}: unreadable kickoff '{day} {time_}'")
            continue
        when = stamp.tz_convert("UTC").to_pydatetime()
        for t in teams:
            out[t] = when
    # A team we timed is not also "unknown".
    time_unknown -= set(out)
    return KickoffIndex(int(week), out, frozenset(time_unknown),
                        frozenset(season_teams), dropped, tuple(problems))


def lock_state(team: str, kickoffs: KickoffIndex | Mapping[str, datetime] | None,
               now: datetime) -> Lock:
    """The lock state of one player's team. Sleeper locks a player at his
    game's kickoff, so every branch that cannot prove the kickoff is in the
    future returns UNKNOWN rather than a permissive default."""
    if kickoffs is None:
        return Lock(UNKNOWN, "lock state UNKNOWN: no schedule loaded for this week")
    if not isinstance(kickoffs, KickoffIndex):     # a bare mapping: legacy callers
        kickoffs = KickoffIndex(0, dict(kickoffs), frozenset(),
                                frozenset(kickoffs), 0, ())
    t = _team(team)
    if not t:
        return Lock(UNKNOWN, "lock state UNKNOWN: no NFL team on this player's record")
    if t in kickoffs.time_unknown:
        return Lock(UNKNOWN, f"lock state UNKNOWN: {t} has a week-{kickoffs.week} game "
                             f"but the schedule carries no usable kickoff time for it")
    when = kickoffs.kickoffs.get(t)
    if when is None:
        if not kickoffs.complete:
            return Lock(UNKNOWN, f"lock state UNKNOWN: {t} has no readable week-"
                                 f"{kickoffs.week} game and the week's schedule is "
                                 f"incomplete ({kickoffs.summary()}), so a bye cannot "
                                 f"be told apart from a row that failed to parse")
        if t not in kickoffs.season_teams:
            return Lock(UNKNOWN, f"lock state UNKNOWN: {t} does not appear anywhere in "
                                 f"this schedule — an unrecognised team is not a bye")
        return Lock(OPEN, f"no week-{kickoffs.week} game: BYE (the week's schedule "
                          f"parsed completely and {t} plays in other weeks)")
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    stamp = when.astimezone(timezone.utc)
    if now >= when:
        return Lock(LOCKED, f"LOCKED — kicked off {stamp:%a %H:%M} UTC", when)
    return Lock(OPEN, f"kicks off {stamp:%a %H:%M} UTC", when)


# --------------------------------------------------------------------------
# The plan
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Alternative:
    """One legal swap: `bench` into `slot`, displacing `starter` (None when
    the slot is empty)."""

    bench: Player
    slot: str
    slot_index: int
    starter: Player | None
    delta_points: float
    z: float

    @property
    def within_noise(self) -> bool:
        return abs(self.z) < NOISE_Z

    def describe(self) -> str:
        who = self.starter.name if self.starter else "an EMPTY slot"
        verdict = ("within noise" if self.within_noise else
                   "favoured" if self.delta_points > 0 else "not favoured")
        return (f"{self.bench.name} ({self.bench.position}) into {self.slot} for "
                f"{who}: {self.delta_points:+.2f} pts, z={self.z:+.2f} ({verdict})")


@dataclass(frozen=True)
class LineupPlan:
    slots: tuple[str, ...]
    current: tuple[Player | None, ...]
    best: tuple[Player | None, ...]
    alternatives: tuple[Alternative, ...]
    frozen: tuple[tuple[Player, str], ...]      # players the optimizer left alone
    abstained: str = ""                          # non-empty = no recommendation at all
    #: Bench players who could legally enter a lineup this week: projected,
    #: provably movable, not on IR. The action layer reads this to name a
    #: BACKUP when the first choice cannot be made.
    bench_pool: tuple[Player, ...] = field(default=())

    @property
    def current_points(self) -> float:
        return sum((p.value or 0.0) for p in self.current if p is not None)

    @property
    def best_points(self) -> float:
        return sum((p.value or 0.0) for p in self.best if p is not None)

    @property
    def improvement(self) -> float:
        return self.best_points - self.current_points

    @property
    def unprojected_starters(self) -> tuple[Player, ...]:
        return tuple(p for p in self.current if p is not None and not p.projected)

    def changes(self) -> tuple[tuple[int, Player | None, Player | None], ...]:
        """(slot index, current occupant, best occupant) where they differ."""
        out = []
        for i, (c, b) in enumerate(zip(self.current, self.best)):
            cid = c.sleeper_id if c else None
            bid = b.sleeper_id if b else None
            if cid != bid:
                out.append((i, c, b))
        return tuple(out)


def current_lineup(players: Sequence[Player], starters: Sequence[str],
                   slots: Sequence[str]) -> tuple[Player | None, ...]:
    """Map Sleeper's positional `starters` list onto the slots. '0' or an
    unknown id is an empty slot."""
    by_id = {p.sleeper_id: p for p in players}
    out: list[Player | None] = []
    for i in range(len(slots)):
        sid = str(starters[i]) if i < len(starters) else "0"
        out.append(by_id.get(sid) if sid not in ("0", "", "None") else None)
    return tuple(out)


def _fill(slots: Sequence[str], fixed: dict[int, Player],
          pool: list[Player]) -> tuple[Player | None, ...]:
    """Top-k per dedicated slot, then FLEX from the remainder."""
    out: list[Player | None] = [fixed.get(i) for i in range(len(slots))]
    remaining = sorted(pool, key=lambda p: (p.value or 0.0), reverse=True)
    used: set[str] = {p.sleeper_id for p in fixed.values()}
    for pass_flex in (False, True):
        for i, slot in enumerate(slots):
            if out[i] is not None or (slot == "FLEX") != pass_flex:
                continue
            for cand in remaining:
                if cand.sleeper_id in used or not eligible(slot, cand.position):
                    continue
                out[i] = cand
                used.add(cand.sleeper_id)
                break
    return tuple(out)


def _stabilize(slots: Sequence[str], current: Sequence[Player | None],
               best: Sequence[Player | None]) -> tuple[Player | None, ...]:
    """Keep every retained starter in the slot he already holds when the
    lineup is equally legal that way. Two RBs trading RB and FLEX for the
    same total is churn, not a change, and the page should not list it."""
    out = list(best)
    where = {p.sleeper_id: i for i, p in enumerate(current) if p is not None}
    for _ in range(len(out) * len(out) + 1):
        moved = False
        for i, p in enumerate(out):
            if p is None:
                continue
            j = where.get(p.sleeper_id)
            if j is None or j == i:
                continue
            other = out[j]
            if eligible(slots[j], p.position) and (
                    other is None or eligible(slots[i], other.position)):
                out[i], out[j] = other, p
                moved = True
                break
        if not moved:
            break
    return tuple(out)


def plan_lineup(players: Sequence[Player], starters: Sequence[str],
                slots: Sequence[str], *, locks_known: bool = True) -> LineupPlan:
    """The best legal lineup reachable from the current one, plus every
    single-swap alternative, or an explicit abstention."""
    slots = tuple(slots)
    current = current_lineup(players, starters, slots)
    frozen: list[tuple[Player, str]] = []

    if not locks_known:
        for p in players:
            if p.lineup == "START":
                frozen.append((p, "lock state unknown"))
        return LineupPlan(slots, current, current, (), tuple(frozen),
                          abstained="lock state UNKNOWN — no schedule for this week, "
                                    "so no lineup change can be verified legal")

    fixed: dict[int, Player] = {}
    for i, p in enumerate(current):
        if p is None:
            continue
        if not p.movable:
            # LOCKED and UNKNOWN both land here. A starter we cannot prove is
            # still movable stays where he is: proposing a swap Sleeper would
            # reject is worse than proposing nothing.
            fixed[i] = p
            frozen.append((p, p.lock_reason))
        elif not p.projected:
            fixed[i] = p
            frozen.append((p, "no projection: " + "; ".join(p.projection.reasons)))
    fixed_ids = {p.sleeper_id for p in fixed.values()}
    pool: list[Player] = []
    for p in players:
        if p.sleeper_id in fixed_ids or p.lineup == "IR":
            continue
        if not p.movable:
            if p.lineup == "BENCH":
                frozen.append((p, p.lock_reason))
            continue
        if not p.projected:
            if p.lineup == "BENCH":
                frozen.append((p, "no projection: " + "; ".join(p.projection.reasons)))
            continue
        pool.append(p)
    best = _stabilize(slots, current, _fill(slots, fixed, pool))

    # Single-swap alternatives: every unlocked, projected bench player into
    # the slot where he displaces the least valuable unlocked, projected
    # starter (or fills an empty one).
    alts: list[Alternative] = []
    starter_ids = {p.sleeper_id for p in current if p is not None}
    for b in players:
        if b.sleeper_id in starter_ids or b.lineup == "IR" or not b.movable \
                or not b.projected:
            continue
        options: list[Alternative] = []
        for i, slot in enumerate(slots):
            if not eligible(slot, b.position):
                continue
            s = current[i]
            if s is not None and (not s.movable or not s.projected):
                continue
            s_val = s.value if s is not None else 0.0
            delta = float(b.value or 0.0) - float(s_val or 0.0)
            denom = sqrt(b.sd ** 2 + (s.sd ** 2 if s is not None else 0.0))
            z = delta / denom if denom > 0 else 0.0
            options.append(Alternative(b, slot, i, s, round(delta, 3), round(z, 3)))
        if options:
            alts.append(max(options, key=lambda a: a.delta_points))
    alts.sort(key=lambda a: a.delta_points, reverse=True)
    bench_pool = tuple(p for p in pool if p.sleeper_id not in starter_ids)
    return LineupPlan(slots, current, best, tuple(alts), tuple(frozen),
                      bench_pool=bench_pool)
