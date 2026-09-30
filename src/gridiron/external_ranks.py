"""Outside rankings (trade charts, tier lists) resolved to player ids, once.

The owner reads other people's rankings — screenshots of a trade-value chart,
a tiered ROS list — and asks "who on my team is ranked below a free agent?".
Those lists carry names, never ids, so they are the one input this repo takes
by name. Rule #3 still governs everything after the boundary; this module IS
the boundary, and it is deliberately strict:

  * A ranking row resolves only against the players the record already holds
    by id — the owner's roster and the available pool — never against the
    league at large, so a common name has as few chances as possible to land
    on the wrong man.
  * The key is the whole normalised name AND the position. Exact equality
    only: no substring, prefix or fuzzy matching, ever. Suffixes (Jr., III)
    and punctuation are dropped, because "D.J. Moore" / "DJ Moore" and
    "Marvin Harrison Jr." / "Marvin Harrison" are spelling, not identity.
  * Two candidates for one key is AMBIGUOUS and resolves to nobody. A team
    that disagrees (a trade, or a chart's JAC vs Sleeper's JAX after the
    alias map) still resolves, but the row says TEAM DIFFERS so a person
    looks.
  * A row that matches nobody is NOT_HELD: on another manager's roster, not
    on an NFL roster, or spelled differently — this module cannot tell which,
    and says so instead of guessing.

The list's own numbers are opaque here: `rank` orders the rows and `value`
(if the chart has one) is carried through untouched. Nothing here converts
another site's value into this repo's points.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from gridiron.ids import normalize_id

RESOLVED, TEAM_DIFFERS, AMBIGUOUS, NOT_HELD = (
    "RESOLVED", "TEAM DIFFERS", "AMBIGUOUS", "NOT HELD")

_SUFFIX = re.compile(r"\b(jr|sr|ii|iii|iv|v)\b")

#: Team spellings charts use that Sleeper does not. Both sides are mapped
#: through this before comparing, so the direction does not matter.
TEAM_ALIASES = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS", "LVR": "LV",
                "OAK": "LV", "SD": "LAC", "STL": "LA", "KCC": "KC",
                "GBP": "GB", "NEP": "NE", "NOS": "NO", "SFO": "SF",
                "TBB": "TB"}

_POSITIONS = {"QB", "RB", "WR", "TE", "K", "DEF", "DST"}


def name_key(name: object) -> str:
    """Spelling-insensitive, identity-preserving: letters only, no suffix."""
    text = unicodedata.normalize("NFKD", str(name or "")).encode(
        "ascii", "ignore").decode().lower()
    text = re.sub(r"[.'`\-]", "", text)
    text = _SUFFIX.sub(" ", text)
    return re.sub(r"[^a-z]", "", text)


def team_key(team: object) -> str:
    t = str(team or "").strip().upper()
    return TEAM_ALIASES.get(t, t)


def position_key(pos: object) -> str:
    p = re.sub(r"[^A-Z]", "", str(pos or "").upper())
    return "DEF" if p == "DST" else p


@dataclass(frozen=True)
class RankRow:
    source: str
    rank: int
    name: str
    position: str
    team: str
    value: float | None = None


@dataclass(frozen=True)
class Resolved:
    row: RankRow
    status: str
    sleeper_id: str = ""
    held_by: str = ""            # "mine" | "available" | ""
    note: str = ""

    @property
    def matched(self) -> bool:
        return self.status in (RESOLVED, TEAM_DIFFERS)


def load_ranks(path: str | Path, *, source: str | None = None) -> list[RankRow]:
    """Read a ranking CSV: columns rank, name, position (or pos), team, and an
    optional value. Header names are case-insensitive. A row without a rank,
    name or position is skipped and reported by `load_problems`."""
    path = Path(path)
    label = source or path.stem
    rows: list[RankRow] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for raw in reader:
            r = {str(k or "").strip().lower(): (v or "").strip()
                 for k, v in raw.items()}
            pos = position_key(r.get("position") or r.get("pos"))
            try:
                rank = int(float(r.get("rank") or ""))
            except ValueError:
                continue
            name = r.get("name") or r.get("player") or ""
            if not name or pos not in _POSITIONS:
                continue
            try:
                value = float(r["value"]) if r.get("value") else None
            except ValueError:
                value = None
            rows.append(RankRow(label, rank, name, pos, team_key(r.get("team")),
                                value))
    return sorted(rows, key=lambda x: x.rank)


def universe_from_record(record: Mapping[str, object]
                         ) -> list[tuple[str, str, str, str, str]]:
    """(sleeper_id, name, position, team, held_by) for every player a record
    holds by id: the owner's roster, then the available pool."""
    out: list[tuple[str, str, str, str, str]] = []
    for p in record.get("roster") or []:
        out.append((normalize_id(p.get("sleeper_id")), str(p.get("name") or ""),
                    position_key(p.get("position")), team_key(p.get("team")),
                    "mine"))
    radar = record.get("radar") or {}
    for p in (radar.get("pool") if isinstance(radar, Mapping) else None) or []:
        out.append((normalize_id(p.get("id")), str(p.get("name") or ""),
                    position_key(p.get("position")), team_key(p.get("team")),
                    "available"))
    return [u for u in out if u[0]]


def resolve(rows: Iterable[RankRow],
            universe: Iterable[tuple[str, str, str, str, str]]) -> list[Resolved]:
    """Resolve each row to at most one id. Exact key equality only."""
    index: dict[tuple[str, str], list[tuple[str, str, str]]] = {}
    for sid, name, pos, team, held in universe:
        index.setdefault((name_key(name), pos), []).append((sid, team, held))
    out: list[Resolved] = []
    for row in rows:
        hits = index.get((name_key(row.name), row.position), [])
        ids = {h[0] for h in hits}
        if not hits:
            out.append(Resolved(row, NOT_HELD, note="not on your roster or in the "
                                "available pool (another manager's player, or a "
                                "spelling this list does not share)"))
        elif len(ids) > 1:
            out.append(Resolved(row, AMBIGUOUS, note=f"{len(ids)} players share "
                                "this name and position; resolved to nobody"))
        else:
            sid, team, held = hits[0]
            if row.team and team and team != row.team:
                out.append(Resolved(row, TEAM_DIFFERS, sid, held,
                                    note=f"list says {row.team}, Sleeper says {team}"))
            else:
                out.append(Resolved(row, RESOLVED, sid, held))
    return out


def ranked_above(resolved: Iterable[Resolved]) -> dict[str, list[Resolved]]:
    """For each of the owner's ranked players (by sleeper id), the available
    players this list ranks above him. Positions are mixed on purpose: the
    list's own order is the claim being compared."""
    rs = sorted((r for r in resolved if r.matched), key=lambda r: r.row.rank)
    avail = [r for r in rs if r.held_by == "available"]
    return {r.sleeper_id: [a for a in avail if a.row.rank < r.row.rank]
            for r in rs if r.held_by == "mine"}
