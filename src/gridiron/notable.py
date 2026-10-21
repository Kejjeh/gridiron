"""Free agents the page must not leave unnamed.

The Free Agent Radar ranks every pool player it can PROJECT. Everyone else
was a count ("missing evidence: 346"), and that is how a former starter sat
in the pool for five days, unranked, until another manager claimed him
(2026 week 7: dropped by a league team on a Thursday night, on the exempt
list, no game yet this season, Sleeper projecting him from the Tuesday on).

Two lists close that hole (`build_watch`). Neither is a projection of ours
and neither is a move: they say WHO to look at, and why, with the check the
owner makes in Sleeper.

  * notable — a pool player who was a starter last season (`prior_lines`:
    league points per game, ranked within his position LAST season among
    players with `MIN_PRIOR_GAMES`), or who was dropped by a team in THIS
    league in the last `DROP_WINDOW_DAYS` days (`league_drops`, from the
    snapshot's transactions) and is worth a look this week: Sleeper ranks
    him a starter at his position, or ours or Sleeper's number reaches the
    owner's weakest eligible starter (an empty slot he can fill counts as
    0). A player designated Out is never "worth a look this week".
    Each row carries the drop and the waiver clock the league's settings
    imply (`waiver_clock`), his status tags and both numbers.
  * market — every pool player WE abstain on, and who is not known to sit
    (withheld, or designated Out by Sleeper), for whom Sleeper's pre-kickoff
    projection exists, sorted by it, with OUR reason for abstaining and
    what Sleeper's number would mean against the owner's weakest eligible
    starter. Labelled as Sleeper's number, never ours.

The first-screen ALERT is a transition (rule #8), carried record to record
(`PreviousWatch`): a row is alerted for `ALERT_HOURS` after he entered the
pool, after he was first listed, and after each change of his waiver clock
(dropped by a league team; his waiver period ran out and he became an
instant add; his game locked him on waivers until the weekly run, when
claims are the only way in) — never for the owner's own drop. A starter from last season who has sat in the pool for
weeks stays in the radar's list and off the first screen. A previous record
that could not compare (no watch block, last season not loaded, a pool
built without the player map) marks nothing new and carries its times.

Joins are by id only (rule #3): Sleeper ids from the snapshot and the pool,
gsis ids through the crosswalk the pool was built with; last season's box
scores are keyed by nflverse `player_id` (gsis).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd

from gridiron.ids import normalize_id
from gridiron.league_config import (DEFAULT_SCORING, WAIVER_CLEAR_DAYS,
                                    WAIVER_CLEAR_WEEKDAY, ScoringRules)
from gridiron.lineup import Player, eligible
# A feed with fewer players than this at a position is a failed fetch, not a
# ranking (the floor `feed_absence` uses).
from gridiron.models.advanced import FEED_MIN_PER_POSITION
from gridiron.usage import score_frame

#: A "starter last season" by league points per game, within position.
STARTER_RANK = {"QB": 12, "RB": 24, "WR": 30, "TE": 12}
MIN_PRIOR_GAMES = 4
#: How far back a league drop still makes a pool player notable.
DROP_WINDOW_DAYS = 10
#: A notable row is on the first screen this long after each transition.
ALERT_HOURS = 48
#: Rows shown before "and N more" (the record keeps every notable row: its
#: first-seen times carry to the next build).
NOTABLE_CAP = 12
MARKET_CAP = 15
#: Sleeper runs this league's waivers at about 03:00 Eastern on the waiver
#: day (observed 07:17 UTC on Wed 2026-10-21; league_config provenance).
WAIVER_RUN_LOCAL_HOUR = 3
_ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class PriorLine:
    """One player's last regular season, scored with this league's rules."""

    gsis_id: str
    position: str             # LAST season's (nflverse): an FB there is not an RB
    team: str
    games: int
    ppg: float
    rank: int | None          # within that position, among MIN_PRIOR_GAMES players

    @property
    def starter(self) -> bool:
        cutoff = STARTER_RANK.get(self.position)
        return bool(cutoff and self.rank is not None and self.rank <= cutoff)

    def describe(self, season: int | None = None) -> str:
        yr = f"{season} " if season else "last season "
        rk = f"{self.position}{self.rank}, " if self.rank else ""
        return f"{yr}{rk}{self.ppg:.1f} ppg over {self.games} g"


def prior_lines(prior_weekly: pd.DataFrame | None,
                rules: ScoringRules = DEFAULT_SCORING) -> dict[str, PriorLine]:
    """gsis id -> last season's line, scored by `gridiron.scoring` (rule #2).
    Empty when the frame is absent: an unknown prior season is unknown."""
    if prior_weekly is None or len(prior_weekly) == 0 or "player_id" not in prior_weekly \
            or "position" not in prior_weekly or "week" not in prior_weekly:
        return {}
    f = score_frame(prior_weekly, rules)
    f["gsis_id"] = f["player_id"].map(normalize_id)
    f = f.loc[f["gsis_id"] != ""]
    team_col = next((c for c in ("team", "recent_team") if c in f), None)
    f["_team"] = f[team_col].fillna("").astype(str) if team_col else ""
    f["position"] = f["position"].fillna("").astype(str)
    g = f.sort_values("week").groupby("gsis_id")
    summ = pd.DataFrame({"position": g["position"].last(), "team": g["_team"].last(),
                         "games": g["week"].nunique(), "ppg": g["league_points"].mean()})
    summ["rank"] = None
    ok = summ["games"] >= MIN_PRIOR_GAMES
    summ.loc[ok, "rank"] = (summ.loc[ok].groupby("position")["ppg"]
                            .rank(ascending=False, method="first"))
    out = {}
    for gid, r in summ.iterrows():
        rank = None if r["rank"] is None or pd.isna(r["rank"]) else int(r["rank"])
        out[str(gid)] = PriorLine(str(gid), str(r["position"] or ""), str(r["team"] or ""),
                                  int(r["games"]), float(r["ppg"]), rank)
    return out


@dataclass(frozen=True)
class Drop:
    """The latest completed drop of one player by a team in this league."""

    sleeper_id: str
    roster_id: int | None
    at: datetime
    kind: str                 # free_agent | waiver | trade | commissioner

    def describe(self, mine: int | None = None) -> str:
        who = (f"YOU (roster #{self.roster_id})" if mine is not None and self.roster_id == mine
               else f"roster #{self.roster_id}" if self.roster_id is not None
               else "a league team")
        return f"dropped by {who} {self.at:%a %d %b %H:%M} UTC"


def league_drops(transactions: Mapping[str, object] | None) -> tuple[dict[str, Drop], str]:
    """sleeper id -> latest completed drop, from the snapshot's
    `transactions` block (`SleeperReadOnly.recent_transactions`), and a
    sentence when the block is missing or failed (drops UNKNOWN, not none)."""
    if not isinstance(transactions, Mapping):
        return {}, "league transactions not in this snapshot: drops UNKNOWN"
    if transactions.get("error"):
        return {}, f"league transactions not read ({transactions.get('error')}): drops UNKNOWN"
    out: dict[str, Drop] = {}
    for t in transactions.get("rows") or []:
        if not isinstance(t, Mapping) or t.get("status") != "complete" \
                or not isinstance(t.get("drops") or {}, Mapping):
            continue
        stamp = t.get("status_updated") or t.get("created")
        try:
            at = datetime.fromtimestamp(float(stamp) / 1000.0, tz=timezone.utc)
        except (TypeError, ValueError, OverflowError):
            continue
        for sid, rid in (t.get("drops") or {}).items():
            sid = normalize_id(sid)
            prev = out.get(sid)
            if sid and (prev is None or at > prev.at):
                try:
                    roster = int(rid)
                except (TypeError, ValueError):
                    roster = None
                out[sid] = Drop(sid, roster, at, str(t.get("type") or ""))
    return out, ""


def next_waiver_run(after: datetime) -> datetime:
    """The first league waiver run strictly after `after`: the waiver day
    (WAIVER_CLEAR_WEEKDAY, Monday = 0) at about 03:00 Eastern."""
    local = after.astimezone(_ET)
    for i in range(8):
        d = local.date() + timedelta(days=i)
        if d.weekday() == WAIVER_CLEAR_WEEKDAY:
            run = datetime(d.year, d.month, d.day, WAIVER_RUN_LOCAL_HOUR, tzinfo=_ET)
            if run > local:
                return run.astimezone(timezone.utc)
    raise AssertionError("no waiver day within eight days")    # unreachable


def waiver_run_after(dropped: datetime) -> datetime:
    """The end of a drop's episode: the first waiver run after his
    `WAIVER_CLEAR_DAYS`-day clear. By then he has been claimed, or he was a
    free agent until his kickoff and is one again after the run."""
    return next_waiver_run(dropped + timedelta(days=WAIVER_CLEAR_DAYS) - timedelta(seconds=1))


#: Between the last game of an NFL week (Monday night, ~00:15 UTC Tuesday)
#: and the waiver run (~07:00 UTC Wednesday) every player whose team played
#: is on waivers; the board's week has already rolled to the next game.
_AFTER_LAST_GAMES = timedelta(hours=31)


@dataclass(frozen=True)
class WaiverClock:
    """Where a dropped player stands, by the league's settings (Sleeper
    keeps the authoritative clock on his player card).

    The cycle, consistent with every transaction in this league's history:
    on WAIVERS for `WAIVER_CLEAR_DAYS` after the drop, then a FREE AGENT (an
    instant add) until his game kicks off, then LOCKED on waivers until the
    weekly run."""

    phase: str                    # WAIVERS | FREE_AGENT | LOCKED | UNSURE
    since: datetime               # when this phase began
    until: datetime | None
    text: str
    #: Every phase change so far (dropped, became a free agent, locked until
    #: the run): each one changes what the owner can do, so each is a
    #: transition the first screen shows for ALERT_HOURS (rule #8).
    events: tuple[datetime, ...] = ()


def _t(t: datetime) -> str:
    return f"{t:%a %d %b %H:%M} UTC"


def waiver_clock(drop: Drop, kickoff: datetime | None, now: datetime) -> WaiverClock:
    """The phase `drop`'s player is in at `now`. `kickoff` is his game in the
    page's week (None: no timed game, so the phase after the clear is
    unsure). Neutral wording: a clock, never an instruction (rule #11)."""
    clear = drop.at + timedelta(days=WAIVER_CLEAR_DAYS)
    run = next_waiver_run(now)
    tail = " (the league's settings; his Sleeper card is the authority)"
    if now < clear:
        return WaiverClock("WAIVERS", drop.at, clear,
                           f"on waivers until about {_t(clear)}, the league's "
                           f"{WAIVER_CLEAR_DAYS}-day clear: claims only, processed then; "
                           f"after that a free agent until his game kicks off" + tail,
                           (drop.at,))
    # a game that kicked off while he was still on waivers keeps him there
    freed = (clear,) if kickoff is None or kickoff > clear else ()
    if kickoff is not None and kickoff <= now:
        lock = max(kickoff, clear)
        return WaiverClock("LOCKED", lock, run,
                           f"his game kicked off {_t(kickoff)}: on waivers until the "
                           f"weekly run, about {_t(run)} — claims only" + tail,
                           (drop.at, *freed, lock))
    if run - now <= _AFTER_LAST_GAMES:
        lock = max(clear, run - _AFTER_LAST_GAMES)
        return WaiverClock("LOCKED", lock, run,
                           f"last week's games have been played: unless his team had a "
                           f"bye he is on waivers until the weekly run, about {_t(run)} — "
                           f"claims only" + tail, (drop.at, clear, lock))
    if kickoff is not None:
        return WaiverClock("FREE_AGENT", clear, kickoff,
                           f"his {WAIVER_CLEAR_DAYS}-day waiver period ended {_t(clear)}: a "
                           f"FREE AGENT (an instant add) until his game kicks off "
                           f"{_t(kickoff)}" + tail, (drop.at, clear))
    return WaiverClock("UNSURE", clear, None,
                       f"his {WAIVER_CLEAR_DAYS}-day waiver period ended {_t(clear)}: a free "
                       f"agent until his game kicks off (no timed game here), then on "
                       f"waivers until the weekly run" + tail, (drop.at, clear))


def market_ranks(players: Mapping[str, object] | None) -> dict[str, int]:
    """sleeper id -> rank of his Sleeper projection within his position, over
    the whole feed (`gridiron.shadow.read_shadow` players). A position the
    feed lists fewer than `FEED_MIN_PER_POSITION` players at is not ranked."""
    by_pos: dict[str, list[tuple[float, str]]] = {}
    for sid, v in (players or {}).items():
        if isinstance(v, Mapping) and v.get("points") is not None and v.get("position"):
            by_pos.setdefault(str(v["position"]), []).append((float(v["points"]),
                                                              normalize_id(sid)))
    out: dict[str, int] = {}
    for rows in by_pos.values():
        if len(rows) < FEED_MIN_PER_POSITION:
            continue
        for i, (_, sid) in enumerate(sorted(rows, key=lambda r: (-r[0], r[1])), 1):
            out[sid] = i
    return out


@dataclass(frozen=True)
class Starter:
    name: str
    mean: float


def weakest_starter(best: Sequence[Player | None], slots: Sequence[str], position: str,
                    roster: Sequence[Player] = ()) -> Starter | None:
    """The owner's lowest-projected starter in a slot `position` can fill
    (its own slot, or FLEX for RB/WR/TE), from the best legal lineup. A slot
    the lineup leaves EMPTY counts as 0 when no active roster player could
    fill it (an unprojected or frozen player there is unknown, not empty)."""
    placed = {p.sleeper_id for p in best if p is not None}
    out: list[Starter] = []
    for s, p in zip(slots, best):
        if not eligible(s, position):
            continue
        if p is not None and p.projection.usable:
            out.append(Starter(p.name, float(p.projection.mean)))
        elif p is None and roster and not any(
                r.lineup != "IR" and r.sleeper_id not in placed and eligible(s, r.position)
                for r in roster):
            out.append(Starter(f"an empty {s} slot", 0.0))
    return min(out, key=lambda x: x.mean) if out else None


def _status(p: Player, sleeper: Mapping[str, object], reserve: set[str] | None,
            reserve_week: int | None) -> str:
    bits = []
    tag = str(sleeper.get("injury_status") or "")
    if tag:
        bits.append(f"Sleeper: {tag}")
    if reserve is not None and p.gsis_id and p.gsis_id in reserve:
        bits.append("on an NFL reserve list (RES/EXE"
                    + (f", week-{reserve_week} roster)" if reserve_week else ")"))
    return "; ".join(bits) or "no tag"


@dataclass(frozen=True)
class NotableFreeAgent:
    player: Player
    why: tuple[str, ...]
    prior: PriorLine | None
    drop: Drop | None
    ours: float | None            # our projection mean, None when abstained or withheld
    ours_note: str                # why not, when not
    market: float | None          # Sleeper's pre-kickoff number
    status: str
    waiver: str
    new: bool                     # a transition THIS build (entered the pool or first listed)
    versus: str = ""
    #: The latest of: entered the pool, first listed (carried record to
    #: record; None when no comparable record can say).
    first_seen: datetime | None = None
    #: On the first screen: a transition within ALERT_HOURS (rule #8).
    alert: bool = False
    clock: WaiverClock | None = None

    def record(self) -> dict:
        return {"id": self.player.sleeper_id, "gsis_id": self.player.gsis_id,
                "name": self.player.name, "position": self.player.position,
                "team": self.player.team, "why": list(self.why), "new": self.new,
                "alert": self.alert,
                "first_seen": (None if self.first_seen is None
                               else self.first_seen.isoformat(timespec="seconds")),
                "ours": self.ours, "ours_note": self.ours_note, "market": self.market,
                "status": self.status, "waiver": self.waiver, "versus": self.versus,
                "waiver_phase": None if self.clock is None else self.clock.phase,
                "dropped_by": None if self.drop is None else self.drop.roster_id,
                "dropped_at": None if self.drop is None
                else self.drop.at.isoformat(timespec="seconds"),
                "prior": None if self.prior is None else
                {"games": self.prior.games, "ppg": round(self.prior.ppg, 2),
                 "rank": self.prior.rank}}


@dataclass(frozen=True)
class MarketRow:
    player: Player
    market: float
    why_unprojected: str
    status: str
    versus: str
    gain: float | None            # Sleeper's number minus the weakest eligible starter's
    rank: int | None = None       # Sleeper's rank at his position this week

    def record(self) -> dict:
        return {"id": self.player.sleeper_id, "gsis_id": self.player.gsis_id,
                "name": self.player.name, "position": self.player.position,
                "team": self.player.team, "market": self.market,
                "why_unprojected": self.why_unprojected, "status": self.status,
                "versus": self.versus, "gain": self.gain, "rank": self.rank}


def _versus(value: float | None, w: Starter | None, label: str) -> tuple[str, float | None]:
    if value is None or w is None:
        return "", None
    gain = round(value - w.mean, 2)
    word = "above" if gain > 0 else "below"
    return (f"at {label} {value:.1f}: {abs(gain):.1f} {word} your weakest eligible starter "
            f"({w.name}, {w.mean:.1f})"), gain


def _iso(t: datetime | None) -> str | None:
    return None if t is None else t.isoformat(timespec="seconds")


def _times(raw: object) -> dict[str, datetime]:
    out: dict[str, datetime] = {}
    for k, v in (raw.items() if isinstance(raw, Mapping) else ()):
        try:
            t = datetime.fromisoformat(str(v))
        except (TypeError, ValueError):
            continue
        if t.tzinfo is not None and normalize_id(k):
            out[normalize_id(k)] = t
    return out


@dataclass(frozen=True)
class PreviousWatch:
    """What the previous record knew, for transitions (rule #8).

    `pool` is None when that record's pool cannot be compared (no record,
    or a pool built without the player map: empty); `listed` is None when
    its list cannot be (no pool, no watch block, or last season was not
    loaded). The two time maps carry either way."""

    pool: frozenset[str] | None = None
    listed: frozenset[str] | None = None
    pool_since: Mapping[str, datetime] = field(default_factory=dict)
    listed_since: Mapping[str, datetime] = field(default_factory=dict)

    @classmethod
    def from_record(cls, record: Mapping[str, object] | None) -> "PreviousWatch":
        if not isinstance(record, Mapping):
            return cls()
        radar = record.get("radar") if isinstance(record.get("radar"), Mapping) else {}
        rows = radar.get("pool") if isinstance(radar.get("pool"), list) else []
        ids = frozenset(normalize_id(r.get("id")) for r in rows
                        if isinstance(r, Mapping) and normalize_id(r.get("id")))
        w = record.get("watch") if isinstance(record.get("watch"), Mapping) else None
        listed = None           # a list drawn from no pool compares nothing
        if ids and w is not None and w.get("prior_loaded") is True:
            listed = frozenset(normalize_id(r.get("id")) for r in w.get("notable") or []
                               if isinstance(r, Mapping) and normalize_id(r.get("id")))
        return cls(pool=ids or None, listed=listed,
                   pool_since=_times((w or {}).get("pool_since")),
                   listed_since=_times((w or {}).get("listed_since")))


@dataclass(frozen=True)
class FreeAgentWatch:
    notable: tuple[NotableFreeAgent, ...] = field(default=())   # every row, alerts first
    market: tuple[MarketRow, ...] = field(default=())           # every row, highest first
    notes: tuple[str, ...] = field(default=())
    prior_loaded: bool = False
    #: Transition times carried to the next record (pruned to ALERT_HOURS).
    pool_since: Mapping[str, datetime] = field(default_factory=dict)
    listed_since: Mapping[str, datetime] = field(default_factory=dict)

    @property
    def notable_total(self) -> int:
        return len(self.notable)

    @property
    def market_total(self) -> int:
        return len(self.market)

    @property
    def alerts(self) -> tuple[NotableFreeAgent, ...]:
        return tuple(n for n in self.notable if n.alert)

    def record(self) -> dict:
        return {"notable": [n.record() for n in self.notable],
                "notable_total": self.notable_total,
                "market": [m.record() for m in self.market],
                "market_total": self.market_total, "notes": list(self.notes),
                "prior_loaded": self.prior_loaded,
                "pool_since": {k: _iso(v) for k, v in sorted(self.pool_since.items())},
                "listed_since": {k: _iso(v) for k, v in sorted(self.listed_since.items())}}


def build_watch(pool: Sequence[Player], *, sleeper_players: Mapping[str, Mapping[str, object]],
                market_of, prior: Mapping[str, PriorLine], prior_season: int | None,
                drops: Mapping[str, Drop], drops_note: str, now: datetime,
                best: Sequence[Player | None], slots: Sequence[str],
                roster: Sequence[Player] = (), ranks: Mapping[str, int] | None = None,
                out_designations: Iterable[str] = (),
                reserve: set[str] | None = None, reserve_week: int | None = None,
                my_roster_id: int | None = None,
                previous: PreviousWatch | None = None) -> FreeAgentWatch:
    """Both lists. `market_of(player)` returns Sleeper's pre-kickoff number
    for him or None (the dashboard's `shadow_number`, which already refuses
    a stale or post-kickoff feed); `ranks` is `market_ranks` of that feed,
    read only for a player `market_of` serves. `out_designations` are the
    Sleeper tags that mean he will not play (the dashboard's
    WITHHOLD_DESIGNATIONS). `previous` is the last record's view
    (`PreviousWatch.from_record`)."""
    prev = previous or PreviousWatch()
    ranks = ranks or {}
    outs = {str(x).upper() for x in out_designations}
    window_s = timedelta(hours=ALERT_HOURS)
    notes = [drops_note] if drops_note else []
    if not prior:
        notes.append("last season's box scores not loaded: 'a starter last season' "
                     "cannot be checked")

    # -- when each pool player entered the pool (a transition), carried
    ids = [normalize_id(p.sleeper_id) for p in pool]
    if ids:
        pool_since = {}
        for sid in ids:
            if prev.pool is not None and sid not in prev.pool:
                pool_since[sid] = now
            elif sid in prev.pool_since:
                pool_since[sid] = prev.pool_since[sid]
    else:                       # no pool this build (no player map): carry, compare nothing
        pool_since = dict(prev.pool_since)

    notable: list[NotableFreeAgent] = []
    market: list[MarketRow] = []
    listed_since: dict[str, datetime] = {}
    window = now - timedelta(days=DROP_WINDOW_DAYS)
    for p in pool:
        sid = normalize_id(p.sleeper_id)
        rec = sleeper_players.get(sid) or {}
        pr = p.projection
        sits = pr.is_withheld or str(rec.get("injury_status") or "").upper() in outs
        if pr.usable and not pr.is_withheld:
            ours, ours_note = float(pr.mean), ""
        elif pr.is_withheld:            # known not to play this week: 0, with why
            ours, ours_note = None, (pr.reasons[-1] if pr.reasons else "withheld")
        else:
            ours, ours_note = None, (pr.reasons[0] if pr.reasons else "not projected")
        mkt = market_of(p)
        mrank = ranks.get(sid) if mkt is not None else None
        status = _status(p, rec, reserve, reserve_week)
        cutoff = STARTER_RANK.get(p.position)       # this week's position, for this week's rank
        w = weakest_starter(best, slots, p.position, roster)
        # -- the market list: abstained, not known to sit, with a Sleeper number
        if not pr.usable and not sits and mkt is not None:
            vs, gain = _versus(mkt, w, "Sleeper's")
            market.append(MarketRow(p, round(float(mkt), 2), ours_note, status, vs, gain,
                                    mrank))
        # -- notable
        why = []
        pl = prior.get(p.gsis_id) if p.gsis_id else None
        if pl is not None and pl.starter:
            why.append(f"a starter last season ({pl.describe(prior_season)})")
        d = drops.get(sid)
        d = d if d is not None and d.at >= window else None
        if d is not None:
            worth = []
            if not sits:                # a player who will not play is never worth it THIS week
                value = ours if ours is not None else mkt
                if mrank is not None and cutoff and mrank <= cutoff:
                    worth.append(f"Sleeper ranks him {p.position}{mrank} this week "
                                 f"({mkt:.1f})")
                if w is not None and value is not None and value >= w.mean:
                    worth.append(f"{'ours' if ours is not None else 'Sleeper'} {value:.1f} "
                                 f"reaches your weakest eligible starter ({w.name}, "
                                 f"{w.mean:.1f})")
            if worth:
                why.append(f"{d.describe(my_roster_id)}, and " + "; ".join(worth))
            elif why:
                why.append(d.describe(my_roster_id))
        if not why:
            continue
        clock = waiver_clock(d, p.kickoff, now) if d is not None else None
        waiver = clock.text if clock is not None else (
            f"no drop by a league team in the last {DROP_WINDOW_DAYS} days on record: his "
            f"Sleeper card says FREE AGENT or when his waivers clear")
        vs, _ = _versus(None if sits else (ours if ours is not None else mkt), w,
                        "ours" if ours is not None else "Sleeper's")
        # -- when he was first listed (a transition), carried
        if sid in prev.listed_since:
            listed = prev.listed_since[sid]
        elif prev.listed is not None and sid not in prev.listed:
            listed = now
        else:
            listed = None
        if listed is not None:
            listed_since[sid] = listed
        known = [t for t in (pool_since.get(sid), listed) if t is not None]
        first = max(known) if known else None
        own = d is not None and my_roster_id is not None and d.roster_id == my_roster_id
        events = [first, *(clock.events if clock is not None else ())]
        alert = not own and any(t is not None and timedelta(0) <= now - t <= window_s
                                for t in events)
        notable.append(NotableFreeAgent(p, tuple(why), pl, d, ours, ours_note, mkt, status,
                                        waiver, new=first == now, versus=vs,
                                        first_seen=first, alert=alert, clock=clock))

    # carry a recent listing through a build that does not list him (a feed
    # glitch must not end his alert), then forget anything past the window
    for sid, t in prev.listed_since.items():
        listed_since.setdefault(sid, t)
    keep = lambda m: {k: v for k, v in m.items() if now - v <= window_s}   # noqa: E731

    def nkey(n: NotableFreeAgent):
        frac = (n.prior.rank / STARTER_RANK.get(n.prior.position, 30)
                if n.prior and n.prior.rank else 9.0)
        return (0 if n.alert else 1, 0 if n.new else 1, 0 if n.drop else 1, frac,
                -(n.ours if n.ours is not None else (n.market or 0.0)))
    notable.sort(key=nkey)
    market.sort(key=lambda m: -m.market)
    return FreeAgentWatch(tuple(notable), tuple(market), tuple(notes),
                          prior_loaded=bool(prior), pool_since=keep(pool_since),
                          listed_since=keep(listed_since))
