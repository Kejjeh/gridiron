"""The weekly roster report: what we actually know, and what we don't.

This is deliberately NOT a recommendation engine. No projection model has
passed the rule #5 gate yet, so this module prints evidence — usage, snap
share, league points to date, injury designation, opponent and the market's
implied team total — and refuses to rank a lineup. The moment it invents a
number, every downstream decision inherits a fiction, which is the failure
mode the whole repo is shaped against.

Three honesty invariants, each with a test:

1. Rule #11 — "Questionable" is not "out" and "on the roster" is not
   "startable". `AvailabilityNote` reports the DESIGNATION and its source;
   there is no `is_startable` boolean, because a convenience accessor is
   exactly what makes the wrong call easy.
2. Every cell is either evidence or blank. A missing snap count is blank,
   never 0. A player with no box-score row is `games=0`, not `ppg=0`.
3. The freshness block prints BEFORE the table, and `degraded` is true
   whenever any input is stale or missing.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from gridiron.freshness import SourceFreshness, Status, WeekContext, degradations
from gridiron.ids import Crosswalk, is_dst_id, normalize_id
from gridiron.league_config import LEAGUE_NAME, ROSTER_SLOTS, SEASON_YEAR
from gridiron.vegas import implied_totals_from_nflverse

#: What the report is honest about not having.
PROJECTION_STATUS = (
    "NO PROJECTION MODEL SHIPPED — this report ranks nothing. Columns are "
    "measured usage and market lines only (rule #5: no feature ships without "
    "beating the full baseline out-of-sample)."
)

UNKNOWN = "unknown"


@dataclass(frozen=True)
class AvailabilityNote:
    """A designation plus where it came from. Never a verdict.

    The distinction this type exists to keep: "not on the week-2 injury
    report" is EVIDENCE of no designation, while "we never loaded a week-2
    injury report" is an ABSENCE of evidence. Collapsing the two is how a
    report starts telling you a hurt player is fine.

    It also refuses to present a stale designation as current: a row from an
    earlier week is labelled with the week it came from, so a Wednesday
    designation never masquerades as a Sunday one.
    """

    designation: str          # Sleeper live injury_status (Questionable/Out/IR)
    practice: str             # nflverse practice participation, verbatim
    report_status: str        # nflverse game-status report, verbatim
    body_part: str
    source_week: int | None
    report_week: int
    covers_report_week: bool  # did a report for THIS week actually load?
    sources: tuple[str, ...] = field(default=())

    @property
    def known(self) -> bool:
        return bool(self.designation or self.practice or self.report_status)

    @property
    def current(self) -> bool:
        """True only when the designation describes the report week itself."""
        if self.designation:          # Sleeper's field is live, not week-keyed
            return True
        return self.source_week == self.report_week

    def describe(self) -> str:
        tail = f" ({self.body_part})" if self.body_part else ""
        prac = f"; practice: {self.practice}" if self.practice else ""
        if self.designation:
            return f"{self.designation}{tail}{prac}"
        if self.report_status:
            if self.source_week == self.report_week:
                return f"{self.report_status}{tail} — wk{self.report_week} report{prac}"
            return (f"wk{self.source_week} report: {self.report_status}{tail}"
                    f" — NO wk{self.report_week} designation yet{prac}")
        if self.practice:
            wk = self.source_week if self.source_week is not None else self.report_week
            return (f"no game-status designation{tail}; wk{wk} practice: "
                    f"{self.practice}")
        if self.covers_report_week:
            return f"no designation on the wk{self.report_week} report"
        return (f"{UNKNOWN} — no wk{self.report_week} injury report loaded; "
                f"UNRESOLVED, not healthy")


@dataclass(frozen=True)
class GameContext:
    """The upcoming game for one NFL team, from the schedule + closing line."""

    opponent: str
    home: bool
    kickoff: str
    total_line: float | None
    spread_line: float | None
    implied_total: float | None

    @property
    def line_known(self) -> bool:
        return self.total_line is not None and self.spread_line is not None

    @property
    def label(self) -> str:
        if self.opponent in (BYE_LABEL, UNKNOWN_OPPONENT):
            return self.opponent
        return ("@" if not self.home else "") + self.opponent


BYE_LABEL = "BYE"
#: Used when the SCHEDULE itself is missing for the report week. A team we
#: cannot find in a schedule we never loaded is not on a bye — saying "BYE"
#: there would be an invented fact.
UNKNOWN_OPPONENT = "?"

BYE = GameContext(opponent=BYE_LABEL, home=False, kickoff="", total_line=None,
                  spread_line=None, implied_total=None)
NO_SCHEDULE = GameContext(opponent=UNKNOWN_OPPONENT, home=True, kickoff="",
                          total_line=None, spread_line=None, implied_total=None)


def schedule_index(schedule: pd.DataFrame, week: int) -> dict[str, GameContext]:
    """team -> GameContext for one week. Teams absent from the map are on bye.

    A row with no posted line yields a GameContext with `implied_total=None`;
    it is never back-filled from a league average.
    """
    out: dict[str, GameContext] = {}
    if schedule is None or len(schedule) == 0:
        return out
    wk = schedule.loc[schedule["week"] == int(week)]
    for row in wk.itertuples():
        total = getattr(row, "total_line", None)
        spread = getattr(row, "spread_line", None)
        total = None if total is None or pd.isna(total) else float(total)
        spread = None if spread is None or pd.isna(spread) else float(spread)
        home_it = away_it = None
        if total is not None and spread is not None and total > 0:
            home_it, away_it = implied_totals_from_nflverse(total, spread)
        kick = f"{getattr(row, 'gameday', '')} {getattr(row, 'gametime', '')}".strip()
        out[str(row.home_team)] = GameContext(
            str(row.away_team), True, kick, total,
            None if spread is None else -spread, home_it)
        out[str(row.away_team)] = GameContext(
            str(row.home_team), False, kick, total, spread, away_it)
    return out


def injury_index(injuries: pd.DataFrame, week: int) -> dict[str, dict]:
    """gsis_id -> the most recent injury row at or before `week`.

    Chronological by construction: a later week's report can never be read
    into an earlier week's row.
    """
    out: dict[str, dict] = {}
    if injuries is None or len(injuries) == 0:
        return out
    inj = injuries.loc[injuries["week"] <= int(week)].sort_values("week")
    for row in inj.itertuples():
        gid = normalize_id(getattr(row, "gsis_id", ""))
        if not gid:
            continue
        out[gid] = {
            "week": int(row.week),
            "report_status": _txt(getattr(row, "report_status", "")),
            "practice_status": _txt(getattr(row, "practice_status", "")),
            "body_part": _txt(getattr(row, "report_primary_injury", ""))
            or _txt(getattr(row, "practice_primary_injury", "")),
        }
    return out


def _txt(value: object) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return ""
    except (TypeError, ValueError):
        pass
    s = str(value).strip()
    return "" if s.lower() in {"nan", "none", "<na>"} else s


def availability(gsis_id: str, sleeper_player: Mapping[str, object] | None,
                 injuries: Mapping[str, dict], *, report_week: int,
                 covers_report_week: bool) -> AvailabilityNote:
    sp = sleeper_player or {}
    row = injuries.get(normalize_id(gsis_id), {})
    sources = []
    if sp.get("injury_status"):
        sources.append("sleeper")
    if row:
        sources.append(f"nflverse wk{row.get('week')}")
    return AvailabilityNote(
        designation=_txt(sp.get("injury_status")),
        practice=row.get("practice_status", "") or _txt(sp.get("practice_participation")),
        report_status=row.get("report_status", ""),
        body_part=row.get("body_part", "") or _txt(sp.get("injury_body_part")),
        source_week=row.get("week"),
        report_week=int(report_week),
        covers_report_week=bool(covers_report_week),
        sources=tuple(sources),
    )


@dataclass(frozen=True)
class WeeklyReport:
    context: WeekContext
    sources: tuple[SourceFreshness, ...]
    rows: pd.DataFrame
    unresolved_ids: tuple[str, ...]
    notes: tuple[str, ...]

    @property
    def degraded(self) -> bool:
        return bool(self.notes) or any(
            s.status is not Status.FRESH for s in self.sources)

    def to_markdown(self, *, include_names: bool = True) -> str:
        head = [
            f"# Weekly report — {LEAGUE_NAME}" if include_names else "# Weekly report",
            "",
            f"**{self.context.headline()}**",
            f"generated {self.context.now.isoformat(timespec='seconds')}",
            "",
            f"> {PROJECTION_STATUS}",
            "",
            "## Input freshness",
            "",
            "```",
            *[s.line() for s in self.sources],
            "```",
            "",
        ]
        if self.notes:
            head += ["## Degraded inputs", ""]
            head += [f"- {n}" for n in self.notes]
            head += [""]
        else:
            head += ["All inputs current.", ""]
        if self.unresolved_ids:
            head += [
                "## Unresolved player ids",
                "",
                "These roster spots could not be anchored to a stable player id, "
                "so they carry no usage. They are listed, never name-matched "
                "(rule #3):",
                "",
                *[f"- sleeper_id `{i}`" for i in self.unresolved_ids],
                "",
            ]
        head += ["## Roster", ""]
        if len(self.rows) == 0:
            head += ["_no roster rows_"]
            return "\n".join(head)
        cols = [c for c in DISPLAY_COLUMNS if c in self.rows.columns]
        head += [markdown_table(self.rows, cols)]
        head += ["", "## Column meanings", "",
                 "- `snap%`, `tgt`, `tgt_sh`, `opp` — OPPORTUNITY (fast-moving, "
                 "real in-season signal).",
                 "- `y/tgt`, `catch%` — EFFICIENCY (slow-moving prior; a "
                 "two-week delta is noise, rule #6).",
                 "- `pts`/`ppg` — league scoring via `gridiron.scoring`, the one "
                 "implementation (rule #2).",
                 "- `implied` — market implied team total for the report week; "
                 "blank when no line is posted.",
                 "- `availability` — the DESIGNATION and its source. Not a "
                 "start/sit verdict (rule #11)."]
        return "\n".join(head)


DISPLAY_COLUMNS: tuple[str, ...] = (
    "lineup", "player", "pos", "nfl", "opp", "implied", "availability",
    "g", "pts", "ppg", "snap%", "tgt", "tgt_sh", "car", "opp_n",
    "y/tgt", "catch%",
)


def build_report(
    *,
    context: WeekContext,
    sources: Sequence[SourceFreshness],
    roster: Mapping[str, object],
    sleeper_players: Mapping[str, Mapping[str, object]],
    crosswalk: Crosswalk,
    std: pd.DataFrame,
    schedule: pd.DataFrame,
    injuries: pd.DataFrame,
) -> WeeklyReport:
    """Assemble one manager's roster into the week's evidence table."""
    starters = [normalize_id(p) for p in (roster.get("starters") or [])]
    players = [normalize_id(p) for p in (roster.get("players") or [])]
    reserve = {normalize_id(p) for p in (roster.get("reserve") or [])}
    resolution = crosswalk.resolve(players)

    games = schedule_index(schedule, context.report_week)
    inj = injury_index(injuries, context.report_week)
    injuries_cover = _covers_week(injuries, context.report_week, sources,
                                  "injuries")
    schedule_cover = _covers_week(schedule, context.report_week, sources,
                                  "schedules")
    unplayed = BYE if schedule_cover else NO_SCHEDULE
    std_by_gsis = (std.set_index("gsis_id") if len(std) and "gsis_id" in std
                   else pd.DataFrame())

    rows = []
    for sid in players:
        sp = sleeper_players.get(sid) or {}
        gid = resolution.mapping.get(sid, "")
        dst = is_dst_id(sid)
        lineup = ("IR" if sid in reserve else
                  "START" if sid in starters else "BENCH")
        nfl_team = _txt(sp.get("team")) or (sid if dst else "")
        game = games.get(nfl_team, unplayed)
        note = availability(gid, sp, inj, report_week=context.report_week,
                            covers_report_week=injuries_cover)
        row: dict[str, object] = {
            "lineup": lineup,
            "player": _txt(sp.get("full_name")) or (f"{nfl_team} DST" if dst
                                                    else crosswalk.display_name(gid))
            or f"sleeper:{sid}",
            "pos": _txt(sp.get("position")) or ("DST" if dst else ""),
            "nfl": nfl_team,
            "opp": game.label,
            "implied": game.implied_total,
            "availability": ("n/a (team defense)" if dst else note.describe()),
            "sleeper_id": sid,
            "gsis_id": gid,
        }
        stats = (std_by_gsis.loc[gid] if gid and len(std_by_gsis)
                 and gid in std_by_gsis.index else None)
        row.update({
            "g": _get(stats, "games"),
            "pts": _get(stats, "points"),
            "ppg": _get(stats, "ppg"),
            "snap%": _get(stats, "offense_pct"),
            "tgt": _get(stats, "targets"),
            "tgt_sh": _get(stats, "target_share"),
            "car": _get(stats, "carries"),
            "opp_n": _get(stats, "opportunities"),
            "y/tgt": _get(stats, "yards_per_target"),
            "catch%": _get(stats, "catch_rate"),
        })
        rows.append(row)

    frame = pd.DataFrame(rows)
    if len(frame):
        order = {"START": 0, "BENCH": 1, "IR": 2}
        frame = frame.sort_values(
            by=["lineup", "pts"],
            key=lambda c: c.map(order) if c.name == "lineup" else c,
            ascending=[True, False],
        ).reset_index(drop=True)

    notes = list(degradations(sources, context))
    if resolution.unresolved:
        notes.append(
            f"{len(resolution.unresolved)} roster id(s) unresolved against the "
            f"crosswalk — usage blank for those rows (never name-matched)")
    n_slots = sum(v for k, v in ROSTER_SLOTS.items() if k != "BENCH")
    filled = sum(1 for s in starters if s)
    if filled != n_slots:
        notes.append(
            f"Sleeper reports {filled} starters against {n_slots} lineup slots "
            f"— an empty slot scores zero")
    if not schedule_cover:
        notes.append(
            f"no current schedule covering week {context.report_week} — "
            f"opponents and market lines show '{UNKNOWN_OPPONENT}', and a "
            f"blank opponent is NOT a bye")
    if context.season != SEASON_YEAR:
        notes.append(
            f"data season {context.season} != league_config.SEASON_YEAR "
            f"{SEASON_YEAR}")
    return WeeklyReport(context, tuple(sources), frame,
                        resolution.unresolved, tuple(notes))


def _fmt(value: object) -> str:
    """Blank cells stay blank — an empty string is the honest render of
    'no evidence', and it must never round-trip into a 0."""
    if value is None:
        return ""
    try:
        if pd.isna(value):  # type: ignore[arg-type]
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def markdown_table(frame: pd.DataFrame, cols: Sequence[str]) -> str:
    """A GitHub-flavoured table, written here so the repo keeps its
    dependency list to the ingest stack (no tabulate)."""
    cells = [[_fmt(v) for v in row] for row in frame[list(cols)].itertuples(index=False)]
    widths = [max(len(str(c)), *(len(r[i]) for r in cells)) if cells else len(str(c))
              for i, c in enumerate(cols)]
    def line(vals):
        return "| " + " | ".join(v.ljust(w) for v, w in zip(vals, widths)) + " |"
    out = [line([str(c) for c in cols]),
           "|" + "|".join("-" * (w + 2) for w in widths) + "|"]
    out += [line(r) for r in cells]
    return "\n".join(out)


def _covers_week(frame: pd.DataFrame, week: int,
                 sources: Sequence[SourceFreshness], name: str) -> bool:
    """Did a CURRENT pull of `name` actually contain this week's rows?

    Both halves matter. A fresh pull that stops at last week is not coverage,
    and a week's worth of rows from a four-day-old pull is not current. Only
    when both hold does an absence in that frame count as evidence.
    """
    fresh = next((s for s in sources if s.name == name), None)
    if fresh is not None and fresh.status is not Status.FRESH:
        return False
    if frame is None or len(frame) == 0 or "week" not in frame.columns:
        return False
    return bool((frame["week"] == int(week)).any())


def _get(stats, key):
    """Blank, not zero, when the evidence is absent."""
    if stats is None or key not in stats:
        return None
    val = stats[key]
    try:
        return None if pd.isna(val) else float(val)
    except (TypeError, ValueError):
        return None
