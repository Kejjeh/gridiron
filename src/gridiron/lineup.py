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

    A plain `team -> kickoff` dict cannot express the states that matter most
    here: a team whose game we found but could not time, a week whose rows we
    could not fully read, and — the one this type exists for — a week whose
    rows all parsed and which may STILL be missing games nobody told us about.

    Parsing every row we were handed is not evidence that we were handed every
    row. A truncated upstream pull produces a frame in which every row is
    perfectly well formed, so "no parse errors" is a statement about the rows
    that arrived and says nothing about the ones that did not. That is why
    absence from `kickoffs` never means BYE on its own: a bye is asserted only
    for a team in `proven_bye`, which requires positive evidence (below).
    """

    week: int
    kickoffs: Mapping[str, datetime]
    #: Teams with a row for this week whose kickoff time could not be read, or
    #: whose row was damaged enough that its time cannot be trusted either.
    #: We know they play; we do not know when, so they are never movable.
    time_unknown: frozenset[str]
    #: Every team appearing anywhere in the schedule frame. A team not in here
    #: at all is a name this schedule has never heard of, which is not a bye.
    season_teams: frozenset[str]
    #: Week rows that carried no usable team names at all.
    dropped_rows: int
    problems: tuple[str, ...] = field(default=())
    #: Teams whose absence from this week is BACKED BY EVIDENCE: the frame
    #: shows them playing in some week before this one AND some week after it,
    #: so the gap is a scheduled bye rather than the edge of a truncated pull.
    #: A team absent from the week and absent from this set is UNKNOWN.
    proven_bye: frozenset[str] = field(default=frozenset())
    #: Rows naming exactly one team — half a game. The row is demonstrably
    #: damaged, so its kickoff is not trusted either (see `kickoff_index`).
    partial_rows: int = 0
    #: Teams given two different kickoff times by two rows of the same week.
    #: The schedule contradicts itself about them, so neither time is used.
    conflicting: frozenset[str] = field(default=frozenset())
    #: Weeks the frame carries rows for at all, and the largest number of
    #: teams any one of them fields. A week well below that maximum is not
    #: proof of truncation (byes shorten weeks legitimately) but it is the
    #: number a reader needs to judge the week for themselves.
    frame_weeks: tuple[int, ...] = field(default=())
    frame_max_slate: int = 0

    @property
    def rows_intact(self) -> bool:
        """True when every row of this week that arrived parsed into a fully
        named, uniquely timed game. Says nothing about rows that never came."""
        return (not self.time_unknown and not self.dropped_rows
                and not self.partial_rows and not self.conflicting)

    @property
    def complete(self) -> bool:
        """Retained name, corrected meaning: the week's rows are intact AND
        the frame extends past this week, which is the only condition under
        which absence can be reasoned about at all. Callers use it to decide
        whether to warn; only `proven_bye` decides whether a player may move.
        """
        return self.rows_intact and self.extends_past

    @property
    def extends_past(self) -> bool:
        """The frame carries at least one week after this one. Without that,
        no absence can be bracketed and every absent team stays UNKNOWN."""
        return any(w > self.week for w in self.frame_weeks)

    @property
    def teams_playing(self) -> int:
        return len(set(self.kickoffs) | set(self.time_unknown))

    @property
    def slate_short(self) -> int:
        """How many fewer teams this week fields than the frame's busiest
        week. Non-zero is normal on a bye week and expected at the end of a
        truncated pull; it is reported, never acted on by itself."""
        return max(0, self.frame_max_slate - self.teams_playing)

    @property
    def games(self) -> int:
        return len(set(self.kickoffs)) // 2 if self.kickoffs else 0

    def summary(self) -> str:
        bits = [f"{len(self.kickoffs)} team(s) timed"]
        if self.time_unknown:
            bits.append(f"{len(self.time_unknown)} with NO usable kickoff time")
        if self.conflicting:
            bits.append(f"{len(self.conflicting)} with CONFLICTING times")
        if self.partial_rows:
            bits.append(f"{self.partial_rows} half-named row(s)")
        if self.dropped_rows:
            bits.append(f"{self.dropped_rows} unreadable row(s)")
        if not self.extends_past:
            bits.append("frame ends at this week, so no absence can be "
                        "confirmed as a bye")
        elif self.slate_short:
            bits.append(f"{self.slate_short} team(s) absent, "
                        f"{len(self.proven_bye)} of them confirmed on bye")
        return f"week {self.week} schedule: " + ", ".join(bits)


def _team(value: object) -> str:
    t = str(value or "").strip().upper()
    return "" if t in ("", "NAN", "NONE") else t


def _frame_team_weeks(schedule: pd.DataFrame) -> dict[str, set[int]]:
    """Every week each team is shown playing, anywhere in the frame.

    This is the evidence base for a bye. It reads only rows that name the
    team and carry a usable week number; a row's kickoff being unreadable
    does not stop it proving that the team had a game that week.
    """
    out: dict[str, set[int]] = {}
    if "week" not in schedule.columns:
        return out
    cols = [c for c in ("home_team", "away_team") if c in schedule.columns]
    for row in schedule.itertuples():
        try:
            wk = int(getattr(row, "week"))
        except (TypeError, ValueError):
            continue
        for col in cols:
            t = _team(getattr(row, col, ""))
            if t:
                out.setdefault(t, set()).add(wk)
    return out


def kickoff_index(schedule: pd.DataFrame | None, week: int) -> KickoffIndex | None:
    """Read one week's kickoffs. None when there is NO schedule to read.

    What this deliberately does NOT do, because each one was a bug:

      * It never invents a kickoff time. A row with no `gametime` used to be
        stamped 13:00 ET, which is a guess that reads as fact — and a wrong
        guess in the unsafe direction for every 9:30 am London game and
        every flexed night game, reporting a locked player as movable.
      * It never returns an empty index for a week it could not read. An
        empty dict is indistinguishable from "all 32 teams are on bye", and
        the caller treated `{} is not None` as full lock certainty.
      * It never treats a clean parse as proof of a complete slate. A frame
        holding BUF/MIA in week 2 and only NYJ/NE in week 3 parses without a
        single error, and used to make BUF a proven bye in week 3 — so every
        swap involving a Buffalo player was offered as legal off a schedule
        that simply stopped early. A bye is now asserted only for a team the
        frame shows playing on BOTH sides of the gap.
      * It never silently resolves a contradiction. Two rows giving one team
        two different kickoffs mean the schedule does not know when that team
        plays, so neither time is used and the team is UNKNOWN.
      * It never trusts half a game. A row naming one team is damaged; its
        `gametime` field is no more trustworthy than its missing team field,
        so the named team is timed by no row at all.
    """
    if schedule is None or len(schedule) == 0 or "week" not in schedule.columns:
        return None
    team_weeks = _frame_team_weeks(schedule)
    season_teams = set(team_weeks)
    frame_weeks = tuple(sorted({w for weeks in team_weeks.values() for w in weeks}))
    frame_max_slate = 0
    for w in frame_weeks:
        frame_max_slate = max(frame_max_slate,
                              sum(1 for weeks in team_weeks.values() if w in weeks))

    wk = schedule.loc[schedule["week"] == int(week)]
    if len(wk) == 0:
        return None

    out: dict[str, datetime] = {}
    time_unknown: set[str] = set()
    conflicting: set[str] = set()
    problems: list[str] = []
    dropped = 0
    partial = 0
    for row in wk.itertuples():
        home, away = (_team(getattr(row, "home_team", "")),
                      _team(getattr(row, "away_team", "")))
        teams = [t for t in (home, away) if t]
        day = str(getattr(row, "gameday", "") or "").strip()
        time_ = str(getattr(row, "gametime", "") or "").strip()
        if not teams:
            dropped += 1
            problems.append("a week row carried no team names")
            continue
        if len(teams) == 1:
            # Half a game. We cannot say who the opponent is, and a row that
            # lost one of its two required team names is not a row whose
            # kickoff field we should believe either.
            partial += 1
            time_unknown.update(teams)
            problems.append(f"{teams[0]}: the schedule row names only one team, "
                            f"so the game is half-recorded and its kickoff is "
                            f"not trusted")
            continue
        if home == away:
            dropped += 1
            problems.append(f"{home}: a week row lists the same team on both "
                            f"sides, which is not a game")
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
            prior = out.get(t)
            if prior is not None and prior != when:
                conflicting.add(t)
                problems.append(
                    f"{t}: two week-{int(week)} rows give different kickoffs "
                    f"({prior:%a %H:%M} and {when:%a %H:%M} UTC), so the "
                    f"schedule does not establish when {t} plays")
                continue
            out[t] = when
    # A contradicted team keeps neither time.
    for t in conflicting:
        out.pop(t, None)
    time_unknown |= conflicting
    # A team we timed is not also "unknown".
    time_unknown -= set(out)

    # A bye needs evidence on both sides of the gap. Without a later week in
    # the frame nothing can be bracketed, and every absent team stays UNKNOWN.
    playing = set(out) | time_unknown
    proven_bye = {t for t, weeks in team_weeks.items()
                  if t not in playing
                  and any(w < int(week) for w in weeks)
                  and any(w > int(week) for w in weeks)}

    return KickoffIndex(int(week), out, frozenset(time_unknown),
                        frozenset(season_teams), dropped, tuple(problems),
                        frozenset(proven_bye), partial, frozenset(conflicting),
                        frame_weeks, frame_max_slate)


def lock_state(team: str, kickoffs: KickoffIndex | Mapping[str, datetime] | None,
               now: datetime) -> Lock:
    """The lock state of one player's team. Sleeper locks a player at his
    game's kickoff, so every branch that cannot prove the kickoff is in the
    future returns UNKNOWN rather than a permissive default."""
    if kickoffs is None:
        return Lock(UNKNOWN, "lock state UNKNOWN: no schedule loaded for this week")
    if not isinstance(kickoffs, KickoffIndex):     # a bare mapping: legacy callers
        # A bare mapping carries no frame to bracket an absence against, so a
        # team missing from it is UNKNOWN, never a bye. Legacy callers only.
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
        if t not in kickoffs.season_teams:
            return Lock(UNKNOWN, f"lock state UNKNOWN: {t} does not appear anywhere in "
                                 f"this schedule — an unrecognised team is not a bye")
        if not kickoffs.rows_intact:
            return Lock(UNKNOWN, f"lock state UNKNOWN: {t} has no readable week-"
                                 f"{kickoffs.week} game and the week's rows are "
                                 f"damaged ({kickoffs.summary()}), so a bye cannot "
                                 f"be told apart from a row that failed to parse")
        if t not in kickoffs.proven_bye:
            return Lock(UNKNOWN, f"lock state UNKNOWN: {t} has no week-{kickoffs.week} "
                                 f"row, and this schedule does not show {t} playing "
                                 f"both before and after week {kickoffs.week} "
                                 f"({kickoffs.summary()}). Every row that arrived "
                                 f"parsed, which is not evidence that every row "
                                 f"arrived, so the gap is unexplained rather than "
                                 f"a proven bye")
        return Lock(OPEN, f"no week-{kickoffs.week} game: BYE (this schedule shows "
                          f"{t} playing before and after week {kickoffs.week}, and "
                          f"the week's rows are intact)")
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
