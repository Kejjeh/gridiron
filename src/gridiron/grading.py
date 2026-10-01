"""Weekly grading without a person: pin the board each week was decided on,
grade it once the week is final, and turn the season ledger into a report card.

`scripts/weekly/grade_week.py` grades one archive against one week's actual
points (rule #7: grade the choice, from numbers frozen at decision time). A
person ran it by hand. The cloud build can now run it every time, and three
facts shape how:

  * The board a week is graded on has to survive until the week is over.
    The cloud writes a decision record every run and keeps the newest 40
    (`gridiron.carryover.DEFAULT_KEEP`) — about ten hours at a 15-minute
    cadence — so Sunday's board is long gone by Tuesday. `pin_weeks` keeps
    ONE record per week aside: the latest one built before that week's main
    slate kicked off (the Sunday early games), which is the board the
    owner set the lineup from. Without a schedule it falls back to the latest
    record whose phase was still pregame. A pin is a copy, byte for byte; the
    record is never edited (`gridiron.decisions.write_archive`).
  * A week is graded once, when it is FINAL — every regular-season game of
    that week has a score in the schedule — and never earlier. A week that
    already has a row in the ledger, from a person or from an earlier run,
    is left alone: an automatic grade never replaces a manual one.
  * The ledger is counts and rates only (no player names or ids), so it is
    safe to show on a public page and to carry between runs.

`report_card` reads that ledger for the board: one line per week, the
season-to-date rates, and the start/sit shoot-out, each with its sample size.
One week is noise (rule #5); the card says so rather than ranking anything
by it.
"""
from __future__ import annotations

import csv
import shutil
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from gridiron.decisions import archive_stamp, read_archive
from gridiron.lineup import kickoff_index

#: Where pins live inside the carry store: <store>/pins/season<YYYY>/.
PINS = "pins"
#: Where the carried copy of the season ledger lives: <store>/grades/.
GRADES = "grades"

#: The systems the weekly shoot-out compares, as the page names them.
SYSTEM_LABELS: dict[str, str] = {
    "page": "This page", "baseline_v1": "Old baseline", "sleeper": "Sleeper",
    "blend": "Page + Sleeper blend", "stack": "Stack",
}

#: Evening games run past midnight UTC; shifting back six hours files every
#: kickoff under its US calendar day before the slate is chosen.
_DAY_SHIFT = timedelta(hours=6)


# --------------------------------------------------------------- schedule
def main_slate_kickoff(schedule: pd.DataFrame | None, week: int) -> datetime | None:
    """The first kickoff on the day most of the week's teams play (Sunday).

    None when the schedule cannot time the week. The Thursday game is not the
    cutoff: a board built Friday or Saturday is the one the owner acted on
    for every Sunday player, so it is the one to grade."""
    idx = kickoff_index(schedule, week)
    if idx is None or not idx.kickoffs:
        return None
    times = [k.astimezone(timezone.utc) for k in idx.kickoffs.values()]
    by_day = Counter((t - _DAY_SHIFT).date() for t in times)
    day = max(by_day, key=lambda d: (by_day[d], d))
    return min(t for t in times if (t - _DAY_SHIFT).date() == day)


def week_is_final(schedule: pd.DataFrame | None, week: int) -> bool:
    """Every regular-season game of `week` carries both scores. A week with no
    game rows at all is not final — absence is never evidence."""
    if schedule is None or len(schedule) == 0 or "week" not in schedule.columns:
        return False
    if not {"home_score", "away_score"} <= set(schedule.columns):
        return False
    rows = schedule.loc[schedule["week"] == int(week)]
    if "game_type" in rows.columns:
        types = rows["game_type"].astype(str).str.strip().str.upper()
        rows = rows.loc[types == "REG"]
    if len(rows) == 0:
        return False
    return bool(rows["home_score"].notna().all() and rows["away_score"].notna().all())


# ------------------------------------------------------------------- pins
@dataclass(frozen=True)
class PinReport:
    pinned: tuple[str, ...] = ()      # "week NN: <file>" lines, newly pinned
    kept: tuple[int, ...] = ()        # weeks whose pin was already the newest
    skipped: tuple[str, ...] = ()     # reasons, no player names

    def lines(self) -> list[str]:
        out = [f"pinned {x}" for x in self.pinned]
        if self.kept:
            out.append("pin unchanged for week(s) " + ", ".join(map(str, self.kept)))
        out += [f"skipped: {s}" for s in self.skipped]
        return out or ["no decision record qualifies for a pin"]


def pinned(pins_dir: Path, season: int) -> dict[int, Path]:
    """week -> the pinned record for it."""
    folder = Path(pins_dir) / f"season{season}"
    out: dict[int, tuple[datetime, Path]] = {}
    for p in folder.glob("week*.json") if folder.is_dir() else ():
        stamp = archive_stamp(p)
        try:
            week = int(p.name[4:6])
        except ValueError:
            continue
        if stamp is not None and (week not in out or stamp > out[week][0]):
            out[week] = (stamp, p)
    return {w: p for w, (_, p) in out.items()}


def pin_weeks(ledger_dir: Path, pins_dir: Path, *, season: int,
              cutoffs: Mapping[int, datetime | None]) -> PinReport:
    """Keep the latest qualifying record of each week in `pins_dir`.

    Qualifying: built before that week's main-slate kickoff (`cutoffs`), or,
    for a week the schedule cannot time, built while the week was pregame.
    An older pin of the same week is replaced by a newer qualifying record,
    never by an older one, and a pin is never edited."""
    src = Path(ledger_dir) / f"season{season}"
    if not src.is_dir():
        src = Path(ledger_dir)
    best: dict[int, tuple[datetime, Path]] = {}
    skipped: list[str] = []
    for path in sorted(src.glob("week*.json")):
        stamp = archive_stamp(path)
        if stamp is None:
            continue
        try:
            week = int(path.name[4:6])
        except ValueError:
            continue
        cutoff = cutoffs.get(week)
        if cutoff is not None:
            ok = stamp < cutoff
        else:
            try:
                ok = read_archive(path).get("phase") == "pregame"
            except (OSError, ValueError):
                skipped.append(f"{path.name} unreadable")
                continue
        if ok and (week not in best or stamp > best[week][0]):
            best[week] = (stamp, path)

    have = pinned(pins_dir, season)
    target = Path(pins_dir) / f"season{season}"
    done, kept = [], []
    for week, (stamp, path) in sorted(best.items()):
        old = have.get(week)
        old_stamp = archive_stamp(old) if old is not None else None
        if old_stamp is not None and old_stamp >= stamp:
            kept.append(week)
            continue
        target.mkdir(parents=True, exist_ok=True)
        tmp = target / (path.name + ".part")
        shutil.copyfile(path, tmp)
        tmp.replace(target / path.name)
        if old is not None and old.name != path.name:
            old.unlink(missing_ok=True)
        done.append(f"week {week:02d}: {path.name}")
    return PinReport(tuple(done), tuple(kept), tuple(skipped))


# ----------------------------------------------------------------- ledger
def read_ledger(path: Path) -> list[dict]:
    """The season ledger's rows, or [] when there is none. Rows whose season,
    week or counts do not parse are dropped: a carried file is input."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    except (OSError, csv.Error, UnicodeDecodeError):
        return []
    good = []
    for r in rows:
        try:
            int(r["season"]), int(r["week"]), int(r.get("graded") or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if not str(r.get("archive") or "").strip():
            continue
        good.append(r)
    return good


def graded_weeks(rows: Iterable[Mapping[str, object]], season: int) -> set[int]:
    return {int(r["week"]) for r in rows if int(r["season"]) == int(season)}


def one_per_week(rows: Iterable[Mapping[str, object]]) -> list[Mapping[str, object]]:
    """The ledger can hold two rows for a week (a person graded a different
    board than the cloud did). The card counts each week once: the most
    recently graded row wins."""
    best: dict[tuple[int, int], Mapping[str, object]] = {}
    for r in rows:
        k = (int(r["season"]), int(r["week"]))
        if k not in best or str(r.get("graded_at") or "") >= str(best[k].get("graded_at") or ""):
            best[k] = r
    return [best[k] for k in sorted(best)]


# ------------------------------------------------------------ report card
def _i(v: object) -> int:
    try:
        return int(float(str(v)))
    except (TypeError, ValueError):
        return 0


def _f(v: object) -> float | None:
    try:
        f = float(str(v))
    except (TypeError, ValueError):
        return None
    return None if f != f else f


@dataclass(frozen=True)
class WeekGrade:
    week: int
    built: str                 # ISO time of the board that was graded
    graded: int
    ungradeable: int
    agree: int
    scorable: int
    direction_agree: int
    direction_n: int
    mae: float | None
    mae_n: int
    pairs: int
    rates: Mapping[str, float]  # system -> start/sit pairwise rate this week


@dataclass(frozen=True)
class ReportCard:
    season: int
    weeks: tuple[WeekGrade, ...] = ()
    #: system -> (pairs-weighted season rate, pairs)
    shootout: Mapping[str, tuple[float, int]] = field(default_factory=dict)

    @property
    def any(self) -> bool:
        return bool(self.weeks)

    def totals(self) -> dict[str, object]:
        w = self.weeks
        mae_n = sum(g.mae_n for g in w if g.mae is not None)
        return {
            "weeks": len(w),
            "agree": sum(g.agree for g in w), "scorable": sum(g.scorable for g in w),
            "direction_agree": sum(g.direction_agree for g in w),
            "direction_n": sum(g.direction_n for g in w),
            "mae": (sum(g.mae * g.mae_n for g in w if g.mae is not None) / mae_n
                    if mae_n else None),
            "mae_n": mae_n,
        }


def report_card(rows: Sequence[Mapping[str, object]], season: int) -> ReportCard:
    rows = [r for r in one_per_week(rows) if int(r["season"]) == int(season)]
    weeks = []
    hits: dict[str, float] = {}
    pairs_by: dict[str, int] = {}
    for r in rows:
        pairs = _i(r.get("shootout_pairs"))
        rates = {}
        for s in SYSTEM_LABELS:
            v = _f(r.get(f"{s}_pairwise"))
            if v is not None and pairs:
                rates[s] = v
                hits[s] = hits.get(s, 0.0) + v * pairs
                pairs_by[s] = pairs_by.get(s, 0) + pairs
        weeks.append(WeekGrade(
            week=_i(r["week"]), built=str(r.get("archive_built") or ""),
            graded=_i(r.get("graded")), ungradeable=_i(r.get("ungradeable")),
            agree=_i(r.get("agree")), scorable=_i(r.get("scorable")),
            direction_agree=_i(r.get("direction_agree")), direction_n=_i(r.get("direction_n")),
            mae=_f(r.get("projection_mae")), mae_n=_i(r.get("projection_n")),
            pairs=pairs, rates=rates))
    shoot = {s: (hits[s] / pairs_by[s], pairs_by[s]) for s in SYSTEM_LABELS if pairs_by.get(s)}
    return ReportCard(int(season), tuple(weeks), shoot)
