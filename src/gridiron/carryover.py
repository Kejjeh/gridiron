"""Carrying decision records across runners that keep nothing.

A GitHub-hosted runner starts from an empty checkout every time. The
dashboard's ledger (`data/ledger/decisions/`) is gitignored, as rule #10
requires — the archives name the owner's players — so in the cloud it began
each run empty. Two features quietly stopped working as a result:

  * "Since the last snapshot" compared this page against the previous frozen
    page. With no previous page on disk, every cloud run was a first run and
    the section was permanently empty. It did not say so; it said nothing
    had changed.
  * Grading a decision after the fact needs the page that was on the screen
    when the decision was live. A record that never outlives its run cannot
    be graded next week, which is the whole point of freezing it.

This module moves those records between the runner and a private store, with
one rule running through it: **a restored record is untrusted input.** It was
written by an earlier run, but nothing about a file on disk proves that. So
every candidate is validated before it is allowed to count as evidence, and
anything that fails is left where it is and reported, never deleted and never
quietly repaired.

What is checked, and what each check is actually for:

  version    An archive from a format this code does not know is refused
             rather than read with today's assumptions about its keys.
  season     A record from another season is not this season's history.
  identity   The filename must agree with the contents. The name carries the
             write time and a digest of the record; if either disagrees with
             what is inside, the file has been renamed, edited or assembled
             elsewhere, and its name is a claim rather than a fact.
  time       A record stamped in the future is refused: the one thing a
             restored archive must never do is present itself as newer than
             the page being built, because "the previous snapshot" is chosen
             by time and a future stamp would make a stale record win.
  age        Restores are bounded. A record older than `RESTORE_MAX_AGE_DAYS`
             is not carried forward; the season's history is not unbounded
             state to drag through every run.

Retention, honestly. This module does not retain anything — it copies into
and out of a directory somebody else keeps. In the workflow that directory
is a GitHub Actions cache, which is **not durable storage**: entries are
evicted after 7 days without a read, and evicted early when the repository
passes its 10 GB cache limit. So the chain can break, and when it does the
page says "no previous snapshot" rather than inventing one. Anything that
must survive is downloaded from the run's artifact, which has its own stated
retention and is also finite. Neither is a backup.

Nothing here is a refresh. `restore` brings back what an earlier run FROZE;
it never presents that as current data. The page's own freshness and gating
layers decide what today's inputs support, and a run whose refresh failed
writes no new archive at all — so a last-good record stays dated when it was
written and can never be read as today's.
"""
from __future__ import annotations

import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron.decisions import (ARCHIVE_VERSION, archive_stamp, list_archives,
                                record_digest)

#: The oldest record worth carrying between runs. A season of weekly pages is
#: a few dozen files; this bounds a chain that would otherwise only grow.
RESTORE_MAX_AGE_DAYS = 45

#: How many records the private store keeps per season, newest first. Bounded
#: for the same reason, and because the store it lives in (see the module
#: docstring) is a cache with a size limit, not an archive.
DEFAULT_KEEP = 40

#: Tolerance between the write time in a filename and the `generated` field
#: inside it. They are written from the same clock in the same call, so they
#: agree to the second; a minute of slack costs nothing and survives a
#: filesystem that rounds.
STAMP_TOLERANCE = timedelta(minutes=1)


@dataclass(frozen=True)
class Verdict:
    """One candidate file and what was decided about it."""

    name: str
    accepted: bool
    reason: str

    def line(self) -> str:
        return f"{'OK  ' if self.accepted else 'SKIP'} {self.name}: {self.reason}"


@dataclass(frozen=True)
class CarryoverReport:
    action: str                       # "restore" | "publish"
    season: int
    verdicts: tuple[Verdict, ...] = field(default=())
    note: str = ""

    @property
    def accepted(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if v.accepted)

    @property
    def rejected(self) -> tuple[Verdict, ...]:
        return tuple(v for v in self.verdicts if not v.accepted)

    @property
    def ok(self) -> bool:
        """A carryover with nothing to carry is not a failure. A FIRST run
        legitimately has no history, and the page says so rather than
        treating the absence as an error."""
        return True

    def summary(self) -> str:
        bits = [f"{self.action}: {len(self.accepted)} record(s) accepted"]
        if self.rejected:
            bits.append(f"{len(self.rejected)} refused")
        if self.note:
            bits.append(self.note)
        return f"season {self.season} " + ", ".join(bits)


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def inspect_archive(path: Path, *, season: int, now: datetime,
                    max_age_days: int = RESTORE_MAX_AGE_DAYS) -> Verdict:
    """Decide whether one candidate file may be carried forward as evidence.

    Every rejection names what failed. Nothing is repaired: a record that
    does not describe itself correctly is not made to, it is left alone.
    """
    name = path.name
    named = archive_stamp(path)
    if named is None:
        return Verdict(name, False, "the filename is not an archive name, so "
                                    "the record cannot be placed in time")
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Verdict(name, False, f"unreadable ({type(exc).__name__})")
    if not isinstance(blob, dict):
        return Verdict(name, False, "not a decision record (top level is not an object)")

    version = blob.get("archive_version")
    if not isinstance(version, int):
        return Verdict(name, False, "no archive_version, so the layout of the "
                                    "record is not established")
    if version > ARCHIVE_VERSION:
        return Verdict(name, False, f"archive_version {version} is newer than this "
                                    f"code understands ({ARCHIVE_VERSION}); reading "
                                    f"it would mean guessing at its keys")

    if int(blob.get("season") or 0) != int(season):
        return Verdict(name, False, f"season {blob.get('season')!r} is not "
                                    f"season {season}")

    generated = _as_utc(blob.get("generated"))
    if generated is None:
        return Verdict(name, False, "no readable `generated` time inside the record")
    if abs(generated - named) > STAMP_TOLERANCE:
        return Verdict(name, False, f"the filename says it was written "
                                    f"{named:%Y-%m-%d %H:%M}Z but the record inside "
                                    f"says {generated:%Y-%m-%d %H:%M}Z — the name is "
                                    f"a claim the contents do not support")

    cutoff = now.astimezone(timezone.utc)
    if generated > cutoff + STAMP_TOLERANCE:
        return Verdict(name, False, f"stamped {generated:%Y-%m-%d %H:%M}Z, which is "
                                    f"in the future; a restored record must never "
                                    f"be able to outrank the page being built")
    if generated < cutoff - timedelta(days=max_age_days):
        return Verdict(name, False, f"written {(cutoff - generated).days} days ago, "
                                    f"past the {max_age_days}-day carry limit")

    claimed = record_digest({k: v for k, v in blob.items() if k != "archive_version"})
    stem = path.stem.rsplit("_", 1)
    if len(stem) == 2 and len(stem[1]) == 8 and all(c in "0123456789abcdef" for c in stem[1]):
        if stem[1] != claimed:
            return Verdict(name, False, "the content digest in the filename does not "
                                        "match the record inside it; the file has "
                                        "been edited or renamed since it was written")
        return Verdict(name, True, f"digest {claimed} verified against its contents")
    return Verdict(name, True, "no digest in the filename (written before digests); "
                               "time and season verified")


def restore(store: Path, ledger: Path, *, season: int, now: datetime,
            keep: int = DEFAULT_KEEP,
            max_age_days: int = RESTORE_MAX_AGE_DAYS) -> CarryoverReport:
    """Copy validated prior records from the private store into the ledger.

    Never overwrites a record already present in the ledger: this run may
    have written one, and a restored file must not be able to replace it.
    """
    season_dir = Path(store) / f"season{season}"
    target = Path(ledger) / f"season{season}"
    if not season_dir.is_dir():
        return CarryoverReport("restore", season, (),
                               note="no private store yet — this is a first run, "
                                    "so there is no previous snapshot to compare "
                                    "against and the page will say so")
    candidates = sorted(season_dir.glob("week*.json"),
                        key=lambda p: (archive_stamp(p) or datetime.min.replace(
                            tzinfo=timezone.utc), p.name), reverse=True)
    verdicts: list[Verdict] = []
    taken = 0
    for path in candidates:
        if taken >= keep:
            verdicts.append(Verdict(path.name, False,
                                    f"beyond the {keep}-record carry limit"))
            continue
        verdict = inspect_archive(path, season=season, now=now,
                                  max_age_days=max_age_days)
        if verdict.accepted:
            target.mkdir(parents=True, exist_ok=True)
            dest = target / path.name
            if dest.exists():
                verdicts.append(Verdict(path.name, True,
                                        "already in the ledger; left untouched"))
            else:
                shutil.copy2(path, dest)
                verdicts.append(verdict)
            taken += 1
        else:
            verdicts.append(verdict)
    return CarryoverReport("restore", season, tuple(verdicts))


def publish(ledger: Path, store: Path, *, season: int, now: datetime,
            keep: int = DEFAULT_KEEP) -> CarryoverReport:
    """Copy this run's records back into the private store, newest kept.

    Records already in the store are not rewritten — an archive is evidence
    (see `gridiron.decisions.write_archive`), and a second run that produced
    the same file has produced the same bytes.
    """
    target = Path(store) / f"season{season}"
    found = list_archives(Path(ledger), season)
    if not found:
        return CarryoverReport("publish", season, (),
                               note="nothing to publish: this run froze no "
                                    "decision record")
    target.mkdir(parents=True, exist_ok=True)
    verdicts: list[Verdict] = []
    for _when, path in reversed(found[-keep:]):
        dest = target / path.name
        if dest.exists():
            verdicts.append(Verdict(path.name, True, "already stored; not rewritten"))
            continue
        shutil.copy2(path, dest)
        verdicts.append(Verdict(path.name, True, "stored"))
    pruned = prune(target, keep=keep)
    note = f"pruned {pruned} record(s) past the {keep}-record limit" if pruned else ""
    return CarryoverReport("publish", season, tuple(verdicts), note)


def prune(folder: Path, *, keep: int = DEFAULT_KEEP) -> int:
    """Drop the oldest records past `keep`, by write time. Returns how many."""
    folder = Path(folder)
    if not folder.is_dir():
        return 0
    dated = [(archive_stamp(p), p) for p in folder.glob("week*.json")]
    dated = [(w, p) for w, p in dated if w is not None]
    dated.sort(key=lambda pair: (pair[0], pair[1].name))
    doomed = dated[:-keep] if keep > 0 else dated
    for _when, path in doomed:
        path.unlink(missing_ok=True)
    return len(doomed)


def describe(reports: Sequence[CarryoverReport]) -> tuple[str, ...]:
    """Lines for a run summary. Names no player: these are filenames,
    counts and reasons only (rule #10)."""
    out: list[str] = []
    for r in reports:
        out.append(r.summary())
        out.extend("  " + v.line() for v in r.rejected)
    return tuple(out)


def weeks_in(ledger: Path, season: int) -> tuple[int, ...]:
    """Which weeks the ledger now holds records for. Used by the workflow
    summary to show the carry chain without opening a record."""
    weeks: list[int] = []
    for _when, path in list_archives(Path(ledger), season):
        head = path.stem.split("_", 1)[0]
        try:
            weeks.append(int(head.removeprefix("week")))
        except ValueError:
            continue
    return tuple(sorted(set(weeks)))
