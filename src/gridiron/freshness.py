"""As-of / week / staleness. The module that makes degraded output honest.

Rule #8: the week has a shape — Wed waivers, Fri final designations, Sun
inactives — so "is this data fresh?" is cadence-aware, not one global TTL.
A Thursday-morning injury pull is fine on Tuesday and useless on Sunday at
noon.

Everything here is pure: callers hand in `as_of` timestamps and row counts,
and get back a status plus the sentence to print. Nothing guesses. A source
that is MISSING stays MISSING in the output — it is never filled in with a
neighbouring week, a league average, or a stale value silently reused.

The other half of honesty is the week itself. `WeekContext` separates:
  * `report_week`     — the week the report is ABOUT (from Sleeper state)
  * `stats_through`   — the last week nflverse actually has box scores for
and reports the gap between them, because in-season those two diverge every
single week (Tuesday: report week 3, stats through week 2) and a report that
quietly prints week-2 usage under a "week 3" heading is the exact failure
mode this project exists to avoid.
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from zoneinfo import ZoneInfo

LEAGUE_TZ = ZoneInfo("America/New_York")  # NFL/Sleeper cadence is ET

#: How long after the last kickoff a week is still treated as in progress
#: (a 4:25 pm ET game plus overtime plus stat correction lag).
GAME_WINDOW_HOURS = 4.0


class Status(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    MISSING = "missing"


class Phase(str, Enum):
    """Where the report week sits relative to its own games."""

    PREGAME = "pregame"          # no game of this week has kicked off
    IN_PROGRESS = "in_progress"  # some games played, week not final
    COMPLETE = "complete"        # every game of the week is final


class DayShape(str, Enum):
    """Rule #8's week shape, in ET."""

    WAIVER_DAY = "waiver_day"            # Wed — claims clear ~3 AM ET
    DESIGNATION_DAY = "designation_day"  # Fri — final practice report / game status
    GAMEDAY = "gameday"                  # Thu / Sun / Mon
    PLANNING = "planning"                # Tue / Sat


_WEEKDAY_SHAPE = {
    0: DayShape.GAMEDAY,        # Monday
    1: DayShape.PLANNING,       # Tuesday
    2: DayShape.WAIVER_DAY,     # Wednesday
    3: DayShape.GAMEDAY,        # Thursday
    4: DayShape.DESIGNATION_DAY,  # Friday
    5: DayShape.PLANNING,       # Saturday
    6: DayShape.GAMEDAY,        # Sunday
}


def day_shape(now: datetime) -> DayShape:
    return _WEEKDAY_SHAPE[now.astimezone(LEAGUE_TZ).weekday()]


@dataclass(frozen=True)
class Cadence:
    """How stale a source may be before the report stops trusting it."""

    name: str
    max_age_hours: float
    #: tightened age used on days when the source moves fast (injuries on a
    #: gameday, rosters right after waivers clear). None = same as max_age.
    gameday_max_age_hours: float | None = None
    #: a source that must cover the report week itself, not just the last
    #: completed week (injury reports and schedules are forward-looking).
    forward_looking: bool = False

    def limit_for(self, now: datetime) -> float:
        shape = day_shape(now)
        if self.gameday_max_age_hours is not None and shape in (
            DayShape.GAMEDAY, DayShape.DESIGNATION_DAY, DayShape.WAIVER_DAY
        ):
            return self.gameday_max_age_hours
        return self.max_age_hours


#: The sources the weekly report reads, with the cadence each actually moves
#: at. Measured against the 2026 season: nflverse posts box scores within a
#: day of the last game of a week, PFR snap counts lag those by up to a day,
#: and the injury table carries the CURRENT week's practice reports from
#: Wednesday on.
CADENCES: dict[str, Cadence] = {
    "sleeper_league": Cadence("sleeper_league", max_age_hours=24.0,
                              gameday_max_age_hours=6.0, forward_looking=True),
    # This cadence judges the DESIGNATIONS the player map carries, not its
    # identity fields. The map is fetched at most once a day (Sleeper's own
    # ask; gridiron.sleeper.player_map_budget), and ids, names, teams and
    # positions are served from it whatever its age. `injury_status` is the
    # one field in the cache that can flip an hour before kickoff; it is not
    # week-keyed, so the pull time is the only date it has. 6h on a gameday
    # because "Questionable at Friday's practice" is not a statement about
    # Sunday at 1pm. With one request a day that means the designations are
    # current for about six hours of each game day and the moves resting on
    # them are WITHHELD the rest of it, with a check-in-Sleeper step. That is
    # the honest outcome: no documented free source is fresher (nflverse's
    # injury table updates once a day at 07:00 UTC), and widening this limit
    # to fit the budget would present a day-old status as current.
    "sleeper_players": Cadence("sleeper_players", max_age_hours=24.0,
                               gameday_max_age_hours=6.0),
    "injuries": Cadence("injuries", max_age_hours=48.0,
                        gameday_max_age_hours=12.0, forward_looking=True),
    "schedules": Cadence("schedules", max_age_hours=168.0, forward_looking=True),
    "weekly_stats": Cadence("weekly_stats", max_age_hours=72.0),
    "snap_counts": Cadence("snap_counts", max_age_hours=96.0),
    "crosswalk": Cadence("crosswalk", max_age_hours=336.0),  # 14 days
    # Per-game status (pre-game / in-game / final). Its whole value is being
    # current: a feed that said "in game" half an hour ago says nothing about
    # now, so on a gameday it goes STALE in 30 minutes. Terminal statuses
    # (final, canceled) are monotone and survive staleness; the reader keeps
    # those and demotes the rest to UNKNOWN.
    "game_status": Cadence("game_status", max_age_hours=12.0,
                           gameday_max_age_hours=0.5),
}


@dataclass(frozen=True)
class SourceFreshness:
    name: str
    status: Status
    as_of: datetime | None
    rows: int
    covers_through_week: int | None
    reason: str
    #: Every week this source actually has rows for. `covers_through_week` is
    #: only the last of them, which is why a hole in the middle needs its own
    #: field to be visible at all.
    covered_weeks: tuple[int, ...] = field(default=())
    #: True when the newest thing that happened to this source is a FAILED
    #: refresh. The data below it may still be perfectly good; what is not
    #: good is the assumption that it is current. Carried as a field rather
    #: than left inside `reason` so a gate can test it without reading prose.
    refresh_failed: bool = False

    @property
    def usable(self) -> bool:
        """STALE data is still shown — labelled — because a two-day-old snap
        count beats no snap count. MISSING data is never invented."""
        return self.status is not Status.MISSING

    @property
    def week_gaps(self) -> tuple[int, ...]:
        """Weeks missing from inside the covered range.

        "Covers through week 5" off weeks {1,2,4,5} is true and misleading:
        week 3 is absent, so every season-to-date total built on it is short
        by a week and nothing in the header says so. A gap is a data defect,
        not staleness, and it gets named separately.
        """
        if len(self.covered_weeks) < 2:
            return ()
        lo, hi = self.covered_weeks[0], self.covered_weeks[-1]
        have = set(self.covered_weeks)
        return tuple(w for w in range(lo, hi + 1) if w not in have)

    def coverage(self) -> str:
        """Human-readable covered weeks: 'wk1-5', 'wk1-5 (no wk3)', 'wk2'."""
        if not self.covered_weeks:
            return f"wk{self.covers_through_week}" if self.covers_through_week else "—"
        lo, hi = self.covered_weeks[0], self.covered_weeks[-1]
        span = f"wk{lo}" if lo == hi else f"wk{lo}-{hi}"
        gaps = self.week_gaps
        if gaps:
            span += " (no wk" + ",".join(str(w) for w in gaps) + ")"
        return span

    def line(self) -> str:
        stamp = self.as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") \
            if self.as_of else "never"
        return (f"{self.name:<16} {self.status.value.upper():<8} as-of {stamp}"
                f"  covers {self.coverage()}  {self.reason}")


def age_hours(as_of: datetime | None, now: datetime) -> float | None:
    if as_of is None:
        return None
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    return (now - as_of).total_seconds() / 3600.0


def assess(
    name: str,
    *,
    now: datetime,
    as_of: datetime | None,
    rows: int,
    covers_through_week: int | None = None,
    required_week: int | None = None,
    cadence: Cadence | None = None,
    covered_weeks: Sequence[int] = (),
) -> SourceFreshness:
    """Classify one source. Never raises — a broken source is a report line."""
    cad = cadence or CADENCES.get(name) or Cadence(name, max_age_hours=72.0)
    weeks = tuple(sorted({int(w) for w in covered_weeks}))
    if as_of is None or rows <= 0:
        why = "not pulled" if as_of is None else "pulled but empty"
        return SourceFreshness(name, Status.MISSING, as_of, rows,
                               covers_through_week, why, weeks)

    age = age_hours(as_of, now) or 0.0
    limit = cad.limit_for(now)
    if age > limit:
        return SourceFreshness(
            name, Status.STALE, as_of, rows, covers_through_week,
            f"pulled {age:.0f}h ago, over the {limit:.0f}h {day_shape(now).value} limit",
            weeks,
        )

    if (cad.forward_looking and required_week is not None
            and covers_through_week is not None
            and covers_through_week < required_week):
        return SourceFreshness(
            name, Status.STALE, as_of, rows, covers_through_week,
            f"fresh pull but only covers week {covers_through_week}, "
            f"report is for week {required_week}",
            weeks,
        )

    return SourceFreshness(name, Status.FRESH, as_of, rows, covers_through_week,
                           f"pulled {age:.0f}h ago", weeks)


def expires_at(source: SourceFreshness, now: datetime,
               cadence: Cadence | None = None) -> datetime | None:
    """The first instant at or after `now` at which `assess` would call this
    FRESH source STALE on age, or None when it is not FRESH now.

    A page is built once and read for hours; "fresh at build" says nothing
    about the moment it is read. The limit is piecewise constant (it
    tightens at a league-timezone midnight that starts a game day) and the
    age only grows, so the answer is the earliest of: the plain limit, the
    game-day limit, and each midnight at which the game-day limit takes over
    from a longer one. Coverage (forward-looking sources) does not move with
    the clock and is not considered here.
    """
    if source.status is not Status.FRESH or source.as_of is None:
        return None
    cad = cadence or CADENCES.get(source.name) or Cadence(source.name, max_age_hours=72.0)
    as_of = _aware(source.as_of)
    now = _aware(now)
    hard = as_of + timedelta(hours=cad.max_age_hours)
    instants = {hard}
    if cad.gameday_max_age_hours is not None:
        instants.add(as_of + timedelta(hours=cad.gameday_max_age_hours))
        day = now.astimezone(LEAGUE_TZ).date() + timedelta(days=1)
        while True:
            midnight = datetime(day.year, day.month, day.day, tzinfo=LEAGUE_TZ)
            if midnight >= hard:
                break
            instants.add(midnight.astimezone(timezone.utc))
            day += timedelta(days=1)
    for t in sorted(instants):
        if t >= now and (age_hours(as_of, t) or 0.0) >= cad.limit_for(t):
            return t.astimezone(timezone.utc)
    return hard.astimezone(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def phase_for(kickoffs: Sequence[datetime], now: datetime,
              window_hours: float = GAME_WINDOW_HOURS) -> Phase:
    """PREGAME / IN_PROGRESS / COMPLETE for one week's slate of kickoffs."""
    if not kickoffs:
        return Phase.PREGAME
    ks = sorted(_aware(k) for k in kickoffs)
    if now < ks[0]:
        return Phase.PREGAME
    if now >= ks[-1] + timedelta(hours=window_hours):
        return Phase.COMPLETE
    return Phase.IN_PROGRESS


@dataclass(frozen=True)
class WeekContext:
    """Which season/week the report is about, and how far the data lags it."""

    season: int
    report_week: int
    stats_through: int | None
    phase: Phase
    now: datetime
    notes: tuple[str, ...] = field(default=())
    #: Weeks the cache holds that sit AFTER this report's as-of boundary.
    #: They are recorded so the report can say what it refused to read.
    withheld_weeks: tuple[int, ...] = field(default=())

    @property
    def stats_lag_weeks(self) -> int | None:
        """How many weeks behind the report week the box scores are.

        0 on a Tuesday in week N is IMPOSSIBLE mid-week — the report week is
        the upcoming one, so a lag of 1 is the normal healthy state and the
        report says so rather than pretending week N data exists.
        """
        if self.stats_through is None:
            return None
        return max(0, self.report_week - self.stats_through)

    @property
    def expected_lag(self) -> int:
        """The healthy lag for this phase: 0 once the week's games are final,
        1 before them."""
        return 0 if self.phase is Phase.COMPLETE else 1

    @property
    def evidence_boundary(self) -> int:
        """The last week whose box scores this report is ALLOWED to read.

        This is the chronological as-of line. A report about week N that is
        rendered before week N's games are final may read weeks < N and
        nothing else; once the slate is complete week N itself becomes
        evidence. Re-rendering an old week later must produce the same
        answer as it did that week, so the boundary is derived from the
        report week and the phase — never from what happens to be sitting
        in the cache.
        """
        return self.report_week - self.expected_lag

    @property
    def is_historical(self) -> bool:
        """True when the cache holds weeks this report is not allowed to see,
        i.e. we are re-rendering a week that the season has moved past."""
        return bool(self.withheld_weeks)

    @property
    def rolled_over(self) -> bool:
        """True when the box scores are further behind than the phase allows —
        the week ticked over and the data has not caught up yet."""
        lag = self.stats_lag_weeks
        return lag is not None and lag > self.expected_lag

    def headline(self) -> str:
        wk = f"{self.season} week {self.report_week} ({self.phase.value})"
        if self.stats_through is None:
            return f"{wk} — NO box scores loaded"
        return (f"{wk} — usage/box scores through week {self.stats_through} "
                f"(lag {self.stats_lag_weeks}w)")

    @classmethod
    def build(cls, *, season: int, report_week: int,
              stats_weeks: Iterable[int], kickoffs: Sequence[datetime],
              now: datetime) -> "WeekContext":
        weeks = sorted({int(w) for w in stats_weeks})
        phase = phase_for(kickoffs, now)
        boundary = int(report_week) - (0 if phase is Phase.COMPLETE else 1)

        # THE CHRONOLOGICAL CUT. Everything after the boundary is dropped here,
        # before any aggregation sees it, so a week-2 report rendered in week 6
        # reads exactly the weeks it could have read in week 2. Truncating
        # later (in season_to_date) would be a second line of defence; doing it
        # here means the report's own headline cannot overstate its coverage.
        admissible = [w for w in weeks if w <= boundary]
        withheld = tuple(w for w in weeks if w > boundary)
        stats_through = admissible[-1] if admissible else None

        notes: list[str] = []
        if withheld:
            notes.append(
                f"as-of boundary week {boundary}: the cache also holds week(s) "
                f"{', '.join(str(w) for w in withheld)}, which are WITHHELD "
                f"from this week-{report_week} report — later results must not "
                f"inform an earlier week")
            notes.append(
                "re-rendered after the fact: injury designations and market "
                "lines come from the LATEST pull, so they reflect what is "
                "known now, not what was known before kickoff")
        if stats_through is None:
            notes.append(
                "no nflverse box scores this report may read — usage columns "
                "are blank, not zero"
                + (f" (the cache starts at week {weeks[0]}, after the "
                   f"boundary)" if weeks else ""))
        ctx = cls(season, report_week, stats_through, phase, now, tuple(notes),
                  withheld)
        if ctx.rolled_over:
            notes.append(
                f"week rolled over to {report_week} but box scores stop at "
                f"week {stats_through} — usage shown is {ctx.stats_lag_weeks} "
                f"week(s) old")
            ctx = cls(season, report_week, stats_through, phase, now,
                      tuple(notes), withheld)
        return ctx


def degradations(sources: Iterable[SourceFreshness],
                 ctx: WeekContext) -> tuple[str, ...]:
    """Every reason this report is less than fully trustworthy, in one list.

    The weekly report prints this block before any numbers. An empty tuple is
    the only thing that means 'all inputs current'.
    """
    out: list[str] = list(ctx.notes)
    for s in sources:
        if s.status is Status.MISSING:
            out.append(f"{s.name}: MISSING ({s.reason}) — related columns are blank")
        elif s.status is Status.STALE:
            out.append(f"{s.name}: STALE ({s.reason}) — shown but not current")
        if s.week_gaps:
            out.append(
                f"{s.name}: GAP — no rows for week(s) "
                f"{', '.join(str(w) for w in s.week_gaps)} inside a range that "
                f"reaches week {s.covers_through_week}; season totals built on "
                f"it are short by those weeks")
    return tuple(out)
