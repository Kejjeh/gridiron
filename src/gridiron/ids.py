"""The ONE player-id crosswalk (rule #3). Every cross-source join goes here.

Names are never a join key: they collide (two Josh Allens), get suffixed
(Jr./III) and get reformatted per source. This module maps platform ids onto
nflverse `gsis_id` and nothing else resolves them.

Edges we actually need
- Sleeper `player_id` -> `gsis_id`: rosters, matchups and the league's own
  scoring are keyed by Sleeper ids; every nflverse stat frame is keyed by
  gsis.
- `gsis_id` -> `pfr_id`: snap counts come from Pro-Football-Reference and
  carry `pfr_player_id`, not gsis.

Source of record: dynastyprocess `db_playerids.csv`. Measured 2026-09-17 on
this league: it resolved 168/168 rostered non-DST players, while Sleeper's
own `gsis_id` field resolved only 36/168 (it is sparse for current players).
Sleeper's field is still merged in as an overlay so a player dynastyprocess
has not picked up yet can still resolve without a name match.

`nflreadpy.load_ff_playerids()` reads the same file through
github.com/.../raw/..., which the session egress proxy answers 403; we read
raw.githubusercontent.com directly (see docs/DECISIONS.md 2026-09-17).

Team defenses are NOT in this crosswalk. Sleeper identifies them by team
abbreviation ("SEA"), which IS a stable id — `is_dst_id` recognises them so
callers route them rather than silently dropping them.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

CROSSWALK_URL = (
    "https://raw.githubusercontent.com/dynastyprocess/data/master/files/"
    "db_playerids.csv"
)

#: Kept columns. `name`/`position`/`team` are for DISPLAY and diagnostics
#: only — joining on them is the banned strategy this module exists to stop.
KEEP_COLUMNS: tuple[str, ...] = (
    "sleeper_id", "gsis_id", "pfr_id", "espn_id", "mfl_id",
    "name", "position", "team",
)

_ID_COLUMNS = ("sleeper_id", "gsis_id", "pfr_id", "espn_id", "mfl_id")


def normalize_id(value: object) -> str:
    """Ids arrive with stray whitespace (Sleeper ships ' 00-0035057'), as
    floats from CSV round-trips ('1234.0'), or as nulls. One spelling out."""
    if value is None:
        return ""
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "<na>"}:
        return ""
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    return s


def is_dst_id(sleeper_id: object) -> bool:
    """Sleeper names team defenses by team abbreviation, not a numeric id."""
    s = normalize_id(sleeper_id)
    return bool(s) and not s.isdigit()


@dataclass(frozen=True)
class Resolution:
    """Outcome of resolving a batch of platform ids.

    `unresolved` is a first-class result, never an exception and never a
    silent drop: a player we cannot anchor is reported as such in the weekly
    report rather than guessed at.
    """

    mapping: dict[str, str]
    unresolved: tuple[str, ...]
    dst: tuple[str, ...]

    @property
    def coverage(self) -> float:
        n = len(self.mapping) + len(self.unresolved)
        return 1.0 if n == 0 else len(self.mapping) / n


class Crosswalk:
    """Immutable id map. Build with `from_csv`, extend with `with_overlay`."""

    def __init__(self, sleeper_to_gsis: Mapping[str, str],
                 gsis_to_pfr: Mapping[str, str],
                 display: Mapping[str, str] | None = None) -> None:
        self._s2g = dict(sleeper_to_gsis)
        self._g2p = dict(gsis_to_pfr)
        self._display = dict(display or {})

    def __len__(self) -> int:
        return len(self._s2g)

    @classmethod
    def from_csv(cls, path: str | Path) -> "Crosswalk":
        import csv

        s2g: dict[str, str] = {}
        g2p: dict[str, str] = {}
        display: dict[str, str] = {}
        with open(path, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                sid = normalize_id(row.get("sleeper_id"))
                gid = normalize_id(row.get("gsis_id"))
                pid = normalize_id(row.get("pfr_id"))
                if sid and gid:
                    s2g[sid] = gid
                if gid and pid:
                    g2p[gid] = pid
                if gid:
                    display.setdefault(gid, normalize_id(row.get("name")))
        return cls(s2g, g2p, display)

    def with_overlay(self, sleeper_to_gsis: Mapping[str, str]) -> "Crosswalk":
        """Return a copy that also honours `sleeper_to_gsis`. Existing entries
        win: the measured source of record is not overridden by a fallback."""
        merged = dict(self._s2g)
        for sid, gid in sleeper_to_gsis.items():
            sid, gid = normalize_id(sid), normalize_id(gid)
            if sid and gid:
                merged.setdefault(sid, gid)
        return Crosswalk(merged, self._g2p, self._display)

    def gsis(self, sleeper_id: object) -> str | None:
        return self._s2g.get(normalize_id(sleeper_id))

    def pfr(self, gsis_id: object) -> str | None:
        return self._g2p.get(normalize_id(gsis_id))

    def display_name(self, gsis_id: object) -> str:
        return self._display.get(normalize_id(gsis_id), "")

    def resolve(self, sleeper_ids: Iterable[object]) -> Resolution:
        mapping: dict[str, str] = {}
        unresolved: list[str] = []
        dst: list[str] = []
        for raw in sleeper_ids:
            sid = normalize_id(raw)
            if not sid:
                continue
            if is_dst_id(sid):
                dst.append(sid)
                continue
            gid = self._s2g.get(sid)
            if gid:
                mapping[sid] = gid
            else:
                unresolved.append(sid)
        return Resolution(mapping, tuple(sorted(set(unresolved))),
                          tuple(sorted(set(dst))))


def sleeper_gsis_overlay(players: Mapping[str, Mapping[str, object]]) -> dict[str, str]:
    """Pull whatever gsis ids Sleeper's own /players/nfl dump carries."""
    out: dict[str, str] = {}
    for sid, rec in players.items():
        gid = normalize_id((rec or {}).get("gsis_id"))
        sid = normalize_id(sid)
        if sid and gid:
            out[sid] = gid
    return out
