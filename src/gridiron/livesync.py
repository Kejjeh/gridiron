"""Five-minute Sleeper-only league sync: the logic, with the network injected.

WHAT THIS IS FOR. The weekly puller (`scripts/ingest/pull_week.py`) is a heavy,
occasional job: nflverse frames, a 16 MB player dump, a crosswalk. None of that
changes on a five-minute timescale and pulling it on one would be abusive. What
DOES change inside a single afternoon is the league itself — a starter swapped,
a player moved to reserve, the matchup scoreboard. So this module syncs exactly
that and nothing else: league, users, rosters, current-week matchups, NFL state.
The player dump stays on its own once-a-day cadence, which is what Sleeper's own
documentation recommends for it.

READ-ONLY. Every call goes through `gridiron.sleeper.SleeperReadOnly`, which is
GET-only by construction and has no method that could mutate the league. This
module adds no endpoint of its own.

THE ENDPOINTS ARE NOT A TRANSACTION. Sleeper offers five separate GETs, not one
consistent read. A roster can change between the rosters call and the matchups
call and no API here would tell us. Three things follow, and all three are
implemented below rather than hoped for:

  1. The read window is BOUNDED and recorded (`read_window_seconds`), so a
     reader can see how much time the snapshot spans instead of assuming zero.
  2. NFL state is read at both ends of the window. If the week or season type
     moved underneath us — the rollover case — the snapshot is discarded and
     re-read once, because a snapshot half from week 2 and half from week 3 is
     worse than no snapshot at all.
  3. The snapshot never claims atomicity. `consistency` says plainly what it is.

FAILURE PRESERVES DATA. A 404, an empty list, a partial payload, a timeout, a
rate limit or a malformed body all end the same way: the previous good snapshot
stays exactly where it was, and the failure is recorded in the sync state with
its reason and time. The published file is only ever replaced by a payload that
passed validation whole.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import platform
import time
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from gridiron.league_config import (
    MY_SLEEPER_USERNAME,
    NUM_TEAMS,
    SEASON_YEAR,
    SLEEPER_LEAGUE_ID,
)

#: The owner asked for five minutes, explicitly. It is the sync interval AND
#: the basis for "next due"; the Windows task is configured to match.
SYNC_INTERVAL_SECONDS = 300
#: The whole point of the separate cadence: the player dump is bulk and slow-
#: moving, and Sleeper asks callers to fetch it at most once a day. Nothing in
#: this module fetches it; this constant exists so the number is stated in the
#: same file that states the five-minute one, where the contrast is visible.
PLAYER_DUMP_MIN_INTERVAL_SECONDS = 86_400

#: Snapshots are IMMUTABLE GENERATIONS: every publish writes a new
#: `sleeper_league_<utc stamp>.json` and nothing ever rewrites one in place.
#: The manifest entry is then replaced atomically to point at it, so the
#: (as_of, path) pair a reader gets is always the pair that was written
#: together. Overwriting one well-known filename cannot offer that: between
#: the snapshot write and the manifest write there is a window where the file
#: is new and the timestamp beside it is old, and a reader landing in that
#: window ages fresh data by the previous pull's clock — or, worse, reads a
#: week-3 roster under a week-2 as-of. The generation file closes the window
#: by never letting the old path and the new bytes be the same object.
GENERATION_PREFIX = "sleeper_league_"
GENERATION_SUFFIX = ".json"
#: The pre-generation filename. Still READ as a fallback so an existing cache
#: written by the old puller keeps working; never written by this module.
LEGACY_SNAPSHOT_NAME = "sleeper_league.json"
#: How many old generations survive a publish. Two spares is enough to
#: diagnose "what changed in the last ten minutes" and bounded enough that a
#: five-minute cadence cannot fill a disk.
KEEP_GENERATIONS = 3
STATE_NAME = "sync_state.json"
LOCK_NAME = "sync.lock"
#: The per-game status feed, kept beside the league snapshot under the same
#: lock. One fixed filename: the feed is small, the as-of is written INSIDE
#: the file as well as in the manifest, and a reader that lands between the
#: two writes uses the file's own stamp. Terminal statuses are monotone, so an
#: older copy can only be less complete, never wrong about a final.
GAME_STATUS_NAME = "game_status"
GAME_STATUS_FILE = "game_status.json"
#: Statuses actually observed in the feed (2026-09-20). Anything else is
#: recorded verbatim and rendered as UNKNOWN with the raw word shown; the
#: reader never maps an unseen word onto a state it did not earn.
OBSERVED_GAME_STATUSES: frozenset[str] = frozenset(
    {"pre_game", "in_game", "complete", "suspended", "canceled"})



_TEMP_SEQ = itertools.count()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# Validation: what a publishable snapshot has to look like
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Rejection:
    """One reason a payload is not fit to publish.

    `partial` marks the difference that matters operationally: a payload that
    is the WRONG LEAGUE is a configuration error a human must fix, while a
    payload that is merely INCOMPLETE is usually a bad minute on the wire and
    the next run will be fine. Both refuse to publish; only one is worth
    shouting about.
    """

    code: str
    detail: str
    partial: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.detail}"


def _mapping(x: Any) -> Mapping[str, Any] | None:
    return x if isinstance(x, Mapping) else None


def _dicts(x: Any) -> list[Mapping[str, Any]]:
    if not isinstance(x, Sequence) or isinstance(x, (str, bytes)):
        return []
    return [d for d in x if isinstance(d, Mapping)]


def _member_defects(x: Any) -> int:
    """How many members of a list are not objects.

    Silently filtering them — which `_dicts` does, because every caller past
    validation wants clean rows — would let a payload of twelve nulls validate
    as an empty list and a payload of six rosters and six strings validate as
    a six-roster league. Counted here so validation can refuse it.
    """
    if not isinstance(x, Sequence) or isinstance(x, (str, bytes)):
        return 0
    return sum(1 for d in x if not isinstance(d, Mapping))


def validate_snapshot(payload: Any, *, league_id: str = SLEEPER_LEAGUE_ID,
                      season: int = SEASON_YEAR,
                      owner_username: str | None = MY_SLEEPER_USERNAME,
                      ) -> tuple[Rejection, ...]:
    """Every reason this payload must not replace the last good one.

    Empty tuple means publishable. The checks run in identity-then-shape order
    because "this is someone else's league" and "this is last season" are the
    failures that would do real damage if they were published quietly, and they
    are also the ones a retry will never fix.
    """
    out: list[Rejection] = []
    snap = _mapping(payload)
    if snap is None:
        return (Rejection("malformed", "payload is not a JSON object"),)

    league = _mapping(snap.get("league"))
    if league is None:
        out.append(Rejection("malformed", "no league object in payload",
                             partial=True))
    else:
        got_id = str(league.get("league_id") or "")
        if got_id != str(league_id):
            out.append(Rejection(
                "wrong_league",
                f"payload is league {got_id or '<none>'}, configured "
                f"league is {league_id}"))
        got_season = str(league.get("season") or "")
        if not got_season:
            # Fail closed. An absent season is not "probably this season": it
            # is a payload we cannot place in time, and publishing it would
            # let last season's league object sit under this season's manifest.
            out.append(Rejection(
                "missing_season", "league object carries no season"))
        elif got_season != str(season):
            out.append(Rejection(
                "wrong_season",
                f"league object is season {got_season}, configured season "
                f"is {season}"))
        if not (_mapping(league.get("scoring_settings")) or {}):
            out.append(Rejection(
                "missing_settings", "league object carries no scoring_settings"))
        if not (league.get("roster_positions") or []):
            out.append(Rejection(
                "missing_settings", "league object carries no roster_positions"))

    state = _mapping(snap.get("state")) or {}
    try:
        state_season = int(state.get("season"))
    except (TypeError, ValueError):
        state_season = None
    if state_season is None:
        out.append(Rejection(
            "missing_season", "NFL state carries no readable season"))
    elif state_season != int(season):
        out.append(Rejection(
            "wrong_season",
            f"NFL state reports season {state_season}, configured season "
            f"is {season}"))

    users = _dicts(snap.get("users"))
    rosters = _dicts(snap.get("rosters"))
    if not users:
        out.append(Rejection("empty", "users list is empty", partial=True))
    if not rosters:
        out.append(Rejection("empty", "rosters list is empty", partial=True))
    elif len(rosters) != NUM_TEAMS:
        # Not pedantry: a short rosters list is the shape a truncated or
        # rate-limited response takes, and it would read downstream as teams
        # having vanished from the league.
        out.append(Rejection(
            "partial",
            f"{len(rosters)} rosters for a {NUM_TEAMS}-team league",
            partial=True))
    for label, raw in (("users", snap.get("users")),
                       ("rosters", snap.get("rosters")),
                       ("matchups", snap.get("matchups"))):
        bad = _member_defects(raw)
        if bad:
            out.append(Rejection(
                "malformed", f"{bad} {label} entr{'y is' if bad == 1 else 'ies are'} "
                             f"not an object", partial=True))
    if rosters and not all(r.get("roster_id") is not None for r in rosters):
        out.append(Rejection("malformed", "a roster has no roster_id",
                             partial=True))
    elif rosters:
        ids = [str(r.get("roster_id")) for r in rosters]
        if len(set(ids)) != len(ids):
            # Duplicate roster ids make every id-anchored join downstream
            # ambiguous (rule #3). A merged or truncated response is the
            # likely cause and neither is publishable.
            dupes = sorted({i for i in ids if ids.count(i) > 1})
            out.append(Rejection(
                "malformed", f"duplicate roster_id(s): {', '.join(dupes)}"))

    # Owner identity. Not a credential — a public handle — but publishing a
    # snapshot in which the owner does not appear means the league is not his,
    # and every roster-aware thing downstream would be reading a stranger's team.
    if owner_username and users:
        by_name = {str(u.get("display_name") or "").casefold(): str(u.get("user_id"))
                   for u in users}
        uid = by_name.get(str(owner_username).casefold())
        if uid is None:
            out.append(Rejection(
                "owner_absent",
                f"configured owner is not among the {len(users)} league users"))
        elif rosters and not any(
                str(r.get("owner_id")) == uid
                or uid in {str(c) for c in (r.get("co_owners") or [])}
                for r in rosters):
            out.append(Rejection(
                "owner_absent", "configured owner owns no roster in this league"))

    # Matchups are legitimately absent before the season starts, so this is a
    # defect only once there is a regular-season week that should have them.
    season_type = str(state.get("season_type") or "regular")
    try:
        week = int(snap.get("week"))
    except (TypeError, ValueError):
        week = 0
    if season_type == "regular" and week >= 1:
        matchups = _dicts(snap.get("matchups"))
        if not matchups:
            out.append(Rejection(
                "empty",
                f"no matchups for regular-season week {week}", partial=True))
        elif rosters:
            # Every roster plays every week, exactly once. A count check
            # accepts twelve copies of one roster; the SET must match.
            m_ids = [str(m.get("roster_id")) for m in matchups]
            r_ids = {str(r.get("roster_id")) for r in rosters}
            if len(set(m_ids)) != len(m_ids):
                out.append(Rejection(
                    "malformed", f"duplicate roster_id in week {week} matchups"))
            elif set(m_ids) != r_ids:
                missing = sorted(r_ids - set(m_ids))
                extra = sorted(set(m_ids) - r_ids)
                out.append(Rejection(
                    "partial",
                    f"week {week} matchups do not cover the roster set"
                    + (f"; missing {missing}" if missing else "")
                    + (f"; unknown {extra}" if extra else ""),
                    partial=not extra))
    return tuple(out)


# --------------------------------------------------------------------------
# Settings drift
# --------------------------------------------------------------------------
#: The parts of the league object that define how the league SCORES and is
#: SHAPED. A change to any of these invalidates rule-#1's verification, which
#: is why it is watched every sync rather than only when someone remembers to
#: run the verifier.
_WATCHED_SETTINGS = (
    "playoff_teams", "playoff_week_start", "num_teams", "waiver_type",
    "waiver_budget", "waiver_day_of_week", "waiver_clear_days",
    "trade_deadline", "trade_review_days", "max_keepers",
)


def settings_digest(league: Mapping[str, Any] | None) -> dict[str, Any]:
    """The scoring/shape subset of the league object, canonically ordered."""
    lg = _mapping(league) or {}
    settings = _mapping(lg.get("settings")) or {}
    scoring = _mapping(lg.get("scoring_settings")) or {}
    return {
        "scoring_settings": {str(k): scoring[k] for k in sorted(scoring)},
        "roster_positions": [str(p) for p in (lg.get("roster_positions") or [])],
        "settings": {k: settings.get(k) for k in _WATCHED_SETTINGS
                     if k in settings},
    }


def settings_fingerprint(league: Mapping[str, Any] | None) -> str:
    blob = json.dumps(settings_digest(league), sort_keys=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def settings_changes(old: Mapping[str, Any] | None,
                     new: Mapping[str, Any] | None) -> tuple[str, ...]:
    """Which watched keys differ between two league objects.

    Reported, never acted on. Detecting drift does NOT re-verify settings and
    does NOT touch SETTINGS_VERIFIED: rule #1 says verification is a human
    decision backed by a pull, and a sync noticing a changed number is the
    trigger for that decision, not a substitute for it.
    """
    a, b = settings_digest(old), settings_digest(new)
    changed: list[str] = []
    for section in ("scoring_settings", "settings"):
        keys = set(a[section]) | set(b[section])
        changed += [f"{section}.{k}" for k in sorted(keys)
                    if a[section].get(k) != b[section].get(k)]
    if a["roster_positions"] != b["roster_positions"]:
        changed.append("roster_positions")
    return tuple(changed)


# --------------------------------------------------------------------------
# Sync state: what the local status command reads
# --------------------------------------------------------------------------
@dataclass
class SyncState:
    """The running record of this sync, kept beside the snapshot.

    Separate from the ingest manifest on purpose. The manifest answers "how old
    is this DATA"; this answers "is the SYNC healthy" — when it last succeeded,
    what the last attempt did, how many failures have stacked up. A long run of
    failures with a good snapshot still on disk is invisible in the manifest's
    terms and is exactly what an operator needs to see.
    """

    last_success: str = ""
    last_attempt: str = ""
    last_error: str = ""
    last_result: str = "never run"
    consecutive_failures: int = 0
    successes: int = 0
    failures: int = 0
    snapshot_week: int | None = None
    read_window_seconds: float = 0.0
    settings_fingerprint: str = ""
    settings_drift: list[str] = field(default_factory=list)
    settings_drift_at: str = ""

    @classmethod
    def load(cls, directory: Path) -> "SyncState":
        p = Path(directory) / STATE_NAME
        if not p.exists():
            return cls()
        try:
            blob = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            # A corrupt state file must not stop the sync: state is a report
            # about syncing, not an input to it. Losing the counters is a far
            # smaller harm than a scheduled job that refuses to run.
            return cls(last_result="state file unreadable; counters reset")
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in blob.items() if k in known})

    def save(self, directory: Path) -> Path:
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        p = d / STATE_NAME
        _atomic_write(p, json.dumps(asdict(self), indent=1))
        return p

    @property
    def last_success_dt(self) -> datetime | None:
        if not self.last_success:
            return None
        dt = datetime.fromisoformat(self.last_success)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

    def next_due(self, *, interval: int = SYNC_INTERVAL_SECONDS
                 ) -> datetime | None:
        got = self.last_success_dt
        return None if got is None else got + timedelta(seconds=interval)

    def age_seconds(self, now: datetime) -> float | None:
        got = self.last_success_dt
        return None if got is None else (now - got).total_seconds()

    def is_due(self, now: datetime, *, interval: int = SYNC_INTERVAL_SECONDS
               ) -> bool:
        age = self.age_seconds(now)
        return True if age is None else age >= interval


def _atomic_write(path: Path, text: str) -> None:
    """Rename into place so a reader never sees half a file.

    `os.replace` is atomic on Windows as well as POSIX, which matters here
    because the scheduled writer and a human running `status` genuinely do race.

    The temp name is UNIQUE per process and per call. A shared `<name>.part`
    is its own race: two writers land on the same scratch file, one truncates
    the other's half-written bytes, and whichever renames last publishes a
    spliced file that is individually valid JSON and collectively nobody's
    data. The state file is written outside the snapshot lock, so this is not
    theoretical there.
    """
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{next(_TEMP_SEQ)}.part")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Publishing: one coherent (snapshot, manifest) pair
# --------------------------------------------------------------------------
def generation_name(now: datetime) -> str:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{GENERATION_PREFIX}{stamp}{GENERATION_SUFFIX}"


def current_snapshot(directory: Path, manifest=None) -> Path | None:
    """The snapshot a reader should read: whatever the manifest points at.

    The manifest is the authority, not the directory listing — a newer
    generation file that no manifest entry references is a publish that did
    not finish, and reading it would be reading uncommitted data. Falls back
    to the legacy fixed filename so a cache written before generations existed
    is still readable.
    """
    d = Path(directory)
    if manifest is not None:
        p = manifest.file("sleeper_league")
        if p is not None:
            return p
    legacy = d / LEGACY_SNAPSHOT_NAME
    return legacy if legacy.exists() else None


def publish_snapshot(directory: Path, payload: Mapping[str, Any], *,
                     now: datetime, source: str, rows: int,
                     week: int | None = None, season: int = SEASON_YEAR,
                     holder: str = "sleeper_sync") -> Path:
    """Write a new generation and repoint the manifest at it, under the lock.

    Every writer of `sleeper_league` goes through here — the five-minute sync
    and the weekly puller both — because a lock only one writer takes is not a
    lock. The manifest is RE-READ inside the lock and only this one entry is
    touched, so a concurrent nflverse pull that recorded `weekly_stats` while
    we were fetching does not get erased by a stale in-memory copy.
    """
    with single_writer(Path(directory), now=now, holder=holder):
        return _publish_locked(directory, payload, now=now, source=source,
                               rows=rows, week=week, season=season)


def _publish_locked(directory: Path, payload: Mapping[str, Any], *,
                    now: datetime, source: str, rows: int,
                    week: int | None = None,
                    season: int = SEASON_YEAR) -> Path:
    """The publish itself, for a caller that ALREADY holds the lock.

    Split out because the OS lock is not reentrant: `sync_once` holds it across
    the whole read-fetch-publish-save sequence, and re-acquiring it here would
    deadlock rather than merely be redundant.
    """
    d = Path(directory)
    if True:
        from gridiron.ingest import Manifest

        target = d / generation_name(now)
        n = 1
        while target.exists():          # same-second republish; never clobber
            target = d / f"{generation_name(now)[:-len(GENERATION_SUFFIX)]}-{n}{GENERATION_SUFFIX}"
            n += 1
        _atomic_write(target, json.dumps(dict(payload), indent=1))
        manifest = Manifest.load(d, season)
        manifest.record("sleeper_league", path=target, rows=int(rows),
                        source=source,
                        weeks=[int(week)] if week is not None else [],
                        as_of=now)
        manifest.save()
        _prune_generations(d, keep=target.name)
        return target


def _prune_generations(directory: Path, *, keep: str) -> None:
    """Drop all but the newest few generations, never the referenced one."""
    gens = sorted(
        (p for p in Path(directory).glob(f"{GENERATION_PREFIX}*{GENERATION_SUFFIX}")),
        key=lambda p: p.name, reverse=True)
    for stale in gens[KEEP_GENERATIONS:]:
        if stale.name != keep:
            stale.unlink(missing_ok=True)


# --------------------------------------------------------------------------
# Single writer
# --------------------------------------------------------------------------
class LockBusy(RuntimeError):
    """Another sync holds the lock. Not an error condition — the expected way
    a five-minute schedule declines to pile a second run on top of a slow one."""


def _os_lock(fh) -> bool:
    """Take an exclusive, non-blocking OS lock on the open file. False if held.

    The OS owns this, which is the entire point. A lock implemented by creating
    and deleting a file cannot be made safe: two processes can both observe an
    abandoned lock, both unlink it, and both create their own — and a token
    written into the file only tells a holder whether to release, long after
    both have already entered. There is no ordering of userspace steps that
    fixes it. An OS lock has no such window, and the kernel drops it when the
    holder dies, so a killed process needs no timeout and no reclaiming.
    """
    if os.name == "posix":
        import fcntl

        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    import msvcrt                    # Windows

    try:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def _os_unlock(fh) -> None:
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        else:
            import msvcrt

            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except OSError:
        pass                         # closing the handle releases it anyway


@contextmanager
def single_writer(directory: Path, *, now: datetime | None = None,
                  holder: str = "sleeper_sync") -> Iterator[Path]:
    """Exclusive write access to the cache directory, held by the OS.

    The lock file is PERSISTENT and is never unlinked. Its existence means
    nothing; only the OS lock on it does. That is what removes the
    create/delete race: every process locks the same inode, so there is no
    moment at which two of them can each believe they hold it.

    Not reentrant, deliberately. `fcntl.flock` on a second descriptor in the
    same process would block or fail, so anything that needs to publish while
    already holding the lock calls `_publish_locked` rather than nesting.
    """
    d = Path(directory)
    d.mkdir(parents=True, exist_ok=True)
    lock = d / LOCK_NAME
    stamp = now or _now()
    fh = open(lock, "a+")
    try:
        if not _os_lock(fh):
            raise LockBusy(f"another writer holds {lock.name}")
        try:
            # Diagnostics only. Nothing reads this to decide anything.
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps({"pid": os.getpid(), "node": platform.node(),
                                 "holder": holder, "started_at": _iso(stamp)}))
            fh.flush()
            yield lock
        finally:
            _os_unlock(fh)
    finally:
        fh.close()


def lock_holder(directory: Path) -> dict | None:
    """Who last took the lock, for status output. Never used to decide."""
    try:
        return json.loads((Path(directory) / LOCK_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --------------------------------------------------------------------------
# The sync itself
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class SyncResult:
    ok: bool
    code: str
    detail: str
    state: SyncState
    published: bool = False
    week: int | None = None
    drift: tuple[str, ...] = ()

    def line(self) -> str:
        mark = "OK " if self.ok else "!! "
        wk = f" week {self.week}" if self.week is not None else ""
        return f"{mark}{self.code}{wk}: {self.detail}"


def read_league(client, *, now: datetime | None = None,
                retry_on_rollover: bool = True) -> tuple[dict, str]:
    """One bounded read of the league, re-read once if the week moved.

    Returns the payload plus a note describing the consistency actually
    achieved. The second `state()` call is the cheap endpoint and is the whole
    rollover guard: if the NFL week ticked over mid-read we throw the snapshot
    away rather than publish one stitched from two different weeks.
    """
    for attempt in (0, 1):
        started = now if (now is not None and attempt == 0) else _now()
        before = client.state()
        payload = client.snapshot(week=int(before.week))
        after = client.state()
        finished = _now()
        payload["read_window_seconds"] = round(
            max((finished - started).total_seconds(), 0.0), 3)
        payload["consistency"] = (
            "five sequential GETs, not an atomic read; NFL state was confirmed "
            "unchanged across the window")
        moved = (before.week, before.season, before.season_type) != (
            after.week, after.season, after.season_type)
        if not moved:
            return payload, "stable"
        if not retry_on_rollover or attempt == 1:
            payload["consistency"] = (
                f"NFL state moved from week {before.week} to week {after.week} "
                f"during the read; snapshot NOT published")
            return payload, "rollover"
    raise AssertionError("unreachable")


def sync_once(client, directory: Path, *, now: datetime | None = None,
              league_id: str = SLEEPER_LEAGUE_ID, season: int = SEASON_YEAR,
              owner_username: str | None = MY_SLEEPER_USERNAME,
              manifest=None) -> SyncResult:
    # `manifest` is accepted and ignored: the manifest is now re-read inside
    # the lock, which is the only copy that can be trusted.
    """Refresh the league snapshot, or fail without touching the good one.

    The ordering below is deliberate and is the reason a failure is safe:
    fetch, then validate, and only then write. Nothing on disk is opened for
    writing until a complete payload has passed every check, so there is no
    window in which the published snapshot is a partial one.
    """
    stamp = now or _now()
    d = Path(directory)
    try:
        with single_writer(d, now=stamp, holder="sleeper_sync"):
            return _sync_locked(client, d, stamp, league_id, season,
                                owner_username)
    except LockBusy as exc:
        # A previous run is still working. Nothing is read, nothing is written,
        # and the counters are left alone — recording an attempt here would
        # mean a busy tick could reset a streak it never tested.
        return SyncResult(False, "busy", str(exc), SyncState.load(d))


def _sync_locked(client, d: Path, stamp: datetime, league_id: str, season: int,
                 owner_username: str | None) -> SyncResult:
    """The whole sync, serialized.

    State load, fetch, validate, publish and state save all happen inside one
    lock hold. Reading the state before taking the lock was its own race: a
    slow run could finish after a faster later one and write back both an older
    snapshot pointer and stale counters over it.
    """
    state = SyncState.load(d)
    state.last_attempt = _iso(stamp)

    def fail(code: str, detail: str) -> SyncResult:
        state.last_error = detail
        state.last_result = code
        state.consecutive_failures += 1
        state.failures += 1
        state.save(d)
        return SyncResult(False, code, detail, state)

    try:
        payload, consistency = read_league(client, now=stamp)
    except Exception as exc:  # timeout, rate limit, transport, decode
        # Deliberately broad: every way a network call can fail ends in the
        # same place — the previous snapshot stays, and we say what happened.
        return fail("fetch_failed", f"{type(exc).__name__}: {exc}"[:300])

    if consistency == "rollover":
        return fail("rollover",
                    "NFL week changed during the read window; discarded the "
                    "snapshot rather than publish a mixed-week one")

    problems = validate_snapshot(payload, league_id=league_id, season=season,
                                 owner_username=owner_username)
    if problems:
        code = "partial" if all(p.partial for p in problems) else "invalid"
        return fail(code, "; ".join(str(p) for p in problems)[:500])

    from gridiron.ingest import Manifest

    manifest = Manifest.load(d)
    prior = current_snapshot(d, manifest)
    previous = _read_json(prior) if prior is not None else None
    drift = settings_changes(_mapping(previous or {}).get("league")
                             if previous else None,
                             payload.get("league")) if previous else ()

    week = int(payload["week"])
    try:
        _publish_locked(d, payload, now=stamp,
                        source="sleeper.snapshot (live sync)",
                        rows=len(_dicts(payload.get("rosters"))),
                        week=week, season=int(season))
    except OSError as exc:
        return fail("write_failed", f"{type(exc).__name__}: {exc}"[:300])

    state.last_success = _iso(stamp)
    state.last_error = ""
    state.last_result = "ok"
    state.consecutive_failures = 0
    state.successes += 1
    state.snapshot_week = week
    state.read_window_seconds = float(payload.get("read_window_seconds") or 0.0)
    fingerprint = settings_fingerprint(payload.get("league"))
    if drift:
        # Recorded, surfaced, and left for a human. Rule #1: nothing here
        # re-verifies settings or flips a flag.
        state.settings_drift = list(drift)
        state.settings_drift_at = _iso(stamp)
    state.settings_fingerprint = fingerprint
    state.save(d)
    return SyncResult(True, "ok",
                      f"{len(_dicts(payload.get('rosters')))} rosters, "
                      f"{len(_dicts(payload.get('matchups')))} matchup rows",
                      state, published=True, week=week, drift=drift)


# --------------------------------------------------------------------------
# Per-game status feed
# --------------------------------------------------------------------------
def validate_game_status(payload: Any) -> tuple[list[dict], int, str]:
    """Normalise the feed to the rows a reader may trust.

    Returns `(games, dropped, reason)`. `games` is the list of well-formed
    rows (team names upper-cased, week an int, status a lower-cased word);
    `dropped` counts members that were not; `reason` is non-empty when the
    whole payload must be refused — not a list, or no usable row at all. A
    feed with a few broken rows still counts, because a final that IS in it
    is worth more than a run that refuses everything over one bad member.
    """
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        return [], 0, "game status feed is not a list"
    out: list[dict] = []
    dropped = 0
    for raw in payload:
        row = _mapping(raw)
        if row is None:
            dropped += 1
            continue
        try:
            week = int(row.get("week"))
        except (TypeError, ValueError):
            dropped += 1
            continue
        home = str(row.get("home") or "").strip().upper()
        away = str(row.get("away") or "").strip().upper()
        status = str(row.get("status") or "").strip().lower()
        if week < 1 or not home or not away or home == away or not status:
            dropped += 1
            continue
        out.append({"week": week, "home": home, "away": away, "status": status,
                    "date": str(row.get("date") or ""),
                    "game_id": str(row.get("game_id") or "")})
    if not out:
        return [], dropped, "game status feed carries no usable game row"
    return out, dropped, ""


def refresh_game_status(client, directory: Path, *, now: datetime | None = None,
                        season: int = SEASON_YEAR) -> SyncResult:
    """Fetch the per-game status feed and record it, or fail without touching
    the last good copy. Separate from `sync_once` on purpose: the league
    snapshot's publish path and its validation are reviewed invariants, and
    this feed is an optional companion — a run where the league sync works
    and this fails still leaves a coherent snapshot behind.
    """
    stamp = now or _now()
    d = Path(directory)
    source = f"api.sleeper.app/schedule/nfl/regular/{int(season)} (undocumented feed)"
    try:
        with single_writer(d, now=stamp, holder="game_status"):
            from gridiron.ingest import Manifest

            manifest = Manifest.load(d, season)

            def fail(code: str, detail: str) -> SyncResult:
                manifest.record_failure(GAME_STATUS_NAME, source=source,
                                        error=detail[:300], at=stamp)
                manifest.save()
                return SyncResult(False, code, detail, SyncState.load(d))

            fetch = getattr(client, "schedule", None)
            if fetch is None:
                return fail("unsupported", "this client has no schedule feed")
            try:
                payload = fetch(int(season))
            except Exception as exc:  # timeout, rate limit, transport, decode
                return fail("fetch_failed", f"{type(exc).__name__}: {exc}"[:300])
            games, dropped, why = validate_game_status(payload)
            if why:
                return fail("invalid", why)
            blob = {"as_of": _iso(stamp), "season": int(season), "source": source,
                    "dropped": dropped, "games": games}
            try:
                _atomic_write(d / GAME_STATUS_FILE, json.dumps(blob, indent=1))
            except OSError as exc:
                return fail("write_failed", f"{type(exc).__name__}: {exc}"[:300])
            manifest.record(GAME_STATUS_NAME, path=d / GAME_STATUS_FILE,
                            rows=len(games), source=source,
                            weeks=sorted({g["week"] for g in games}), as_of=stamp)
            manifest.save()
            unseen = sorted({g["status"] for g in games} - OBSERVED_GAME_STATUSES)
            return SyncResult(True, "ok",
                              f"{len(games)} game rows"
                              + (f", {dropped} dropped" if dropped else "")
                              + (f", unrecognised status word(s): {', '.join(unseen)}"
                                 if unseen else ""),
                              SyncState.load(d), published=True)
    except LockBusy as exc:
        return SyncResult(False, "busy", str(exc), SyncState.load(d))


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def status_lines(state: SyncState, directory: Path, *,
                 now: datetime | None = None,
                 interval: int = SYNC_INTERVAL_SECONDS) -> list[str]:
    """Human-readable local status. No network, no league data, no names."""
    stamp = now or _now()
    d = Path(directory)
    from gridiron.ingest import Manifest

    snap = current_snapshot(d, Manifest.load(d))
    age = state.age_seconds(stamp)
    due = state.next_due(interval=interval)
    out = [
        f"snapshot      {snap.name if snap is not None else 'ABSENT'}"
        f"{'' if snap is None else ('  (immutable generation)' if snap.name.startswith(GENERATION_PREFIX) else '  (LEGACY fixed filename, pre-generation)')}",
        f"last success  {state.last_success or 'never'}"
        + (f"  ({age / 60:.1f} min ago)" if age is not None else ""),
        f"last attempt  {state.last_attempt or 'never'}  [{state.last_result}]",
        f"next due      {_iso(due) if due else 'now (never synced)'}"
        + ("  — OVERDUE" if state.is_due(stamp, interval=interval) else ""),
        f"week          {state.snapshot_week if state.snapshot_week else '-'}",
        f"counters      {state.successes} ok / {state.failures} failed"
        f"  (streak of {state.consecutive_failures} failing)",
        f"read window   {state.read_window_seconds:.1f}s "
        f"(sequential GETs, not atomic)",
    ]
    if state.last_error:
        out.append(f"last error    {state.last_error[:200]}")
    if state.settings_drift:
        out.append(f"SETTINGS DRIFT detected {state.settings_drift_at}: "
                   f"{', '.join(state.settings_drift)}")
        out.append("              settings remain UNVERIFIED-BY-THIS-TOOL; run "
                   "scripts/verify_league_settings.py and decide by hand (rule #1)")
    who = lock_holder(d)
    if who:
        out.append(f"lock          last taken by {who.get('holder')} at "
                   f"{who.get('started_at')} (file is persistent; presence "
                   f"does not mean held)")
    return out
