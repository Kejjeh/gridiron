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

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gridiron.paths import LEDGER

ARCHIVE_DIR = LEDGER / "decisions"
ARCHIVE_VERSION = 1


def archive_path(season: int, week: int, now: datetime,
                 root: Path | None = None) -> Path:
    stamp = now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return (root or ARCHIVE_DIR) / f"season{season}" / f"week{week:02d}_{stamp}.json"


def write_archive(record: Mapping[str, object], path: Path) -> Path:
    """Atomic write (temp + replace) so a half-written archive never exists."""
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = json.dumps({"archive_version": ARCHIVE_VERSION, **record},
                      indent=1, default=str)
    tmp = path.with_name(path.name + ".part")
    tmp.write_text(blob, encoding="utf-8")
    tmp.replace(path)
    return path


def read_archive(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Grading
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class GradedAlternative:
    kind: str                       # "start_sit" | "waiver"
    slot: str
    chosen_id: str                  # the side the lineup actually held
    rejected_id: str
    chosen_projected: float | None
    rejected_projected: float | None
    chosen_actual: float | None
    rejected_actual: float | None
    projected_edge: float | None    # chosen - rejected, at decision time
    realized_edge: float | None     # chosen - rejected, actual
    status: str                     # "graded" | "ungradeable"
    reason: str = ""


@dataclass(frozen=True)
class Grade:
    week: int
    graded: tuple[GradedAlternative, ...]
    projection_mae: float | None
    projection_n: int
    ungradeable: int
    notes: tuple[str, ...] = field(default=())

    @property
    def n(self) -> int:
        return len(self.graded)

    def summary(self) -> str:
        ok = [g for g in self.graded if g.status == "graded"]
        agree = sum(1 for g in ok if g.projected_edge is not None and g.realized_edge is not None
                    and (g.projected_edge >= 0) == (g.realized_edge >= 0))
        mae = (f"projection MAE {self.projection_mae:.2f} over {self.projection_n} "
               f"roster player(s)" if self.projection_mae is not None else "no projection graded")
        return (f"week {self.week}: {len(ok)} alternative(s) graded, {self.ungradeable} "
                f"ungradeable; projected edge agreed with realized edge in "
                f"{agree}/{len(ok)}; {mae}")


def _actual(actuals: Mapping[str, float], gsis: str) -> float | None:
    if not gsis:
        return None
    v = actuals.get(gsis)
    return None if v is None else float(v)


def grade_archive(archive: Mapping[str, object],
                  actuals: Mapping[str, float]) -> Grade:
    """Grade a decision archive against actual league points by gsis id.

    `actuals` must be the ACTUAL points of the archive's week only. The
    grader never projects and never reads the archive's inputs for anything
    but the numbers that were on the page.
    """
    week = int(archive.get("week") or 0)
    projections: dict[str, dict] = {str(p["sleeper_id"]): p
                                    for p in (archive.get("roster") or [])}
    graded: list[GradedAlternative] = []
    ungradeable = 0

    def one(kind: str, slot: str, chosen: Mapping | None, rejected: Mapping | None) -> None:
        nonlocal ungradeable
        if chosen is None or rejected is None:
            return
        c_act = _actual(actuals, str(chosen.get("gsis_id") or ""))
        r_act = _actual(actuals, str(rejected.get("gsis_id") or ""))
        c_proj = chosen.get("projected")
        r_proj = rejected.get("projected")
        p_edge = (None if c_proj is None or r_proj is None
                  else round(float(c_proj) - float(r_proj), 3))
        if c_act is None or r_act is None:
            ungradeable += 1
            missing = [n for n, a in (("chosen", c_act), ("rejected", r_act)) if a is None]
            graded.append(GradedAlternative(
                kind, slot, str(chosen.get("sleeper_id")), str(rejected.get("sleeper_id")),
                c_proj, r_proj, c_act, r_act, p_edge, None, "ungradeable",
                f"no actual for the {' and '.join(missing)} side (bye, cut, or no stat line)"))
            return
        graded.append(GradedAlternative(
            kind, slot, str(chosen.get("sleeper_id")), str(rejected.get("sleeper_id")),
            c_proj, r_proj, c_act, r_act, p_edge, round(c_act - r_act, 3), "graded"))

    for alt in archive.get("alternatives") or []:
        one("start_sit", str(alt.get("slot") or ""),
            projections.get(str(alt.get("starter_id") or "")),
            projections.get(str(alt.get("bench_id") or "")))
    for up in archive.get("upgrades") or []:
        # The roster kept the drop and passed on the add: chosen = drop.
        one("waiver", str(up.get("slot") or ""),
            projections.get(str(up.get("drop_id") or "")), up.get("add"))

    errs = []
    for p in projections.values():
        proj, gsis = p.get("projected"), str(p.get("gsis_id") or "")
        act = _actual(actuals, gsis)
        if proj is not None and act is not None and not p.get("withheld"):
            errs.append(abs(float(proj) - act))
    mae = round(sum(errs) / len(errs), 3) if errs else None
    return Grade(week, tuple(graded), mae, len(errs), ungradeable,
                 notes=("graded from archived numbers only; nothing re-projected",))
