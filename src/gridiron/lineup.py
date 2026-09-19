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

    @property
    def value(self) -> float | None:
        return self.projection.mean

    @property
    def sd(self) -> float:
        return float(self.projection.sd or 0.0)

    @property
    def projected(self) -> bool:
        return self.projection.usable


# --------------------------------------------------------------------------
# Kickoff locks
# --------------------------------------------------------------------------
def kickoff_index(schedule: pd.DataFrame | None, week: int) -> dict[str, datetime] | None:
    """team -> kickoff (UTC) for one week. None when there is NO schedule to
    read, which is a different fact from 'every team is on bye'."""
    if schedule is None or len(schedule) == 0 or "week" not in schedule.columns:
        return None
    wk = schedule.loc[schedule["week"] == int(week)]
    if len(wk) == 0:
        return None
    out: dict[str, datetime] = {}
    for row in wk.itertuples():
        day = str(getattr(row, "gameday", "") or "")
        time_ = str(getattr(row, "gametime", "") or "")
        if not day or day == "nan":
            continue
        try:
            stamp = pd.Timestamp(f"{day} {time_ if time_ and time_ != 'nan' else '13:00'}",
                                 tz="America/New_York")
        except (ValueError, TypeError):
            continue
        when = stamp.tz_convert("UTC").to_pydatetime()
        out[str(row.home_team)] = when
        out[str(row.away_team)] = when
    return out


def lock_state(team: str, kickoffs: Mapping[str, datetime] | None,
               now: datetime) -> tuple[bool, str]:
    """(locked, note). Sleeper locks a player at his game's kickoff."""
    if kickoffs is None:
        return False, "lock state UNKNOWN: no schedule loaded"
    when = kickoffs.get(str(team or ""))
    if when is None:
        return False, "no game this week (bye or unknown team)"
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if now >= when:
        return True, f"LOCKED — kicked off {when.astimezone(timezone.utc):%a %H:%M} UTC"
    return False, f"kicks off {when.astimezone(timezone.utc):%a %H:%M} UTC"


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
        if p.locked:
            fixed[i] = p
            frozen.append((p, p.lock_note))
        elif not p.projected:
            fixed[i] = p
            frozen.append((p, "no projection: " + "; ".join(p.projection.reasons)))
    fixed_ids = {p.sleeper_id for p in fixed.values()}
    pool: list[Player] = []
    for p in players:
        if p.sleeper_id in fixed_ids or p.lineup == "IR":
            continue
        if p.locked:
            if p.lineup == "BENCH":
                frozen.append((p, p.lock_note))
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
        if b.sleeper_id in starter_ids or b.lineup == "IR" or b.locked or not b.projected:
            continue
        options: list[Alternative] = []
        for i, slot in enumerate(slots):
            if not eligible(slot, b.position):
                continue
            s = current[i]
            if s is not None and (s.locked or not s.projected):
                continue
            s_val = s.value if s is not None else 0.0
            delta = float(b.value or 0.0) - float(s_val or 0.0)
            denom = sqrt(b.sd ** 2 + (s.sd ** 2 if s is not None else 0.0))
            z = delta / denom if denom > 0 else 0.0
            options.append(Alternative(b, slot, i, s, round(delta, 3), round(z, 3)))
        if options:
            alts.append(max(options, key=lambda a: a.delta_points))
    alts.sort(key=lambda a: a.delta_points, reverse=True)
    return LineupPlan(slots, current, best, tuple(alts), tuple(frozen))
