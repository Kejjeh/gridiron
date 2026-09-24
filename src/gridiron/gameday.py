"""Game Day: the Sunday screen, built from platform-observed actuals.

The weekly board (`gridiron.dashboard`) answers "what should I do before
kickoff?". This module answers the five questions the owner has on a phone
once the games are under way, in the order they are asked:

  1. Am I ahead, and how current is that? — the platform's own matchup
     totals, dated to the snapshot or the refresh that fetched them.
  2. Who on each side is yet to play, playing, finished, or unknown? — per
     starter, from a per-game status feed, never from the clock.
  3. What can I still legally do? — only moves Sleeper would accept now:
     both players proven unlocked, still on the roster, lineup unchanged.
  4. What changed since my last reliable snapshot? — like-for-like diffs
     of the same source kind, with corrections named as corrections.
  5. What did the pregame board actually advise, and what is still
     unproven? — the archived decision-time record, joined by season, week,
     league and roster, never reconstructed.

WHAT IS AN ACTUAL. Every number on this page comes from Sleeper's matchup
rows: `points`, `starters_points` and `players_points` are what the platform
scored, including kickers and defenses the projection model cannot support,
including zero, including negative, including a commissioner override
(`custom_points`). A value the platform did not send is UNKNOWN, not zero.
The page reconciles the starters' sum against the platform total and
DISCLOSES any difference; it never edits a total to make them agree.

WHAT IS A GAME STATUS. A schedule gives an expected kickoff. It does not say
whether the game started on time, is in overtime, was suspended, or ended.
Those come only from an observed status (`livesync.GAME_STATUS_NAME`); when
that feed is absent or older than its cadence, a non-terminal status is
UNKNOWN and the page says so. Elapsed hours, a zero, and an absent row are
never read as "final", "bye" or "not playing". Game status, injury status,
roster eligibility and the kickoff lock are four separate columns.

WHAT IS NOT HERE. No live win probability: the pregame board's UNCALIBRATED
number is shown as pregame context and is not updated by the score. No
"remaining projection": points already earned are never added to a
projection, and a full-game projection is never scaled by the clock. No
inference that the owner acted on advice: the live lineup is an observation.
No hindsight: the current injury designation cannot show what was knowable
before kickoff, and a bench player outscoring a starter is descriptive until
both games are final and the decision-time record is in hand.
"""
from __future__ import annotations

import html
import json
import re
import secrets
from urllib.parse import urlsplit
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gridiron.decisions import (ARCHIVE_RE, list_archives, read_archive,
                                record_digest)
from gridiron import theme
from gridiron.freshness import CADENCES, LEAGUE_TZ, SourceFreshness, Status
from gridiron.ids import TEAM_ALIASES, is_dst_id, nflverse_team, normalize_id
from gridiron.league_config import FLEX_ELIGIBLE, LEAGUE_NAME
from gridiron.lineup import (LOCKED, OPEN, KickoffIndex, eligible, kickoff_index,
                             lock_state, slot_order)
from gridiron.livesync import GAME_STATUS_NAME, OBSERVED_GAME_STATUSES
from gridiron.sleeper import BASE as SLEEPER_BASE
from gridiron.sleeper import SCHEDULE_BASE

# --------------------------------------------------------------------------
# Vocabulary
# --------------------------------------------------------------------------
NOT_STARTED = "NOT STARTED"
PLAYING = "PLAYING"
FINAL = "FINAL"
SUSPENDED = "SUSPENDED"
CANCELED = "CANCELED"
UNKNOWN = "UNKNOWN"
#: A slot with no player in it. Sleeper's sentinel is the string "0".
EMPTY = "EMPTY"

#: Feed word -> page state. Only words actually observed in the feed are
#: mapped; anything else is UNKNOWN with the raw word shown beside it.
_FEED_STATE = {"pre_game": NOT_STARTED, "in_game": PLAYING, "complete": FINAL,
               "suspended": SUSPENDED, "canceled": CANCELED}
assert set(_FEED_STATE) == set(OBSERVED_GAME_STATUSES)
#: States that cannot move backwards, so an old feed is still right about them.
TERMINAL = frozenset({FINAL, CANCELED})
#: The order the remaining-exposure line lists states in.
_STATE_ORDER = (NOT_STARTED, PLAYING, SUSPENDED, UNKNOWN, FINAL, CANCELED, EMPTY)

#: Sleeper's empty-slot sentinel.
EMPTY_SENTINEL = "0"

#: How far apart two platform numbers may sit before the page calls them
#: different. Sleeper scores to two decimals.
POINTS_EPS = 0.005

#: Sleeper designations under which a player is never OFFERED as a start.
#: Questionable and Doubtful are deliberately absent (rule #11): a doubtful
#: player is eligible, the card says the word, and the owner decides.
NOT_STARTABLE = frozenset({"OUT", "IR", "PUP", "SUS", "NA", "COV", "DNR"})
#: The sources a lineup move's legality rests on: the lineup and roster, the
#: positions and designations, the kickoff times. The SCORE displays on the
#: league snapshot's own freshness; ADVICE needs all three current, and a
#: refresh in the browser renews only the first.
LEGALITY_SOURCES = ("sleeper_league", "sleeper_players", "schedules")
#: A record may claim a generation time this far ahead of the clock before
#: it is refused as a record from the future (device clocks drift a little).
FUTURE_SLACK = timedelta(minutes=5)


def team_key(value: object) -> str:
    """ONE spelling for a team on both sides of every join here: the feed's
    `home`/`away`, the player record's `team` and the schedule's team
    columns all pass through `ids.nflverse_team`, which is the repo's single
    alias table (LAR -> LA, OAK -> LV). Never compared raw."""
    return nflverse_team(value)


def _utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _parse_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    except ValueError:
        return None


def _stamp(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if dt else "unknown time"


def _age_text(then: datetime | None, now: datetime) -> str:
    if then is None:
        return "age unknown"
    secs = max(0.0, (now - then).total_seconds())
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 5400:
        return f"{int(secs // 60)} min ago"
    if secs < 172800:
        return f"{secs / 3600:.1f} h ago"
    return f"{secs / 86400:.1f} days ago"


def _num(value: object) -> float | None:
    """A platform number, or None. Booleans are not numbers; strings are not
    numbers even when they look like them — the platform sends floats."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    f = float(value)
    return f if f == f else None            # NaN is not a score


def _pts(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


# --------------------------------------------------------------------------
# Game status
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class GameStatus:
    """What is known about one team's game this week, and how."""

    team: str
    state: str                       # NOT STARTED | PLAYING | FINAL | SUSPENDED | CANCELED | UNKNOWN
    raw: str = ""                    # the feed's own word, verbatim
    opponent: str = ""
    home: bool | None = None
    date: str = ""
    note: str = ""
    #: True when the state comes from a feed within its cadence, or is
    #: terminal (a final does not un-final). False means "was <raw> at <as-of>".
    current: bool = False

    @property
    def settled(self) -> bool:
        return self.state in TERMINAL

    @property
    def pending(self) -> bool:
        return self.state in (NOT_STARTED, PLAYING, SUSPENDED, UNKNOWN)


@dataclass(frozen=True)
class GameFeed:
    """The week's rows of the per-game status feed, as the page may use them."""

    present: bool
    as_of: datetime | None
    fresh: bool
    reason: str
    week: int
    rows: tuple[dict, ...] = field(default=())

    @classmethod
    def from_blob(cls, blob: object, *, week: int, freshness: SourceFreshness | None,
                  now: datetime) -> "GameFeed":
        if not isinstance(blob, Mapping):
            return cls(False, None, False, "no game-status feed in the cache; a "
                       "kickoff time alone cannot prove a game started or ended",
                       week)
        as_of = _parse_dt(blob.get("as_of")) or (freshness.as_of if freshness else None)
        fresh = freshness is not None and freshness.status is Status.FRESH
        if freshness is None:
            reason = "feed freshness was never assessed"
        elif freshness.status is Status.FRESH:
            reason = f"observed {_age_text(as_of, now)}"
        else:
            reason = f"feed is {freshness.status.value.upper()}: {freshness.reason}"
        rows = tuple(r for r in (blob.get("games") or []) if isinstance(r, Mapping)
                     and _as_week(r.get("week")) == int(week))
        return cls(True, as_of, fresh, reason, week, rows)

    def status_for(self, team: str) -> GameStatus:
        t = team_key(team)
        if not t:
            return GameStatus("", UNKNOWN, note="no NFL team on this player's record")
        if not self.present:
            return GameStatus(t, UNKNOWN, note=self.reason)
        hits = [r for r in self.rows
                if team_key(r.get("home")) == t or team_key(r.get("away")) == t]
        if not hits:
            return GameStatus(t, UNKNOWN,
                              note=f"the feed lists no week-{self.week} game for {t}; "
                                   f"a missing row is not a bye and not a final")
        if len(hits) > 1:
            words = sorted({str(r.get("status")) for r in hits})
            if len(words) > 1:
                return GameStatus(t, UNKNOWN,
                                  note=f"the feed lists {len(hits)} week-{self.week} "
                                       f"games for {t} with different statuses "
                                       f"({', '.join(words)}); neither is trusted")
        row = hits[0]
        raw = str(row.get("status") or "")
        state = _FEED_STATE.get(raw, UNKNOWN)
        home = team_key(row.get("home")) == t
        opp = team_key(row.get("away") if home else row.get("home"))
        stamp = _stamp(self.as_of)
        if state == UNKNOWN:
            return GameStatus(t, UNKNOWN, raw, opp, home, str(row.get("date") or ""),
                              f'the feed reports "{raw}", a status this page does not '
                              f'know; nothing is assumed from it', current=self.fresh)
        if state in TERMINAL:
            return GameStatus(t, state, raw, opp, home, str(row.get("date") or ""),
                              f"feed: {raw} (observed {stamp})", current=True)
        if not self.fresh:
            return GameStatus(t, UNKNOWN, raw, opp, home, str(row.get("date") or ""),
                              f"the feed said {raw} at {stamp}, which is too old to "
                              f"say anything about now ({self.reason})", current=False)
        return GameStatus(t, state, raw, opp, home, str(row.get("date") or ""),
                          f"feed: {raw} (observed {stamp})", current=True)


def _as_week(value: object) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------
# Starters and sides
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class StarterView:
    slot: str
    sleeper_id: str
    name: str
    position: str
    team: str
    points: float | None             # the platform's number; None = not sent
    points_note: str
    game: GameStatus
    lock: str                        # LOCKED | OPEN | UNKNOWN
    lock_note: str
    kickoff: datetime | None
    injury: str                      # current designation, or ""
    injury_note: str
    empty: bool = False
    named: bool = True               # False when the cached player dump lacks him
    #: Every position the platform lists him at (`fantasy_positions`), so a
    #: FLEX or multi-position eligibility is checked against the list and not
    #: against the one tag the roster shows.
    positions: tuple[str, ...] = field(default=())

    @property
    def state(self) -> str:
        return EMPTY if self.empty else self.game.state

    def record(self) -> dict:
        return {"slot": self.slot, "sleeper_id": self.sleeper_id, "name": self.name,
                "position": self.position, "team": self.team, "points": self.points,
                "points_note": self.points_note, "state": self.state,
                "game_raw": self.game.raw, "game_note": self.game.note,
                "game_current": self.game.current, "opponent": self.game.opponent,
                "lock": self.lock, "lock_note": self.lock_note,
                "kickoff": self.kickoff.isoformat() if self.kickoff else None,
                "injury": self.injury, "injury_note": self.injury_note,
                "empty": self.empty, "named": self.named,
                "positions": list(self.positions)}


@dataclass(frozen=True)
class SideView:
    roster_id: int | None
    label: str
    starters: tuple[StarterView, ...]
    bench: tuple[StarterView, ...]
    platform_points: float | None    # `points` as sent, or the override
    override: float | None           # `custom_points` when the platform set one
    starters_sum: float | None       # sum of the starters' known values
    unknown_points: int              # starters whose value the platform did not send
    reconciliation: str
    notes: tuple[str, ...] = field(default=())

    @property
    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.starters:
            out[s.state] = out.get(s.state, 0) + 1
        return out

    @property
    def pending(self) -> tuple[StarterView, ...]:
        """Starters whose game is not settled: still able to move the score."""
        return tuple(s for s in self.starters if not s.empty and s.game.pending)

    @property
    def all_settled(self) -> bool:
        return all(s.empty or s.game.settled for s in self.starters)

    @property
    def any_unknown(self) -> bool:
        return any(not s.empty and s.game.state == UNKNOWN for s in self.starters)

    def exposure(self) -> str:
        """'3 yet to play (RB, K, DST), 1 playing, 6 final' — text, so a
        reader without colour gets the same answer."""
        c = self.counts
        bits: list[str] = []
        for state in _STATE_ORDER:
            n = c.get(state, 0)
            if not n:
                continue
            word = {NOT_STARTED: "yet to play", PLAYING: "playing", FINAL: "final",
                    SUSPENDED: "suspended", CANCELED: "canceled", UNKNOWN: "unknown",
                    EMPTY: "empty slot"}[state]
            if state in (NOT_STARTED, PLAYING, SUSPENDED, UNKNOWN):
                who = ", ".join(s.position or s.slot for s in self.starters if s.state == state)
                bits.append(f"{n} {word} ({who})")
            else:
                bits.append(f"{n} {word}")
        return "; ".join(bits) if bits else "no starters"

    def record(self) -> dict:
        return {"roster_id": self.roster_id, "label": self.label,
                "platform_points": self.platform_points, "override": self.override,
                "starters_sum": self.starters_sum, "unknown_points": self.unknown_points,
                "reconciliation": self.reconciliation, "counts": self.counts,
                "exposure": self.exposure(), "all_settled": self.all_settled,
                "any_unknown": self.any_unknown,
                "starters": [s.record() for s in self.starters],
                "bench": [s.record() for s in self.bench],
                "notes": list(self.notes)}


def _side_totals(row: Mapping[str, object], starters: Sequence[StarterView]
                 ) -> tuple[float | None, float | None, float | None, int, str]:
    """Platform total, override, starters' sum, unknown count, and the
    reconciliation sentence. Totals are never edited to agree."""
    sent = _num(row.get("points"))
    override = _num(row.get("custom_points"))
    total = override if override is not None else sent
    known = [s.points for s in starters if not s.empty and s.points is not None]
    unknown = sum(1 for s in starters if not s.empty and s.points is None)
    ssum = round(sum(known), 2) if known or not unknown else None
    if not starters:
        return total, override, None, 0, "no starters listed"
    parts: list[str] = []
    if override is not None:
        parts.append(f"the platform total is a commissioner OVERRIDE (custom_points "
                     f"{override:.2f}; the scored total was {_pts(sent)})")
    if total is None:
        parts.append("the platform sent no total for this roster; nothing is summed "
                     "in its place")
    elif unknown:
        parts.append(f"{unknown} starter(s) have no platform value; the {len(known)} "
                     f"known ones sum to {_pts(ssum)} against the platform total "
                     f"{total:.2f}, so the totals cannot be reconciled")
    else:
        diff = round(total - float(ssum or 0.0), 2)
        if abs(diff) > POINTS_EPS:
            parts.append(f"the starters' points sum to {_pts(ssum)} but the platform "
                         f"total is {total:.2f} — an unexplained difference of "
                         f"{diff:+.2f}; the platform total is shown unchanged")
        else:
            parts.append("the starters' points add up to the platform total")
    return total, override, ssum, unknown, "; ".join(parts)


# --------------------------------------------------------------------------
# Score
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class ScoreView:
    mine: SideView
    opp: SideView | None
    opp_reason: str
    as_of: datetime | None
    as_of_note: str

    @property
    def margin(self) -> float | None:
        if self.opp is None or self.mine.platform_points is None \
                or self.opp.platform_points is None:
            return None
        return round(self.mine.platform_points - self.opp.platform_points, 2)

    def lead(self) -> str:
        m = self.margin
        if m is None:
            if self.opp is None:
                return "no opponent to compare against"
            return "margin unknown: a platform total is missing"
        if abs(m) <= POINTS_EPS:
            return "level"
        return f"{'ahead' if m > 0 else 'behind'} by {abs(m):.2f}"

    def settled(self) -> str:
        """Whether the result can move. A lead is never called safe while
        anyone on either side could still score."""
        if self.opp is None:
            return ""
        pend_me, pend_them = self.mine.pending, self.opp.pending
        if self.mine.any_unknown or self.opp.any_unknown:
            return ("not settled: at least one starter's game status is UNKNOWN, so "
                    "the page cannot say who can still score")
        if not pend_me and not pend_them:
            return ("every starter on both sides is final; the result stands unless "
                    "the platform corrects a score")
        bits = []
        if pend_them:
            bits.append(f"they still have {len(pend_them)} to play or playing ("
                        + ", ".join(s.position or s.slot for s in pend_them) + ")")
        if pend_me:
            bits.append(f"you still have {len(pend_me)} ("
                        + ", ".join(s.position or s.slot for s in pend_me) + ")")
        return "not settled — " + "; ".join(bits)

    def record(self) -> dict:
        return {"mine": self.mine.record(), "opp": self.opp.record() if self.opp else None,
                "opp_reason": self.opp_reason, "margin": self.margin, "lead": self.lead(),
                "settled": self.settled(),
                "as_of": self.as_of.isoformat() if self.as_of else None,
                "as_of_note": self.as_of_note}


# --------------------------------------------------------------------------
# Legal remaining actions
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class LiveAction:
    """One archived pregame action, re-judged against what is legal NOW."""

    kind: str
    title: str
    body: str
    deadline: datetime | None
    deadline_note: str
    backup: str
    archived_status: str             # ACTIONABLE | WITHHELD, at decision time
    available: bool
    why: str                         # why not, or how it was proven legal
    player_ids: tuple[str, ...] = field(default=())
    eligible: bool = True            # decided before its own deadline
    generated: datetime | None = None   # when the record it comes from was written
    slot: str = ""                   # the destination slot the record named, if any
    superseded: str = ""             # a later decision-time board dropped this move

    def record(self) -> dict:
        return {"kind": self.kind, "title": self.title, "body": self.body,
                "deadline": self.deadline.isoformat() if self.deadline else None,
                "deadline_note": self.deadline_note, "backup": self.backup,
                "archived_status": self.archived_status, "available": self.available,
                "why": self.why, "player_ids": list(self.player_ids),
                "eligible": self.eligible,
                "generated": self.generated.isoformat() if self.generated else None,
                "slot": self.slot, "superseded": self.superseded}


@dataclass(frozen=True)
class Capacity:
    """How much lineup freedom is left, counted from proven lock states."""

    open_starters: int
    locked_starters: int
    unknown_starters: int
    open_bench: int
    empty_slots: int

    def sentence(self) -> str:
        if self.unknown_starters:
            return (f"{self.unknown_starters} starter(s) have an UNKNOWN lock state, "
                    f"so this page cannot say what is still legal; Sleeper's own "
                    f"lineup screen can. {self.open_starters} starter(s) are proven "
                    f"unlocked and {self.open_bench} bench player(s) are.")
        if not self.open_starters and not self.empty_slots:
            return ("No legal lineup change remains this week: every starter has "
                    "kicked off, and a locked slot cannot be changed.")
        if not self.open_bench:
            return (f"{self.open_starters} starter(s) are still unlocked, but no bench "
                    f"player is, so no swap can be made; an unlocked starter could "
                    f"only be moved to an empty slot.")
        return (f"A swap needs both players unlocked: {self.open_starters} "
                f"starter(s) and {self.open_bench} bench player(s) still are"
                + (f", and {self.empty_slots} slot(s) are empty" if self.empty_slots
                   else "") + ".")

    def record(self) -> dict:
        return {"open_starters": self.open_starters, "locked_starters": self.locked_starters,
                "unknown_starters": self.unknown_starters, "open_bench": self.open_bench,
                "empty_slots": self.empty_slots, "sentence": self.sentence()}


# --------------------------------------------------------------------------
# Pregame record
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PregameView:
    path: Path | None
    generated: datetime | None
    note: str                        # why there is / is not a record
    actions: tuple[LiveAction, ...] = field(default=())
    outcomes: tuple[str, ...] = field(default=())
    projection: str = ""             # the archived matchup projection, as context
    withheld: tuple[str, ...] = field(default=())
    designation_changes: tuple[str, ...] = field(default=())
    tagged: bool = True              # every record that qualifies carries its tags
    #: Every qualifying decision-time record, oldest first (stamps).
    records: tuple[str, ...] = field(default=())
    #: Archives of this season and week that were seen and NOT used as
    #: evidence, each with the reason: untagged, future, altered, malformed.
    context: tuple[str, ...] = field(default=())
    #: What the pregame board's Free Agent Radar found, summarised from the
    #: record's own block: counts, its snapshot as-of, and the LINEUP
    #: candidates by name with their gain and drop. Empty when the record
    #: predates the radar. Never re-evaluated here: a pickup is not a
    #: game-day move, and this page says what the board said.
    radar: Mapping[str, object] = field(default_factory=dict)

    @property
    def found(self) -> bool:
        return self.path is not None

    def record(self) -> dict:
        return {"path": str(self.path) if self.path else None,
                "generated": self.generated.isoformat() if self.generated else None,
                "note": self.note, "actions": [a.record() for a in self.actions],
                "outcomes": list(self.outcomes), "projection": self.projection,
                "withheld": list(self.withheld),
                "designation_changes": list(self.designation_changes),
                "tagged": self.tagged, "records": list(self.records),
                "context": list(self.context), "radar": dict(self.radar)}


@dataclass(frozen=True)
class ArchiveMatch:
    """One archived board that qualifies as decision-time evidence for THIS
    owner in THIS league, season and week: tagged, digest-valid, readable,
    and not from the future."""

    stamp: datetime                  # from the filename (the ordering key)
    generated: datetime              # from the record itself
    path: Path
    record: dict

    def keys(self) -> set[tuple]:
        return {_action_key(a) for a in (self.record.get("actions") or [])
                if isinstance(a, Mapping)}


def _action_key(raw: Mapping[str, object]) -> tuple:
    """What makes two archived actions 'the same move': the kind and the
    player ids, never the wording."""
    return (str(raw.get("kind") or ""),
            tuple(normalize_id(i) for i in (raw.get("player_ids") or []) if normalize_id(i)))


def _identity(value: object, expected: object, *, as_int: bool) -> str:
    """'ok' = matches, 'other' = a DIFFERENT identity, 'absent' = no tag,
    'malformed' = a tag of the wrong shape (never coerced into a match)."""
    if value is None or value == "":
        return "absent"
    try:
        if as_int:
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                return "malformed"
            if isinstance(value, str) and not value.strip().isdigit():
                return "malformed"
            return "ok" if int(value) == int(expected) else "other"
        if not isinstance(value, str):
            return "malformed"
        return "ok" if value == str(expected) else "other"
    except (TypeError, ValueError):
        return "malformed"


def qualifying_records(root: Path | None, *, season: int, week: int, league_id: str,
                       my_roster_id: int | None, now: datetime
                       ) -> tuple[tuple[ArchiveMatch, ...], tuple[str, ...]]:
    """Every archived board that can stand as decision-time evidence, oldest
    first, and a note for each archive of this season and week that cannot.

    A record qualifies only when it names EXACTLY this league and this
    roster (an untagged record is historical context, never personalised
    advice), its bytes still match the digest in its own filename, its
    identity fields are well formed, and it does not claim to be written in
    the future. A record for another league or roster is somebody else's
    and is neither used nor listed.
    """
    matches: list[ArchiveMatch] = []
    context: list[str] = []
    now = _utc(now)
    for stamp, path in list_archives(root, season):
        try:
            rec = read_archive(path)
        except (OSError, ValueError):
            context.append(f"{path.name}: unreadable, not used")
            continue
        if not isinstance(rec, Mapping):
            context.append(f"{path.name}: not a record, not used")
            continue
        if rec.get("week") != int(week) or rec.get("season") != int(season):
            continue
        m = ARCHIVE_RE.match(path.stem)
        digest = m.group("digest") if m else None
        if digest:
            body = {k: v for k, v in rec.items() if k != "archive_version"}
            if record_digest(body) != digest:
                context.append(f"{path.name}: its bytes no longer match the digest in its "
                               f"name (altered or damaged), not used")
                continue
        lid = _identity(rec.get("league_id"), league_id, as_int=False)
        rid = (_identity(rec.get("my_roster_id"), my_roster_id, as_int=True)
               if my_roster_id is not None else "other")
        if "malformed" in (lid, rid):
            context.append(f"{path.name}: malformed league/roster identity, not used")
            continue
        if "other" in (lid, rid):
            continue                     # another league or roster: not ours, not listed
        if "absent" in (lid, rid):
            missing = "league_id" if lid == "absent" else "my_roster_id"
            context.append(f"{path.name}: carries no valid {missing} tag, so it cannot be "
                           f"shown to be this roster's record — historical context only, "
                           f"no advice is taken from it")
            continue
        generated = _parse_dt(rec.get("generated"))
        if generated is None:
            context.append(f"{path.name}: no readable generation time, not used")
            continue
        if generated > now + FUTURE_SLACK:
            context.append(f"{path.name}: claims to be written at {_stamp(generated)}, after "
                           f"now ({_stamp(now)}); a record from the future is not "
                           f"decision-time evidence, not used")
            continue
        matches.append(ArchiveMatch(stamp, generated, path, dict(rec)))
    matches.sort(key=lambda m: (m.generated, m.stamp, m.path.name))
    return tuple(matches), tuple(context)


def find_pregame_record(root: Path | None, *, season: int, week: int,
                        league_id: str, my_roster_id: int | None,
                        now: datetime | None = None
                        ) -> tuple[Path | None, dict | None, str, bool]:
    """The newest qualifying board (see `qualifying_records`), or nothing.

    Returns (path, record, note, tagged). `tagged` is True for every record
    returned: one that carries no league/roster identity never qualifies.
    Nothing is rebuilt: if no record qualifies, there is no pregame record.
    """
    matches, context = qualifying_records(root, season=season, week=week, league_id=league_id,
                                          my_roster_id=my_roster_id,
                                          now=now or datetime.now(timezone.utc))
    if not matches:
        note = (f"no pregame record for week {week}: no archived board matches this season, "
                f"week, league and roster, and none is reconstructed after the fact")
        if context:
            note += " (" + "; ".join(context) + ")"
        return None, None, note, False
    m = matches[-1]
    return m.path, m.record, f"archived board of {_stamp(m.generated)}", True


def select_evidence(matches: Sequence[ArchiveMatch]
                    ) -> list[tuple[Mapping[str, object], ArchiveMatch, str]]:
    """For every distinct move the boards listed, the record that is
    decision-time evidence for it: the NEWEST board written before that
    move's own deadline. A later board written after the deadline (a postgame
    render) never displaces it. A later board written BEFORE the deadline
    that dropped the move supersedes it, and the note says so.

    Returns (raw action, its record, supersession note) in the order the
    evidence records list them.
    """
    seen: dict[tuple, list[tuple[Mapping[str, object], ArchiveMatch]]] = {}
    for m in matches:
        for raw in (m.record.get("actions") or []):
            if isinstance(raw, Mapping):
                seen.setdefault(_action_key(raw), []).append((raw, m))
    out: list[tuple[Mapping[str, object], ArchiveMatch, str]] = []
    for key, pairs in seen.items():
        chosen = None
        for raw, m in pairs:                       # oldest first: the last hit wins
            deadline = _parse_dt(raw.get("deadline"))
            if deadline is not None and m.generated < deadline:
                chosen = (raw, m)
        if chosen is None:
            chosen = pairs[-1]
        raw, m = chosen
        deadline = _parse_dt(raw.get("deadline"))
        later = [x for x in matches if x.generated > m.generated
                 and (deadline is None or x.generated < deadline) and key not in x.keys()]
        note = ""
        if later:
            note = (f"a later decision-time board ({_stamp(later[-1].generated)}) no longer "
                    f"listed this move, so the board of {_stamp(m.generated)} is superseded")
        out.append((raw, m, note))
    order = {id(m): i for i, m in enumerate(matches)}
    out.sort(key=lambda t: (order[id(t[1])], ))
    return out


# --------------------------------------------------------------------------
# Since the last game-day snapshot
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class GameDayChanges:
    previous: str
    items: tuple[tuple[str, str, str], ...] = field(default=())   # (kind, subject, detail)
    note: str = ""

    def record(self) -> dict:
        return {"previous": self.previous, "items": [list(i) for i in self.items],
                "note": self.note}


def diff_gameday(previous: Mapping[str, object] | None, current: Mapping[str, object]
                 ) -> GameDayChanges:
    """Transitions between two game-day records of the SAME kind of source.

    Reads both as data. A total that went DOWN is named a correction, not
    hidden. A previous record from another week, league or roster is not
    compared at all; a first visit says so.
    """
    if not isinstance(previous, Mapping):
        return GameDayChanges("", note="no earlier game-day snapshot to compare against")
    prev_stamp = str(previous.get("generated") or "unknown")
    for key in ("season", "week", "league_id", "my_roster_id"):
        if previous.get(key) != current.get(key):
            return GameDayChanges(prev_stamp, note=(
                f"the previous snapshot ({prev_stamp}) is {key} "
                f"{previous.get(key)!r}, this page is {current.get(key)!r}; "
                f"nothing is compared across that boundary"))
    if previous.get("source_kind") != current.get("source_kind"):
        return GameDayChanges(prev_stamp, note=(
            "the previous snapshot came from a different kind of source; not compared"))
    items: list[tuple[str, str, str]] = []
    ps, cs = previous.get("score") or {}, current.get("score") or {}
    for side in ("mine", "opp"):
        a, b = (ps.get(side) or {}), (cs.get(side) or {})
        if not a or not b:
            if bool(a) != bool(b):
                items.append(("matchup", side, "opponent " + ("appeared" if b else "gone")))
            continue
        if a.get("roster_id") != b.get("roster_id"):
            items.append(("matchup", side, f"roster #{a.get('roster_id')} -> "
                                           f"#{b.get('roster_id')}"))
            continue
        pa, pb = _num(a.get("platform_points")), _num(b.get("platform_points"))
        if pa != pb:
            if pa is not None and pb is not None:
                word = "score CORRECTION (lowered)" if pb < pa else "score"
                items.append((word, b.get("label", side), f"{pa:.2f} -> {pb:.2f} ({pb - pa:+.2f})"))
            else:
                items.append(("score", b.get("label", side), f"{_pts(pa)} -> {_pts(pb)}"))
        was = {str(s.get("sleeper_id")): s for s in a.get("starters") or [] if isinstance(s, Mapping)}
        now = {str(s.get("sleeper_id")): s for s in b.get("starters") or [] if isinstance(s, Mapping)}
        if [s.get("sleeper_id") for s in a.get("starters") or []] != \
                [s.get("sleeper_id") for s in b.get("starters") or []]:
            items.append(("lineup", b.get("label", side), "starting lineup changed"))
        for sid in sorted(was.keys() & now.keys()):
            x, y = was[sid], now[sid]
            name = str(y.get("name") or sid)
            xp, yp = _num(x.get("points")), _num(y.get("points"))
            if xp != yp and (xp is None or yp is None or abs(yp - xp) > POINTS_EPS):
                kind = "score CORRECTION (lowered)" if (xp is not None and yp is not None
                                                        and yp < xp) else "points"
                items.append((kind, name, f"{_pts(xp)} -> {_pts(yp)}"))
            if x.get("state") != y.get("state"):
                items.append(("status", name, f"{x.get('state')} -> {y.get('state')}"))
    pf, cf = previous.get("feed") or {}, current.get("feed") or {}
    if bool(pf.get("present")) != bool(cf.get("present")):
        items.append(("source", "game status feed",
                      "now available" if cf.get("present") else "NO LONGER available"))
    elif bool(pf.get("fresh")) != bool(cf.get("fresh")):
        items.append(("source", "game status feed",
                      "current again" if cf.get("fresh") else "went STALE"))
    return GameDayChanges(prev_stamp, tuple(items),
                          note="" if items else "nothing changed since the previous snapshot")


# --------------------------------------------------------------------------
# The page
# --------------------------------------------------------------------------
#: The kind of source every server-built record comes from. A browser
#: refresh writes the same kind (Sleeper matchups + the status feed), so the
#: page can diff against its own last-good state like for like.
SOURCE_KIND = "sleeper.matchups+game_status"


@dataclass(frozen=True)
class GameDay:
    season: int
    week: int
    league_id: str
    my_roster_id: int | None
    generated: datetime
    sources: tuple[SourceFreshness, ...]
    score: ScoreView
    feed: GameFeed
    capacity: Capacity
    actions: tuple[LiveAction, ...]
    pregame: PregameView
    changes: GameDayChanges
    notes: tuple[str, ...]
    slots: tuple[str, ...]
    #: What the inline script needs to refresh: the raw week rows for the
    #: two rosters, kickoffs by team, names by id, and the API bases.
    embedded: dict
    league_name: str = LEAGUE_NAME
    state_week: int | None = None

    @property
    def degraded(self) -> bool:
        return bool(self.notes) or any(s.status is not Status.FRESH for s in self.sources)

    def record(self) -> dict:
        return {
            "generated": self.generated.isoformat(timespec="seconds"),
            "season": self.season, "week": self.week, "league_id": self.league_id,
            "my_roster_id": self.my_roster_id, "source_kind": SOURCE_KIND,
            "state_week": self.state_week,
            "sources": [s.line() for s in self.sources],
            "score": self.score.record(),
            "feed": {"present": self.feed.present, "fresh": self.feed.fresh,
                     "as_of": self.feed.as_of.isoformat() if self.feed.as_of else None,
                     "reason": self.feed.reason},
            "capacity": self.capacity.record(),
            "actions": [a.record() for a in self.actions],
            "available_actions": sum(1 for a in self.actions if a.available),
            "pregame": self.pregame.record(),
            "changes": self.changes.record(),
            "notes": list(self.notes),
            "degraded": self.degraded,
        }

    def to_html(self, *, include_names: bool = True) -> str:
        return render_gameday_html(self, include_names=include_names)


def _name_of(sid: str, rec: Mapping[str, object] | None) -> tuple[str, str, str, bool]:
    """(name, position, team, named) for a sleeper id, from the cached dump.
    A DST id IS its team. An id the dump lacks is shown as the id — never
    looked up by name anywhere (rule #3)."""
    sid = normalize_id(sid)
    if is_dst_id(sid):
        return (f"{team_key(sid)} DST", "DST", team_key(sid), True)
    if not rec:
        return (f"sleeper:{sid}", "", "", False)
    return (str(rec.get("full_name") or f"sleeper:{sid}"),
            str(rec.get("position") or "").upper(), team_key(rec.get("team")), True)


def _positions_of(sid: str, rec: Mapping[str, object] | None, position: str) -> tuple[str, ...]:
    """Every position the platform lists him at. `fantasy_positions` when the
    dump carries it, else the one position tag; a DST is a DST."""
    if is_dst_id(normalize_id(sid)):
        return ("DST",)
    fp = (rec or {}).get("fantasy_positions")
    out = [str(x).upper() for x in fp if x] if isinstance(fp, (list, tuple)) else []
    if position and position not in out:
        out.append(position)
    return tuple(out)


def build_gameday(*, season: int, week: int, league_id: str, owner_id: str,
                  snapshot: Mapping[str, object],
                  sleeper_players: Mapping[str, Mapping[str, object]],
                  players_as_of: datetime | None, players_fresh: bool,
                  schedule: pd.DataFrame | None, feed: GameFeed,
                  sources: Sequence[SourceFreshness], now: datetime,
                  archive_root: Path | None = None,
                  previous: Mapping[str, object] | None = None,
                  api_bases: Sequence[str] = ()) -> GameDay:
    """Assemble the page from the cache. Never opens a socket."""
    now = _utc(now)
    rosters = [r for r in (snapshot.get("rosters") or []) if isinstance(r, Mapping)]
    matchups = [m for m in (snapshot.get("matchups") or []) if isinstance(m, Mapping)]
    league = snapshot.get("league") or {}
    slots = slot_order(league.get("roster_positions") if isinstance(league, Mapping) else None)
    kickoffs = kickoff_index(schedule, week)
    notes: list[str] = []
    state = snapshot.get("state") if isinstance(snapshot.get("state"), Mapping) else {}
    state_week = _as_week(state.get("week"))

    league_source = next((s for s in sources if s.name == "sleeper_league"), None)
    snap_as_of = _parse_dt(snapshot.get("as_of")) or (league_source.as_of if league_source else None)
    # What ADVICE rests on, judged separately from what the score rests on.
    by_source = {s.name: s for s in sources}
    blockers: list[str] = []
    for name in LEGALITY_SOURCES:
        src = by_source.get(name)
        if src is None:
            blockers.append(f"{name} freshness was never assessed")
        elif src.status is not Status.FRESH:
            blockers.append(f"{name} is {src.status.value.upper()} ({src.reason})")
    if players_fresh is False and not any(b.startswith("sleeper_players") for b in blockers):
        blockers.append("sleeper_players (designations and positions) is not current")
    if league_source is None or league_source.status is not Status.FRESH:
        notes.append("the league snapshot the scores come from is "
                     + (league_source.status.value.upper() if league_source else "UNASSESSED")
                     + (f": {league_source.reason}" if league_source else "")
                     + " — the score is the last one seen, not a live one")

    def status_and_lock(team: str) -> tuple[GameStatus, str, str, datetime | None]:
        game = feed.status_for(team)
        lk = lock_state(team_key(team), kickoffs, now)
        lock = LOCKED if lk.locked else (OPEN if lk.known else "UNKNOWN")
        note = lk.note
        # Observed status and scheduled kickoff can disagree. Neither wins:
        # the disagreement is stated, and for legality the safer side rules.
        if game.current and game.state == NOT_STARTED and lock == LOCKED:
            game = GameStatus(game.team, game.state, game.raw, game.opponent, game.home,
                              game.date, game.note + "; the schedule kickoff has passed "
                              "but the feed still says pre-game (delayed start, moved "
                              "game, or feed lag) — Sleeper locks at the real kickoff",
                              game.current)
        elif game.current and game.state in (PLAYING, FINAL, SUSPENDED) and lock == OPEN:
            lock, note = LOCKED, (f"LOCKED — the feed says the game is {game.raw} "
                                  f"although the schedule kickoff is still ahead; the "
                                  f"observed status wins over the timetable")
        return game, lock, note, lk.kickoff

    def starter(sid: str, slot: str, lineup: str) -> StarterView:
        sid = normalize_id(sid)
        if not sid or sid == EMPTY_SENTINEL:
            return StarterView(slot, "", "EMPTY", "", "", None, "an empty slot scores "
                               "nothing", GameStatus("", EMPTY), OPEN,
                               "an empty slot can take any unlocked eligible player",
                               None, "", "", empty=True)
        rec = sleeper_players.get(sid)
        name, pos, team, named = _name_of(sid, rec)
        game, lock, lnote, kick = status_and_lock(team)
        inj = str((rec or {}).get("injury_status") or "")
        inj_note = ""
        if inj:
            inj_note = (f"designation from the player dump of {_stamp(players_as_of)}"
                        + ("" if players_fresh else " — STALE, not a current status"))
        return StarterView(slot, sid, name, pos, team, None, "", game, lock, lnote, kick,
                           inj, inj_note, named=named, positions=_positions_of(sid, rec, pos))

    def side(row: Mapping[str, object] | None, roster: Mapping[str, object] | None,
             label: str, with_bench: bool) -> SideView | None:
        if row is None:
            return None
        ids = [normalize_id(s) for s in (row.get("starters") or [])]
        if not ids and roster is not None:
            ids = [normalize_id(s) for s in (roster.get("starters") or [])]
        sp = row.get("starters_points") if isinstance(row.get("starters_points"), Sequence) else []
        pp = row.get("players_points") if isinstance(row.get("players_points"), Mapping) else {}
        views: list[StarterView] = []
        for i, sid in enumerate(ids):
            slot = slots[i] if i < len(slots) else f"SLOT{i + 1}"
            v = starter(sid, slot, "START")
            if v.empty:
                views.append(v)
                continue
            val = _num(sp[i]) if i < len(sp) else None
            note = "platform starters_points"
            if val is None:
                val = _num(pp.get(sid))
                note = "platform players_points" if val is not None else \
                    "the platform sent no value for this starter — UNKNOWN, not 0"
            if val is not None and v.game.state == NOT_STARTED and abs(val) <= POINTS_EPS:
                note += "; game not started, so this 0 is a placeholder"
            views.append(StarterView(v.slot, v.sleeper_id, v.name, v.position, v.team,
                                     val, note, v.game, v.lock, v.lock_note, v.kickoff,
                                     v.injury, v.injury_note, named=v.named,
                                     positions=v.positions))
        bench: list[StarterView] = []
        if with_bench and roster is not None:
            reserve = {normalize_id(s) for s in (roster.get("reserve") or [])}
            taxi = {normalize_id(s) for s in (roster.get("taxi") or [])}
            for sid in (roster.get("players") or []):
                sid = normalize_id(sid)
                if sid in ids or not sid:
                    continue
                v = starter(sid, "IR" if sid in reserve else "TAXI" if sid in taxi else "BN",
                            "BENCH")
                val = _num(pp.get(sid))
                bench.append(StarterView(v.slot, v.sleeper_id, v.name, v.position, v.team,
                                         val, "platform players_points" if val is not None
                                         else "no platform value", v.game, v.lock,
                                         v.lock_note, v.kickoff, v.injury, v.injury_note,
                                         named=v.named, positions=v.positions))
        total, override, ssum, unknown, recon = _side_totals(row, views)
        side_notes: list[str] = []
        if len(ids) != len(slots):
            side_notes.append(f"{len(ids)} starters listed for {len(slots)} slots")
        if sp and len(sp) != len(ids):
            side_notes.append(f"starters_points has {len(sp)} values for {len(ids)} starters")
        rid = _as_week(row.get("roster_id"))
        return SideView(rid, label, tuple(views), tuple(bench), total, override, ssum,
                        unknown, recon, tuple(side_notes))

    # ---- owner and opponent, by stable ids only
    my = next((r for r in rosters if str(r.get("owner_id")) == str(owner_id)
               or str(owner_id) in {str(c) for c in (r.get("co_owners") or [])}), None)
    my_rid = _as_week(my.get("roster_id")) if my is not None else None
    mine_row = next((m for m in matchups if my_rid is not None
                     and str(m.get("roster_id")) == str(my_rid)), None)
    opp_row, opp_roster, opp_reason = None, None, ""
    if my is None:
        opp_reason = "owner roster not found in the snapshot"
        notes.append(opp_reason)
    elif mine_row is None:
        opp_reason = f"no week-{week} matchup row for roster #{my_rid} in the snapshot"
    elif mine_row.get("matchup_id") is None:
        opp_reason = (f"roster #{my_rid} has no matchup this week (matchup_id is null: "
                      f"bye week, or playoffs not yet drawn)")
    else:
        others = [m for m in matchups if m.get("matchup_id") == mine_row.get("matchup_id")
                  and str(m.get("roster_id")) != str(my_rid)]
        if not others:
            opp_reason = (f"matchup {mine_row.get('matchup_id')} has no other roster in it "
                          f"(unpaired week or a format this page does not support)")
        elif len(others) > 1:
            opp_reason = (f"matchup {mine_row.get('matchup_id')} has {len(others)} other "
                          f"rosters — a multi-team or median format this page does not "
                          f"support; no opponent is chosen arbitrarily")
        else:
            opp_row = others[0]
            opp_roster = next((r for r in rosters
                               if str(r.get("roster_id")) == str(opp_row.get("roster_id"))), None)
    mine = side(mine_row if mine_row is not None else ({} if my is not None else None),
                my, "You", True)
    if mine is None:
        mine = SideView(my_rid, "You", (), (), None, None, None, 0,
                        "owner roster not found", ("owner roster not found",))
    opp = side(opp_row, opp_roster, f"Roster #{opp_row.get('roster_id')}" if opp_row else "",
               False)
    as_of_note = (f"platform scores as of {_stamp(snap_as_of)} ({_age_text(snap_as_of, now)})"
                  if snap_as_of else "the league snapshot carries no as-of time")
    score = ScoreView(mine, opp, opp_reason, snap_as_of, as_of_note)

    if state_week is not None and state_week != int(week):
        notes.append(f"the platform says the NFL is in week {state_week}; this page is "
                     f"week {week}. Nothing here is compared across weeks.")
    if not feed.present:
        notes.append("no per-game status feed in the cache: every game is UNKNOWN. The "
                     "schedule's kickoff times drive the lock column only")
    elif not feed.fresh:
        notes.append(f"the per-game status feed is not current ({feed.reason}); finals "
                     f"and cancellations are kept, every other status reads UNKNOWN")
    if kickoffs is None:
        notes.append("no schedule loaded: every lock state is UNKNOWN")
    elif not kickoffs.rows_intact:
        notes.append(f"kickoff schedule DAMAGED — {kickoffs.summary()}; affected players "
                     f"have UNKNOWN locks")

    # ---- capacity, from proven locks
    cap = Capacity(
        open_starters=sum(1 for s in mine.starters if not s.empty and s.lock == OPEN),
        locked_starters=sum(1 for s in mine.starters if not s.empty and s.lock == LOCKED),
        unknown_starters=sum(1 for s in mine.starters if not s.empty and s.lock == "UNKNOWN"),
        open_bench=sum(1 for s in mine.bench if s.lock == OPEN and s.slot == "BN"),
        empty_slots=sum(1 for s in mine.starters if s.empty))

    # ---- pregame record and the actions still legal
    matches, context = qualifying_records(archive_root, season=season, week=week,
                                          league_id=league_id, my_roster_id=my_rid, now=now)
    by_id = {s.sleeper_id: s for s in (*mine.starters, *mine.bench) if s.sleeper_id}
    starter_ids = [s.sleeper_id for s in mine.starters]
    actions: list[LiveAction] = []
    outcomes: list[str] = []
    projection = ""
    withheld: tuple[str, ...] = ()
    desig_changes: list[str] = []
    generated = None
    path = None
    if not matches:
        pnote = (f"no pregame record for week {week}: no archived board matches this season, "
                 f"week, league and roster, and none is reconstructed after the fact")
    else:
        evidence = select_evidence(matches)
        used = {id(m) for _, m, _ in evidence}
        primary = next((m for m in reversed(matches) if id(m) in used), matches[-1])
        path, rec, generated = primary.path, primary.record, primary.generated
        stamps = [_stamp(m.generated) for m in matches]
        pnote = (f"archived board of {_stamp(generated)}" if len(matches) == 1 else
                 f"archived boards of {', '.join(stamps)}; each move is judged from the "
                 f"newest board written before its own deadline")
        withheld = tuple(str(w) for w in (rec.get("withheld_actions") or []))
        m = rec.get("matchup") if isinstance(rec.get("matchup"), Mapping) else None
        if m:
            pw = m.get("pwin")
            projection = (f"pregame projection: you {_num(m.get('my_mean')) or 0:.1f} vs roster "
                          f"#{m.get('opponent_roster_id')} {_num(m.get('opp_mean')) or 0:.1f}; "
                          f"P(win) {'abstained' if pw is None else f'{float(pw):.0%}'} "
                          f"(UNCALIBRATED, pregame; not updated by the score, and never "
                          f"added to points already earned)")
        else:
            projection = "the pregame board had no matchup projection"
        arch_roster = {str(p.get("sleeper_id")): p for p in (rec.get("roster") or [])
                       if isinstance(p, Mapping)}
        for s in mine.starters:
            a = arch_roster.get(s.sleeper_id)
            if a is None or s.empty:
                continue
            then = str(a.get("availability") or "")
            now_d = s.injury or "no designation"
            if then and s.injury and not re.search(rf"\b{re.escape(s.injury)}\b", then, re.I):
                desig_changes.append(
                    f"{s.name}: pregame record said \"{then}\"; the player dump now says "
                    f"{now_d} ({s.injury_note or 'current'}). The later status cannot "
                    f"show what was knowable before kickoff.")
        for raw, m, superseded in evidence:
            actions.append(_judge_action(raw, generated=m.generated, now=now, by_id=by_id,
                                         starter_ids=starter_ids, blockers=blockers,
                                         empty_slots=[s.slot for s in mine.starters if s.empty],
                                         superseded=superseded))
        outcomes = _outcomes(actions, by_id, starter_ids)
    radar_summary: dict = {}
    if matches:
        radar_summary = summarise_radar(primary.record.get("radar"))
    pregame = PregameView(path, generated, pnote, tuple(actions), tuple(outcomes),
                          projection, withheld, tuple(desig_changes), bool(matches),
                          tuple(_stamp(m.generated) for m in matches), context,
                          radar=radar_summary)

    # ---- since the last game-day snapshot (server-side, like for like)
    partial = {"season": season, "week": week, "league_id": league_id,
               "my_roster_id": my_rid, "source_kind": SOURCE_KIND,
               "score": score.record(),
               "feed": {"present": feed.present, "fresh": feed.fresh}}
    changes = diff_gameday(previous, partial)

    embedded = _embedded(season=season, week=week, league_id=league_id, my_rid=my_rid,
                         slots=slots, mine_row=mine_row, opp_row=opp_row, my=my,
                         opp_roster=opp_roster, sleeper_players=sleeper_players,
                         kickoffs=kickoffs, feed=feed, players_as_of=players_as_of,
                         players_fresh=players_fresh, snap_as_of=snap_as_of,
                         pregame=pregame, now=now, api_bases=api_bases,
                         all_ids=[s.sleeper_id for s in (*mine.starters, *mine.bench,
                                                          *(opp.starters if opp else ()))
                                  if s.sleeper_id], sources=sources, blockers=blockers)
    return GameDay(int(season), int(week), str(league_id), my_rid, now, tuple(sources),
                   score, feed, cap, tuple(actions), pregame, changes, tuple(notes),
                   slots, embedded, state_week=state_week)


def _lapse(deadline: object, built: datetime) -> tuple[str, str]:
    """Attributes that let the page mark a pregame pickup OFF at its first
    kickoff, and the words to show now when the build itself is already
    past it (Game Day is usually built after kickoff)."""
    try:
        t = datetime.fromisoformat(str(deadline)) if deadline else None
    except ValueError:
        t = None
    if t is None:
        return "", ""
    t = (t if t.tzinfo else t.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    text = (f"OFF — the first kickoff among the players involved ({t:%a %d %b %H:%M} UTC) "
            f"has passed; this pickup can no longer help this week's lineup.")
    attrs = (f" data-deadline=\"{_e(t.isoformat(timespec='seconds'))}\""
             f" data-lapse-text=\"{_e(text)}\"")
    if built >= t:
        return attrs + " data-live=\"lapsed\"", _e(text)
    return attrs, ""


def _move_deadline(c: Mapping[str, object]) -> str:
    """The first kickoff among the player coming in and the starter he
    displaces, as the board wrote it; "" when it cannot be read. Only used
    to mark the move OFF once it passes — never to call it on."""
    if isinstance(c.get("deadline"), str) and c.get("deadline"):
        return str(c["deadline"])
    disp = c.get("displaces") if isinstance(c.get("displaces"), Mapping) else {}
    stamps = []
    for raw in (c.get("kickoff"), disp.get("kickoff")):
        try:
            t = datetime.fromisoformat(str(raw)) if raw else None
        except ValueError:
            t = None
        if t is not None:
            stamps.append(t if t.tzinfo else t.replace(tzinfo=timezone.utc))
    return min(stamps).astimezone(timezone.utc).isoformat(timespec="seconds") if stamps else ""


def summarise_radar(block: object) -> dict:
    """The board's radar, reduced to what Game Day shows: counts, the
    snapshot it was compared against, and the LINEUP candidates. Read as
    data; a record without the block yields {} and the page says so."""
    if not isinstance(block, Mapping):
        return {}
    counts = block.get("counts") if isinstance(block.get("counts"), Mapping) else {}
    moves = []
    for c in block.get("candidates") or []:
        if not isinstance(c, Mapping) or c.get("verdict") != "LINEUP":
            continue
        drop = c.get("drop") if isinstance(c.get("drop"), Mapping) else {}
        # An explicit null drop is an add into an open roster spot; an older
        # record always named one, and a missing key is not read as "none".
        no_drop = "drop" in c and c.get("drop") is None
        moves.append({"no_drop": no_drop, "deadline": _move_deadline(c),"id": str(c.get("id") or ""), "name": str(c.get("name") or ""),
                      "position": str(c.get("position") or ""),
                      "lineup_gain": _num(c.get("lineup_gain")), "slot": str(c.get("slot") or ""),
                      "drop": str(drop.get("name") or ""), "drop_id": str(drop.get("id") or ""),
                      # Older records have no drop_check; they predate the
                      # rule and are shown without one, not as verified.
                      "drop_check": str(c.get("drop_check") or "")})
    return {"snapshot_as_of": str(block.get("snapshot_as_of") or ""),
            "pool": _num(counts.get("pool")), "projected": _num(counts.get("projected")),
            "evaluated": _num(counts.get("evaluated")),
            "unprojected": _num(counts.get("unprojected")),
            "abstained": str(block.get("abstained") or ""), "moves": moves[:5]}


def _judge_action(raw: Mapping[str, object], *, generated: datetime | None, now: datetime,
                  by_id: Mapping[str, StarterView], starter_ids: Sequence[str],
                  blockers: Sequence[str] = (), empty_slots: Sequence[str] = (),
                  superseded: str = "") -> LiveAction:
    """Is this archived action still something Sleeper would accept NOW?

    The archive's ACTIONABLE is history: it says the board endorsed the move
    with the inputs it had then. Every gate is re-run against the present —
    the record's own timing, the freshness of the roster, designations and
    schedule, each player's lock, game status, roster placement (bench, not
    IR or taxi) and current designation, and the destination slot's
    eligibility for the incoming player's listed positions. The score on the
    page never depends on any of this.
    """
    kind = str(raw.get("kind") or "")
    status = str(raw.get("status") or "")
    title = str(raw.get("title") or raw.get("headline") or "")
    body = str(raw.get("body") or raw.get("detail") or "")
    deadline = _parse_dt(raw.get("deadline"))
    ids = tuple(normalize_id(i) for i in (raw.get("player_ids") or []) if normalize_id(i))
    backup = str(raw.get("backup") or "")
    dnote = str(raw.get("deadline_note") or "")
    slot_named = str(raw.get("slot") or "").upper()

    def no(why: str, eligible: bool = True) -> LiveAction:
        return LiveAction(kind, title, body, deadline, dnote, backup, status, False, why,
                          ids, eligible, generated, slot_named, superseded)

    if kind == "acquire":
        return no("a pickup is not a game-day move: eligibility was UNVERIFIED pregame "
                  "and waivers do not process during the slate")
    if status != "ACTIONABLE":
        return no("the pregame board WITHHELD this (its inputs were stale or unreadable); "
                  "a withheld comparison does not become advice because the games started")
    if deadline is None:
        return no("its deadline was UNKNOWN at decision time, so it was never a timed, "
                  "legal move", eligible=False)
    if generated is None or generated >= deadline:
        return no("the record was written at or after its own deadline, so it is not "
                  "decision-time evidence for this move", eligible=False)
    if generated > now + FUTURE_SLACK:
        return no(f"the record claims to be written at {_stamp(generated)}, after now "
                  f"({_stamp(now)}); a record from the future is not decision-time "
                  f"evidence", eligible=False)
    if superseded:
        return no(superseded)
    if now >= deadline:
        return no(f"deadline passed ({_stamp(deadline)})")
    if not ids:
        return no("the record names no player ids (written before actions carried them), "
                  "so legality cannot be checked without name-matching, which is never done")
    if blockers:
        return no("the inputs legality rests on are not current — " + "; ".join(blockers)
                  + ". The score above stands on its own; the advice does not")
    for sid in ids:
        p = by_id.get(sid)
        if p is None:
            return no(f"sleeper:{sid} is no longer on the roster (observed lineup, not a "
                      f"statement about what you did)")
        if p.lock == LOCKED:
            return no(f"{p.name} is LOCKED — {p.lock_note}")
        if p.lock != OPEN:
            return no(f"{p.name}'s lock state is UNKNOWN ({p.lock_note}); a move cannot be "
                      f"shown legal")
        if p.game.state in (PLAYING, FINAL, SUSPENDED):
            return no(f"{p.name}'s game is {p.game.state} per the feed")
        if p.game.state == CANCELED:
            # Either side of the move: a player in a canceled game cannot be
            # started into points, and a comparison the board made assuming
            # both games would be played is not re-modelled here.
            return no(f"{p.name}'s game is CANCELED per the feed; the board's comparison "
                      f"assumed it would be played, and this page does not re-model it")
        if not p.game.current:
            return no(f"{p.name}'s game status is UNCONFIRMED ({p.game.note}); the schedule "
                      f"says not started, but a kickoff time alone does not prove it, so "
                      f"the move is not shown legal")
        if p.game.state != NOT_STARTED:
            # A current feed whose word this page does not know: freshness is
            # not permission. Only an observed pre-game status endorses a move.
            return no(f"{p.name}'s game status is UNKNOWN ({p.game.note}); only an observed "
                      f"pre-game status makes a move endorsable, so it is not shown legal")
    # ---- placement: who moves in, who moves out, and where
    incoming = by_id[ids[0]] if kind in ("swap", "inactive_starter", "empty_slot") else None
    outgoing = None
    if kind == "swap" and len(ids) >= 2:
        outgoing = by_id[ids[1]]
        if outgoing.sleeper_id not in starter_ids or incoming.sleeper_id in starter_ids:
            return no("the lineup already differs from the one this advice was about "
                      "(an observation, not proof that you acted on it)")
    elif kind == "inactive_starter":
        if len(ids) < 2:
            return no("the board found no legal replacement for this starter at decision "
                      "time; there is nothing to move in")
        outgoing = by_id[ids[1]]
        if outgoing.sleeper_id not in starter_ids or incoming.sleeper_id in starter_ids:
            return no("the lineup already differs from the one this advice was about "
                      "(an observation, not proof that you acted on it)")
    elif kind == "empty_slot":
        if incoming.sleeper_id in starter_ids:
            return no("the slot is no longer empty in the observed lineup")
    if incoming is not None:
        if incoming.slot in ("IR", "TAXI"):
            return no(f"{incoming.name} is on {incoming.slot}; moving him to the active "
                      f"roster is a roster move, not a lineup change, and it is not "
                      f"offered here")
        if incoming.injury.upper() in NOT_STARTABLE:
            return no(f"{incoming.name}'s current designation is {incoming.injury} "
                      f"({incoming.injury_note or 'player dump'}); a player the platform "
                      f"lists {incoming.injury} is not offered as a start")
        if not incoming.positions:
            return no(f"{incoming.name}'s position is not known (id not in the cached "
                      f"player dump), so slot eligibility cannot be checked")
    dest = ""
    if outgoing is not None:
        dest = outgoing.slot
    elif kind == "empty_slot":
        empties = list(empty_slots)
        if slot_named:
            if slot_named not in empties:
                return no(f"the {slot_named} slot is no longer empty in the observed lineup")
            dest = slot_named
        elif len(empties) == 1:
            dest = empties[0]
        elif not empties:
            return no("no slot is empty in the observed lineup")
        else:
            return no(f"the record does not say which slot was empty and {len(empties)} "
                      f"are ({', '.join(empties)}); a slot is not guessed")
    if incoming is not None and dest:
        if not any(eligible(dest, pos) for pos in incoming.positions):
            return no(f"{incoming.name} ({'/'.join(incoming.positions)}) is not eligible for "
                      f"the {dest} slot the move would fill")
    why = (f"every player involved is proven unlocked, not under way per the feed, on the "
           f"active roster and still where the advice left them"
           + (f"; {incoming.name} ({'/'.join(incoming.positions)}) is eligible for {dest}"
              if incoming is not None and dest else "")
           + (f"; {incoming.name} is listed {incoming.injury} — eligible, not out (rule #11)"
              if incoming is not None and incoming.injury else "")
           + "; the roster, designations and schedule are all current. Sleeper will still "
             "refuse it after the real kickoff")
    if slot_named and dest and slot_named != dest:
        why += f". The record named {slot_named}; the observed lineup has him at {dest}"
    return LiveAction(kind, title, body, deadline, dnote, backup, status, True, why, ids,
                      True, generated, slot_named, superseded)


def _outcomes(actions: Sequence[LiveAction], by_id: Mapping[str, StarterView],
              starter_ids: Sequence[str]) -> list[str]:
    """Descriptive only. 'Bench outscored starter' is a fact about two box
    scores; it becomes a statement about a decision only with a decision
    record and two finals, and this page has neither the record of what the
    owner did nor, usually, both finals."""
    out: list[str] = []
    for a in actions:
        if a.kind != "swap" or a.archived_status != "ACTIONABLE" or len(a.player_ids) < 2:
            continue
        b, s = by_id.get(a.player_ids[0]), by_id.get(a.player_ids[1])
        if b is None or s is None:
            out.append(f"{a.title}: one of the players is no longer on the roster; unproven")
            continue
        observed = (f"observed now: {b.name} is {'starting' if b.sleeper_id in starter_ids else 'on the bench'}, "
                    f"{s.name} is {'starting' if s.sleeper_id in starter_ids else 'on the bench'} "
                    f"— an observation, not a record of a decision")
        if b.game.settled and s.game.settled and b.points is not None and s.points is not None:
            out.append(f"{a.title}: both final — {b.name} {b.points:.2f}, {s.name} "
                       f"{s.points:.2f} (descriptive; whether the move was made is not "
                       f"known). {observed}")
        else:
            out.append(f"{a.title}: UNPROVEN — {b.name} {_pts(b.points)} ({b.state}), "
                       f"{s.name} {_pts(s.points)} ({s.state}); not both final. {observed}")
    return out


def _embedded(*, season, week, league_id, my_rid, slots, mine_row, opp_row, my, opp_roster,
              sleeper_players, kickoffs: KickoffIndex | None, feed: GameFeed,
              players_as_of, players_fresh, snap_as_of, pregame: PregameView, now,
              api_bases, all_ids, sources: Sequence[SourceFreshness] = (),
              blockers: Sequence[str] = ()) -> dict:
    """What the inline script needs. No projection, no name lookup, no more
    of the player dump than the two rosters on the page."""
    names: dict[str, dict] = {}
    for sid in all_ids + [i for a in pregame.actions for i in a.player_ids]:
        rec = sleeper_players.get(sid)
        name, pos, team, named = _name_of(sid, rec)
        names[sid] = {"name": name, "position": pos, "team": team, "named": named,
                      "injury": str((rec or {}).get("injury_status") or ""),
                      "positions": list(_positions_of(sid, rec, pos))}
    # The cadences the script ages each source against, so a flag computed
    # at build time is never frozen as true: the page re-judges at every tick.
    by_source = {s.name: s for s in sources}
    src_blob: dict[str, dict] = {}
    for name in (*LEGALITY_SOURCES, GAME_STATUS_NAME):
        cad = CADENCES.get(name)
        src = by_source.get(name)
        as_of = src.as_of if src else (feed.as_of if name == GAME_STATUS_NAME else None)
        if name == "sleeper_players" and players_as_of is not None:
            as_of = players_as_of
        fresh = (src is not None and src.status is Status.FRESH)
        if name == "sleeper_players":
            fresh = fresh and bool(players_fresh)
        if name == GAME_STATUS_NAME:
            fresh = feed.fresh
        blocker = None
        if name != GAME_STATUS_NAME and not fresh:
            blocker = next((b for b in blockers if b.startswith(name)),
                           f"{name} freshness was never assessed")
        src_blob[name] = {
            "as_of": as_of.astimezone(timezone.utc).isoformat() if as_of else None,
            "status": (src.status.value if src else "missing"),
            "fresh_at_build": bool(fresh),
            "blocker": blocker,
            "reason": (src.reason if src else "freshness was never assessed"),
            "max_age_hours": cad.max_age_hours if cad else 72.0,
            "gameday_max_age_hours": cad.gameday_max_age_hours if cad else None,
            "assessed": src is not None}
    kick = {t: dt.astimezone(timezone.utc).isoformat() for t, dt in
            (kickoffs.kickoffs.items() if kickoffs else ())}
    return {
        "season": int(season), "week": int(week), "league_id": str(league_id),
        "my_roster_id": my_rid, "slots": list(slots),
        "rendered_at": now.astimezone(timezone.utc).isoformat(timespec="seconds"),
        "snapshot": {"as_of": snap_as_of.isoformat() if snap_as_of else None,
                     "rows": [dict(r) for r in (mine_row, opp_row) if r is not None],
                     "my_players": list((my or {}).get("players") or []),
                     "my_reserve": list((my or {}).get("reserve") or []),
                     "my_taxi": list((my or {}).get("taxi") or []),
                     "opp_players": list((opp_roster or {}).get("players") or [])},
        "sources": src_blob,
        "legality_sources": list(LEGALITY_SOURCES),
        "league_tz": str(LEAGUE_TZ.key),
        "not_startable": sorted(NOT_STARTABLE),
        "flex_eligible": list(FLEX_ELIGIBLE),
        "future_slack_ms": int(FUTURE_SLACK.total_seconds() * 1000),
        "feed": {"present": feed.present, "fresh": feed.fresh,
                 "as_of": feed.as_of.isoformat() if feed.as_of else None,
                 "reason": feed.reason, "rows": [dict(r) for r in feed.rows]},
        "names": names,
        "players_as_of": players_as_of.isoformat() if players_as_of else None,
        "players_fresh": bool(players_fresh),
        "kickoffs": kick,
        "time_unknown": sorted(kickoffs.time_unknown) if kickoffs else [],
        "declared_bye": sorted(kickoffs.declared_bye) if kickoffs else [],
        "schedule_loaded": kickoffs is not None,
        "rows_intact": bool(kickoffs.rows_intact) if kickoffs else False,
        "season_teams": sorted(kickoffs.season_teams) if kickoffs else [],
        "pregame": {"generated": pregame.generated.isoformat() if pregame.generated else None,
                    "actions": [a.record() for a in pregame.actions]},
        "api": {"base": (list(api_bases)[0] if api_bases else SLEEPER_BASE),
                "schedule_base": (list(api_bases)[1] if len(list(api_bases)) > 1
                                  else SCHEDULE_BASE)},
        "feed_words": sorted(_FEED_STATE.items()),
        "team_aliases": dict(TEAM_ALIASES),
    }


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
_CSS = theme.CSS + """
.modebar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0}
.status{min-height:1.4em}
.score{display:grid;grid-template-columns:1fr auto 1fr;gap:8px;align-items:center;text-align:center}
.score .who{font-size:12px;color:var(--muted);letter-spacing:.1em;text-transform:uppercase;font-weight:700}
.score .pts{font-size:44px;font-weight:900;font-variant-numeric:tabular-nums;line-height:1.05;letter-spacing:-.02em}
.score .vs{color:var(--dim);font-size:12px;letter-spacing:.1em;text-transform:uppercase}
.lead{font-size:20px;font-weight:800;text-align:center;margin:10px 0 2px}
.settle{text-align:center}
.roster{list-style:none;margin:0;padding:0}
.roster li{display:grid;grid-template-columns:3.6em 1fr auto;gap:2px 10px;padding:9px 0;border-bottom:1px solid var(--line);align-items:start}
.roster li:last-child{border-bottom:0}
.roster .slot{font-weight:800;color:var(--dim);font-size:12px;padding-top:3px;letter-spacing:.06em}
.roster .name{font-weight:700}.roster .meta{grid-column:2/4;font-size:13px;color:var(--muted)}
.roster .pts{font-variant-numeric:tabular-nums;font-weight:800;text-align:right;white-space:nowrap;font-size:17px}
.roster .pts.unk{color:var(--muted);font-weight:500}
.st{font-weight:800;font-size:11.5px;letter-spacing:.06em}.st.NOTSTARTED{color:var(--cyan)}.st.PLAYING{color:var(--lime)}
.st.FINAL{color:var(--muted)}.st.SUSPENDED,.st.UNKNOWN,.st.CANCELED{color:var(--warn)}.st.EMPTY{color:var(--bad)}
.act h3{margin:6px 0 4px}
.chg li{margin:3px 0}
.moves li{margin:4px 0}
.gd-h1{font-size:22px;margin:4px 0 10px}
.hero-card{background:var(--card2);border-color:var(--line2);padding:20px 18px 14px;margin-top:4px}
.modebar #gd-refresh{margin-left:auto;min-width:112px}
.hero-card .modebar{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:4px 14px;margin:12px 0 0;padding-top:12px;border-top:1px solid var(--line)}.hero-card #gd-modepill{grid-column:1;justify-self:start}.hero-card #gd-asof{grid-column:1}.hero-card #gd-refresh{grid-column:2;grid-row:1 / span 2;margin-left:0}.hero-card .status{margin:6px 0 0}.about{margin:2px 0 0}.about>summary{min-height:44px;display:flex;align-items:center;font-size:var(--t-s);color:var(--muted);font-weight:600}
@media (min-width:700px){.gd-h1{font-size:26px}.score .pts{font-size:56px}}
@media (max-width:560px){.score .pts{font-size:40px}.hero-card{padding:16px 12px 12px}}
"""


def _e(x: object) -> str:
    return html.escape("" if x is None else str(x))


def _json_for_html(blob: object) -> str:
    """JSON that cannot break out of a <script type=application/json> block:
    every angle bracket and ampersand is a \\u escape, which JSON.parse
    turns back into the character."""
    return (json.dumps(blob, separators=(",", ":"), default=str)
            .replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def _origin(url: object) -> str:
    parts = urlsplit(str(url or ""))
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else "'none'"


def _pill(state: str) -> str:
    cls = {NOT_STARTED: "info", PLAYING: "ok", FINAL: "", SUSPENDED: "warn",
           CANCELED: "warn", UNKNOWN: "warn", EMPTY: "bad"}.get(state, "")
    return f"<span class=\"pill {cls}\">{_e(state)}</span>"


def _starter_li(s: StarterView, *, show_lock: bool = True) -> str:
    pts = ("<span class=\"pts unk\">—</span>" if s.empty or s.points is None
           else f"<span class=\"pts\">{s.points:.2f}</span>")
    meta = [f"<span class=\"st {_e(s.state.replace(' ', ''))}\">{_e(s.state)}</span>"]
    if s.empty:
        meta.append("this slot scores nothing")
    else:
        meta.append(_e(f"{s.position or '?'} · {s.team or '?'}"))
        if s.game.opponent:
            meta.append(_e(f"{'vs' if s.game.home else 'at'} {s.game.opponent}"))
        if show_lock:
            meta.append(f"<span class=\"{'warn' if s.lock == 'UNKNOWN' else ''}\">"
                        f"{_e('lock ' + s.lock)}</span>")
        if s.injury:
            meta.append(f"<span class=\"warn\">{_e(s.injury)}</span>")
        if not s.named:
            meta.append("<span class=\"warn\">name not in the cached player dump</span>")
    detail = []
    if not s.empty:
        detail.append(f"game: {s.game.note}")
        if show_lock:
            detail.append(f"lock: {s.lock_note}")
        detail.append(f"points: {s.points_note}")
        if s.injury_note:
            detail.append(f"injury: {s.injury_note}")
    return ("<li>"
            f"<span class=\"slot\">{_e(s.slot)}</span>"
            f"<span class=\"name\">{_e(s.name)}</span>{pts}"
            f"<span class=\"meta\">{' · '.join(meta)}"
            + (f"<details><summary class=\"small\">why</summary><ul class=\"small\">"
               + "".join(f"<li>{_e(d)}</li>" for d in detail) + "</ul></details>"
               if detail else "")
            + "</span></li>")


def _side_html(side: SideView | None, reason: str, *, mine: bool) -> str:
    if side is None:
        return f"<p class=\"warn\"><b>No opponent:</b> {_e(reason)}</p>"
    out = [f"<p class=\"small sub\">{_e(side.exposure())}</p>",
           "<ul class=\"roster\">" + "".join(_starter_li(s, show_lock=mine) for s in side.starters)
           + "</ul>"]
    if side.override is not None:
        out.append(f"<p class=\"small warn\">platform override in effect: total {side.override:.2f}</p>")
    out.append(f"<p class=\"small sub\">{_e(side.reconciliation)}</p>")
    for n in side.notes:
        out.append(f"<p class=\"small warn\">{_e(n)}</p>")
    return "".join(out)


def _action_html(a: LiveAction) -> str:
    cls = "act" if a.available else "act off"
    head = ("<span class=\"pill ok\">AVAILABLE</span>" if a.available
            else "<span class=\"pill\">NOT NOW</span>")
    out = [f"<div class=\"{cls}\">{head} <span class=\"pill\">{_e(a.kind)}</span>",
           f"<h3>{_e(a.title)}</h3>"]
    if a.available:
        out.append(f"<p>{_e(a.body)}</p><p><b>{_e(a.deadline_note)}</b></p>")
        if a.backup:
            out.append(f"<p class=\"why\">Backup — {_e(a.backup)}</p>")
        out.append(f"<p class=\"why\">{_e(a.why)}. Advice as of the pregame record; "
                   f"check the current designation above before acting. Nothing here "
                   f"is submitted to Sleeper.</p>")
    else:
        out.append(f"<p class=\"why\">{_e(a.why)}.</p>")
    out.append("</div>")
    return "".join(out)


def render_gameday_html(d: GameDay, *, include_names: bool = True) -> str:
    nonce = secrets.token_urlsafe(16)
    api = d.embedded.get("api", {})
    # CSP source expressions are ORIGINS: a path on a source must match the
    # request path exactly, so `https://host/v1` would block `/v1/state/nfl`.
    bases = " ".join(sorted({_origin(api.get("base")), _origin(api.get("schedule_base"))}))
    title = f"Game Day — week {d.week}"
    if include_names:
        title += f" — {d.league_name}"
    s, m, o = d.score, d.score.mine, d.score.opp
    lead = s.lead()
    lead_cls = "ok" if (s.margin or 0) > POINTS_EPS else "bad" if (s.margin or 0) < -POINTS_EPS else ""
    out: list[str] = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        f"<meta name=\"viewport\" content=\"{theme.VIEWPORT}\">",
        # The policy is the request fan-out guard: the page may talk to the
        # Sleeper hosts it names and to nothing else, run only its own script,
        # load no image, font or frame, and submit no form anywhere.
        # 'self' is the published-build check: a conditional GET of this
        # page's own URL on the hosting origin, and nothing else.
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        f"connect-src 'self' {_e(bases)}; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        f"base-uri 'none'; form-action 'none'\">",
        theme.build_meta(d.generated.astimezone(timezone.utc).isoformat(timespec="seconds"),
                         "gameday_latest.html"),
        f"<title>{_e(title)}</title><style nonce=\"{nonce}\">{_CSS}</style></head><body><main>",
        theme.nav_html("gameday"),
        "<header class=\"hero\">",
        f"<div class=\"eyebrow\">Week {d.week} · Game Day"
        + (f" · {_e(d.league_name)}" if include_names and d.league_name else "")
        + f" · roster #{_e(d.my_roster_id)}</div>",
        f"<h1 class=\"gd-h1\">Game Day — week {d.week}</h1></header>",
        theme.snapshot_banner_html(),
    ]
    # 1. score — the first thing on the page, with the one control that
    # renews it right beside it.
    # The script re-renders #gd-score; the refresh bar beside it in the same
    # card is outside it, so a refresh never removes its own controls.
    out.append("<h2 class=\"vh\">Score</h2><div class=\"card hero-card\"><div id=\"gd-score\">")
    if o is None:
        out.append(f"<div class=\"score\"><div><div class=\"who\">{_e(m.label)}</div>"
                   f"<div class=\"pts\">{_e(_pts(m.platform_points))}</div></div>"
                   f"<div class=\"vs\">vs</div><div><div class=\"who\">no opponent</div>"
                   f"<div class=\"pts\">—</div></div></div>"
                   f"<p class=\"warn\">{_e(s.opp_reason)}</p>")
    else:
        out.append(f"<div class=\"score\"><div><div class=\"who\">{_e(m.label)}</div>"
                   f"<div class=\"pts\">{_e(_pts(m.platform_points))}</div></div>"
                   f"<div class=\"vs\">vs</div><div><div class=\"who\">{_e(o.label)}</div>"
                   f"<div class=\"pts\">{_e(_pts(o.platform_points))}</div></div></div>"
                   f"<p class=\"lead {lead_cls}\">{_e(lead)}</p>"
                   f"<p class=\"settle small\">{_e(s.settled())}</p>"
                   f"<p class=\"small sub\">You: {_e(m.exposure())}<br>They: {_e(o.exposure())}</p>")
    out.append("</div>")
    # The as-of is stated once, in the refresh bar at the foot of the score
    # card, which the script keeps current after every refresh. The mode pill
    # and the status line stay in view (a failed refresh is said there); how
    # refresh works is one tap below.
    out += [
        "<div class=\"modebar\" id=\"gd-mode\">"
        "<span class=\"pill\" id=\"gd-modepill\">SNAPSHOT</span>"
        f"<span id=\"gd-asof\" class=\"small\">{_e(s.as_of_note)}</span>"
        "<button type=\"button\" id=\"gd-refresh\" class=\"primary\" disabled "
        "title=\"needs the page script\">Refresh</button>"
        "</div>",
        "<p class=\"small status\" id=\"gd-status\" role=\"status\" aria-live=\"polite\">"
        "Snapshot: the cloud build's scores. Refresh needs the page script.</p>",
        "<details class=\"about\" id=\"gd-about\"><summary>About refresh</summary>"
        "<p class=\"small sub\">Reloading the file fetches nothing; Refresh makes three "
        "read-only requests to Sleeper for scores and game statuses, and the page then keeps "
        "itself current while it is open and visible. Designations, positions, kickoff times "
        "and the pregame record are from the cloud build "
        f"({_e(_stamp(d.generated))}) and are not refreshed by this page. Platform totals "
        "as sent; nothing here is projected or scaled.</p></details></div>",
    ]
    if d.notes:
        out.append("<div class=\"banner\" id=\"gd-notes\"><b class=\"warn\">Read first</b><ul class=\"small\">"
                   + "".join(f"<li>{_e(n)}</li>" for n in d.notes) + "</ul></div>")
    else:
        out.append("<div class=\"banner ok hidden\" id=\"gd-notes\"></div>")

    # 2. actions
    out.append("<h2>What you can still do</h2><div class=\"card\" id=\"gd-actions\">")
    out.append(f"<p>{_e(d.capacity.sentence())}</p>")
    live = [a for a in d.actions if a.available]
    held = [a for a in d.actions if not a.available]
    if not d.pregame.found:
        out.append(f"<p class=\"sub\">{_e(d.pregame.note)}. Without a decision-time record "
                   f"there is no advice to re-check; only the lock counts above are known.</p>")
    elif not d.actions:
        out.append("<p class=\"sub\">The pregame board listed no action for this week.</p>")
    else:
        if live:
            out += [_action_html(a) for a in live]
        else:
            out.append("<p class=\"sub\">None of the pregame board's actions is still "
                       "available.</p>")
        if held:
            out.append(f"<details><summary>{len(held)} pregame item(s) not available now</summary>"
                       + "".join(_action_html(a) for a in held) + "</details>")
    out.append("<p class=\"small sub\">Legal means: both players proven unlocked by the "
               "schedule, not under way per the feed, still on the roster and still where "
               "the advice left them. Nothing is ever submitted to Sleeper.</p></div>")

    # 2b. what the board's radar found — the board's finding, not a re-judgement
    p = d.pregame
    r = p.radar
    out.append("<h2>Free agents — what the pregame board found</h2><div class=\"card\" id=\"gd-radar\">")
    if not p.found:
        out.append("<p class=\"sub\">No pregame record, so no radar to report.</p>")
    elif not r:
        out.append("<p class=\"sub\">The pregame record predates the Free Agent Radar and carries "
                   "no pool comparison.</p>")
    else:
        moves = r.get("moves") or []
        def n(key: str) -> str:
            v = r.get(key)
            return str(int(v)) if isinstance(v, (int, float)) else "?"
        out.append(f"<p><b>{len(moves)} available player(s) improved that week's lineup</b> on the "
                   f"board's arithmetic, out of {n('evaluated')} compared "
                   f"(pool {n('pool')}, {n('unprojected')} without a projection), "
                   f"against the league snapshot of {_e(r.get('snapshot_as_of'))}.</p>")
        if r.get("abstained"):
            out.append(f"<p class=\"small warn\">The board abstained: {_e(r.get('abstained'))}</p>")
        if moves:
            out.append("<ul class=\"moves\">" + "".join(
                f"<li{_lapse(m.get('deadline'), d.generated)[0]}><span class=\"pill\">PREGAME</span> "
                f"<b>{_e(m['name'])}</b> ({_e(m['position'])}) "
                f"into {_e(m['slot'])}: <span class=\"num\">{'+' if (m['lineup_gain'] or 0) > 0 else ''}{_pts(m['lineup_gain'])}</span> to the best "
                f"legal lineup on the pregame numbers, "
                + ("with no drop (the roster had an open spot) — " if m.get("no_drop") else
                   f"at the cost of dropping {_e(m['drop'])} — ")
                + f"conditional on availability, which the board could not verify"
                + (f"; drop UNVERIFIED — {_e(m['drop_check'])}" if m.get("drop_check") else "")
                + f"<span class=\"vstate\">{_lapse(m.get('deadline'), d.generated)[1]}</span></li>"
                for m in moves) + "</ul>")
        out.append("<p class=\"small sub\">A pickup is not a game-day move: Sleeper processes "
                   "claims on its own clock and this page never re-judges one. The full "
                   "comparison, with every alternative drop, is the "
                   "<a href=\"dashboard_latest.html#free-agents\">Free Agents</a> tab.</p>")
    out.append("</div>")

    # Where every date on this page comes from: one tap away, below the
    # decisions (the refresh bar above already dates the scores).
    out.append(theme.meta_details(
        "<div class=\"ages\">"
        + theme.age_span("Live scores", None, "snapshot until you tap Refresh")
        + theme.age_span("League snapshot", s.as_of.astimezone(timezone.utc).isoformat(timespec="seconds") if s.as_of else None,
                         _stamp(s.as_of) if s.as_of else "no as-of")
        + theme.age_span("Designations", d.embedded.get("players_as_of"),
                         (_stamp(_parse_dt(d.embedded.get("players_as_of"))) if d.embedded.get("players_as_of") else "never pulled")
                         + " (once-a-day player map)")
        + theme.age_span("Page built", d.generated.astimezone(timezone.utc).isoformat(timespec="seconds"), _stamp(d.generated))
        + "</div>"
        + f"<p class=\"small sub\">season {d.season} · roster #{_e(d.my_roster_id)}</p>"
        + theme.snapshot_strip_html()))
    # 3. changes
    out.append("<h2>Since the last snapshot</h2><div class=\"card\" id=\"gd-changes\">")
    ch = d.changes
    if ch.items:
        out.append(f"<p class=\"small sub\">against the game-day snapshot of {_e(ch.previous)}</p>"
                   "<ul class=\"chg\">" + "".join(
                       f"<li><span class=\"pill\">{_e(k)}</span> <b>{_e(sub)}</b>: {_e(det)}</li>"
                       for k, sub, det in ch.items) + "</ul>")
    else:
        out.append(f"<p class=\"sub\">{_e(ch.note)}"
                   + (f" (previous: {_e(ch.previous)})" if ch.previous else "") + "</p>")
    out.append("<p class=\"small sub\">Like-for-like: this compares Sleeper matchup rows and "
               "the status feed against the same kinds of source only. A total that went "
               "down is named a correction.</p></div>")

    # 4. teams
    out.append(f"<h2>Your starters</h2><div class=\"card\" id=\"gd-mine\">{_side_html(m, '', mine=True)}</div>")
    out.append(f"<h2>{_e(o.label if o else 'Opponent')}</h2><div class=\"card\" id=\"gd-opp\">"
               f"{_side_html(o, s.opp_reason, mine=False)}</div>")
    out.append("<div id=\"gd-bench\"><details><summary>Your bench and IR"
               f" ({len(m.bench)})</summary><div class=\"card\"><ul class=\"roster\">"
               + "".join(_starter_li(b) for b in m.bench)
               + "</ul><p class=\"small sub\">Bench points are the platform's "
                 "players_points where sent. A bench player outscoring a starter is a "
                 "fact about two box scores, not a verdict on a decision.</p></div></details></div>")

    # 5. pregame
    p = d.pregame
    out.append("<h2>What the pregame board said</h2><div class=\"card\" id=\"gd-pregame\">")
    out.append(f"<p class=\"small\">{_e(p.note)}.</p>")
    if p.found:
        if p.projection:
            out.append(f"<p class=\"small sub\">{_e(p.projection)}</p>")
        if p.withheld:
            out.append(f"<p class=\"small warn\">At decision time the board WITHHELD: "
                       f"{_e(', '.join(p.withheld))}.</p>")
        endorsed = [a for a in p.actions if a.archived_status == "ACTIONABLE"]
        out.append(f"<p>{len(endorsed)} action(s) endorsed pregame, "
                   f"{len(p.actions) - len(endorsed)} withheld; "
                   f"{sum(1 for a in p.actions if not a.eligible)} not decision-time "
                   f"evidence (written at or after their own deadline).</p>")
        if p.designation_changes:
            out.append("<p class=\"small\"><b>Designations that changed after the record:</b></p><ul class=\"small\">"
                       + "".join(f"<li>{_e(x)}</li>" for x in p.designation_changes) + "</ul>")
        out.append("<div id=\"gd-outcomes\">")
        if p.outcomes:
            out.append("<p class=\"small\"><b>Advised swaps against the box scores so far:</b></p><ul class=\"small\">"
                       + "".join(f"<li>{_e(x)}</li>" for x in p.outcomes) + "</ul>")
        out.append("</div>")
        out.append(f"<p class=\"small sub\">Record: <code>{_e(p.path.name)}</code> — frozen at "
                   f"decision time and not modified by this page.</p>")
    out.append("</div>")

    # 6. sources
    out.append("<h2>Inputs</h2><div class=\"card\"><ul class=\"small\">"
               + "".join(f"<li><span class=\"{ {'fresh': 'ok', 'stale': 'warn', 'missing': 'bad'}[x.status.value] }\">"
                         f"{_e(x.status.value.upper())}</span> {_e(x.name)} — as of "
                         f"{_e(_stamp(x.as_of))}: {_e(x.reason)}</li>" for x in d.sources)
               + f"<li><span class=\"{'ok' if d.feed.fresh else 'warn'}\">"
                 f"{'FRESH' if d.feed.fresh else 'NOT CURRENT'}</span> game status feed — "
                 f"{_e(d.feed.reason)}; source: Sleeper's schedule feed, which is not in its "
                 f"published API documentation and may change without notice</li></ul>")
    out.append("<p class=\"small sub\">Game status, injury designation, roster eligibility and "
               "the kickoff lock are four different things and are shown as four. A kickoff "
               "time proves nothing about whether a game started, is in overtime, was "
               "suspended or ended; only the feed does, and only while it is current. No "
               "live win probability is computed. No projection is scaled by the clock or "
               "added to points already earned.</p></div>")
    out.append(f"<script type=\"application/json\" id=\"gd-data\">{_json_for_html(d.embedded)}</script>")
    out.append(f"<script nonce=\"{nonce}\">{_JS}</script>")
    out.append(f"<script nonce=\"{nonce}\">{theme.AGES_JS}</script>")
    out.append(f"<script nonce=\"{nonce}\">{theme.SNAPSHOT_JS}</script>")
    out.append(f"<script nonce=\"{nonce}\">{theme.VALIDITY_JS}</script>")
    out.append("</main></body></html>")
    return "\n".join(out)


# --------------------------------------------------------------------------
# The inline script: live mode
# --------------------------------------------------------------------------
#: Mirrors the Python model above, on purpose and by hand (tests/
#: test_gameday_parity.py runs both on one payload): a refresh must turn
#: three raw payloads into the same page without a server. Every string from
#: upstream reaches the DOM through textContent, never innerHTML. Nothing is
#: fetched until the owner taps Refresh once; from then on the page polls
#: only while visible, only while a game can still move, and backs off on
#: every failure. Each request has its own timeout. A payload is validated
#: field by field before it replaces anything (a missing point stays null,
#: never 0); a response that started before a newer one, or whose server
#: date is older than the last applied one, is discarded. A refresh renews
#: the roster, scores and game statuses ONLY: designations, positions,
#: kickoff times and the pregame record stay from the build and the page
#: says so. Every 15 s, on every failure and whenever the tab becomes
#: visible, the page re-judges locks, ages and legality at the present
#: instant WITHOUT fetching, so a kickoff that passes while the page sits
#: idle takes the old AVAILABLE card with it.
_JS = r"""
(function(){
'use strict';
var dataEl=document.getElementById('gd-data'); if(!dataEl||!window.fetch) return;
var D; try{ D=JSON.parse(dataEl.textContent); }catch(e){ return; }
var FEED={}; (D.feed_words||[]).forEach(function(p){ FEED[p[0]]=p[1]; });
var ALIAS=D.team_aliases||{};
var TERMINAL={FINAL:1,CANCELED:1};
var PENDING={'NOT STARTED':1,PLAYING:1,SUSPENDED:1,UNKNOWN:1};
var ORDER=['NOT STARTED','PLAYING','SUSPENDED','UNKNOWN','FINAL','CANCELED','EMPTY'];
var WORD={'NOT STARTED':'yet to play',PLAYING:'playing',FINAL:'final',SUSPENDED:'suspended',
          CANCELED:'canceled',UNKNOWN:'unknown',EMPTY:'empty slot'};
var NOT_STARTABLE={}; (D.not_startable||[]).forEach(function(w){ NOT_STARTABLE[w]=1; });
var FLEX={}; (D.flex_eligible||[]).forEach(function(w){ FLEX[w]=1; });
var LEGALITY=D.legality_sources||['sleeper_league','sleeper_players','schedules'];
var EPS=0.005, THROTTLE_MS=10000, POLL_LIVE_MS=120000, POLL_PRE_MS=600000, POLL_UNKNOWN_MS=600000,
    BACKOFF_MAX_MS=1200000, TICK_MS=15000, WINDOW_WARN_MS=60000, DEFAULT_TIMEOUT_MS=15000;

function $(id){ return document.getElementById(id); }
function el(tag,cls,text){ var n=document.createElement(tag); if(cls) n.className=cls;
  if(text!==undefined&&text!==null) n.textContent=String(text); return n; }
function clear(n){ while(n&&n.firstChild) n.removeChild(n.firstChild); return n; }
function num(v){ return (typeof v==='number'&&isFinite(v)) ? v : null; }
function numOrNull(v){ return v===null||v===undefined||(typeof v==='number'&&isFinite(v)); }
function pts(v){ return v===null||v===undefined ? '—' : v.toFixed(2); }
function teamKey(t){ t=String(t===null||t===undefined?'':t).trim().toUpperCase();
  if(t==='NAN'||t==='NONE') t=''; return ALIAS[t]||t; }
function normId(v){ if(v===null||v===undefined) return ''; var s=String(v).trim();
  if(/^\d+\.0$/.test(s)) s=s.slice(0,-2); return s; }
function fmt(iso){ if(!iso) return 'unknown time'; var d=new Date(iso); if(isNaN(d)) return 'unknown time';
  return d.toISOString().replace('T',' ').slice(0,16)+' UTC'; }
function fmtMs(ms){ return (ms===null||ms===undefined||isNaN(ms))?'unknown time':fmt(new Date(ms).toISOString()); }
function ageText(ms,nowMs){ if(!ms) return 'age unknown'; var s=Math.max(0,(nowMs-ms)/1000);
  if(s<90) return Math.floor(s)+'s ago'; if(s<5400) return Math.floor(s/60)+' min ago';
  if(s<172800) return (s/3600).toFixed(1)+' h ago'; return (s/86400).toFixed(1)+' days ago'; }
function isDst(sid){ return !!sid && !/^\d+$/.test(sid); }
function nameOf(sid){ var r=D.names[sid]; if(isDst(sid)) return {name:teamKey(sid)+' DST',position:'DST',team:teamKey(sid),named:true,injury:'',positions:['DST']};
  if(!r) return {name:'sleeper:'+sid,position:'',team:'',named:false,injury:'',positions:[]}; return r; }
function eligibleFor(slot,pos){ pos=String(pos||'').toUpperCase(); if(pos==='DEF') pos='DST'; slot=String(slot||'').toUpperCase();
  if(slot==='FLEX') return !!FLEX[pos]; return pos===slot; }

// ---- the clock. Real time plus a skew the scenario harness may set; the
// page never trusts it for game status, only for locks, ages and deadlines.
var S={seq:0,applied:0,inflight:false,mode:'snapshot',lastGood:null,lastGoodRaw:null,lastGoodAt:null,lastGoodAsOf:D.snapshot.as_of,
  lastServerMs:null,lastAttemptAt:null,lastError:'',failures:0,timer:null,live:false,rollover:false,history:[],
  skewMs:0,timeoutMs:DEFAULT_TIMEOUT_MS,sig:'',view:null,lastChanges:[],prevStamp:'',asOfNote:'',nextPollMs:null};
function now(){ return Date.now()+S.skewMs; }

// ---- source freshness, re-judged at every tick against the same cadences
// the cloud build used. A flag computed at build time is never frozen.
function etWeekday(ms){ try{ return new Intl.DateTimeFormat('en-US',{timeZone:D.league_tz,weekday:'short'}).format(new Date(ms)); }
  catch(e){ return ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'][new Date(ms).getUTCDay()]; } }
function limitHours(src,nowMs){ var wd=etWeekday(nowMs), planning=(wd==='Tue'||wd==='Sat');
  return (!planning&&src.gameday_max_age_hours!==null&&src.gameday_max_age_hours!==undefined)?src.gameday_max_age_hours:src.max_age_hours; }
function sourceState(name,nowMs,raw){
  var src=(D.sources||{})[name];
  if(!src||!src.assessed) return {fresh:false,as_of:null,reason:name+' freshness was never assessed'};
  var league=(name==='sleeper_league'), asOf=league?raw.as_of:src.as_of;
  if(!src.fresh_at_build&&!(league&&!raw.built)) return {fresh:false,as_of:asOf,reason:src.blocker||(name+' is '+String(src.status).toUpperCase()+' ('+src.reason+')')};
  var t=Date.parse(asOf||''); if(isNaN(t)) return {fresh:false,as_of:asOf,reason:name+' carries no as-of time'};
  var lim=limitHours(src,nowMs), age=(nowMs-t)/3600000;
  if(age>lim) return {fresh:false,as_of:asOf,reason:name+' is STALE ('+age.toFixed(1)+' h old against a '+lim+' h limit'+(league?'':'; not renewed by a browser refresh')+')'};
  return {fresh:true,as_of:asOf,reason:name+' is current ('+age.toFixed(1)+' h old)'};
}
function feedFresh(feed,nowMs){ if(!feed||!feed.present||!feed.fresh) return false; var t=Date.parse(feed.as_of||''); if(isNaN(t)) return false;
  var src=(D.sources||{})[D.feed_source||'game_status']||{max_age_hours:12,gameday_max_age_hours:0.5};
  return (nowMs-t)/3600000<=limitHours(src,nowMs); }

// ---- game status, from observed evidence only
function feedReason(feed,nowMs){ if(!feed.fresh&&feed.reason) return feed.reason;
  var t=Date.parse(feed.as_of||''); if(isNaN(t)) return 'the feed carries no as-of time';
  var src=(D.sources||{})[D.feed_source||'game_status']||{max_age_hours:12,gameday_max_age_hours:0.5};
  return 'feed is STALE: observed '+ageText(t,nowMs)+', over the '+limitHours(src,nowMs)+' h limit'; }
function gameStatus(team,feed,fresh,nowMs){
  var t=teamKey(team);
  if(!t) return {team:'',state:'UNKNOWN',raw:'',opponent:'',home:null,note:'no NFL team on this player’s record',current:false};
  if(!feed||!feed.present) return {team:t,state:'UNKNOWN',raw:'',opponent:'',home:null,note:'no game-status feed in the cache; a kickoff time alone cannot prove a game started or ended',current:false};
  var hits=(feed.rows||[]).filter(function(r){ return teamKey(r.home)===t||teamKey(r.away)===t; });
  if(!hits.length) return {team:t,state:'UNKNOWN',raw:'',opponent:'',home:null,note:'the feed lists no week-'+D.week+' game for '+t+'; a missing row is not a bye and not a final',current:false};
  if(hits.length>1){ var words={}; hits.forEach(function(r){ words[String(r.status)]=1; });
    var ws=Object.keys(words).sort(); if(ws.length>1) return {team:t,state:'UNKNOWN',raw:'',opponent:'',home:null,note:'the feed lists '+hits.length+' week-'+D.week+' games for '+t+' with different statuses ('+ws.join(', ')+'); neither is trusted',current:false}; }
  var row=hits[0], raw=String(row.status||''), state=FEED[raw]||'UNKNOWN';
  var home=teamKey(row.home)===t, opp=teamKey(home?row.away:row.home), stamp=fmt(feed.as_of);
  if(state==='UNKNOWN') return {team:t,state:'UNKNOWN',raw:raw,opponent:opp,home:home,note:'the feed reports "'+raw+'", a status this page does not know; nothing is assumed from it',current:fresh};
  if(TERMINAL[state]) return {team:t,state:state,raw:raw,opponent:opp,home:home,note:'feed: '+raw+' (observed '+stamp+')',current:true};
  if(!fresh) return {team:t,state:'UNKNOWN',raw:raw,opponent:opp,home:home,note:'the feed said '+raw+' at '+stamp+', which is too old to say anything about now ('+feedReason(feed,nowMs)+')',current:false};
  return {team:t,state:state,raw:raw,opponent:opp,home:home,note:'feed: '+raw+' (observed '+stamp+')',current:true};
}
// ---- kickoff lock, from the schedule only (three-valued)
function lockState(team,nowMs){
  var t=teamKey(team);
  if(!D.schedule_loaded) return {lock:'UNKNOWN',note:'lock state UNKNOWN: no schedule loaded for this week',kickoff:null};
  if(!t) return {lock:'UNKNOWN',note:'lock state UNKNOWN: no NFL team on this player’s record',kickoff:null};
  if(D.time_unknown.indexOf(t)>=0) return {lock:'UNKNOWN',note:'lock state UNKNOWN: '+t+' has a week-'+D.week+' game but no usable kickoff time',kickoff:null};
  if(D.declared_bye.indexOf(t)>=0) return {lock:'OPEN',note:'no week-'+D.week+' game: BYE declared by the schedule',kickoff:null};
  var k=D.kickoffs[t];
  if(!k) return {lock:'UNKNOWN',note:'lock state UNKNOWN: the schedule carries no week-'+D.week+' game for '+t+' and declares no bye; a missing row and a week off look identical',kickoff:null};
  var ms=Date.parse(k); if(isNaN(ms)) return {lock:'UNKNOWN',note:'lock state UNKNOWN: unreadable kickoff',kickoff:null};
  if(nowMs>=ms) return {lock:'LOCKED',note:'LOCKED — kicked off '+fmt(k),kickoff:ms};
  return {lock:'OPEN',note:'kicks off '+fmt(k),kickoff:ms};
}
function statusAndLock(team,ctx){
  var g=gameStatus(team,ctx.feed,ctx.fresh,ctx.nowMs), l=lockState(team,ctx.nowMs);
  if(g.current&&g.state==='NOT STARTED'&&l.lock==='LOCKED')
    g.note+='; the schedule kickoff has passed but the feed still says pre-game (delayed start, moved game, or feed lag) — Sleeper locks at the real kickoff';
  else if(g.current&&(g.state==='PLAYING'||g.state==='FINAL'||g.state==='SUSPENDED')&&l.lock==='OPEN')
    l={lock:'LOCKED',note:'LOCKED — the feed says the game is '+g.raw+' although the schedule kickoff is still ahead; the observed status wins over the timetable',kickoff:l.kickoff};
  return {game:g,lock:l.lock,lockNote:l.note,kickoff:l.kickoff};
}
function starter(sid,slot,ctx){
  sid=normId(sid);
  if(!sid||sid==='0') return {slot:slot,sleeper_id:'',name:'EMPTY',position:'',team:'',points:null,points_note:'an empty slot scores nothing',
    game:{state:'EMPTY',note:'',raw:'',current:false,opponent:'',home:null},state:'EMPTY',lock:'OPEN',lock_note:'an empty slot can take any unlocked eligible player',kickoff:null,injury:'',injury_note:'',empty:true,named:true,positions:[]};
  var n=nameOf(sid), sl=statusAndLock(n.team,ctx);
  var inj=String(n.injury||''), injNote=inj?('designation from the player dump of '+fmt(D.players_as_of)+(ctx.players.fresh?'':' — STALE, not a current status')):'';
  return {slot:slot,sleeper_id:sid,name:n.name,position:n.position,team:n.team,points:null,points_note:'',game:sl.game,state:sl.game.state,
    lock:sl.lock,lock_note:sl.lockNote,kickoff:sl.kickoff,injury:inj,injury_note:injNote,empty:false,named:!!n.named,positions:(n.positions||[]).slice()};
}
function side(row,players,reserve,taxi,label,withBench,ctx){
  if(!row) return null;
  var ids=(row.starters||[]).map(normId), sp=Array.isArray(row.starters_points)?row.starters_points:[],
      pp=(row.players_points&&typeof row.players_points==='object')?row.players_points:{};
  var views=[], i;
  for(i=0;i<ids.length;i++){
    var slot=i<D.slots.length?D.slots[i]:'SLOT'+(i+1), v=starter(ids[i],slot,ctx);
    if(v.empty){ views.push(v); continue; }
    var val=i<sp.length?num(sp[i]):null, note='platform starters_points';
    if(val===null){ val=num(pp[ids[i]]); note=val!==null?'platform players_points':'the platform sent no value for this starter — UNKNOWN, not 0'; }
    if(val!==null&&v.state==='NOT STARTED'&&Math.abs(val)<=EPS) note+='; game not started, so this 0 is a placeholder';
    v.points=val; v.points_note=note; views.push(v);
  }
  var bench=[];
  if(withBench){ var res=(reserve||[]).map(normId), tx=(taxi||[]).map(normId);
    (players||[]).forEach(function(sid){ sid=normId(sid); if(!sid||ids.indexOf(sid)>=0) return;
    var b=starter(sid,res.indexOf(sid)>=0?'IR':tx.indexOf(sid)>=0?'TAXI':'BN',ctx);
    b.points=num(pp[sid]); b.points_note=b.points!==null?'platform players_points':'no platform value'; bench.push(b); }); }
  var sent=num(row.points), override=num(row.custom_points), total=override!==null?override:sent;
  var known=views.filter(function(s){ return !s.empty&&s.points!==null; }).map(function(s){ return s.points; });
  var unknown=views.filter(function(s){ return !s.empty&&s.points===null; }).length;
  var ssum=(known.length||!unknown)?Math.round(known.reduce(function(a,b){ return a+b; },0)*100)/100:null;
  var parts=[];
  if(!views.length) parts.push('no starters listed');
  else {
    if(override!==null) parts.push('the platform total is a commissioner OVERRIDE (custom_points '+override.toFixed(2)+'; the scored total was '+pts(sent)+')');
    if(total===null) parts.push('the platform sent no total for this roster; nothing is summed in its place');
    else if(unknown) parts.push(unknown+' starter(s) have no platform value; the '+known.length+' known ones sum to '+pts(ssum)+' against the platform total '+total.toFixed(2)+', so the totals cannot be reconciled');
    else { var diff=Math.round((total-(ssum||0))*100)/100;
      if(Math.abs(diff)>EPS) parts.push('the starters’ points sum to '+pts(ssum)+' but the platform total is '+total.toFixed(2)+' — an unexplained difference of '+(diff>0?'+':'')+diff.toFixed(2)+'; the platform total is shown unchanged');
      else parts.push('the starters’ points add up to the platform total'); } }
  var notes=[]; if(ids.length!==D.slots.length) notes.push(ids.length+' starters listed for '+D.slots.length+' slots');
  if(sp.length&&sp.length!==ids.length) notes.push('starters_points has '+sp.length+' values for '+ids.length+' starters');
  var counts={}; views.forEach(function(s){ counts[s.state]=(counts[s.state]||0)+1; });
  var pending=views.filter(function(s){ return !s.empty&&PENDING[s.state]; });
  return {roster_id:row.roster_id,label:label,starters:views,bench:bench,platform_points:total,override:override,starters_sum:ssum,
    unknown_points:unknown,reconciliation:parts.join('; '),notes:notes,counts:counts,pending:pending,
    all_settled:views.every(function(s){ return s.empty||TERMINAL[s.state]; }),
    any_unknown:views.some(function(s){ return !s.empty&&s.state==='UNKNOWN'; })};
}
function exposure(sd){ var bits=[]; ORDER.forEach(function(st){ var n=sd.counts[st]||0; if(!n) return;
  if(PENDING[st]) bits.push(n+' '+WORD[st]+' ('+sd.starters.filter(function(s){ return s.state===st; }).map(function(s){ return s.position||s.slot; }).join(', ')+')');
  else bits.push(n+' '+WORD[st]); }); return bits.length?bits.join('; '):'no starters'; }
function lead(vm){ if(!vm.opp) return 'no opponent to compare against'; var m=vm.margin;
  if(m===null) return 'margin unknown: a platform total is missing'; if(Math.abs(m)<=EPS) return 'level';
  return (m>0?'ahead':'behind')+' by '+Math.abs(m).toFixed(2); }
function settled(vm){ if(!vm.opp) return ''; var me=vm.mine.pending, them=vm.opp.pending;
  if(vm.mine.any_unknown||vm.opp.any_unknown) return 'not settled: at least one starter’s game status is UNKNOWN, so the page cannot say who can still score';
  if(!me.length&&!them.length) return 'every starter on both sides is final; the result stands unless the platform corrects a score';
  var bits=[]; if(them.length) bits.push('they still have '+them.length+' to play or playing ('+them.map(function(s){ return s.position||s.slot; }).join(', ')+')');
  if(me.length) bits.push('you still have '+me.length+' ('+me.map(function(s){ return s.position||s.slot; }).join(', ')+')');
  return 'not settled — '+bits.join('; '); }

// ---- legality NOW. Mirrors gameday._judge_action gate for gate: the
// archive's ACTIONABLE is history, every gate is re-run against the present.
function judge(a,nowMs,byId,starterIds,emptySlots,blockers){
  var ids=(a.player_ids||[]).map(normId).filter(Boolean), dl=a.deadline?Date.parse(a.deadline):NaN, gen=a.generated?Date.parse(a.generated):NaN;
  var slotNamed=String(a.slot||'').toUpperCase();
  function out(avail,why,elig){ return {kind:a.kind,title:a.title,body:a.body,deadline:a.deadline,deadline_note:a.deadline_note,backup:a.backup,archived_status:a.archived_status,
    available:avail,why:why,player_ids:ids,eligible:elig!==false,generated:a.generated||null,slot:slotNamed,superseded:a.superseded||''}; }
  function no(why,elig){ return out(false,why,elig); }
  if(a.kind==='acquire') return no('a pickup is not a game-day move: eligibility was UNVERIFIED pregame and waivers do not process during the slate');
  if(a.archived_status!=='ACTIONABLE') return no('the pregame board WITHHELD this (its inputs were stale or unreadable); a withheld comparison does not become advice because the games started');
  if(isNaN(dl)) return no('its deadline was UNKNOWN at decision time, so it was never a timed, legal move',false);
  if(isNaN(gen)||gen>=dl) return no('the record was written at or after its own deadline, so it is not decision-time evidence for this move',false);
  if(gen>nowMs+(D.future_slack_ms||0)) return no('the record claims to be written at '+fmtMs(gen)+', after now ('+fmtMs(nowMs)+'); a record from the future is not decision-time evidence',false);
  if(a.superseded) return no(a.superseded);
  if(nowMs>=dl) return no('deadline passed ('+fmt(a.deadline)+')');
  if(!ids.length) return no('the record names no player ids (written before actions carried them), so legality cannot be checked without name-matching, which is never done');
  if(blockers.length) return no('the inputs legality rests on are not current — '+blockers.join('; ')+'. The score above stands on its own; the advice does not');
  for(var i=0;i<ids.length;i++){ var p=byId[ids[i]];
    if(!p) return no('sleeper:'+ids[i]+' is no longer on the roster (observed lineup, not a statement about what you did)');
    if(p.lock==='LOCKED') return no(p.name+' is LOCKED — '+p.lock_note);
    if(p.lock!=='OPEN') return no(p.name+'’s lock state is UNKNOWN ('+p.lock_note+'); a move cannot be shown legal');
    if(p.state==='PLAYING'||p.state==='FINAL'||p.state==='SUSPENDED') return no(p.name+'’s game is '+p.state+' per the feed');
    if(p.state==='CANCELED') return no(p.name+'’s game is CANCELED per the feed; the board’s comparison assumed it would be played, and this page does not re-model it');
    if(!p.game.current) return no(p.name+'’s game status is UNCONFIRMED ('+p.game.note+'); the schedule says not started, but a kickoff time alone does not prove it, so the move is not shown legal');
    if(p.state!=='NOT STARTED') return no(p.name+'’s game status is UNKNOWN ('+p.game.note+'); only an observed pre-game status makes a move endorsable, so it is not shown legal'); }
  var incoming=(a.kind==='swap'||a.kind==='inactive_starter'||a.kind==='empty_slot')?byId[ids[0]]:null, outgoing=null;
  if(a.kind==='swap'&&ids.length>=2){ outgoing=byId[ids[1]];
    if(starterIds.indexOf(outgoing.sleeper_id)<0||starterIds.indexOf(incoming.sleeper_id)>=0) return no('the lineup already differs from the one this advice was about (an observation, not proof that you acted on it)'); }
  else if(a.kind==='inactive_starter'){ if(ids.length<2) return no('the board found no legal replacement for this starter at decision time; there is nothing to move in');
    outgoing=byId[ids[1]];
    if(starterIds.indexOf(outgoing.sleeper_id)<0||starterIds.indexOf(incoming.sleeper_id)>=0) return no('the lineup already differs from the one this advice was about (an observation, not proof that you acted on it)'); }
  else if(a.kind==='empty_slot'){ if(starterIds.indexOf(incoming.sleeper_id)>=0) return no('the slot is no longer empty in the observed lineup'); }
  if(incoming){
    if(incoming.slot==='IR'||incoming.slot==='TAXI') return no(incoming.name+' is on '+incoming.slot+'; moving him to the active roster is a roster move, not a lineup change, and it is not offered here');
    if(NOT_STARTABLE[String(incoming.injury||'').toUpperCase()]) return no(incoming.name+'’s current designation is '+incoming.injury+' ('+(incoming.injury_note||'player dump')+'); a player the platform lists '+incoming.injury+' is not offered as a start');
    if(!incoming.positions.length) return no(incoming.name+'’s position is not known (id not in the cached player dump), so slot eligibility cannot be checked'); }
  var dest='';
  if(outgoing) dest=outgoing.slot;
  else if(a.kind==='empty_slot'){
    if(slotNamed){ if(emptySlots.indexOf(slotNamed)<0) return no('the '+slotNamed+' slot is no longer empty in the observed lineup'); dest=slotNamed; }
    else if(emptySlots.length===1) dest=emptySlots[0];
    else if(!emptySlots.length) return no('no slot is empty in the observed lineup');
    else return no('the record does not say which slot was empty and '+emptySlots.length+' are ('+emptySlots.join(', ')+'); a slot is not guessed'); }
  if(incoming&&dest&&!incoming.positions.some(function(p){ return eligibleFor(dest,p); }))
    return no(incoming.name+' ('+incoming.positions.join('/')+') is not eligible for the '+dest+' slot the move would fill');
  var why='every player involved is proven unlocked, not under way per the feed, on the active roster and still where the advice left them'
    +(incoming&&dest?('; '+incoming.name+' ('+incoming.positions.join('/')+') is eligible for '+dest):'')
    +(incoming&&incoming.injury?('; '+incoming.name+' is listed '+incoming.injury+' — eligible, not out (rule #11)'):'')
    +'; the roster, designations and schedule are all current. Sleeper will still refuse it after the real kickoff';
  if(slotNamed&&dest&&slotNamed!==dest) why+='. The record named '+slotNamed+'; the observed lineup has him at '+dest;
  return out(true,why,true);
}
function outcomes(actions,byId,starterIds){ var out=[]; actions.forEach(function(a){
  if(a.kind!=='swap'||a.archived_status!=='ACTIONABLE'||a.player_ids.length<2) return;
  var b=byId[a.player_ids[0]], s=byId[a.player_ids[1]];
  if(!b||!s){ out.push(a.title+': one of the players is no longer on the roster; unproven'); return; }
  var obs='observed now: '+b.name+' is '+(starterIds.indexOf(b.sleeper_id)>=0?'starting':'on the bench')+', '+s.name+' is '+(starterIds.indexOf(s.sleeper_id)>=0?'starting':'on the bench')+' — an observation, not a record of a decision';
  if(TERMINAL[b.state]&&TERMINAL[s.state]&&b.points!==null&&s.points!==null) out.push(a.title+': both final — '+b.name+' '+b.points.toFixed(2)+', '+s.name+' '+s.points.toFixed(2)+' (descriptive; whether the move was made is not known). '+obs);
  else out.push(a.title+': UNPROVEN — '+b.name+' '+pts(b.points)+' ('+b.state+'), '+s.name+' '+pts(s.points)+' ('+s.state+'); not both final. '+obs); }); return out; }

// ---- the view model, from raw payloads, AT an instant
function compute(raw,nowMs){
  var feed=raw.feed, fresh=feedFresh(feed,nowMs), rows=raw.rows||[];
  var players=sourceState('sleeper_players',nowMs,raw), blockers=[];
  LEGALITY.forEach(function(n){ var st=sourceState(n,nowMs,raw); if(!st.fresh) blockers.push(st.reason); });
  var ctx={feed:feed,fresh:fresh,nowMs:nowMs,players:players};
  var mineRow=null, oppRow=null, reason='', i;
  for(i=0;i<rows.length;i++) if(rows[i]&&String(rows[i].roster_id)===String(D.my_roster_id)) mineRow=rows[i];
  if(D.my_roster_id===null||D.my_roster_id===undefined) reason='owner roster not found in the snapshot';
  else if(!mineRow) reason='no week-'+D.week+' matchup row for roster #'+D.my_roster_id;
  else if(mineRow.matchup_id===null||mineRow.matchup_id===undefined) reason='roster #'+D.my_roster_id+' has no matchup this week (matchup_id is null: bye week, or playoffs not yet drawn)';
  else { var others=rows.filter(function(r){ return r&&r.matchup_id===mineRow.matchup_id&&String(r.roster_id)!==String(D.my_roster_id); });
    if(!others.length) reason='matchup '+mineRow.matchup_id+' has no other roster in it (unpaired week or a format this page does not support)';
    else if(others.length>1) reason='matchup '+mineRow.matchup_id+' has '+others.length+' other rosters — a multi-team or median format this page does not support; no opponent is chosen arbitrarily';
    else oppRow=others[0]; }
  var myPlayers=(mineRow&&mineRow.players&&mineRow.players.length)?mineRow.players:raw.my_players;
  var mine=side(mineRow||{},myPlayers,raw.my_reserve,raw.my_taxi,'You',true,ctx);
  var opp=oppRow?side(oppRow,null,null,null,'Roster #'+oppRow.roster_id,false,ctx):null;
  var vm={mine:mine,opp:opp,opp_reason:reason,as_of:raw.as_of,feed_present:!!(feed&&feed.present),feed_fresh:fresh,feed_as_of:feed?feed.as_of:null,blockers:blockers,now_ms:nowMs};
  vm.margin=(opp&&mine.platform_points!==null&&opp.platform_points!==null)?Math.round((mine.platform_points-opp.platform_points)*100)/100:null;
  vm.lead=lead(vm); vm.settled=settled(vm);
  var st=mine.starters.filter(function(s){ return !s.empty; });
  vm.capacity={open_starters:st.filter(function(s){ return s.lock==='OPEN'; }).length,locked_starters:st.filter(function(s){ return s.lock==='LOCKED'; }).length,
    unknown_starters:st.filter(function(s){ return s.lock==='UNKNOWN'; }).length,open_bench:mine.bench.filter(function(s){ return s.lock==='OPEN'&&s.slot==='BN'; }).length,
    empty_slots:mine.starters.filter(function(s){ return s.empty; }).length};
  var c=vm.capacity;
  vm.capacity.sentence=c.unknown_starters?(c.unknown_starters+' starter(s) have an UNKNOWN lock state, so this page cannot say what is still legal; Sleeper’s own lineup screen can. '+c.open_starters+' starter(s) are proven unlocked and '+c.open_bench+' bench player(s) are.')
    :(!c.open_starters&&!c.empty_slots)?'No legal lineup change remains this week: every starter has kicked off, and a locked slot cannot be changed.'
    :(!c.open_bench)?(c.open_starters+' starter(s) are still unlocked, but no bench player is, so no swap can be made; an unlocked starter could only be moved to an empty slot.')
    :('A swap needs both players unlocked: '+c.open_starters+' starter(s) and '+c.open_bench+' bench player(s) still are'+(c.empty_slots?(', and '+c.empty_slots+' slot(s) are empty'):'')+'.');
  var byId={}; mine.starters.concat(mine.bench).forEach(function(s){ if(s.sleeper_id) byId[s.sleeper_id]=s; });
  var starterIds=mine.starters.map(function(s){ return s.sleeper_id; }), emptySlots=mine.starters.filter(function(s){ return s.empty; }).map(function(s){ return s.slot; });
  vm.actions=((D.pregame&&D.pregame.actions)||[]).map(function(a){ return judge(a,nowMs,byId,starterIds,emptySlots,blockers); });
  vm.outcomes=outcomes(vm.actions,byId,starterIds);
  vm.any_playing=[mine].concat(opp?[opp]:[]).some(function(sd){ return sd.starters.some(function(s){ return s.state==='PLAYING'; }); });
  vm.any_unknown=[mine].concat(opp?[opp]:[]).some(function(sd){ return sd.any_unknown; });
  var next=null; [mine].concat(opp?[opp]:[]).forEach(function(sd){ sd.starters.forEach(function(s){ if(s.kickoff&&s.kickoff>nowMs&&(next===null||s.kickoff<next)) next=s.kickoff; }); });
  vm.next_kickoff=next; vm.all_settled=mine.all_settled&&(!opp||opp.all_settled);
  return vm;
}
function diff(prev,cur){ var items=[]; if(!prev) return items;
  ['mine','opp'].forEach(function(k){ var a=prev[k], b=cur[k];
    if(!a||!b){ if(!!a!==!!b) items.push(['matchup',k,'opponent '+(b?'appeared':'gone')]); return; }
    if(String(a.roster_id)!==String(b.roster_id)){ items.push(['matchup',k,'roster #'+a.roster_id+' -> #'+b.roster_id]); return; }
    var pa=a.platform_points, pb=b.platform_points;
    if(pa!==pb){ if(pa!==null&&pb!==null){ if(Math.abs(pb-pa)>EPS) items.push([pb<pa?'score CORRECTION (lowered)':'score',b.label,pa.toFixed(2)+' -> '+pb.toFixed(2)+' ('+(pb-pa>0?'+':'')+(pb-pa).toFixed(2)+')']); }
      else items.push(['score',b.label,pts(pa)+' -> '+pts(pb)]); }
    var was={}, now={}; a.starters.forEach(function(s){ was[s.sleeper_id]=s; }); b.starters.forEach(function(s){ now[s.sleeper_id]=s; });
    if(a.starters.map(function(s){ return s.sleeper_id; }).join(',')!==b.starters.map(function(s){ return s.sleeper_id; }).join(',')) items.push(['lineup',b.label,'starting lineup changed']);
    Object.keys(now).sort().forEach(function(sid){ if(!was[sid]||!sid) return; var x=was[sid], y=now[sid];
      if(x.points!==y.points&&(x.points===null||y.points===null||Math.abs(y.points-x.points)>EPS)) items.push([(x.points!==null&&y.points!==null&&y.points<x.points)?'score CORRECTION (lowered)':'points',y.name,pts(x.points)+' -> '+pts(y.points)]);
      if(x.state!==y.state) items.push(['status',y.name,x.state+' -> '+y.state]); }); });
  if(prev.feed_present!==cur.feed_present) items.push(['source','game status feed',cur.feed_present?'now available':'NO LONGER available']);
  else if(prev.feed_fresh!==cur.feed_fresh) items.push(['source','game status feed',cur.feed_fresh?'current again':'went STALE']);
  return items; }

// ---- rendering (textContent only)
function pill(state){ var cls={'NOT STARTED':'info',PLAYING:'ok',FINAL:'',SUSPENDED:'warn',CANCELED:'warn',UNKNOWN:'warn',EMPTY:'bad'}[state]||'';
  return el('span','pill '+cls,state); }
function li(s,showLock){ var n=el('li'); n.appendChild(el('span','slot',s.slot)); n.appendChild(el('span','name',s.name));
  n.appendChild(el('span',(s.empty||s.points===null)?'pts unk':'pts',(s.empty||s.points===null)?'—':s.points.toFixed(2)));
  var meta=el('span','meta'); meta.appendChild(el('span','st '+s.state.replace(/ /g,''),s.state));
  function sep(){ meta.appendChild(document.createTextNode(' · ')); }
  if(s.empty){ sep(); meta.appendChild(document.createTextNode('this slot scores nothing')); }
  else { sep(); meta.appendChild(document.createTextNode((s.position||'?')+' · '+(s.team||'?')));
    if(s.game.opponent){ sep(); meta.appendChild(document.createTextNode((s.game.home?'vs ':'at ')+s.game.opponent)); }
    if(showLock){ sep(); meta.appendChild(el('span',s.lock==='UNKNOWN'?'warn':'','lock '+s.lock)); }
    if(s.injury){ sep(); meta.appendChild(el('span','warn',s.injury)); }
    if(!s.named){ sep(); meta.appendChild(el('span','warn','name not in the cached player dump')); }
    var det=el('details'), sum=el('summary','small','why'), ul=el('ul','small'); det.appendChild(sum);
    ul.appendChild(el('li',null,'game: '+s.game.note)); if(showLock) ul.appendChild(el('li',null,'lock: '+s.lock_note));
    ul.appendChild(el('li',null,'points: '+s.points_note)); if(s.injury_note) ul.appendChild(el('li',null,'injury: '+s.injury_note));
    det.appendChild(ul); meta.appendChild(det); }
  n.appendChild(meta); return n; }
function renderSide(root,sd,reason,mine){ clear(root); if(!sd){ var p=el('p','warn'); p.appendChild(el('b',null,'No opponent: ')); p.appendChild(document.createTextNode(reason)); root.appendChild(p); return; }
  root.appendChild(el('p','small sub',exposure(sd))); var ul=el('ul','roster'); sd.starters.forEach(function(s){ ul.appendChild(li(s,mine)); }); root.appendChild(ul);
  if(sd.override!==null) root.appendChild(el('p','small warn','platform override in effect: total '+sd.override.toFixed(2)));
  root.appendChild(el('p','small sub',sd.reconciliation)); sd.notes.forEach(function(n){ root.appendChild(el('p','small warn',n)); }); }
function renderScore(vm,asOfNote){ var root=clear($('gd-score')), sc=el('div','score'), m=vm.mine, o=vm.opp;
  function col(who,p){ var d=el('div'); d.appendChild(el('div','who',who)); d.appendChild(el('div','pts',pts(p))); return d; }
  sc.appendChild(col(m.label,m.platform_points)); sc.appendChild(el('div','vs','vs')); sc.appendChild(o?col(o.label,o.platform_points):col('no opponent',null)); root.appendChild(sc);
  if(!o) root.appendChild(el('p','warn',vm.opp_reason));
  else { root.appendChild(el('p','lead '+((vm.margin||0)>EPS?'ok':(vm.margin||0)<-EPS?'bad':''),vm.lead)); root.appendChild(el('p','settle small',vm.settled));
    var ex=el('p','small sub'); ex.appendChild(document.createTextNode('You: '+exposure(m))); ex.appendChild(el('br')); ex.appendChild(document.createTextNode('They: '+exposure(o))); root.appendChild(ex); }
}
function actCard(a){ var d=el('div',a.available?'act':'act off'); d.appendChild(el('span',a.available?'pill ok':'pill',a.available?'AVAILABLE':'NOT NOW'));
  d.appendChild(document.createTextNode(' ')); d.appendChild(el('span','pill',a.kind)); d.appendChild(el('h3',null,a.title));
  if(a.available){ d.appendChild(el('p',null,a.body)); var b=el('p'); b.appendChild(el('b',null,a.deadline_note)); d.appendChild(b);
    if(a.backup) d.appendChild(el('p','why','Backup — '+a.backup));
    d.appendChild(el('p','why',a.why+'. Advice as of the pregame record of '+fmt(a.generated)+'; check the current designation above before acting. Nothing here is submitted to Sleeper.')); }
  else d.appendChild(el('p','why',a.why+'.')); return d; }
function renderActions(vm){ var root=clear($('gd-actions')); root.appendChild(el('p',null,vm.capacity.sentence));
  if(vm.blockers.length) root.appendChild(el('p','small warn','Advice withheld: '+vm.blockers.join('; ')+'. The score is shown on its own freshness.'));
  var live=vm.actions.filter(function(a){ return a.available; }), held=vm.actions.filter(function(a){ return !a.available; });
  if(!D.pregame||!D.pregame.generated) root.appendChild(el('p','sub','No pregame record for this week; without a decision-time record there is no advice to re-check. Only the lock counts above are known.'));
  else if(!vm.actions.length) root.appendChild(el('p','sub','The pregame board listed no action for this week.'));
  else { if(live.length) live.forEach(function(a){ root.appendChild(actCard(a)); }); else root.appendChild(el('p','sub','None of the pregame board’s actions is still available.'));
    if(held.length){ var det=el('details'); det.appendChild(el('summary',null,held.length+' pregame item(s) not available now')); held.forEach(function(a){ det.appendChild(actCard(a)); }); root.appendChild(det); } }
  root.appendChild(el('p','small sub','Legal means: both players proven unlocked by the schedule, reported pre-game by a current feed (an unknown or canceled status is never permission), on the active roster, still where the advice left them, eligible for the slot, not listed out, with the roster, designations and schedule all current. Re-judged at every tick; a refresh renews the roster and scores only. Nothing is ever submitted to Sleeper.')); }
function renderChanges(items,prevStamp,note){ var root=clear($('gd-changes'));
  if(items.length){ root.appendChild(el('p','small sub','against your last reliable snapshot of '+prevStamp)); var ul=el('ul','chg');
    items.forEach(function(c){ var l=el('li'); l.appendChild(el('span','pill',c[0])); l.appendChild(document.createTextNode(' ')); l.appendChild(el('b',null,c[1])); l.appendChild(document.createTextNode(': '+c[2])); ul.appendChild(l); }); root.appendChild(ul); }
  else root.appendChild(el('p','sub',note+(prevStamp?' (previous: '+prevStamp+')':'')));
  root.appendChild(el('p','small sub','Like-for-like: this compares Sleeper matchup rows and the status feed against the same kinds of source only. A total that went down is named a correction.')); }
function renderBench(vm){ var root=clear($('gd-bench')), det=el('details'); det.appendChild(el('summary',null,'Your bench and IR ('+vm.mine.bench.length+')'));
  var card=el('div','card'), ul=el('ul','roster'); vm.mine.bench.forEach(function(b){ ul.appendChild(li(b,true)); }); card.appendChild(ul);
  card.appendChild(el('p','small sub','Bench points are the platform’s players_points where sent. A bench player outscoring a starter is a fact about two box scores, not a verdict on a decision.')); det.appendChild(card); root.appendChild(det); }
function renderOutcomes(vm){ var root=$('gd-outcomes'); if(!root) return; clear(root); if(!vm.outcomes.length) return;
  var p=el('p','small'); p.appendChild(el('b',null,'Advised swaps against the box scores so far:')); root.appendChild(p);
  var ul=el('ul','small'); vm.outcomes.forEach(function(x){ ul.appendChild(el('li',null,x)); }); root.appendChild(ul); }
function renderAll(vm,changes,prevStamp,asOfNote){ renderScore(vm,asOfNote); renderActions(vm); renderChanges(changes,prevStamp,changes.length?'':'nothing changed since your last reliable snapshot');
  renderSide($('gd-mine'),vm.mine,'',true); renderSide($('gd-opp'),vm.opp,vm.opp_reason,false); renderBench(vm); renderOutcomes(vm); }
function setMode(mode,cls){ var p=$('gd-modepill'); p.textContent=mode; p.className='pill '+(cls||''); }
function setStatus(text){ $('gd-status').textContent=text; }
function setAsOf(){ var t=S.lastGoodAt?ageText(S.lastGoodAt,now()):'', a=$('gd-asof');
  if(S.mode==='snapshot') a.textContent='platform scores as of '+fmt(S.lastGoodAsOf)+' (cached by the cloud build; '+ageText(Date.parse(S.lastGoodAsOf||''),now())+')';
  else a.textContent='last good refresh '+fmtMs(S.lastGoodAt)+' ('+t+')'+(S.mode==='stale'?' — STALE: the latest refresh failed':''); }
function buildNote(){ return 'Designations, positions, kickoff times and the pregame record are from the cloud build ('+fmt(D.rendered_at)+') and are not refreshed by this page'; }

// ---- the tick: re-judge locks, ages and legality at the present instant
// WITHOUT fetching. Kickoffs pass, sources age and a failed refresh leaves
// old numbers behind; none of that may leave an old AVAILABLE card standing.
function signature(vm){ return JSON.stringify({a:vm.actions.map(function(a){ return [a.available,a.why]; }),c:vm.capacity.sentence,b:vm.blockers,f:vm.feed_fresh,
  m:vm.mine.starters.map(function(s){ return [s.state,s.lock,s.injury_note]; }),o:vm.opp?vm.opp.starters.map(function(s){ return [s.state,s.lock]; }):null,
  n:vm.mine.bench.map(function(s){ return [s.state,s.lock]; })}); }
function tick(){ var t=now(); var vm=compute(S.lastGoodRaw,t); var sig=signature(vm);
  if(sig!==S.sig){ S.sig=sig; S.view=vm; renderAll(vm,S.lastChanges,S.prevStamp,S.asOfNote); }
  setAsOf(); return vm; }

var embeddedRaw={rows:D.snapshot.rows,my_players:D.snapshot.my_players,my_reserve:D.snapshot.my_reserve,my_taxi:D.snapshot.my_taxi,feed:D.feed,as_of:D.snapshot.as_of,built:true};
S.lastGood=compute(embeddedRaw,Date.parse(D.rendered_at)||now()); S.lastGoodRaw=embeddedRaw; S.view=S.lastGood; S.sig=signature(S.lastGood);
S.prevStamp=''; S.asOfNote='platform scores as of '+fmt(D.snapshot.as_of)+' (cloud snapshot)';

// ---- fetching: read-only GETs, each with a timeout, none trusted unread
function get(url){ var t0=Date.now(), ctl=(typeof AbortController==='function')?new AbortController():null, timer=null;
  var opts={cache:'no-store',credentials:'omit',mode:'cors'}; if(ctl) opts.signal=ctl.signal;
  var work=fetch(url,opts).then(function(r){
    var serverMs=Date.parse(r.headers.get('date')||''); if(!r.ok) return {ok:false,status:r.status,error:'HTTP '+r.status,serverMs:serverMs,ms:Date.now()-t0};
    return r.json().then(function(j){ return {ok:true,status:r.status,json:j,serverMs:serverMs,ms:Date.now()-t0}; },function(e){ return {ok:false,status:r.status,error:'malformed JSON',serverMs:serverMs,ms:Date.now()-t0}; });
  },function(e){ return {ok:false,status:0,error:(e&&e.name==='AbortError')?('timeout after '+S.timeoutMs+' ms'):((e&&e.message)||'network error'),serverMs:NaN,ms:Date.now()-t0}; });
  var late=new Promise(function(resolve){ timer=setTimeout(function(){ if(ctl) ctl.abort(); resolve({ok:false,status:0,error:'timeout after '+S.timeoutMs+' ms',serverMs:NaN,ms:Date.now()-t0}); },S.timeoutMs); });
  return Promise.race([work,late]).then(function(x){ clearTimeout(timer); return x; }); }
function checkRows(j,expectStarters){
  if(!Array.isArray(j)) return 'matchup payload is not a list'; if(!j.length) return 'matchup payload is empty';
  var seen={}, mine=null;
  for(var i=0;i<j.length;i++){ var r=j[i];
    if(!r||typeof r!=='object'||Array.isArray(r)) return 'row '+i+' is not an object';
    var rid=r.roster_id; if(rid===null||rid===undefined||String(rid)===''||(typeof rid!=='number'&&typeof rid!=='string')) return 'row '+i+' has no roster_id';
    if(seen[String(rid)]) return 'roster #'+rid+' appears twice in the payload'; seen[String(rid)]=1;
    if(!('matchup_id' in r)) return 'roster #'+rid+' has no matchup_id field';
    if(!Array.isArray(r.starters)) return 'roster #'+rid+' has no starters list';
    if(!('points' in r)||!numOrNull(r.points)) return 'roster #'+rid+' has no numeric points (or null)';
    if(r.custom_points!==undefined&&!numOrNull(r.custom_points)) return 'roster #'+rid+' custom_points is not a number or null';
    if(r.starters_points!==undefined&&r.starters_points!==null&&(!Array.isArray(r.starters_points)||!r.starters_points.every(numOrNull))) return 'roster #'+rid+' starters_points is not a list of numbers and nulls';
    if(r.players_points!==undefined&&r.players_points!==null&&(typeof r.players_points!=='object'||Array.isArray(r.players_points))) return 'roster #'+rid+' players_points is not an object';
    if(r.players!==undefined&&r.players!==null&&!Array.isArray(r.players)) return 'roster #'+rid+' players is not a list';
    if(String(rid)===String(D.my_roster_id)) mine=r; }
  if(!mine) return 'this roster (#'+D.my_roster_id+') is missing from the payload';
  if(!mine.starters.length) return 'this roster has an empty starters list';
  if(expectStarters&&mine.starters.length!==expectStarters) return 'this roster lists '+mine.starters.length+' starters where the last good row had '+expectStarters;
  return ''; }
function checkState(j){ if(!j||typeof j!=='object'||Array.isArray(j)) return 'not an object';
  var w=parseInt(j.week,10), sn=parseInt(j.season,10); if(isNaN(w)||isNaN(sn)) return 'no numeric season/week';
  if(typeof j.season_type!=='string'||!j.season_type) return 'no season_type'; return ''; }
function normFeed(j,asOfIso){ var rows=[]; (Array.isArray(j)?j:[]).forEach(function(r){ if(!r||typeof r!=='object') return; var w=parseInt(r.week,10); if(w!==D.week) return;
  var home=String(r.home||'').trim().toUpperCase(), away=String(r.away||'').trim().toUpperCase(), status=String(r.status||'').trim().toLowerCase();
  if(!home||!away||home===away||!status) return; rows.push({week:w,home:home,away:away,status:status,date:String(r.date||''),game_id:String(r.game_id||'')}); });
  return {present:true,fresh:true,as_of:asOfIso,reason:'',rows:rows}; }
function checkFeed(j){ if(!Array.isArray(j)) return 'not a list'; if(!j.length) return 'empty list';
  var n=normFeed(j,null).rows.length; if(!n) return 'no usable week-'+D.week+' row'; return ''; }

function refresh(manual){
  if(S.inflight) return Promise.resolve('inflight');
  var nowMs=now();
  if(manual&&S.lastAttemptAt&&nowMs-S.lastAttemptAt<THROTTLE_MS){ setStatus('A refresh ran '+ageText(S.lastAttemptAt,nowMs)+'; wait a few seconds.'); return Promise.resolve('throttled'); }
  var seq=++S.seq; S.inflight=true; S.lastAttemptAt=nowMs; $('gd-refresh').disabled=true; setStatus('Refreshing… (3 read-only requests to Sleeper)');
  var started=Date.now(), base=D.api.base, sb=D.api.schedule_base;
  return Promise.all([get(base+'/state/nfl'),get(base+'/league/'+encodeURIComponent(D.league_id)+'/matchups/'+D.week),get(sb+'/nfl/regular/'+D.season)]).then(function(res){
    var finished=Date.now(), st=res[0], mu=res[1], fd=res[2], notes=[];
    if(seq<S.applied){ notes.push('a late response (started before a newer one) was discarded'); return finish(false,'late response discarded',notes,seq); }
    if(finished-started>WINDOW_WARN_MS) notes.push('the three responses arrived over '+Math.round((finished-started)/1000)+' s, so they may not describe one instant');
    var stWhy=st.ok?checkState(st.json):(st.error||'?');
    if(!stWhy){ var w=parseInt(st.json.week,10), sn=parseInt(st.json.season,10), stype=String(st.json.season_type);
      if(sn!==D.season){ S.rollover=true; return finish(false,'the platform reports season '+sn+'; this page is season '+D.season+' and stops refreshing',notes,seq); }
      if(stype!=='regular'){ S.rollover=true; notes.push('the platform is in the '+stype+' season; this page is a regular-season week, so week-'+D.week+' scores are shown as they stand and no further refresh runs'); }
      else if(w!==D.week){ S.rollover=true; notes.push('the platform is now in week '+w+'; this page is week '+D.week+', so week-'+D.week+' scores are shown as they stand and no further refresh runs'); } }
    else notes.push('NFL state could not be read ('+stWhy+'); the week was not re-confirmed');
    var feed=S.lastGoodRaw.feed, feedWhy=fd.ok?checkFeed(fd.json):(fd.error||'?');
    if(!feedWhy) feed=normFeed(fd.json,new Date(isNaN(fd.serverMs)?finished:fd.serverMs).toISOString());
    else notes.push('game status feed not refreshed ('+feedWhy+'); keeping the last good copy, dated '+fmt(feed.as_of));
    var lastMine=(S.lastGoodRaw.rows||[]).filter(function(r){ return r&&String(r.roster_id)===String(D.my_roster_id); })[0];
    var muWhy=mu.ok?checkRows(mu.json,lastMine&&Array.isArray(lastMine.starters)?lastMine.starters.length:0):(mu.error||'?');
    if(mu.status===429) muWhy='rate limited (HTTP 429)';
    if(muWhy){
      // A feed that did arrive still updates statuses on the last good scores.
      if(feed!==S.lastGoodRaw.feed){ S.lastGoodRaw={rows:S.lastGoodRaw.rows,my_players:S.lastGoodRaw.my_players,my_reserve:S.lastGoodRaw.my_reserve,my_taxi:S.lastGoodRaw.my_taxi,feed:feed,as_of:S.lastGoodRaw.as_of,built:S.lastGoodRaw.built}; }
      return finish(false,'scores not refreshed: '+muWhy+'; last good kept',notes,seq); }
    var serverMs=isNaN(mu.serverMs)?finished:mu.serverMs;
    if(S.lastServerMs&&serverMs<S.lastServerMs){ notes.push('discarded a response dated '+fmtMs(serverMs)+', older than the one already applied'); return finish(false,'older response discarded',notes,seq); }
    var raw={rows:mu.json,my_players:S.lastGoodRaw.my_players,my_reserve:S.lastGoodRaw.my_reserve,my_taxi:S.lastGoodRaw.my_taxi,feed:feed,as_of:new Date(serverMs).toISOString(),built:false};
    var mine=raw.rows.filter(function(r){ return String(r.roster_id)===String(D.my_roster_id); })[0];
    if(mine&&Array.isArray(mine.players)&&mine.players.length) raw.my_players=mine.players;
    var at=now(), vm=compute(raw,at), changes=diff(S.lastGood,vm), prevStamp=S.mode==='snapshot'?fmt(S.lastGoodAsOf):fmtMs(S.lastGoodAt);
    S.lastGood=vm; S.lastGoodRaw=raw; S.lastGoodAt=at; S.lastGoodAsOf=raw.as_of; S.lastServerMs=serverMs; S.applied=seq; S.failures=0; S.lastError=''; S.live=true; S.mode='live';
    S.history.push({at:finished,seq:seq,window_ms:finished-started,changes:changes.length});
    var asOfNote='platform scores fetched '+fmt(raw.as_of)+' (read window '+(finished-started)+' ms; device clock '+(Math.abs(at-serverMs)>300000?'differs from Sleeper’s by '+Math.round(Math.abs(at-serverMs)/60000)+' min, so Sleeper’s date is used':'agrees with Sleeper’s')+'). '+buildNote();
    S.view=vm; S.sig=signature(vm); S.lastChanges=changes; S.prevStamp=prevStamp; S.asOfNote=asOfNote;
    renderAll(vm,changes,prevStamp,asOfNote); setMode(S.rollover?'LIVE (week over)':'LIVE','ok');
    return finish(true,'Updated scores and game statuses '+fmt(raw.as_of)+': '+(changes.length?changes.length+' change(s) since your last snapshot':'no change since your last snapshot')+'. '+buildNote()+'.'+(notes.length?' '+notes.join('. '):''),[],seq);
  }).then(null,function(e){ return finish(false,'the page could not apply the response ('+((e&&e.message)||String(e))+'); last good kept',[],seq); }); }
function finish(ok,msg,notes,seq){ S.inflight=false; $('gd-refresh').disabled=false;
  if(!ok){ S.failures+=1; S.lastError=msg; if(S.live) S.mode='stale'; setMode(S.live?'STALE':'SNAPSHOT (refresh failed)',S.live?'warn':'bad');
    setStatus('Refresh failed: '+msg+'. Showing the last good data from '+(S.live?fmtMs(S.lastGoodAt):fmt(S.lastGoodAsOf)+' (cloud snapshot)')+'.'+(notes.length?' '+notes.join('. '):'')); tick(); }
  else setStatus(msg);
  setAsOf(); planNext(); return Promise.resolve(ok?'ok':'failed'); }
function planNext(){ if(S.timer){ clearTimeout(S.timer); S.timer=null; } S.nextPollMs=null; if(!S.live||S.rollover||document.hidden) return;
  var vm=S.view||S.lastGood, wait=null;
  if(S.failures) wait=Math.min(BACKOFF_MAX_MS,60000*Math.pow(2,S.failures-1));
  else if(vm.any_playing) wait=POLL_LIVE_MS;
  else if(vm.any_unknown&&!vm.all_settled) wait=POLL_UNKNOWN_MS;
  else if(vm.next_kickoff&&vm.next_kickoff-now()<3*3600*1000) wait=POLL_PRE_MS;
  if(wait!==null) S.timer=setTimeout(function(){ refresh(false); },wait); S.nextPollMs=wait; }
document.addEventListener('visibilitychange',function(){ if(document.hidden){ planNext(); return; } tick();
  if(S.live&&!S.inflight&&S.lastGoodAt&&now()-S.lastGoodAt>POLL_LIVE_MS) refresh(false); else planNext(); });
var btn=$('gd-refresh'); btn.disabled=false; btn.title='Fetch the current scores and game statuses from Sleeper (read-only)';
btn.addEventListener('click',function(){ refresh(true); });
setStatus('Snapshot: the cloud build’s scores. Tap Refresh for live scores (read-only).');
tick(); setInterval(tick,TICK_MS);
window.gridironGameDay={refresh:refresh,compute:compute,diff:diff,judge:judge,tick:tick,now:now,state:function(){ return S; },data:D,
  tune:function(o){ o=o||{}; if(typeof o.skewMs==='number') S.skewMs=o.skewMs; if(typeof o.timeoutMs==='number'&&o.timeoutMs>0) S.timeoutMs=o.timeoutMs; return tick(); }};
})();
"""
