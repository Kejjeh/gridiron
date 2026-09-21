"""Decision-time archive, and grading that cannot see the future.

Rule #7: log every decision WITH the rejected side, and grade the choice
given what was known at the time. The only way a later grader can be
prevented from leaking the outcome into the assessment is to freeze the
inputs at decision time: the archive written here carries the projections,
the freshness of every source they were built from, the lineup as Sleeper
showed it, the best legal lineup the optimizer found, every alternative
with both sides, and the waiver pairs — all as numbers computed THEN.

`grade_archive` reads only the archive and a table of actual points. It
recomputes nothing. A projection that would have looked different with
next week's usage cannot be "improved" after the fact because the archive
holds the number that was on the page, and the grader is not allowed to
project.

The ungradeable terminal state (plv_clone #54) is defined up front: an
alternative whose rejected side has no actual — the player was on a bye,
was cut, did not record a stat line — is `ungradeable`, not zero and not
skipped silently. Its count is part of the grade.

Ids. The archive carries each player's sleeper id and gsis id as resolved
by `gridiron.ids.Crosswalk` at decision time; the grader joins actuals on
the archived gsis id and never resolves anything itself (rule #3).

Storage. Archives are roster-bearing (they name the owner's players and
lineup), so they live under `data/ledger/decisions/` which is GITIGNORED,
like the rendered weekly report and for the same reason (rule #10 carve-out,
2026-09-17). They are local records, regenerable from a cache the owner
also holds.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gridiron.paths import LEDGER

ARCHIVE_DIR = LEDGER / "decisions"
ARCHIVE_VERSION = 1

#: `week03_20260926T120000Z_1f4c9a2b.json`. The trailing digest is what makes
#: the record immutable in practice: two different pages built in the same
#: second land on different paths instead of one silently replacing the other.
#: The group is optional so archives written before the digest still parse.
ARCHIVE_RE = re.compile(
    r"^week(?P<week>\d{1,2})_(?P<stamp>\d{8}T\d{6}Z)(?:_(?P<digest>[0-9a-f]{8}))?$")
_STAMP_FMT = "%Y%m%dT%H%M%SZ"


class ArchiveCollision(RuntimeError):
    """Raised when a write would replace an existing record with different
    content. A decision-time archive is evidence; evidence is not edited."""


def record_digest(record: Mapping[str, object]) -> str:
    blob = json.dumps(record, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:8]


def archive_path(season: int, week: int, now: datetime,
                 root: Path | None = None,
                 record: Mapping[str, object] | None = None) -> Path:
    """Where this record belongs.

    Pass `record` and the filename carries an 8-hex content digest, so two
    DIFFERENT pages built at the same timestamp get two different files and
    the same page written twice gets one. Without it the path is the bare
    timestamp, which two writes in the same second share — that is how one
    archived choice came to overwrite another.
    """
    stamp = now.astimezone(timezone.utc).strftime(_STAMP_FMT)
    tail = f"_{record_digest(record)}" if record is not None else ""
    return (root or ARCHIVE_DIR) / f"season{season}" / f"week{week:02d}_{stamp}{tail}.json"


def write_archive(record: Mapping[str, object], path: Path,
                  *, overwrite: bool = False) -> Path:
    """Atomic, immutable write.

    Atomic (temp + replace) so a half-written archive never exists, and
    immutable so a second write cannot quietly redefine what the page said:
    re-writing byte-identical content is a no-op, and writing DIFFERENT
    content to an existing path raises rather than replacing it. Grading
    later depends on the record being the one that was on the screen.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"archive_version": ARCHIVE_VERSION, **record},
                      indent=1, default=str)
    if path.exists() and not overwrite:
        existing = path.read_text(encoding="utf-8")
        if existing == blob:
            return path                     # idempotent: same page, same file
        raise ArchiveCollision(
            f"{path.name} already holds a DIFFERENT decision record. An "
            f"archive is decision-time evidence and is never rewritten; pass "
            f"`record=` to archive_path() so each distinct page gets its own "
            f"filename.")
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(blob, encoding="utf-8")
    tmp.replace(path)
    return path


def read_archive(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def archive_stamp(path: Path) -> datetime | None:
    """When this archive was written, from its own filename.

    The filename stamp is the ordering key. Sorting the NAMES instead puts
    `week05_<september>` after `week04_<october>`, because the week prefix
    dominates the comparison — so "the newest archive before now" used to
    return whichever file had the highest week number.
    """
    m = ARCHIVE_RE.match(Path(path).stem)
    if not m:
        return None
    try:
        return datetime.strptime(m.group("stamp"), _STAMP_FMT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def list_archives(root: Path | None, season: int) -> tuple[tuple[datetime, Path], ...]:
    """Every readable archive for a season, oldest write FIRST.

    Files whose names do not parse are skipped rather than guessed at: an
    archive that cannot be placed in time cannot be used as "the previous
    one", and silently ordering it by name is how this went wrong before.
    """
    folder = (root or ARCHIVE_DIR) / f"season{season}"
    if not folder.is_dir():
        return ()
    found: list[tuple[datetime, Path]] = []
    for p in folder.glob("week*.json"):
        when = archive_stamp(p)
        if when is not None:
            found.append((when, p))
    return tuple(sorted(found, key=lambda pair: (pair[0], pair[1].name)))


def previous_archive(root: Path | None, season: int, before: datetime) -> Path | None:
    """The archive for this season written latest in TIME strictly before
    `before`.

    Used only to diff two decision-time records against each other. It never
    feeds a projection: archives are frozen pages, and reading one to build a
    newer one would put yesterday's numbers inside today's evidence.
    """
    cutoff = before.astimezone(timezone.utc)
    older = [p for when, p in list_archives(root, season) if when < cutoff]
    return older[-1] if older else None


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------
# An archive records what the page SHOWED. It does not record what the owner
# DID, because nothing in this system watches the owner: no lineup is
# submitted here, no transaction is made here, and the snapshot that produced
# the page was taken before any of it. Grading therefore starts from a
# distinction the old grader collapsed:
#
#   * A COMPARISON is hypothetical. "The roster held Higbee over Goedert" is
#     a fact about the roster, not evidence of a deliberate keep — the owner
#     may never have opened the page, and on a withheld page the advice was
#     explicitly not given. Comparisons are graded, and labelled hypothetical.
#   * A DECISION requires an OBSERVATION supplied from outside: someone has
#     to state that the owner acted or declined. Without one, `decisions` is
#     empty and no line of output says the owner followed anything.
#
# The stance the page took is part of the record too: an action whose inputs
# were stale was WITHHELD, meaning the page showed the comparison and
# explicitly refused to advise on it. Scoring those together with endorsed
# advice measures a recommendation that was never made.
#: A comparison graded from the archive alone, with no claim about conduct.
HYPOTHETICAL = "hypothetical"
#: A comparison an outside observation says the owner acted on or declined.
OBSERVED = "observed"

#: What the page did with this class of action at decision time.
ENDORSED, WITHHELD, UNKNOWN_STANCE = "ENDORSED", "WITHHELD", "UNKNOWN"

#: The two observations a caller may supply per comparison.
ACTED, DECLINED = "acted", "declined"

#: Which gate governed which comparison kind.
_GATE_FOR = {"start_sit": "lineup", "waiver": "waiver"}


@dataclass(frozen=True)
class GradedComparison:
    """One two-sided comparison as the archive recorded it, scored.

    `held` is the side the roster carried at decision time. It is deliberately
    not called "chosen": the roster holding a player is not evidence that the
    owner considered and kept him.
    """

    kind: str                       # "start_sit" | "waiver"
    slot: str
    held_id: str
    alternative_id: str
    held_projected: float | None
    alternative_projected: float | None
    held_actual: float | None
    alternative_actual: float | None
    projected_edge: float | None    # held - alternative, at decision time
    realized_edge: float | None     # held - alternative, actual
    status: str                     # "graded" | "ungradeable"
    stance: str = UNKNOWN_STANCE    # ENDORSED | WITHHELD | UNKNOWN
    basis: str = HYPOTHETICAL       # HYPOTHETICAL | OBSERVED
    owner_action: str = ""          # "" | ACTED | DECLINED
    eligibility: str = ""           # "UNVERIFIED" for every waiver pair
    reason: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.slot}:{self.held_id}:{self.alternative_id}"

    @property
    def is_decision(self) -> bool:
        """True only when an observation said what the owner actually did."""
        return self.basis == OBSERVED and self.owner_action in (ACTED, DECLINED)

    @property
    def scorable(self) -> bool:
        """Counts toward the headline agreement rate: graded, endorsed by the
        page, and not resting on an eligibility the page never established."""
        return (self.status == "graded" and self.stance == ENDORSED
                and not self.eligibility)

    def label(self) -> str:
        bits = [self.basis]
        if self.stance != ENDORSED:
            bits.append(f"page stance {self.stance}")
        if self.eligibility:
            bits.append(f"eligibility {self.eligibility}")
        if self.owner_action:
            bits.append(f"owner {self.owner_action}")
        return ", ".join(bits)


@dataclass(frozen=True)
class Grade:
    week: int
    comparisons: tuple[GradedComparison, ...]
    projection_mae: float | None
    projection_n: int
    ungradeable: int
    notes: tuple[str, ...] = field(default=())

    @property
    def n(self) -> int:
        return len(self.comparisons)

    @property
    def decisions(self) -> tuple[GradedComparison, ...]:
        """Comparisons an observation confirmed. Empty unless one was given —
        this system never watches what the owner does, so it never assumes."""
        return tuple(c for c in self.comparisons if c.is_decision)

    @property
    def scorable(self) -> tuple[GradedComparison, ...]:
        return tuple(c for c in self.comparisons if c.scorable)

    @property
    def withheld(self) -> tuple[GradedComparison, ...]:
        return tuple(c for c in self.comparisons if c.stance == WITHHELD)

    @property
    def unverified(self) -> tuple[GradedComparison, ...]:
        return tuple(c for c in self.comparisons if c.eligibility)

    def agreement(self) -> tuple[int, int]:
        """How often the projected edge pointed the way the actual edge did,
        over the SCORABLE comparisons only."""
        ok = self.scorable
        agree = sum(1 for g in ok
                    if g.projected_edge is not None and g.realized_edge is not None
                    and (g.projected_edge >= 0) == (g.realized_edge >= 0))
        return agree, len(ok)

    def summary(self) -> str:
        graded = [c for c in self.comparisons if c.status == "graded"]
        agree, n = self.agreement()
        mae = (f"projection MAE {self.projection_mae:.2f} over {self.projection_n} "
               f"roster player(s)" if self.projection_mae is not None
               else "no projection graded")
        bits = [f"week {self.week}: {len(graded)} comparison(s) graded, "
                f"{self.ungradeable} ungradeable"]
        if n:
            bits.append(f"projected edge pointed the right way in {agree}/{n} "
                        f"comparison(s) the page ENDORSED")
        else:
            bits.append("no comparison was both endorsed and gradeable, so no "
                        "agreement rate is reported")
        if self.withheld:
            bits.append(f"{len(self.withheld)} shown but WITHHELD (advice was "
                        f"not given)")
        if self.unverified:
            bits.append(f"{len(self.unverified)} rest on an add this page never "
                        f"established was available")
        bits.append(f"{len(self.decisions)} confirmed owner decision(s)")
        bits.append(mae)
        return "; ".join(bits)


def _actual(actuals: Mapping[str, float], gsis: str) -> float | None:
    if not gsis:
        return None
    v = actuals.get(gsis)
    return None if v is None else float(v)


def _stance_for(archive: Mapping[str, object], kind: str) -> str:
    """What the page did with this class of action, read from the archived
    gate. An archive written before the gate existed says UNKNOWN rather than
    being assumed to have endorsed anything."""
    gate = archive.get("gate")
    if not isinstance(gate, Mapping):
        return UNKNOWN_STANCE
    entry = gate.get(_GATE_FOR.get(kind, kind))
    if not isinstance(entry, Mapping) or "allowed" not in entry:
        return UNKNOWN_STANCE
    return ENDORSED if entry.get("allowed") else WITHHELD


def grade_archive(archive: Mapping[str, object],
                  actuals: Mapping[str, float],
                  *, observed: Mapping[str, str] | None = None) -> Grade:
    """Grade a decision archive against actual league points by gsis id.

    `actuals` must be the ACTUAL points of the archive's week only. The
    grader never projects and never reads the archive's inputs for anything
    but the numbers that were on the page.

    `observed` is the ONLY way a comparison becomes a decision. Map a
    comparison's `key` to `ACTED` or `DECLINED` when — and only when —
    something outside this program established what the owner did. Anything
    absent stays hypothetical, and the summary says so. Nothing here infers
    conduct from the roster: a player still being on it proves that nobody
    removed him, not that anyone chose to keep him.
    """
    week = int(archive.get("week") or 0)
    seen: dict[str, dict] = {str(p["sleeper_id"]): p
                             for p in (archive.get("roster") or [])}
    watched = dict(observed or {})
    graded: list[GradedComparison] = []
    ungradeable = 0

    def one(kind: str, slot: str, held: Mapping | None, alt: Mapping | None,
            eligibility: str = "") -> None:
        nonlocal ungradeable
        if held is None or alt is None:
            return
        h_act = _actual(actuals, str(held.get("gsis_id") or ""))
        a_act = _actual(actuals, str(alt.get("gsis_id") or ""))
        h_proj = held.get("projected")
        a_proj = alt.get("projected")
        p_edge = (None if h_proj is None or a_proj is None
                  else round(float(h_proj) - float(a_proj), 3))
        held_id, alt_id = str(held.get("sleeper_id")), str(alt.get("sleeper_id"))
        stance = _stance_for(archive, kind)
        action = watched.get(f"{kind}:{slot}:{held_id}:{alt_id}", "")
        basis = OBSERVED if action in (ACTED, DECLINED) else HYPOTHETICAL
        if h_act is None or a_act is None:
            ungradeable += 1
            missing = [n for n, a in (("held", h_act), ("alternative", a_act))
                       if a is None]
            graded.append(GradedComparison(
                kind, slot, held_id, alt_id, h_proj, a_proj, h_act, a_act,
                p_edge, None, "ungradeable", stance, basis, action, eligibility,
                f"no actual for the {' and '.join(missing)} side "
                f"(bye, cut, or no stat line)"))
            return
        graded.append(GradedComparison(
            kind, slot, held_id, alt_id, h_proj, a_proj, h_act, a_act,
            p_edge, round(h_act - a_act, 3), "graded", stance, basis, action,
            eligibility))

    for alt in archive.get("alternatives") or []:
        one("start_sit", str(alt.get("slot") or ""),
            seen.get(str(alt.get("starter_id") or "")),
            seen.get(str(alt.get("bench_id") or "")))
    for up in archive.get("upgrades") or []:
        # The roster held the drop and did not have the add. Whether the add
        # was even claimable is not something the page established, so every
        # waiver pair carries UNVERIFIED and stays out of the headline rate.
        one("waiver", str(up.get("slot") or ""),
            seen.get(str(up.get("drop_id") or "")), up.get("add"),
            eligibility="UNVERIFIED")

    errs = []
    for p in seen.values():
        proj, gsis = p.get("projected"), str(p.get("gsis_id") or "")
        act = _actual(actuals, gsis)
        if proj is not None and act is not None and not p.get("withheld"):
            errs.append(abs(float(proj) - act))
    mae = round(sum(errs) / len(errs), 3) if errs else None

    notes = ["graded from archived numbers only; nothing re-projected",
             "every comparison is HYPOTHETICAL unless an observation was "
             "supplied: this program never sees what the owner actually did"]
    withheld_n = sum(1 for c in graded if c.stance == WITHHELD)
    if withheld_n:
        notes.append(f"{withheld_n} comparison(s) were WITHHELD on the page — "
                     f"shown as last-known information with the advice "
                     f"explicitly not given — and are excluded from the "
                     f"agreement rate")
    if any(c.stance == UNKNOWN_STANCE for c in graded):
        notes.append("this archive carries no gate record, so what the page "
                     "endorsed cannot be established from it")
    return Grade(week, tuple(graded), mae, len(errs), ungradeable, tuple(notes))


# --------------------------------------------------------------------------
# What changed since the last time this page was built
# --------------------------------------------------------------------------
#: A projection move smaller than this is noise from one more box score, not
#: news. Rule #8: alerts fire on TRANSITIONS, and a transition has to be big
#: enough that a human would act differently.
MOVE_POINTS = 1.5


@dataclass(frozen=True)
class Change:
    kind: str            # roster | projection | availability | lock | lineup | freshness
    subject: str         # display name, or the source name
    detail: str

    def line(self) -> str:
        return f"[{self.kind}] {self.subject}: {self.detail}"


@dataclass(frozen=True)
class Changes:
    previous: str                  # the previous archive's generated stamp
    previous_week: int | None
    items: tuple[Change, ...] = field(default=())
    note: str = ""

    @property
    def any(self) -> bool:
        return bool(self.items)

    def of(self, kind: str) -> tuple[Change, ...]:
        return tuple(c for c in self.items if c.kind == kind)


def _roster_index(blob: Mapping[str, object]) -> dict[str, dict]:
    return {str(p.get("sleeper_id")): p
            for p in (blob.get("roster") or []) if isinstance(p, dict) and p}


def diff_archives(previous: Mapping[str, object],
                  current: Mapping[str, object]) -> Changes:
    """Transitions between two frozen pages — rule #8, alerts on CHANGES.

    Reads both archives as data and computes nothing else. Anything that
    needed a projection was projected when its page was built.
    """
    was, now = _roster_index(previous), _roster_index(current)
    items: list[Change] = []

    for sid in sorted(now.keys() - was.keys()):
        items.append(Change("roster", str(now[sid].get("name") or sid),
                            "ADDED to the roster since the last snapshot"))
    for sid in sorted(was.keys() - now.keys()):
        items.append(Change("roster", str(was[sid].get("name") or sid),
                            "GONE from the roster since the last snapshot"))

    for sid in sorted(now.keys() & was.keys()):
        a, b = was[sid], now[sid]
        name = str(b.get("name") or sid)
        pa, pb = a.get("projected"), b.get("projected")
        if pa is None and pb is not None:
            items.append(Change("projection", name,
                                f"now projectable ({pb:.2f}); previously abstained"))
        elif pa is not None and pb is None:
            items.append(Change("projection", name,
                                f"no longer projectable (was {pa:.2f})"))
        elif isinstance(pa, (int, float)) and isinstance(pb, (int, float)):
            if abs(pb - pa) >= MOVE_POINTS:
                items.append(Change("projection", name,
                                    f"{pa:.2f} -> {pb:.2f} ({pb - pa:+.2f})"))
        if str(a.get("availability") or "") != str(b.get("availability") or ""):
            items.append(Change("availability", name,
                                f"{a.get('availability') or '—'} -> "
                                f"{b.get('availability') or '—'}"))
        if bool(a.get("withheld")) != bool(b.get("withheld")):
            items.append(Change("availability", name,
                                "now WITHHELD (projected 0, will not play)"
                                if b.get("withheld") else
                                "no longer withheld — back in the projection"))
        if bool(a.get("locked")) != bool(b.get("locked")):
            items.append(Change("lock", name,
                                "LOCKED (his game has kicked off)"
                                if b.get("locked") else "no longer locked"))

    def lineup_names(blob: Mapping[str, object], key: str) -> list[str]:
        idx = _roster_index(blob)
        return [str((idx.get(str(s)) or {}).get("name") or s) if s else "—"
                for s in (blob.get(key) or [])]

    if lineup_names(previous, "current_lineup") != lineup_names(current, "current_lineup"):
        items.append(Change("lineup", "starting lineup",
                            "changed in Sleeper since the last snapshot"))

    was_src = {line.split()[0]: line for line in (previous.get("sources") or [])
               if isinstance(line, str) and line.split()}
    now_src = {line.split()[0]: line for line in (current.get("sources") or [])
               if isinstance(line, str) and line.split()}
    for name in sorted(now_src):
        old_status = was_src.get(name, "").split()[1:2]
        new_status = now_src[name].split()[1:2]
        if old_status and new_status and old_status != new_status:
            items.append(Change("freshness", name,
                                f"{old_status[0]} -> {new_status[0]}"))

    return Changes(str(previous.get("generated") or "unknown"),
                   previous.get("week") if isinstance(previous.get("week"), int) else None,
                   tuple(items),
                   note=("" if items else
                         "nothing material changed since the previous snapshot"))
