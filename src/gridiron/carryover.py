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

This module moves those records between the runner and a carry store, with
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
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from gridiron import ingest as ing
from gridiron.sleeper import PLAYER_MAP_LEDGER, valid_player_map_ledger
from gridiron.decisions import (ARCHIVE_RE, ARCHIVE_VERSION, archive_stamp,
                                list_archives, record_digest)

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

    @property
    def noun(self) -> str:
        return "file(s)" if self.action.endswith("-inputs") else "record(s)"

    def summary(self) -> str:
        bits = [f"{self.action}: {len(self.accepted)} {self.noun} accepted"]
        if self.rejected:
            bits.append(f"{len(self.rejected)} refused")
        if self.note:
            bits.append(self.note)
        return f"season {self.season} " + ", ".join(bits)


def _as_int(value: object) -> int | None:
    """An int, or None for anything that is not plainly one.

    Total by construction. `int(x)` is not: it raises on `"invalid"`, on
    `None` via the `or 0` idiom's blind spot, on a list, and on a float that
    is not finite. A validator that raises on malformed input is not a
    validator — it hands the malformed file the power to stop the run, which
    is precisely the outcome validation exists to prevent. Every field read
    out of a restored record goes through this.
    """
    if isinstance(value, bool):        # bool is an int; a flag is not a season
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _as_utc(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        stamp = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def _as_text(value: object) -> str | None:
    """A string, or None. Not `str(value)`: coercing a list to its repr would
    manufacture a plausible-looking field out of a malformed one."""
    return value if isinstance(value, str) else None


def _safe_basename(value: object) -> str | None:
    """A plain filename inside one directory, or None for anything else.

    A carried manifest is a file somebody else wrote, and `path` is the field
    that decides which bytes get read as this season's data. Three shapes have
    to be refused here, and only the first is obvious:

      wrong type   `["bad"]` is not a path. `Path()` raises TypeError on it,
                   which takes down the restore of every sound entry beside it.
      absolute     `dir / "/etc/passwd"` is `/etc/passwd` — pathlib DISCARDS
                   the left operand when the right one is absolute. An entry
                   carrying an absolute path therefore reads a file that was
                   never in the store and was never validated by anything.
      traversal    `../../x` walks out of the cache the same way, and taking
                   `.name` off it silently turns it into a different file
                   rather than refusing it.

    Basenaming a bad value is not a fix — it accepts the entry while changing
    what it means. So this returns the name only when the value ALREADY is
    one, and None otherwise, for the caller to reject and report.
    """
    text = _as_text(value)
    if text is None:
        return None
    if not text or text != text.strip():
        # NOT stripped. `" weekly_stats.parquet "` names a different file from
        # `"weekly_stats.parquet"`, and quietly trimming it is the same defect
        # as basenaming an absolute path: it accepts the entry while changing
        # which bytes it means.
        return None
    if text in {".", ".."}:
        return None
    if "/" in text or "\\" in text:          # POSIX and Windows separators
        return None
    if any(ord(c) < 32 for c in text):       # NUL and friends: open() raises
        return None
    if text != Path(text).name:              # drive letters, anything left
        return None
    return text


def _as_week_list(value: object) -> list[int] | None:
    """A list of week numbers, or None. `freshness.assess` does
    `int(w) for w in covered_weeks`, so a string here iterates its characters
    and a non-int member raises in the middle of a render."""
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return None
    out: list[int] = []
    for w in value:
        n = _as_int(w)
        if n is None:
            return None
        out.append(n)
    return sorted(set(out))


def _as_text_list(value: object) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)):
        return None
    out: list[str] = []
    for c in value:
        text = _as_text(c)
        if text is None:
            return None
        out.append(text)
    return out


def inspect_archive(path: Path, *, season: int, now: datetime,
                    max_age_days: int = RESTORE_MAX_AGE_DAYS) -> Verdict:
    """Decide whether one candidate file may be carried forward as evidence.

    Every rejection names what failed. Nothing is repaired: a record that
    does not describe itself correctly is not made to, it is left alone.
    """
    name = path.name
    named_name = ARCHIVE_RE.match(path.stem)
    named = archive_stamp(path)
    if named_name is None or named is None:
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

    want = _as_int(season)
    if want is None:
        # The caller asked for a season that is not one. Refusing here keeps a
        # bad argument from being answered with a confident accept.
        return Verdict(name, False, f"the season asked for ({season!r}) is not a "
                                    f"year, so nothing can be matched against it")
    got = _as_int(blob.get("season"))
    if got is None:
        return Verdict(name, False, f"season {blob.get('season')!r} inside the "
                                    f"record is not a year, so the record does not "
                                    f"say which season it belongs to")
    if got != want:
        return Verdict(name, False, f"season {got} is not season {want}")

    # The filename says which week this record is for, and the page's week is
    # inside it. They are written together and must agree: a record filed under
    # week 3 that holds week 9 would be picked as week 3's previous snapshot
    # and compared against the wrong page. The digest does not cover this —
    # it is computed over the contents alone, so a name can disagree with a
    # body that is itself perfectly intact.
    named_week = _as_int(named_name.group("week"))
    inner_week = _as_int(blob.get("week"))
    if inner_week is None:
        return Verdict(name, False, f"week {blob.get('week')!r} inside the record "
                                    f"is not a week, so the record does not say "
                                    f"which page it froze")
    if named_week != inner_week:
        return Verdict(name, False, f"the filename files this under week "
                                    f"{named_week} but the record inside is week "
                                    f"{inner_week}; a record filed under the wrong "
                                    f"week would be compared against the wrong page")

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
        try:
            verdict = inspect_archive(path, season=season, now=now,
                                      max_age_days=max_age_days)
        except Exception as exc:                       # noqa: BLE001
            # A belt to go with the braces above. `inspect_archive` is written
            # to be total, but it reads a file some other process wrote, and
            # one unhandled shape in it must never take down the restore of
            # every other record beside it. The file is rejected, named, and
            # left exactly where it is.
            verdicts.append(Verdict(path.name, False,
                                    f"rejected: validating it raised "
                                    f"{type(exc).__name__}, so the record could "
                                    f"not be established as sound"))
            continue
        if verdict.accepted:
            target.mkdir(parents=True, exist_ok=True)
            dest = target / path.name
            if dest.exists():
                verdicts.append(Verdict(path.name, True,
                                        "already in the ledger; left untouched"))
            else:
                try:
                    shutil.copy2(path, dest)
                except OSError as exc:
                    verdicts.append(Verdict(path.name, False,
                                            f"validated but could not be copied "
                                            f"into the ledger ({type(exc).__name__})"))
                    continue
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


# --------------------------------------------------------------------------
# Carrying the INPUTS, not just the conclusions
# --------------------------------------------------------------------------
# Records alone were not enough, and the gap was structural rather than a
# missing feature. The workflow marks both refresh steps `continue-on-error`
# so that a bad upstream day still produces a page. On a hosted runner that
# promise was empty: the cache the page renders FROM is gitignored and dies
# with the container, so "keep going without a refresh" meant keeping going
# with nothing at all. The render exited 2, no page was written, the artifact
# step failed, and the run summary said "Built." A restored decision record
# is a picture of a page that can no longer be rebuilt.
#
# So the last-good INPUTS travel too, under the same rule as the records: a
# restored file is untrusted input, validated before it is allowed to count,
# never repaired, never restamped. Three things make this safe to render from
# rather than dangerous:
#
#   as_of is never touched. Every entry keeps the time its pull SUCCEEDED, so
#   the page dates its evidence to when the evidence was actually gathered.
#   Nothing here can make old data look new, because nothing here writes a
#   time into `as_of` at all.
#
#   Every restored entry is marked as not refreshed. `Entry.error` is the
#   existing mechanism for "the latest attempt did not succeed" and it already
#   forces the source to STALE with `refresh_failed` set, which withholds
#   every action resting on it (`gridiron.gating`). A carried-forward page
#   therefore shows the last known comparison and recommends nothing.
#
#   A carried entry still within its refresh threshold is not a failed
#   refresh: `pull_week.py` judges it by the as-of of the pull that fetched
#   it, as it would a local cache, and clears the mark without touching the
#   as-of. Without that, the mark made every run re-download every input.
#
#   A successful refresh overrides it. `pull_week.py` loads the manifest from
#   disk and `Manifest.record` clears the error for whatever it pulled, so a
#   run where Sleeper works and nflverse does not carries exactly the entries
#   that failed and no more.

#: Where carried inputs live inside the private store.
INPUTS_DIR = "inputs"

#: Inputs are bounded harder than records. The season cache is dominated by
#: one ~16 MB player dump, and the store it lives in is a cache with a repo
#: wide size limit; a carry that grows without a ceiling evicts the records
#: it travels with. A cache larger than this is not carried and says so.
INPUT_MAX_BYTES = 64 * 1024 * 1024
INPUT_MAX_FILES = 24

#: Inputs go stale faster than records do. A frozen page from five weeks ago
#: is still a valid record of what was shown; a five-week-old roster is not
#: something to render a decision board from, even a withheld one.
INPUT_MAX_AGE_DAYS = 21

#: What `Entry.error` is set to on a restored entry. It is a sentence rather
#: than a flag because it is printed to the owner on the page, under the
#: source's own as-of line — and it is kept SHORT on purpose: freshness
#: truncates an error to 100 characters when it builds that line, so a longer
#: sentence would reach the owner cut off in the middle of a word.
CARRIED_FORWARD = "CARRIED FORWARD from an earlier run; this run did not refresh it"


#: The fields `ing.Entry` is built from. Read off the dataclass rather than
#: retyped, so a field added there cannot silently slip past this validator
#: as an unknown key — or, worse, be accepted unvalidated.
_ENTRY_FIELDS: frozenset[str] = frozenset(f.name for f in fields(ing.Entry))


def _validated_entry(name: object, raw: object) -> tuple[ing.Entry | None, str]:
    """Build one `ing.Entry` from a carried manifest, or say why it cannot be.

    `ing.Entry(**raw)` looks like validation and is not. A dataclass checks
    which KEYS it was handed, never what they hold, so `{"path": ["bad"]}`
    constructs a perfectly well-formed Entry whose `path` is a list — and the
    failure surfaces later, either as a `TypeError` out of `Path()` that takes
    the whole restore down, or (worse) as a crash inside `Manifest.freshness`
    half way through a render, long after carryover reported success.

    So every field is read through a total reader BEFORE construction, and a
    field that does not read is a rejection of this entry alone. The entry is
    never repaired: a value that is not what it claims to be is not quietly
    replaced with a default, because a default is an assertion about the data
    that nobody made.
    """
    key = _as_text(name)
    if not key or not key.strip():
        return None, "the carried manifest names this source with something that "\
                     "is not a name"
    if not isinstance(raw, dict):
        return None, f"the carried entry is {type(raw).__name__}, not an object"

    unknown = sorted(str(k) for k in raw if str(k) not in _ENTRY_FIELDS)
    if unknown:
        return None, (f"the carried entry carries field(s) this code's manifest "
                      f"layout does not have ({', '.join(unknown)}), so it was "
                      f"written by something that does not agree with this code "
                      f"about what an entry is")

    # `path` decides which bytes get read as this season's data (see
    # `_safe_basename`), so it is the strictest field here.
    path = _safe_basename(raw.get("path"))
    if path is None:
        return None, (f"path {raw.get('path')!r} is not a plain filename inside "
                      f"the carried cache; a carried entry may only ever name a "
                      f"file that travelled with it")

    rows = _as_int(raw.get("rows"))
    if rows is None or rows < 0:
        return None, (f"rows {raw.get('rows')!r} is not a row count, so the "
                      f"source cannot be judged empty or not")

    # An input that cannot be dated must not be rendered from. The whole
    # promise of the carry is that the page states when its evidence was
    # gathered; an entry with no readable `as_of` cannot keep it, and
    # `Entry.as_of_dt` raises on an unparseable one mid-render.
    as_of = _as_text(raw.get("as_of"))
    if as_of is None or _as_utc(as_of) is None:
        return None, (f"as_of {raw.get('as_of')!r} is not a time, so this input "
                      f"cannot be dated and must not be rendered from")

    source = _as_text(raw.get("source"))
    if source is None:
        return None, f"source {raw.get('source')!r} is not a source name"

    weeks = _as_week_list(raw.get("weeks"))
    if weeks is None:
        return None, (f"weeks {raw.get('weeks')!r} is not a list of week "
                      f"numbers, so the source's coverage cannot be read")

    missing = _as_text_list(raw.get("missing_columns"))
    if missing is None:
        return None, (f"missing_columns {raw.get('missing_columns')!r} is not a "
                      f"list of column names")

    error = _as_text(raw.get("error", ""))
    last_attempt = _as_text(raw.get("last_attempt", ""))
    if error is None or last_attempt is None:
        return None, "error and last_attempt must be text or absent"

    # `as_of` is passed through EXACTLY as written. It is the one field the
    # carry must not restate, and re-serialising a parsed copy of it would be
    # restating it.
    return ing.Entry(name=key, path=path, rows=rows, as_of=as_of, source=source,
                     weeks=weeks, error=error, last_attempt=last_attempt,
                     missing_columns=missing), ""


def _dest_key(name: str) -> str:
    """How two carried filenames are told apart when deciding a collision.

    Case-folded on EVERY platform, not just the case-insensitive ones. The
    store is written by one machine and read by another — a cache published
    from Windows and restored on a Linux runner is the normal path here — so
    the safe rule is the strictest of the platforms involved, not the one
    doing the reading. Refusing `Shared.json` beside `shared.json` on Linux
    costs one rejected entry and says why; accepting it on Windows silently
    overwrites a file this run pulled.
    """
    return os.path.normcase(name).casefold()


def _load_local(cache_dir: Path, season: int) -> ing.Manifest | None:
    """This machine's own manifest, or None if it cannot be read.

    Nothing here invents an empty manifest to carry on with. The local
    manifest is how a carry knows what this run already pulled, and guessing
    that the answer is "nothing" is exactly how a carried file would come to
    displace a real one.
    """
    try:
        return ing.Manifest.load(Path(cache_dir), season)
    except Exception:                                  # noqa: BLE001
        return None


def _cache_files(manifest: ing.Manifest) -> dict[str, Path]:
    """The files a manifest actually points at, by entry name. An entry whose
    file is missing — or whose `path` is not a plain filename — is left out:
    the manifest is a claim about the disk and the disk is the arbiter."""
    out: dict[str, Path] = {}
    for name, entry in manifest.entries.items():
        safe = _safe_basename(getattr(entry, "path", None))
        if safe is None:
            continue
        candidate = manifest.directory / safe
        if candidate.is_file():
            out[name] = candidate
    return out


def publish_inputs(cache_dir: Path, store: Path, *, season: int,
                   now: datetime) -> CarryoverReport:
    """Copy this run's season cache into the private store.

    Only a cache that a refresh actually filled is worth storing, so an entry
    whose latest attempt FAILED is published with its file (the data is still
    the last good copy) while an entry with no file at all is skipped and
    named. Nothing is rewritten or restamped: `manifest.json` goes across as
    it stands, which is what keeps every `as_of` honest on the far side.
    """
    cache_dir = Path(cache_dir)
    manifest = _load_local(cache_dir, season)
    if manifest is None:
        return CarryoverReport("publish-inputs", season, (),
                               note="nothing to publish: this run's ingest "
                                    "manifest could not be read, and a cache "
                                    "whose index does not parse is not a "
                                    "last-good anything")
    if not manifest.entries or not manifest.path.is_file():
        return CarryoverReport("publish-inputs", season, (),
                               note="nothing to publish: this run has no ingest "
                                    "manifest, so there are no last-good inputs "
                                    "to carry")
    if _as_int(manifest.season) != _as_int(season):
        return CarryoverReport(
            "publish-inputs", season,
            (Verdict(ing.MANIFEST_NAME, False,
                     f"the cache is season {manifest.season!r}, not {season}; "
                     f"another season's inputs are not this season's history"),))

    files = _cache_files(manifest)
    total = manifest.path.stat().st_size + sum(p.stat().st_size for p in files.values())
    if len(files) > INPUT_MAX_FILES:
        return CarryoverReport("publish-inputs", season, (),
                               note=f"not carried: the cache holds {len(files)} "
                                    f"files, past the {INPUT_MAX_FILES}-file limit")
    if total > INPUT_MAX_BYTES:
        return CarryoverReport("publish-inputs", season, (),
                               note=f"not carried: the cache is "
                                    f"{total // (1024 * 1024)} MB, past the "
                                    f"{INPUT_MAX_BYTES // (1024 * 1024)} MB carry "
                                    f"limit; the store it travels in is a size-"
                                    f"capped cache and an oversized carry would "
                                    f"evict the decision records beside it")

    target = Path(store) / INPUTS_DIR / f"season{season}"
    target.mkdir(parents=True, exist_ok=True)
    verdicts: list[Verdict] = []
    for name, path in sorted(files.items()):
        try:
            shutil.copy2(path, target / path.name)
        except OSError as exc:
            verdicts.append(Verdict(path.name, False,
                                    f"could not be stored ({type(exc).__name__})"))
            continue
        verdicts.append(Verdict(path.name, True, f"stored as the last-good {name}"))
    for name, entry in sorted(manifest.entries.items()):
        if name in files:
            continue
        safe = _safe_basename(getattr(entry, "path", None))
        if safe is None:
            if getattr(entry, "path", None):
                verdicts.append(Verdict(str(name), False,
                                        f"the manifest points {name} at "
                                        f"{entry.path!r}, which is not a plain "
                                        f"filename in the cache; it is not carried"))
            continue
        verdicts.append(Verdict(safe, False,
                                f"the manifest lists {name} but no such file "
                                f"is on disk, so there is nothing to carry"))
    # The player-map request ledger travels with the inputs: without it every
    # fresh runner would see no request on record, and Sleeper's once-a-day
    # budget would reset with each run (gridiron.sleeper.player_map_budget).
    # It is not a manifest entry because it is a count of requests, not data.
    ledger = cache_dir / PLAYER_MAP_LEDGER
    carried_ledger = False
    if ledger.is_file():
        try:
            shutil.copy2(ledger, target / PLAYER_MAP_LEDGER)
            carried_ledger = True
            verdicts.append(Verdict(PLAYER_MAP_LEDGER, True,
                                    "stored; player-map request times unchanged"))
        except OSError as exc:
            verdicts.append(Verdict(PLAYER_MAP_LEDGER, False,
                                    f"could not be stored ({type(exc).__name__})"))
    # The manifest goes LAST. Until it lands, the stored directory has no
    # index and a concurrent restore reads nothing rather than half a cache.
    shutil.copy2(manifest.path, target / manifest.path.name)
    verdicts.append(Verdict(manifest.path.name, True,
                            "stored; every as_of inside it is unchanged"))
    # Anything in the store the manifest no longer points at is a leftover
    # from a previous shape of the cache. Dropping it keeps the carry bounded.
    keepers = {p.name for p in files.values()} | {manifest.path.name}
    if carried_ledger:
        keepers.add(PLAYER_MAP_LEDGER)
    dropped = 0
    for stale in target.iterdir():
        if stale.is_file() and stale.name not in keepers:
            stale.unlink(missing_ok=True)
            dropped += 1
    note = f"dropped {dropped} file(s) the manifest no longer lists" if dropped else ""
    return CarryoverReport("publish-inputs", season, tuple(verdicts), note)


def inspect_inputs(store: Path, *, season: int, now: datetime,
                   max_age_days: int = INPUT_MAX_AGE_DAYS) -> tuple[Verdict, dict]:
    """Validate a carried cache without copying anything.

    Returns the verdict and, when accepted, the manifest blob that passed. The
    checks mirror `inspect_archive` in posture: totality first (nothing here
    may raise on a malformed field), then season, then time, then the claim
    the index makes about the disk beside it.
    """
    source = Path(store) / INPUTS_DIR / f"season{season}"
    index = source / ing.MANIFEST_NAME
    if not index.is_file():
        return Verdict(f"{INPUTS_DIR}/season{season}", False,
                       "no carried inputs in the store"), {}
    try:
        blob = json.loads(index.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return Verdict(index.name, False,
                       f"the carried manifest is unreadable ({type(exc).__name__})"), {}
    if not isinstance(blob, dict):
        return Verdict(index.name, False,
                       "the carried manifest is not an object"), {}
    want = _as_int(season)
    got = _as_int(blob.get("season"))
    if want is None or got is None or got != want:
        return Verdict(index.name, False,
                       f"the carried inputs are season {blob.get('season')!r}, "
                       f"not season {season!r}"), {}
    entries = blob.get("entries")
    if not isinstance(entries, dict) or not entries:
        return Verdict(index.name, False,
                       "the carried manifest lists no sources"), {}

    cutoff = now.astimezone(timezone.utc)
    newest: datetime | None = None
    for name, raw in entries.items():
        if not isinstance(raw, dict):
            # Not a manifest-wide failure. This entry is malformed and
            # `restore_inputs` rejects it by name; a sound source beside it
            # is still a sound source and still deserves to be carried.
            continue
        stamp = _as_utc(raw.get("as_of"))
        if stamp is None:
            continue
        if stamp > cutoff + STAMP_TOLERANCE:
            return Verdict(index.name, False,
                           f"{name} is stamped {stamp:%Y-%m-%d %H:%M}Z, which is in "
                           f"the future; carried inputs may never present "
                           f"themselves as newer than the run reading them"), {}
        newest = stamp if newest is None else max(newest, stamp)
    if newest is None:
        return Verdict(index.name, False,
                       "no entry in the carried manifest carries a readable "
                       "as_of, so the inputs cannot be dated"), {}
    if newest < cutoff - timedelta(days=max_age_days):
        return Verdict(index.name, False,
                       f"the freshest carried input was pulled "
                       f"{(cutoff - newest).days} days ago, past the "
                       f"{max_age_days}-day limit for rendering from last-good "
                       f"inputs"), {}
    return Verdict(index.name, True,
                   f"carried inputs verified, freshest pulled "
                   f"{newest:%Y-%m-%d %H:%M}Z"), blob


def _restore_ledger(source: Path, cache_dir: Path, now: datetime) -> list[Verdict]:
    """Lay the carried player-map request ledger down verbatim, and say whether
    it is trustworthy.

    Never over a local one (this machine's own count wins). A ledger that
    fails validation (unreadable, malformed, stamped in the future) is laid
    down all the same but reported REFUSED: the budget reads it as RECOVERY
    NEEDED and never requests from it, while the request times and gap mark
    that still parse keep blocking a bootstrap
    (gridiron.sleeper.read_player_map_history). Dropping it here would erase
    that evidence and let a bootstrap request beside a known recent one."""
    src = Path(source) / PLAYER_MAP_LEDGER
    dest = Path(cache_dir) / PLAYER_MAP_LEDGER
    if not src.is_file():
        return []
    if dest.exists():
        return [Verdict(PLAYER_MAP_LEDGER, False,
                        "this run already has its own request ledger")]
    refused = ""
    lines: list[dict] = []
    try:
        blob = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        refused = f"the carried request ledger is unreadable ({type(exc).__name__})"
    else:
        lines, why = valid_player_map_ledger(blob)
        cutoff = now.astimezone(timezone.utc) + STAMP_TOLERANCE
        if why:
            refused = f"the carried request ledger is {why}"
        elif any(datetime.fromisoformat(x["at"]) > cutoff for x in lines):
            refused = ("the carried request ledger has a request stamped in the "
                       "future; not trusted, so it cannot block the player map")
    try:
        shutil.copy2(src, dest)
    except OSError as exc:
        return [Verdict(PLAYER_MAP_LEDGER, False,
                        f"could not be laid down ({type(exc).__name__})")]
    if refused:
        return [Verdict(PLAYER_MAP_LEDGER, False,
                        f"{refused}; laid down only as bootstrap evidence — the "
                        f"budget reads it as RECOVERY NEEDED")]
    return [Verdict(PLAYER_MAP_LEDGER, True,
                    f"laid down; {len(lines)} player-map request(s) on record, "
                    f"times unchanged")]


def restore_inputs(store: Path, cache_dir: Path, *, season: int, now: datetime,
                   max_age_days: int = INPUT_MAX_AGE_DAYS) -> CarryoverReport:
    """Lay last-good inputs into an empty cache so a failed refresh can render.

    Two rules decide what this may touch:

      * It never overwrites a file the local cache already has. The refresh
        steps run AFTER this one, but a re-run, a warm runner or a desktop
        invocation can all put a real cache here first, and a carried file
        must never displace one this machine pulled itself.
      * Every entry it does lay down is marked as not refreshed by this run,
        with its original `as_of` intact. That combination is the whole point:
        the page can say what was true and when, and cannot say what to do.
    """
    verdict, blob = inspect_inputs(store, season=season, now=now,
                                   max_age_days=max_age_days)
    if not verdict.accepted:
        return CarryoverReport("restore-inputs", season, (verdict,),
                               note="no last-good inputs were laid down; if this "
                                    "run's refresh also fails there is nothing to "
                                    "render and the run will say so")
    source = Path(store) / INPUTS_DIR / f"season{season}"
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    local = _load_local(cache_dir, season)
    if local is None:
        return CarryoverReport(
            "restore-inputs", season,
            (Verdict(ing.MANIFEST_NAME, False,
                     "this run's own cache manifest could not be read, so there "
                     "is no way to tell which sources this run already pulled; "
                     "nothing was laid down rather than risk displacing one"),))
    local_files = _cache_files(local)

    # Which FILENAMES the cache already holds, and who owns each. The entry
    # NAME is not enough to decide a collision: a carried `weekly_stats`
    # pointing at `shared.json` and a local `sleeper_state` pointing at the
    # same `shared.json` share no name at all, and overwriting the file leaves
    # the LOCAL entry — as_of current, `error` empty, so the gate does not
    # withhold on it — vouching for bytes it never described.
    occupied: dict[str, str] = {}
    owner: dict[str, str] = {}
    if cache_dir.is_dir():
        for present in cache_dir.iterdir():
            if present.is_file():
                occupied[_dest_key(present.name)] = present.name
    for local_name, local_path in local_files.items():
        owner[_dest_key(local_path.name)] = local_name

    verdicts: list[Verdict] = []
    carried: dict[str, ing.Entry] = {}
    raw_entries = blob.get("entries")
    raw_entries = raw_entries if isinstance(raw_entries, dict) else {}
    for name, raw in sorted(raw_entries.items(), key=lambda kv: str(kv[0])):
        entry, why = _validated_entry(name, raw)
        if entry is None:
            verdicts.append(Verdict(str(name), False, why))
            continue
        if entry.name in local_files:
            verdicts.append(Verdict(entry.name, False,
                                    "this run already has its own copy; the "
                                    "carried one is not used"))
            continue
        key = _dest_key(entry.path)
        dest = cache_dir / entry.path
        # Decided by DESTINATION, not by name, and refused rather than merged:
        # there is no way to tell from here whether two sources that name the
        # same file mean the same bytes, and a carry may never be the thing
        # that finds out.
        if key in occupied or dest.exists():
            held = occupied.get(key, entry.path)
            by = owner.get(key)
            verdicts.append(Verdict(
                entry.path, False,
                f"the carried {entry.name} would be written to {held}, which "
                f"this run already holds"
                + (f" as its own {by}" if by else "")
                + "; a carried file never displaces one that is already here"))
            continue
        src = source / entry.path
        if not src.is_file():
            verdicts.append(Verdict(entry.path, False,
                                    f"the carried manifest lists {entry.name} but "
                                    f"the file is not in the store"))
            continue
        try:
            shutil.copy2(src, cache_dir / src.name)
        except OSError as exc:
            verdicts.append(Verdict(src.name, False,
                                    f"could not be laid down ({type(exc).__name__})"))
            continue
        # `as_of` is copied through untouched. `error` and `last_attempt`
        # describe THIS run, which did not refresh anything.
        # Claimed, so a second carried entry naming the same file is refused
        # by the same rule rather than quietly overwriting the first.
        occupied[key] = entry.path
        owner[key] = entry.name
        carried[entry.name] = replace(entry, error=CARRIED_FORWARD,
                                      last_attempt=now.astimezone(timezone.utc)
                                      .isoformat(timespec="seconds"))
        verdicts.append(Verdict(src.name, True,
                                f"laid down as last-good {entry.name}, pulled "
                                f"{entry.as_of}, marked NOT REFRESHED by this run"))
    verdicts.extend(_restore_ledger(source, cache_dir, now))
    if not carried:
        return CarryoverReport("restore-inputs", season, tuple(verdicts),
                               note="nothing was laid down")
    merged = dict(local.entries)
    merged.update(carried)
    ing.Manifest(cache_dir, merged, season).save()
    return CarryoverReport(
        "restore-inputs", season, tuple(verdicts),
        note=f"{len(carried)} source(s) laid down from the last good run and "
             f"marked NOT REFRESHED; the page will date them to when they were "
             f"pulled and withhold every action resting on them")
