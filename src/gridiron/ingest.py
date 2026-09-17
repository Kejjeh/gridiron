"""Ingest cache + manifest: where every number's as-of timestamp comes from.

Bulk pulls land in `data/research/cache/season{YEAR}/` (gitignored, rule #10)
as parquet, so NaN stays NaN and an integer count never comes back as a float
that reads like a measurement. Beside them sits `manifest.json`, which records
for every source: when it was pulled, how many rows it had, which weeks it
covers, and where it came from.

The manifest is the honest part. Report rendering reads it — never the file
mtimes — so "how fresh is this?" is answered by when the data was actually
fetched from upstream, not by when a file was last touched. A source that
failed to pull is recorded as a failure with its error, not omitted: an
absent source and a broken source degrade the report differently.
"""
from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from gridiron.freshness import SourceFreshness, Status, assess
from gridiron.league_config import SEASON_YEAR
from gridiron.paths import RESEARCH_CACHE

MANIFEST_NAME = "manifest.json"


def season_cache(season: int = SEASON_YEAR, root: Path | None = None) -> Path:
    return (root or RESEARCH_CACHE) / f"season{season}"


@dataclass
class Entry:
    name: str
    path: str
    rows: int
    as_of: str                      # ISO-8601 UTC, when the PULL happened
    source: str
    weeks: list[int] = field(default_factory=list)
    error: str = ""

    @property
    def covers_through_week(self) -> int | None:
        return max(self.weeks) if self.weeks else None

    @property
    def as_of_dt(self) -> datetime | None:
        if not self.as_of:
            return None
        dt = datetime.fromisoformat(self.as_of)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class Manifest:
    """Per-season ingest record. Small, JSON, safe to read offline."""

    def __init__(self, directory: Path, entries: dict[str, Entry] | None = None,
                 season: int = SEASON_YEAR) -> None:
        self.directory = Path(directory)
        self.season = season
        self.entries: dict[str, Entry] = dict(entries or {})

    @property
    def path(self) -> Path:
        return self.directory / MANIFEST_NAME

    @classmethod
    def load(cls, directory: Path, season: int = SEASON_YEAR) -> "Manifest":
        p = Path(directory) / MANIFEST_NAME
        if not p.exists():
            return cls(directory, {}, season)
        blob = json.loads(p.read_text(encoding="utf-8"))
        entries = {k: Entry(**v) for k, v in (blob.get("entries") or {}).items()}
        return cls(directory, entries, int(blob.get("season", season)))

    def save(self) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"season": self.season,
             "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "entries": {k: asdict(v) for k, v in sorted(self.entries.items())}},
            indent=1), encoding="utf-8")
        return self.path

    def record(self, name: str, *, path: Path | str, rows: int, source: str,
               weeks: Iterable[int] = (), as_of: datetime | None = None,
               error: str = "") -> Entry:
        stamp = (as_of or datetime.now(timezone.utc)).astimezone(timezone.utc)
        entry = Entry(
            name=name,
            path=str(Path(path).name),
            rows=int(rows),
            as_of=stamp.isoformat(timespec="seconds"),
            source=source,
            weeks=sorted({int(w) for w in weeks}),
            error=error,
        )
        self.entries[name] = entry
        return entry

    def get(self, name: str) -> Entry | None:
        return self.entries.get(name)

    def file(self, name: str) -> Path | None:
        e = self.entries.get(name)
        if e is None or not e.path or e.error:
            return None
        p = self.directory / e.path
        return p if p.exists() else None

    def age_ok(self, name: str, now: datetime, max_age_hours: float) -> bool:
        """Used by the puller to skip a source that is already current."""
        e = self.entries.get(name)
        if e is None or e.error or self.file(name) is None:
            return False
        as_of = e.as_of_dt
        if as_of is None:
            return False
        return (now - as_of).total_seconds() / 3600.0 <= max_age_hours

    def freshness(self, name: str, *, now: datetime,
                  required_week: int | None = None) -> SourceFreshness:
        e = self.entries.get(name)
        if e is None:
            return SourceFreshness(name, Status.MISSING, None, 0, None,
                                   "never pulled")
        if e.error:
            return SourceFreshness(name, Status.MISSING, e.as_of_dt, 0,
                                   e.covers_through_week,
                                   f"last pull failed: {e.error[:120]}")
        return assess(name, now=now, as_of=e.as_of_dt, rows=e.rows,
                      covers_through_week=e.covers_through_week,
                      required_week=required_week)

    def freshness_report(self, names: Sequence[str], *, now: datetime,
                         required_week: int | None = None
                         ) -> tuple[SourceFreshness, ...]:
        return tuple(self.freshness(n, now=now, required_week=required_week)
                     for n in names)

    def read_frame(self, name: str):
        """Load a cached frame, or None when the source is missing/failed."""
        import pandas as pd

        p = self.file(name)
        if p is None:
            return None
        return pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p)

    def read_json(self, name: str):
        p = self.file(name)
        if p is None:
            return None
        return json.loads(p.read_text(encoding="utf-8"))
