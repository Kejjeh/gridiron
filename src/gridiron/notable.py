"""Free agents the page must not leave unnamed.

The Free Agent Radar ranks every pool player it can PROJECT. Everyone else
was a count ("missing evidence: 346"), and that is how a former starter sat
in the pool for five days, unranked, until another manager claimed him
(2026 week 7: dropped by a league team on a Thursday, on the exempt list,
no game yet this season, Sleeper projecting him from the Tuesday on).

Two lists close that hole (`build_watch`). Neither is a projection of ours
and neither is a move: they say WHO to look at, and why, with the check the
owner makes in Sleeper.

  * notable — a pool player who was a starter last season (`prior_lines`:
    league points per game, ranked within his position last season among
    players with `MIN_PRIOR_GAMES`), or who was dropped by a team in THIS
    league in the last `DROP_WINDOW_DAYS` days (`league_drops`, from the
    snapshot's transactions) and is worth a look this week (Sleeper ranks
    him a starter at his position, or ours or Sleeper's number reaches the
    owner's weakest eligible starter). Each row carries the drop (who,
    when), the waiver timing the league's settings imply, his status tags
    and both numbers, and when the page first listed him. The first-screen
    ALERT is a transition (rule #8): a row first listed in the last
    `ALERT_HOURS`, or one still inside its waiver window. A starter from
    last season who has sat in the pool for weeks stays in the radar's list
    and off the first screen.
  * market — every pool player WE abstain on (not withheld: a designated
    Out or a declared/proven bye is known not to play) for whom Sleeper's
    pre-kickoff projection exists, sorted by it, with OUR reason for
    abstaining and what Sleeper's number would mean against the owner's
    weakest eligible starter. Labelled as Sleeper's number, never ours.

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
#: A notable row is on the first screen this long after the page first
#: listed him (and for as long as his waiver window is open).
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


def waiver_run_after(dropped: datetime) -> datetime:
    """The first league waiver run at least WAIVER_CLEAR_DAYS after a drop:
    the next waiver day (WAIVER_CLEAR_WEEKDAY, 0 = Monday) at about
    03:00 Eastern. What the league's settings imply, not Sleeper's clock for
    this player — the page says to verify it there."""
    earliest = dropped + timedelta(days=WAIVER_CLEAR_DAYS)
    local = earliest.astimezone(_ET)
    target = WAIVER_CLEAR_WEEKDAY
    day = local.date()
    for i in range(8):
        d = day + timedelta(days=i)
        if d.weekday() == target:
            run = datetime(d.year, d.month, d.day, WAIVER_RUN_LOCAL_HOUR, tzinfo=_ET)
            if run >= local:
                return run.astimezone(timezone.utc)
    return earliest


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


def weakest_starter(best: Sequence[Player | None], slots: Sequence[str],
                    position: str) -> Player | None:
    """The owner's lowest-projected starter in a slot `position` can fill
    (its own slot, or FLEX for RB/WR/TE), from the best legal lineup."""
    pool = [p for s, p in zip(slots, best) if p is not None and p.projection.usable
            and eligible(s, position)]
    return min(pool, key=lambda p: p.projection.mean) if pool else None


def _status(p: Player, sleeper: Mapping[str, object], reserve: set[str] | None) -> str:
    bits = []
    tag = str(sleeper.get("injury_status") or "")
    if tag:
        bits.append(f"Sleeper: {tag}")
    if reserve is not None and p.gsis_id and p.gsis_id in reserve:
        bits.append("on an NFL reserve list (weekly roster: RES/EXE)")
    return "; ".join(bits) or "no tag"


@dataclass(frozen=True)
class NotableFreeAgent:
    player: Player
    why: tuple[str, ...]
    prior: PriorLine | None
    drop: Drop | None
    ours: float | None            # our projection mean, None when abstained
    ours_note: str                # why not, when not
    market: float | None          # Sleeper's pre-kickoff number
    status: str
    waiver: str
    new: bool                     # first listed by THIS build
    versus: str = ""
    #: When a build first listed him (carried record to record); None when
    #: no earlier record can say (the first build, or a record before this list).
    first_seen: datetime | None = None
    #: On the first screen: first listed within ALERT_HOURS, or still inside
    #: his waiver window. The transition, not the standing state (rule #8).
    alert: bool = False

    def record(self) -> dict:
        return {"id": self.player.sleeper_id, "gsis_id": self.player.gsis_id,
                "name": self.player.name, "position": self.player.position,
                "team": self.player.team, "why": list(self.why), "new": self.new,
                "alert": self.alert,
                "first_seen": (None if self.first_seen is None
                               else self.first_seen.isoformat(timespec="seconds")),
                "ours": self.ours, "ours_note": self.ours_note, "market": self.market,
                "status": self.status, "waiver": self.waiver, "versus": self.versus,
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


def _versus(value: float | None, best: Sequence[Player | None], slots: Sequence[str],
            position: str, label: str) -> tuple[str, float | None]:
    w = weakest_starter(best, slots, position)
    if value is None or w is None:
        return "", None
    gain = round(value - float(w.projection.mean), 2)
    word = "above" if gain > 0 else "below"
    return (f"at {label} {value:.1f}: {abs(gain):.1f} {word} your weakest eligible starter "
            f"({w.name}, {w.projection.mean:.1f})"), gain


@dataclass(frozen=True)
class FreeAgentWatch:
    notable: tuple[NotableFreeAgent, ...] = field(default=())   # every row, alerts first
    market: tuple[MarketRow, ...] = field(default=())           # the top MARKET_CAP
    notes: tuple[str, ...] = field(default=())
    market_total: int = 0

    @property
    def notable_total(self) -> int:
        return len(self.notable)

    @property
    def alerts(self) -> tuple[NotableFreeAgent, ...]:
        return tuple(n for n in self.notable if n.alert)

    def record(self) -> dict:
        return {"notable": [n.record() for n in self.notable],
                "notable_total": self.notable_total,
                "market": [m.record() for m in self.market],
                "market_total": self.market_total, "notes": list(self.notes)}


def build_watch(pool: Sequence[Player], *, sleeper_players: Mapping[str, Mapping[str, object]],
                market_of, prior: Mapping[str, PriorLine], prior_season: int | None,
                ranks: Mapping[str, int] | None = None,
                drops: Mapping[str, Drop], drops_note: str, now: datetime,
                best: Sequence[Player | None], slots: Sequence[str],
                reserve: set[str] | None = None, my_roster_id: int | None = None,
                previous_pool_ids: Iterable[str] | None = None,
                previous_first_seen: Mapping[str, str] | None = None) -> FreeAgentWatch:
    """Both lists. `market_of(player)` returns Sleeper's pre-kickoff number
    for him or None (the dashboard's `shadow_number`, which already refuses
    a stale or post-kickoff feed); `ranks` is `market_ranks` of that feed,
    read only for a player `market_of` serves. `previous_pool_ids` is the
    last record's pool (None: no comparable record, nothing marked NEW) and
    `previous_first_seen` its notable rows' first-seen times by id."""
    ranks = ranks or {}
    seen_before = {}
    for k, v in (previous_first_seen or {}).items():
        try:
            t = datetime.fromisoformat(str(v))
        except (TypeError, ValueError):
            continue
        if t.tzinfo is not None:
            seen_before[normalize_id(k)] = t
    prev = None if previous_pool_ids is None else {normalize_id(x) for x in previous_pool_ids}
    notes = [drops_note] if drops_note else []
    if not prior:
        notes.append("last season's box scores not loaded: 'a starter last season' "
                     "cannot be checked")
    notable: list[NotableFreeAgent] = []
    market: list[MarketRow] = []
    window = now - timedelta(days=DROP_WINDOW_DAYS)
    for p in pool:
        sid = normalize_id(p.sleeper_id)
        rec = sleeper_players.get(sid) or {}
        pr = p.projection
        if pr.usable and not pr.is_withheld:
            ours, ours_note = float(pr.mean), ""
        elif pr.is_withheld:            # known not to play this week: 0, with why
            ours, ours_note = None, (pr.reasons[-1] if pr.reasons else "withheld")
        else:
            ours, ours_note = None, (pr.reasons[0] if pr.reasons else "not projected")
        mkt = market_of(p)
        mrank = ranks.get(sid) if mkt is not None else None
        status = _status(p, rec, reserve)
        cutoff = STARTER_RANK.get(p.position)       # this week's position, for this week's rank
        # -- the market list: abstained (not withheld) with a Sleeper number
        if not pr.usable and not pr.is_withheld and mkt is not None:
            vs, gain = _versus(mkt, best, slots, p.position, "Sleeper's")
            market.append(MarketRow(p, round(float(mkt), 2), ours_note, status, vs, gain,
                                    mrank))
        # -- notable
        why = []
        pl = prior.get(p.gsis_id) if p.gsis_id else None
        if pl is not None and pl.starter:
            why.append(f"a starter last season ({pl.describe(prior_season)})")
        d = drops.get(sid)
        if d is not None and d.at >= window:
            value = ours if ours is not None else mkt
            w = weakest_starter(best, slots, p.position)
            worth = []
            if mrank is not None and cutoff and mrank <= cutoff:
                worth.append(f"Sleeper ranks him {p.position}{mrank} this week "
                             f"({mkt:.1f})")
            if w is not None and value is not None and value >= float(w.projection.mean):
                worth.append(f"{'ours' if ours is not None else 'Sleeper'} {value:.1f} "
                             f"reaches your weakest eligible starter "
                             f"({w.projection.mean:.1f})")
            if worth:
                why.append(f"{d.describe(my_roster_id)}, and " + "; ".join(worth))
            elif why:
                why.append(d.describe(my_roster_id))
        if not why:
            continue
        run = waiver_run_after(d.at) if d is not None and d.at >= window else None
        if run is not None:
            waiver = ((f"on waivers: the league's settings put the first claim run at "
                       f"{run:%a %d %b %H:%M} UTC ({WAIVER_CLEAR_DAYS}-day clear, FAAB) — "
                       f"verify in Sleeper and bid before it") if run > now else
                      (f"the league's settings put his waiver run at {run:%a %d %b %H:%M} "
                       f"UTC, already past: unclaimed, he should be a FREE AGENT now — "
                       f"verify in Sleeper"))
        else:
            waiver = ("no league drop in the last "
                      f"{DROP_WINDOW_DAYS} days on record: open him in Sleeper — it shows "
                      "FREE AGENT or the waiver clear time")
        vs, _ = _versus(ours if ours is not None else mkt, best, slots, p.position,
                        "ours" if ours is not None else "Sleeper's")
        new = bool(prev is not None and sid not in prev and sid not in seen_before)
        first = now if new else seen_before.get(sid)
        alert = bool((first is not None and now - first <= timedelta(hours=ALERT_HOURS))
                     or (run is not None and run > now))
        notable.append(NotableFreeAgent(p, tuple(why), pl, d if run is not None else None,
                                        ours, ours_note, mkt, status, waiver, new=new,
                                        versus=vs, first_seen=first, alert=alert))

    def nkey(n: NotableFreeAgent):
        frac = (n.prior.rank / STARTER_RANK.get(n.prior.position, 30)
                if n.prior and n.prior.rank else 9.0)
        return (0 if n.alert else 1, 0 if n.new else 1, 0 if n.drop else 1, frac,
                -(n.ours if n.ours is not None else (n.market or 0.0)))
    notable.sort(key=nkey)
    market.sort(key=lambda m: -m.market)
    return FreeAgentWatch(tuple(notable), tuple(market[:MARKET_CAP]), tuple(notes),
                          market_total=len(market))
