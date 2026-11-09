"""Free agents with a starter's WORK, and free agents behind a vacated role.

Two misses on 2026 week 9 made this module. Both players sat in the pool for
days, the radar ranked them below its cut, and both scored like starters that
Sunday while another manager held them:

  * a receiver playing 79-85% of his team's snaps with 6-10 targets a game,
    ranked WR52 by the rest-of-season model (volume he already HAD);
  * a third-string back whose team's lead back had been released and whose
    second back was ruled Out on the Friday (volume he was ABOUT to have).

Rule #6 says volume is the fast, real signal; a projection that leans on a
slow efficiency prior is late to both. So the watch list (`gridiron.notable`)
names these players on usage alone, never on a projection of ours:

  * `volume_reason` — a pool player whose last `VOLUME_GAMES` games with a
    stat line average a starter's snap share AND a starter's opportunities at
    his position (`VOLUME_SNAP_PCT`, `VOLUME_OPPS`). Both, because snaps
    without the ball are a blocker and touches without snaps are garbage time.
  * `find_vacancies` + `opening_reasons` — a player who had real work for an
    NFL team in the last `VACANCY_RECENT_WEEKS` weeks and now will not do it:
    Sleeper designates him Out (or IR, PUP, suspended, not with the team,
    Doubtful), or Sleeper lists him on another team or on none. The pool
    players at that team and position, highest on Sleeper's depth chart first,
    are named as the ones behind the opening (`OPENING_TAKE`).

What it refuses to invent: "Questionable" is not a vacancy (rule #11); a
week with no stat line is absent, not zero; a player whose snap share is
unknown is never called a volume player; a departure is read only from
Sleeper's current team against the team his own recent stat lines carry,
joined by id (rule #3), never by name. Nothing here is a projection or a
move — each row says who to look at and why.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass

import pandas as pd

from gridiron.ids import nflverse_team, normalize_id

#: Games (with a stat line) the volume test averages, and the weeks they
#: must fall in: a player who has not played for weeks is not "a starter now".
VOLUME_GAMES = 3
VOLUME_WINDOW_WEEKS = 4
#: A starter's work, by position: mean offensive snap share (percent) and
#: mean opportunities (targets + carries) over those games.
VOLUME_SNAP_PCT = {"RB": 40.0, "WR": 65.0, "TE": 60.0}
VOLUME_OPPS = {"RB": 10.0, "WR": 6.0, "TE": 4.0}

#: A vacancy is work done RECENTLY: the departed player's last stat line is
#: within this many weeks of the evidence boundary (one bye week tolerated),
#: and his last two lines average at least this many opportunities (pass
#: attempts + carries for a QB).
VACANCY_RECENT_WEEKS = 2
VACANCY_MIN_OPPS = {"QB": 20.0, "RB": 8.0, "WR": 5.0, "TE": 4.0}
#: Sleeper designations that mean he will not play this week. Doubtful is in
#: (it nearly always means out); Questionable is not (rule #11).
VACATING_DESIGNATIONS = frozenset({"OUT", "IR", "PUP", "SUS", "NA", "COV", "DOUBTFUL"})
#: Pool players named per opening (by Sleeper depth chart, then their own work).
OPENING_TAKE = 2
POSITIONS = ("QB", "RB", "WR", "TE")


def _num(v: object) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) else f


def _opps(row: Mapping[str, object], position: str) -> float | None:
    carries = _num(row.get("carries"))
    other = _num(row.get("attempts" if position == "QB" else "targets"))
    if carries is None and other is None:
        return None
    return (carries or 0.0) + (other or 0.0)


@dataclass(frozen=True)
class Line:
    week: int
    team: str
    snap_pct: float | None          # percent, None when no snap row anchored
    opps: float | None


def lines_by_player(frame: pd.DataFrame | None, *, through_week: int | None
                    ) -> dict[str, list[Line]]:
    """gsis id -> his stat lines through `through_week`, oldest first. Never a
    week after the evidence boundary (chronology, as `trends.usage_line`)."""
    out: dict[str, list[Line]] = {}
    if frame is None or len(frame) == 0 or "gsis_id" not in frame or through_week is None:
        return out
    rows = frame.loc[pd.to_numeric(frame["week"], errors="coerce") <= int(through_week)]
    for rec in rows.to_dict("records"):
        gid = normalize_id(rec.get("gsis_id"))
        if not gid:
            continue
        pos = str(rec.get("position") or "").upper()
        snap = _num(rec.get("offense_pct"))
        out.setdefault(gid, []).append(Line(
            int(rec["week"]), str(rec.get("team") or ""),
            None if snap is None else snap * 100.0, _opps(rec, pos)))
    for gid, ls in out.items():
        by_week = {ln.week: ln for ln in ls}          # one line a week, the last read
        out[gid] = [by_week[w] for w in sorted(by_week)]
    return out


def _mean(xs: Sequence[float | None]) -> float | None:
    if not xs or any(x is None for x in xs):
        return None
    return sum(xs) / len(xs)


def volume_reason(position: str, lines: Sequence[Line], through_week: int | None
                  ) -> tuple[str, float] | None:
    """(why, his opportunities a game) when this pool player already has a
    starter's work, else None."""
    pos = str(position or "").upper()
    if pos not in VOLUME_SNAP_PCT or through_week is None:
        return None
    recent = [ln for ln in lines if ln.week > int(through_week) - VOLUME_WINDOW_WEEKS]
    if len(recent) < VOLUME_GAMES:
        return None
    last = recent[-VOLUME_GAMES:]
    snap = _mean([ln.snap_pct for ln in last])
    opps = _mean([ln.opps for ln in last])
    if snap is None or opps is None:          # unknown is never "a starter"
        return None
    if snap < VOLUME_SNAP_PCT[pos] or opps < VOLUME_OPPS[pos]:
        return None
    weeks = "/".join(str(ln.week) for ln in last)
    why = (f"a starter's work already: {snap:.0f}% of snaps and {opps:.1f} "
           f"opportunities a game over weeks {weeks} (volume only, rule #6)")
    return why, opps


@dataclass(frozen=True)
class Vacancy:
    team: str                   # nflverse abbreviation
    position: str
    sleeper_id: str
    name: str
    opps: float                 # his recent opportunities a game
    why: str                    # "ruled Out", "now with KC", "released (no team)"

    def describe(self) -> str:
        return f"{self.name} ({self.opps:.1f} opportunities a game) {self.why}"


def find_vacancies(sleeper_players: Mapping[str, Mapping[str, object]],
                   lines: Mapping[str, Sequence[Line]], *, through_week: int | None,
                   gsis_of: Callable[[str], str | None]) -> dict[tuple[str, str], list[Vacancy]]:
    """(team, position) -> the recent work that will not be done this week.
    `gsis_of` is the page's crosswalk (`gridiron.ids.Crosswalk.gsis`, rule #3):
    a Sleeper id it cannot resolve is skipped, never matched by name."""
    out: dict[tuple[str, str], list[Vacancy]] = {}
    if through_week is None or not lines:
        return out
    for sid, rec in sleeper_players.items():
        if not isinstance(rec, Mapping):
            continue
        pos = str(rec.get("position") or "").upper()
        if pos not in VACANCY_MIN_OPPS:
            continue
        gid = normalize_id(gsis_of(normalize_id(sid)))
        ls = lines.get(gid) if gid else None
        if not ls:
            continue
        last = ls[-1]
        if last.week <= int(through_week) - VACANCY_RECENT_WEEKS or not last.team:
            continue
        opps = _mean([ln.opps for ln in ls[-2:]])
        if opps is None or opps < VACANCY_MIN_OPPS[pos]:
            continue
        now_team = nflverse_team(rec.get("team")) if rec.get("team") else ""
        tag = str(rec.get("injury_status") or "").upper()
        if not now_team:
            why = "is no longer on the team (Sleeper lists no team)"
        elif now_team != last.team:
            why = f"is now with {now_team}"
        elif tag in VACATING_DESIGNATIONS:
            why = f"is designated {rec.get('injury_status')} (Sleeper)"
        else:
            continue
        name = str(rec.get("full_name") or "").strip() or f"sleeper id {sid}"
        out.setdefault((last.team, pos), []).append(
            Vacancy(last.team, pos, normalize_id(sid), name, opps, why))
    for v in out.values():
        v.sort(key=lambda x: -x.opps)
    return out


def opening_reasons(pool: Iterable, sleeper_players: Mapping[str, Mapping[str, object]],
                    vacancies: Mapping[tuple[str, str], Sequence[Vacancy]],
                    lines: Mapping[str, Sequence[Line]], *,
                    sits: Callable[[object], bool] = lambda p: False
                    ) -> dict[str, tuple[str, float]]:
    """sleeper id -> (why, the vacated work in roles: opportunities a game
    over `VACANCY_MIN_OPPS` at that position) when he is behind
    a vacated role, for the `OPENING_TAKE`
    pool players at each vacated team and position who are not known to sit,
    highest on Sleeper's depth chart first (unlisted last), then by their
    own recent opportunities."""
    groups: dict[tuple[str, str], list] = {}
    for p in pool:
        key = (nflverse_team(p.team), str(p.position or "").upper())
        if key in vacancies and not sits(p):
            groups.setdefault(key, []).append(p)
    out: dict[str, tuple[str, float]] = {}
    for key, players in groups.items():
        def rank(p):
            rec = sleeper_players.get(normalize_id(p.sleeper_id)) or {}
            order = _num(rec.get("depth_chart_order"))
            ls = lines.get(normalize_id(p.gsis_id) or "") or []
            mine = _mean([ln.opps for ln in ls[-2:]]) if ls else None
            return (order if order is not None else 99.0, -(mine or 0.0), p.name)
        gone = "; ".join(v.describe() for v in vacancies[key][:3])
        # vacated work in units of "a real role" at that position, so a QB's
        # 40 pass attempts do not outrank a lead back's 17 touches
        work = sum(v.opps for v in vacancies[key]) / VACANCY_MIN_OPPS[key[1]]
        for i, p in enumerate(sorted(players, key=rank)[:OPENING_TAKE], 1):
            rec = sleeper_players.get(normalize_id(p.sleeper_id)) or {}
            order = rec.get("depth_chart_order")
            spot = f"#{order} on Sleeper's {key[0]} {key[1]} depth chart" if order \
                else f"next {key[1]} in line (not on Sleeper's depth chart)"
            out[normalize_id(p.sleeper_id)] = (
                f"a role opening at {key[0]} {key[1]}: {gone}; he is {spot}", work)
    return out
