"""The weekly decision dashboard: projections, matchup, start/sit, upgrades.

This module assembles the pieces — `projection`, `lineup`, `waivers`,
`evaluate`, `decisions` — into one offline-renderable page and one archive
record. It reads only what the caller hands it (frames from the cache, the
league snapshot, the player dump, the crosswalk) and never opens a socket.

The page is ACTION-FIRST. The owner opens it on a phone on Sunday morning
with a question that has a deadline attached, so the answer comes before the
evidence and the evidence stays one tap away:

  1. **What to do**, ranked by deadline. Each action carries the real lock
     time behind it, a legal backup for when the first choice cannot be
     made, and its own ACTIONABLE / WITHHELD verdict. A withheld action
     keeps its whole comparison and loses its imperative.
  2. **Inputs, and what they are good enough for** — freshness per source,
     then the gate that turns freshness into permission. A label is not a
     gate, which is the bug this section exists to close.
  3. **Since the last snapshot** — transitions only (rule #8), diffed from
     the previous frozen archive, never recomputed.
  4. **Start/sit** — the comparisons behind the actions, with Δpoints, a
     z-score against both SDs, and the frozen slots with their reasons.
  5. **Acquisitions** — a short shortlist, each row naming its drop as the
     cost, eligibility marked UNVERIFIED because the cache cannot prove it,
     and the roster players deliberately protected from the drop list.
  6. **Roster projections** — mean ± SD, the components, the designation and
     its source, the lock state, the reason for every abstention.
  7. **Matchup** — the margin as context. P(win) is real but UNCALIBRATED,
     so it sits behind a disclosure and ranks nothing.
  8. **How the baseline has fared** out-of-sample, and its n.
  9. Where the decision-time archive was written.

Team names, manager display names and the league name are league-private;
the opponent is "roster #N" and the league name appears only when the
caller asks for it. Player names are on the page because the page is a
local file for the owner (rule #10 carve-out); the archive is gitignored.
"""
from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import sqrt
from pathlib import Path

import pandas as pd

from gridiron.decisions import (
    MOVE_POINTS, Changes, archive_path, diff_archives, previous_archive,
    read_archive, write_archive,
)
from gridiron.evaluate import EvaluationReport, chronological_evaluation
from gridiron.freshness import SourceFreshness, Status, WeekContext, degradations
from gridiron.ids import Crosswalk, is_dst_id, nflverse_team, normalize_id
from gridiron.league_config import DEFAULT_SCORING, LEAGUE_NAME, ScoringRules
from gridiron.gating import (ACTIONS, ActionGate, box_score_blockers,
                             build_gate)
from gridiron.lineup import (
    NOISE_Z, KickoffIndex, LineupPlan, Player, eligible, kickoff_index, lock_state,
    plan_lineup, slot_order,
)
from gridiron.projection import (
    BASELINE_LABEL, Projection, abstain, build_evidence, project,
)
from gridiron.scoring import ScoringCoverage
from gridiron.waivers import (
    WaiverBoard, available_ids, build_board, eligibility, pool_players,
)
from gridiron.weekly import (
    BYE, NO_SCHEDULE, availability, injury_index, schedule_index,
)
from gridiron.winprob import leverage_per_point, matchup_win_prob

PWIN_LABEL = ("UNCALIBRATED — closed-form Φ(margin/SD) on independent, "
              "literature-CV projections; no resolved-matchup calibration exists yet")

#: Designations that mean the player will NOT play. "Questionable" and
#: "Doubtful" are NOT here (rule #11): they are flagged, never zeroed.
WITHHOLD_DESIGNATIONS = {"OUT", "IR", "PUP", "SUS", "NA", "COV"}


@dataclass(frozen=True)
class MatchupView:
    opponent_roster_id: int | None
    my_starters: tuple[Player | None, ...]
    opp_starters: tuple[Player | None, ...]
    my_mean: float
    my_sd: float
    opp_mean: float
    opp_sd: float
    pwin: float | None
    pwin_reason: str
    leverage: float | None          # dP(win)/dpoint, None when pwin abstained
    my_unprojected: tuple[str, ...]
    opp_unprojected: tuple[str, ...]
    label: str = PWIN_LABEL

    @property
    def margin(self) -> float:
        return self.my_mean - self.opp_mean


@dataclass(frozen=True)
class Dashboard:
    context: WeekContext
    sources: tuple[SourceFreshness, ...]
    notes: tuple[str, ...]
    generated: datetime
    roster: tuple[Player, ...]
    slots: tuple[str, ...]
    plan: LineupPlan
    matchup: MatchupView | None
    matchup_reason: str
    board: WaiverBoard
    evaluation: EvaluationReport
    unresolved_ids: tuple[str, ...]
    my_roster_id: int | None
    gate: ActionGate = field(default_factory=lambda: build_gate(()))
    actions: tuple["Action", ...] = field(default=())
    changes: Changes | None = None
    locks: KickoffIndex | None = None
    archive: Path | None = None
    league_name: str = LEAGUE_NAME
    #: Public identifiers, not credentials: they let a later reader (the Game
    #: Day page) join this record to the right league and roster by id
    #: instead of by season and week alone.
    league_id: str = ""

    @property
    def degraded(self) -> bool:
        return bool(self.notes) or any(s.status is not Status.FRESH for s in self.sources)

    # ------------------------------------------------------------ archive
    def record(self) -> dict:
        """The decision-time record: every number that was on the page."""
        def player(p: Player | None, withheld: bool = False) -> dict | None:
            if p is None:
                return None
            return {"sleeper_id": p.sleeper_id, "gsis_id": p.gsis_id, "name": p.name,
                    "position": p.position, "team": p.team, "lineup": p.lineup,
                    "projected": p.projection.mean, "sd": p.projection.sd,
                    "withheld": p.projection.is_withheld,
                    "model_mean": p.projection.model_mean,
                    "reasons": list(p.projection.reasons), "locked": p.locked,
                    "lock_known": p.lock_known, "lock_note": p.lock_note,
                    "availability": p.availability, "flags": list(p.flags),
                    "explain": p.projection.explain()}
        m = self.matchup
        return {
            "generated": self.generated.isoformat(timespec="seconds"),
            "season": self.context.season, "week": self.context.report_week,
            "league_id": self.league_id, "my_roster_id": self.my_roster_id,
            "phase": self.context.phase.value,
            "evidence_boundary": self.context.evidence_boundary,
            "stats_through": self.context.stats_through,
            "baseline": BASELINE_LABEL,
            "degraded": self.degraded,
            "sources": [s.line() for s in self.sources],
            "notes": list(self.notes),
            "slots": list(self.slots),
            "roster": [player(p) for p in self.roster],
            "current_lineup": [p.sleeper_id if p else None for p in self.plan.current],
            "best_lineup": [p.sleeper_id if p else None for p in self.plan.best],
            "current_points": round(self.plan.current_points, 3),
            "best_points": round(self.plan.best_points, 3),
            "lineup_abstained": self.plan.abstained,
            "alternatives": [{
                "slot": a.slot, "bench_id": a.bench.sleeper_id,
                "starter_id": a.starter.sleeper_id if a.starter else None,
                "delta_points": a.delta_points, "z": a.z,
                "delta_pwin": _dpwin(m, a.delta_points)} for a in self.plan.alternatives],
            "frozen": [{"sleeper_id": p.sleeper_id, "reason": r} for p, r in self.plan.frozen],
            "matchup": None if m is None else {
                "opponent_roster_id": m.opponent_roster_id,
                "my_mean": round(m.my_mean, 3), "my_sd": round(m.my_sd, 3),
                "opp_mean": round(m.opp_mean, 3), "opp_sd": round(m.opp_sd, 3),
                "pwin": m.pwin, "pwin_reason": m.pwin_reason, "label": m.label,
                "opp_starters": [player(p) for p in m.opp_starters]},
            "matchup_reason": self.matchup_reason,
            "upgrades": [{
                "add": player(u.add), "drop_id": u.drop.sleeper_id, "slot": u.slot,
                "lineup_gain": u.lineup_gain, "depth_gain": u.depth_gain,
                "kind": u.kind} for u in self.board.upgrades],
            "waiver_abstained": self.board.abstained,
            "protected_from_drop": [
                {"sleeper_id": p.sleeper_id, "name": p.name, "reason": r}
                for p, r in self.board.protected],
            "actions": [a.record() for a in self.actions],
            "actionable": sum(1 for a in self.actions if a.actionable),
            "withheld_actions": list(self.gate.withheld),
            "gate": self.gate.record(),
            "locks": None if self.locks is None else {
                "week": self.locks.week, "complete": self.locks.complete,
                "rows_intact": self.locks.rows_intact,
                "extends_past": self.locks.extends_past,
                "timed_teams": sorted(self.locks.kickoffs),
                "time_unknown": sorted(self.locks.time_unknown),
                "conflicting": sorted(self.locks.conflicting),
                "declared_bye": sorted(self.locks.declared_bye),
                "partial_rows": self.locks.partial_rows,
                "slate_short": self.locks.slate_short,
                "dropped_rows": self.locks.dropped_rows,
                "problems": list(self.locks.problems)},
            "changes": None if self.changes is None else {
                "previous": self.changes.previous,
                "previous_week": self.changes.previous_week,
                "items": [c.line() for c in self.changes.items],
                "note": self.changes.note},
            "evaluation": {
                "verdict": self.evaluation.verdict(),
                "scores": [s.line() for s in self.evaluation.scores],
                "pwin_calibrated": self.evaluation.pwin_calibrated},
        }

    # --------------------------------------------------------------- html
    def to_html(self, *, include_names: bool = True) -> str:
        return render_html(self, include_names=include_names)


def _dpwin(m: MatchupView | None, delta_points: float) -> float | None:
    if m is None or m.leverage is None:
        return None
    return round(m.leverage * delta_points, 4)


# --------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------
def _covers(frame: pd.DataFrame | None, week: int, sources: Sequence[SourceFreshness],
            name: str) -> bool:
    fresh = next((s for s in sources if s.name == name), None)
    if fresh is not None and fresh.status is not Status.FRESH:
        return False
    if frame is None or len(frame) == 0 or "week" not in frame.columns:
        return False
    return bool((frame["week"] == int(week)).any())


def build_dashboard(*, context: WeekContext, sources: Sequence[SourceFreshness],
                    snapshot: Mapping[str, object],
                    sleeper_players: Mapping[str, Mapping[str, object]],
                    crosswalk: Crosswalk, weeks: pd.DataFrame | None,
                    schedule: pd.DataFrame | None, injuries: pd.DataFrame | None,
                    scoring: ScoringCoverage, owner_id: str, now: datetime,
                    rules: ScoringRules = DEFAULT_SCORING,
                    archive_root: Path | None = None,
                    write_archive_file: bool = True) -> Dashboard:
    week = context.report_week
    rosters = list(snapshot.get("rosters") or [])
    league = snapshot.get("league") or {}
    slots = slot_order(league.get("roster_positions"))

    evidence = build_evidence(weeks, through_week=context.stats_through, rules=rules) \
        if weeks is not None and len(weeks) else build_evidence(pd.DataFrame(), through_week=None)
    games = schedule_index(schedule, week) if schedule is not None else {}
    kickoffs = kickoff_index(schedule, week)
    schedule_cover = _covers(schedule, week, sources, "schedules")
    injuries_cover = _covers(injuries, week, sources, "injuries")
    inj = injury_index(injuries, week) if injuries is not None else {}
    players_source = next((s for s in sources if s.name == "sleeper_players"), None)
    designation_fresh = players_source is not None and players_source.status is Status.FRESH
    designation_reason = "" if designation_fresh else (
        players_source.reason if players_source is not None
        else "the player dump's freshness was never assessed")

    def make(sid: str, lineup: str) -> Player:
        sid = normalize_id(sid)
        rec = sleeper_players.get(sid) or {}
        dst = is_dst_id(sid)
        pos = str(rec.get("position") or ("DST" if dst else "")).upper()
        team = nflverse_team(rec.get("team") or (sid if dst else ""))
        gid = crosswalk.gsis(sid) or ""
        name = (str(rec.get("full_name") or "") or (f"{team} DST" if dst else "")
                or crosswalk.display_name(gid) or f"sleeper:{sid}")
        flags: list[str] = []
        game = games.get(team, BYE if schedule_cover else NO_SCHEDULE)
        implied = game.implied_total
        if not scoring.scorable(pos) and pos in ("QB", "K"):
            proj = abstain(f"scoring inputs incomplete for {pos}: {scoring.reason()}")
        else:
            proj = project(evidence.players.get(gid) if gid else None, position=pos,
                           week=week, implied_total=implied, evidence=evidence)
        if not gid and not dst:
            proj = abstain("sleeper id unresolved against the crosswalk (never name-matched)")
        note = availability(gid, rec, inj, report_week=week,
                            covers_report_week=injuries_cover,
                            designation_fresh=designation_fresh,
                            designation_reason=designation_reason)
        avail = "n/a (team defense)" if dst else note.describe()
        if proj.usable:
            if game is BYE:
                proj = proj.withheld("BYE week: projected 0")
            desig = str(note.designation or "").upper()
            if desig in WITHHOLD_DESIGNATIONS:
                proj = proj.withheld(
                    f"designated {note.designation}: projected 0"
                    + ("" if note.designation_fresh else
                       f" — from a STALE player pull ({designation_reason}); verify"))
            elif desig in ("QUESTIONABLE", "DOUBTFUL"):
                flags.append(f"{note.designation.upper()} — projection NOT adjusted "
                             f"(rule #11); verify before kickoff")
            if not note.known and not injuries_cover and not dst:
                flags.append("no injury report loaded for this week — status UNRESOLVED")
            if game is NO_SCHEDULE:
                flags.append("no schedule: opponent and line unknown")
        lk = lock_state(team, kickoffs, now)
        if not lk.known:
            flags.append("lock state UNKNOWN — no lineup move involving him can be "
                         "shown legal: " + lk.note)
        return Player(sid, name, pos, team, proj, lineup, lk.locked, lk.note, avail,
                      tuple(flags), gid, lk.known, lk.kickoff)

    # ---- owner roster
    my = next((r for r in rosters if str(r.get("owner_id")) == str(owner_id)
               or str(owner_id) in {str(c) for c in (r.get("co_owners") or [])}), None)
    roster: list[Player] = []
    starters: list[str] = []
    my_roster_id = None
    unresolved: list[str] = []
    if my is not None:
        my_roster_id = int(my.get("roster_id")) if my.get("roster_id") is not None else None
        starters = [normalize_id(s) for s in (my.get("starters") or [])]
        reserve = {normalize_id(s) for s in (my.get("reserve") or [])}
        for sid in (my.get("players") or []):
            sid = normalize_id(sid)
            lineup = "IR" if sid in reserve else "START" if sid in starters else "BENCH"
            p = make(sid, lineup)
            if not p.gsis_id and not is_dst_id(sid):
                unresolved.append(sid)
            roster.append(p)
    # `kickoffs is not None` was the old test and it was wrong: an index built
    # from an unreadable week is not None and proves nothing. Lock certainty
    # now needs at least one timed game; everything finer is per player.
    locks_usable = kickoffs is not None and bool(kickoffs.kickoffs)
    plan = plan_lineup(roster, starters, slots, locks_known=locks_usable)

    # ---- matchup
    matchup, matchup_reason = None, ""
    matchups = list(snapshot.get("matchups") or [])
    mine = next((m for m in matchups if my_roster_id is not None
                 and str(m.get("roster_id")) == str(my_roster_id)), None)
    if my is None:
        matchup_reason = "owner roster not found in the snapshot"
    elif mine is None or mine.get("matchup_id") is None:
        matchup_reason = (f"no week-{week} matchup for roster #{my_roster_id} in the "
                          f"snapshot (bye, playoffs not yet drawn, or preseason)")
    else:
        opp = next((m for m in matchups if m.get("matchup_id") == mine.get("matchup_id")
                    and str(m.get("roster_id")) != str(my_roster_id)), None)
        if opp is None:
            matchup_reason = f"matchup {mine.get('matchup_id')} has no opponent row"
        else:
            opp_roster = next((r for r in rosters
                               if str(r.get("roster_id")) == str(opp.get("roster_id"))), {})
            opp_starters_ids = [normalize_id(s) for s in
                                (opp.get("starters") or opp_roster.get("starters") or [])]
            opp_players = [make(s, "START") for s in opp_starters_ids if s and s != "0"]
            opp_by = {p.sleeper_id: p for p in opp_players}
            opp_line = tuple(opp_by.get(s) if s and s != "0" else None
                             for s in opp_starters_ids[:len(slots)])
            opp_line = opp_line + (None,) * (len(slots) - len(opp_line))
            matchup = _matchup(int(opp.get("roster_id")), plan.current, opp_line)

    # ---- waivers
    ids = available_ids(sleeper_players, rosters)

    def projector(sid: str, gid: str, pos: str, team: str) -> Projection:
        team = nflverse_team(team)
        game = games.get(team, BYE if schedule_cover else NO_SCHEDULE)
        if not gid:
            return abstain("sleeper id unresolved against the crosswalk")
        if not scoring.scorable(pos) and pos in ("QB", "K"):
            return abstain(f"scoring inputs incomplete for {pos}")
        p = project(evidence.players.get(gid), position=pos, week=week,
                    implied_total=game.implied_total, evidence=evidence)
        if p.usable and game is BYE:
            p = p.withheld("BYE week: projected 0")
        rec = sleeper_players.get(sid) or {}
        desig = str(rec.get("injury_status") or "").upper()
        if p.usable and desig in WITHHOLD_DESIGNATIONS:
            p = p.withheld(f"designated {rec.get('injury_status')}: projected 0"
                           + ("" if designation_fresh else " (STALE player pull)"))
        return p

    pool = pool_players(ids, sleeper_players, crosswalk, projector,
                        lambda team: lock_state(nflverse_team(team), kickoffs, now))
    board = build_board(roster, pool, starters, slots, locks_known=locks_usable)

    # ---- evaluation, chronological, capped at this report's boundary
    evaluation = chronological_evaluation(weeks, schedule, through_week=context.stats_through,
                                          rules=rules)

    notes = list(degradations(sources, context))
    if not scoring.complete:
        notes.append(f"SCORING INPUTS INCOMPLETE — {scoring.reason()}; QB/K projections "
                     f"abstain for the affected group(s)")
    if unresolved:
        notes.append(f"{len(unresolved)} roster id(s) unresolved against the crosswalk — "
                     f"no projection for those rows (never name-matched)")
    if my is None:
        notes.append("owner roster not found in the cached league")
    if plan.abstained:
        notes.append("start/sit: " + plan.abstained)
    if kickoffs is None:
        notes.append("kickoff locks unknown (no schedule): no lineup or waiver move is "
                     "recommended")
    elif not kickoffs.rows_intact:
        notes.append(
            f"kickoff schedule DAMAGED — {kickoffs.summary()}. Players whose "
            f"kickoff could not be established are frozen and named; no time was "
            f"invented for them and no missing team was read as a bye. "
            + "; ".join(kickoffs.problems[:4]))
    elif kickoffs.slate_short > len(kickoffs.declared_bye):
        # Not damage, and not a reason to withhold every action: the rows that
        # arrived are all sound. It only means absence cannot be read as a bye,
        # which `lock_state` already handles one player at a time.
        unexplained = kickoffs.slate_short - len(kickoffs.declared_bye)
        notes.append(
            f"{unexplained} team(s) this schedule knows about have no week-"
            f"{kickoffs.week} game row and no declared bye. A week off and a "
            f"row that never arrived look the same from here, so those "
            f"absences are UNKNOWN rather than byes and any player on those "
            f"teams is frozen.")

    extra: dict[str, list[tuple[str, str]]] = {}
    if kickoffs is not None and not kickoffs.rows_intact:
        # Only genuine row damage gates the whole class. A frame that simply
        # stops at this week withholds nothing by itself — the per-player
        # UNKNOWN lock is the proportionate response, and blanket-withholding
        # on it would fire on every end-of-season render and train the reader
        # to ignore the gate.
        blocker = ("schedules",
                   f"the week-{week} schedule is present but not fully readable "
                   f"({kickoffs.summary()}) — a file's timestamp says when it was "
                   f"written, not whether its contents parse")
        extra["lineup"] = [blocker]
        extra["waiver"] = [blocker]
    # Box scores are judged on coverage, never on age: see gating's module
    # docstring. These reach every action, because every action is scored
    # through a projection built from these frames.
    for action, reasons in box_score_blockers(
            sources, evidence_boundary=context.evidence_boundary).items():
        extra.setdefault(action, []).extend(reasons)
    gate = build_gate(sources, extra=extra)
    notes.extend(gate.notes())

    league_source = next((s for s in sources if s.name == "sleeper_league"), None)
    snapshot_as_of = (
        league_source.as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        if league_source is not None and league_source.as_of is not None
        else "at an UNKNOWN time (the league snapshot carries no as-of)")
    actions = build_actions(plan=plan, board=board, gate=gate, now=now, slots=slots,
                            snapshot_as_of=snapshot_as_of)

    league_id = str(snapshot.get("league_id")
                    or (league.get("league_id") if isinstance(league, Mapping) else "")
                    or "")
    dash = Dashboard(context, tuple(sources), tuple(notes), now, tuple(roster), slots,
                     plan, matchup, matchup_reason, board, evaluation,
                     tuple(unresolved), my_roster_id, gate, actions, None, kickoffs,
                     league_id=league_id)

    # What changed since the previous frozen page. Read-only: the diff never
    # feeds a projection, so yesterday's numbers cannot enter today's evidence.
    prev = previous_archive(archive_root, context.season, now)
    if prev is not None:
        try:
            dash = Dashboard(**{**dash.__dict__,
                                "changes": diff_archives(read_archive(prev),
                                                         dash.record())})
        except (OSError, ValueError) as exc:
            dash = Dashboard(**{**dash.__dict__, "changes": Changes(
                str(prev.name), None, (),
                note=f"previous snapshot could not be read ({type(exc).__name__}); "
                     f"no change list this run")})

    if write_archive_file:
        record = dash.record()
        path = archive_path(context.season, week, now, archive_root, record)
        write_archive(record, path)
        dash = Dashboard(**{**dash.__dict__, "archive": path})
    return dash


def _matchup(opp_id: int, mine: tuple[Player | None, ...],
             theirs: tuple[Player | None, ...]) -> MatchupView:
    def total(line: Sequence[Player | None]) -> tuple[float, float, list[str]]:
        mean = var = 0.0
        missing: list[str] = []
        for p in line:
            if p is None:
                continue                      # an empty slot scores zero
            if not p.projected:
                missing.append(f"{p.name} ({p.position})")
                continue
            mean += float(p.value or 0.0)
            var += p.sd ** 2
        return mean, sqrt(var), missing

    my_mean, my_sd, my_missing = total(mine)
    opp_mean, opp_sd, opp_missing = total(theirs)
    blockers = [m for m in my_missing + opp_missing if "(DST)" not in m and "(DEF)" not in m]
    if blockers:
        pwin, reason, lev = None, ("ABSTAINED — unprojected non-DST starter(s): "
                                   + ", ".join(blockers)), None
    elif not any(p is not None and p.projected for p in mine) or \
            not any(p is not None and p.projected for p in theirs):
        pwin, reason, lev = None, ("ABSTAINED — a lineup has no projected starter at all "
                                   "(empty or unprojected lineup)"), None
    else:
        pwin = round(matchup_win_prob(my_mean, my_sd, opp_mean, opp_sd), 4)
        lev = round(leverage_per_point(my_mean, my_sd, opp_mean, opp_sd), 5)
        reason = ("DST slots excluded on both sides (no DST scoring implementation); "
                  "independence assumed between all starters")
    return MatchupView(opp_id, mine, theirs, my_mean, my_sd, opp_mean, opp_sd, pwin,
                       reason, lev, tuple(my_missing), tuple(opp_missing))


# --------------------------------------------------------------------------
# Actions: what to do, by when, and what to do instead
# --------------------------------------------------------------------------
#: A deadline inside this many hours is happening NOW.
URGENCY_NOW_HOURS = 6.0
#: ...and inside this many is TODAY. Beyond it, THIS WEEK.
URGENCY_TODAY_HOURS = 36.0

#: Ranking of the action kinds. An empty lineup slot and a starter who is not
#: playing cost guaranteed points; a marginal swap does not.
_KIND_RANK = {"empty_slot": 0, "inactive_starter": 1, "verify": 2,
              "swap": 3, "acquire": 4}


@dataclass(frozen=True)
class Action:
    """One thing the owner might do, with the deadline and the fallback.

    `status` is the whole point of this type. An ACTIONABLE action is one
    every input behind it is current for; a WITHHELD action is displayed with
    its comparison intact and its imperative removed, because the page cannot
    stand behind advice built on a five-day-old roster. Nothing here submits
    anything to Sleeper — every action is a description of a move the owner
    makes by hand.
    """

    kind: str
    status: str                       # ACTIONABLE | WITHHELD
    urgency: str                      # NOW | TODAY | THIS WEEK | UNKNOWN | INFO
    headline: str
    detail: str
    deadline: datetime | None
    deadline_note: str
    backup: str
    #: The same card with the imperative taken out. A WITHHELD action must
    #: not read "start him" or "add him" merely because a badge above it says
    #: WITHHELD — the badge is a label and the sentence is the instruction,
    #: and a reader who skims the sentence has been told to act on a stale
    #: input. These say what the last snapshot SHOWED, in the past tense,
    #: and are what `title`/`body` render when the action is withheld.
    neutral_headline: str = ""
    neutral_detail: str = ""
    evidence: tuple[str, ...] = field(default=())
    withheld_reasons: tuple[str, ...] = field(default=())
    verify: tuple[str, ...] = field(default=())
    delta_points: float | None = None
    z: float | None = None
    #: The sleeper ids of the players this action moves, in the order the
    #: sentence names them (bench first for a swap). Carried so a later page
    #: can re-check legality by ID and never by name (rule #3).
    player_ids: tuple[str, ...] = field(default=())
    #: The lineup slot the move fills, for the moves that fill one. Carried
    #: so Game Day can check the incoming player's eligibility for the slot
    #: the record meant, instead of parsing a title.
    slot: str = ""
    #: Tiebreak WITHIN a kind, set by the producer. Acquisitions need it
    #: because a lineup gain and a depth gain are not comparable quantities —
    #: sorting the two together once put a +15 bye-week depth add above a +6
    #: change to this week's starting lineup.
    order: int = 0

    @property
    def actionable(self) -> bool:
        return self.status == "ACTIONABLE"

    @property
    def title(self) -> str:
        """What the card actually says. Imperative only when endorsed."""
        if not self.actionable and self.neutral_headline:
            return self.neutral_headline
        return self.headline

    @property
    def body(self) -> str:
        if not self.actionable and self.neutral_detail:
            return self.neutral_detail
        return self.detail

    @property
    def rank(self) -> tuple:
        order = {"NOW": 0, "UNKNOWN": 1, "TODAY": 2, "THIS WEEK": 3, "INFO": 4}
        return (order.get(self.urgency, 5), _KIND_RANK.get(self.kind, 9),
                self.order, -abs(self.delta_points or 0.0))

    def record(self) -> dict:
        return {"kind": self.kind, "status": self.status, "urgency": self.urgency,
                "title": self.title, "body": self.body,
                "headline": self.headline, "detail": self.detail,
                "deadline": self.deadline.isoformat() if self.deadline else None,
                "deadline_note": self.deadline_note, "backup": self.backup,
                "evidence": list(self.evidence), "verify": list(self.verify),
                "withheld_reasons": list(self.withheld_reasons),
                "delta_points": self.delta_points, "z": self.z,
                "player_ids": list(self.player_ids), "slot": self.slot}


def _edge_sentence(delta: float, z: float | None) -> str:
    """How big the edge is, said truthfully.

    z IS the ratio of the edge to the combined uncertainty of the two
    projections (z = Δ / √(SD²+SD²)), so a z of 0.7 means the edge is 0.7
    TIMES that uncertainty — smaller than it. The card used to read "the edge
    is larger than the combined uncertainty" for everything above the 0.5
    noise floor, which is false across the whole 0.5–1.0 band and was the
    most confident sentence on the page. The floor is unchanged; only the
    claim is, and it now states the ratio rather than asserting a comparison.
    """
    if z is None:
        return (f"{_num(delta, 2)} projected points. The uncertainty of the two "
                f"projections could not be computed, so the size of this edge "
                f"relative to its own error bars is unknown.")
    az = abs(z)
    if az < NOISE_Z:
        return (f"{_num(delta, 2)} projected points, z={_num(z, 2)}. That edge is "
                f"inside the noise of the two projections — either choice is "
                f"defensible and doing nothing is fine.")
    if az < 1.0:
        return (f"{_num(delta, 2)} projected points, z={_num(z, 2)}: the edge is "
                f"{az:.2f}x the combined uncertainty of the two projections, so it "
                f"leans this way but is still SMALLER than that uncertainty. Both "
                f"projections are UNVALIDATED.")
    return (f"{_num(delta, 2)} projected points, z={_num(z, 2)}: the edge is "
            f"{az:.2f}x the combined uncertainty of the two projections, so it is "
            f"larger than that uncertainty. Both projections are UNVALIDATED.")


def _urgency(deadline: datetime | None, now: datetime) -> str:
    """How soon this has to happen. An UNKNOWN deadline sorts as urgently as
    NOW on purpose: not knowing when a game starts is not a reason to relax."""
    if deadline is None:
        return "UNKNOWN"
    hours = (deadline - now).total_seconds() / 3600.0
    if hours <= 0:
        return "NOW"
    if hours <= URGENCY_NOW_HOURS:
        return "NOW"
    if hours <= URGENCY_TODAY_HOURS:
        return "TODAY"
    return "THIS WEEK"


def _deadline_for(players: Sequence[Player | None], now: datetime
                  ) -> tuple[datetime | None, str]:
    """The moment by which a move involving these players must be made: the
    EARLIEST kickoff among them, since the first lock ends the option.

    Returns (None, why) when any of them has an unknown lock state — a
    deadline computed from a partially-known schedule would be a deadline the
    page cannot keep.
    """
    stamps: list[datetime] = []
    for p in players:
        if p is None:
            continue
        if not p.lock_known:
            return None, f"deadline UNKNOWN — {p.lock_reason}"
        if p.locked:
            return None, f"already locked — {p.lock_note}"
        stamp = p.kickoff
        if stamp is None:
            if "BYE" in (p.lock_note or ""):
                continue                 # a bye player never locks
            return None, f"deadline UNKNOWN — no kickoff time for {p.team}"
        stamps.append(stamp)
    if not stamps:
        return None, "no kickoff applies (bye week)"
    first = min(stamps)
    return first, (f"act before {first.astimezone(timezone.utc):%a %d %b %H:%M} UTC "
                   f"(first kickoff among the players involved)")


def _slot_backup(plan: LineupPlan, slot_index: int, exclude: set[str]) -> str:
    """The next legal body for a slot, for when the first choice cannot be
    made (he is hurt after all, or already locked by the time you look)."""
    slot = plan.slots[slot_index]
    bench = [p for p in plan.bench_pool
             if p.sleeper_id not in exclude and eligible(slot, p.position)]
    if not bench:
        return ("no other legal body for this slot on the roster — leaving the "
                "current starter in place is the only legal option")
    best = max(bench, key=lambda p: (p.value or 0.0))
    return (f"if that cannot be done: {best.name} ({best.position}, "
            f"{_num(best.value, 2)} pts) is the next legal option for {slot}")


def build_actions(*, plan: LineupPlan, board: WaiverBoard, gate: ActionGate,
                  now: datetime, slots: Sequence[str],
                  snapshot_as_of: str) -> tuple[Action, ...]:
    """Turn the plan and the board into a ranked to-do list.

    Every action carries the freshness verdict of the inputs it rests on. A
    gated action keeps its comparison and loses its imperative.
    """
    lineup_gate, waiver_gate = gate.gate("lineup"), gate.gate("waiver")
    out: list[Action] = []
    #: How a withheld card refers to the moment its numbers describe. Every
    #: neutral sentence is anchored to it, so "was" has a date attached
    #: rather than being a vague hedge.
    seen = ("as of the league snapshot taken " + snapshot_as_of
            if not snapshot_as_of.startswith("at an UNKNOWN")
            else "as of a league snapshot that carries no time")

    def status_of(g) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
        if g.allowed:
            return "ACTIONABLE", (), ()
        return "WITHHELD", (g.why(),), g.verify()

    if plan.abstained:
        out.append(Action(
            "verify", "WITHHELD", "UNKNOWN",
            "No lineup move can be shown legal this week",
            plan.abstained, None, "deadline UNKNOWN — the schedule is not readable",
            "Set your lineup in Sleeper, which knows the real kickoff times.",
            withheld_reasons=(plan.abstained,),
            verify=("confirm the kickoff time for every game you are relying on",)))

    l_status, l_why, l_verify = status_of(lineup_gate)

    # 1. Empty starting slots and starters who are not playing.
    for i, cur in enumerate(plan.current):
        best = plan.best[i] if i < len(plan.best) else None
        if cur is None:
            deadline, note = _deadline_for([best], now)
            who = (f"{best.name} ({best.position}, {_num(best.value, 2)} pts)"
                   if best else "no legal body on the roster")
            out.append(Action(
                "empty_slot", l_status, _urgency(deadline, now),
                f"{slots[i]} is EMPTY — it scores 0 as it stands",
                f"Fill it with {who}.", deadline, note,
                _slot_backup(plan, i, {best.sleeper_id} if best else set()),
                neutral_headline=f"{slots[i]} was EMPTY in the last snapshot",
                neutral_detail=(f"{slots[i]} held no player {seen}, and an "
                                f"unfilled slot scores 0. The best legal bench "
                                f"option in that same snapshot was {who}. "
                                f"Whether the slot is still empty is not "
                                f"something this page can see."),
                evidence=("an unfilled slot scores nothing; this is not a "
                          "projection question",),
                withheld_reasons=l_why, verify=l_verify,
                player_ids=(best.sleeper_id,) if best else (), slot=slots[i]))
            continue
        if cur.projection.is_withheld and cur.movable:
            replacement = best if (best and best.sleeper_id != cur.sleeper_id) else None
            deadline, note = _deadline_for([cur, replacement], now)
            why = "; ".join(cur.projection.reasons)
            detail = (f"{cur.name} is projected 0 ({why}). "
                      + (f"Start {replacement.name} ({replacement.position}, "
                         f"{_num(replacement.value, 2)} pts) instead."
                         if replacement else
                         "No projected bench player is eligible for this slot, so "
                         "the roster has no legal replacement."))
            neutral = (f"{slots[i]}: {cur.name} was projected 0 in the last "
                       f"snapshot ({why}), and was in the lineup {seen}."
                       + (f" The best legal alternative in that snapshot was "
                          f"{replacement.name} ({replacement.position}, "
                          f"{_num(replacement.value, 2)} pts)."
                          if replacement else
                          " No projected bench player was eligible for the slot."))
            out.append(Action(
                "inactive_starter", l_status, _urgency(deadline, now),
                f"{slots[i]}: {cur.name} is in your lineup and is NOT playing",
                detail, deadline, note,
                _slot_backup(plan, i, {cur.sleeper_id} |
                             ({replacement.sleeper_id} if replacement else set())),
                neutral_headline=(f"{slots[i]}: {cur.name} was in the lineup and "
                                  f"projected 0 in the last snapshot"),
                neutral_detail=neutral,
                evidence=(cur.availability,) if cur.availability else (),
                withheld_reasons=l_why, verify=l_verify,
                delta_points=(float(replacement.value or 0.0) if replacement else None),
                player_ids=((replacement.sleeper_id, cur.sleeper_id) if replacement
                            else (cur.sleeper_id,)), slot=slots[i]))

    # 2. Favourable swaps the optimizer found, above the noise floor.
    for a in plan.alternatives:
        if a.delta_points <= 0 or a.starter is None:
            continue
        if a.starter.projection.is_withheld:
            continue                       # already reported as inactive_starter
        deadline, note = _deadline_for([a.bench, a.starter], now)
        noise = a.within_noise
        out.append(Action(
            "swap", l_status, "INFO" if noise else _urgency(deadline, now),
            (f"Optional: {a.bench.name} over {a.starter.name} at {a.slot}"
             if noise else
             f"{a.slot}: start {a.bench.name} over {a.starter.name}"),
            _edge_sentence(a.delta_points, a.z),
            deadline, note,
            _slot_backup(plan, a.slot_index, {a.bench.sleeper_id,
                                              a.starter.sleeper_id}),
            neutral_headline=(f"{a.slot}: the last snapshot projected "
                              f"{a.bench.name} above {a.starter.name}"),
            neutral_detail=(f"{_edge_sentence(a.delta_points, a.z)} Those numbers "
                            f"are {seen} and are not a recommendation to make "
                            f"the change now."),
            evidence=(f"{a.bench.name}: {_num(a.bench.value, 2)} ± {_num(a.bench.sd, 2)}",
                      f"{a.starter.name}: {_num(a.starter.value, 2)} ± "
                      f"{_num(a.starter.sd, 2)}"),
            withheld_reasons=l_why, verify=l_verify,
            delta_points=a.delta_points, z=a.z,
            player_ids=(a.bench.sleeper_id, a.starter.sleeper_id), slot=a.slot))

    # 3. Acquisitions. Never an imperative: eligibility is never proven here.
    w_status, w_why, w_verify = status_of(waiver_gate)
    # Short by design: the full shortlist is a section of its own. The board is
    # already ranked lineup-gain first, and that order is preserved here rather
    # than re-sorted on a number that mixes two different kinds of gain.
    for i, u in enumerate(board.upgrades[:2]):
        elig = eligibility(snapshot_as_of=snapshot_as_of)
        deadline, note = (None, "waiver timing NOT established — " + elig.verify)
        out.append(Action(
            "acquire", w_status, "INFO",
            f"Consider claiming {u.add.name} ({u.add.position})",
            (f"{u.describe()}. Cost of the drop: {u.drop.name} "
             f"({u.drop.position}, {_num(u.drop.value, 2)} projected this week). "
             f"Eligibility {elig.state}: this page cannot tell a free agent from a "
             f"player on waivers."),
            deadline, note,
            f"if {u.add.name} is claimed by someone else, the next candidate on the "
            f"shortlist below applies with the same drop",
            neutral_headline=(f"The last snapshot ranked {u.add.name} "
                              f"({u.add.position}) above your cheapest legal drop"),
            # Deliberately NOT `u.describe()`. That sentence opens "add X,
            # drop Y", which is an instruction, and an instruction inside a
            # withheld card is the exact failure the neutral wording exists
            # to prevent — the badge says no advice is being given while the
            # first words of the body give some.
            neutral_detail=(f"{seen}, {u.add.name} ({u.add.position}) projected "
                            f"{_num(u.add.value, 2)} and the cheapest legal drop "
                            f"beside him, {u.drop.name} ({u.drop.position}), "
                            f"projected {_num(u.drop.value, 2)}; the pair was worth "
                            f"{_num(u.lineup_gain or u.depth_gain, 2)} pts "
                            + (f"to the best lineup via {u.slot}"
                               if u.kind == "lineup" else "to depth, with that "
                               "week's lineup unchanged")
                            + f". Eligibility {elig.state} in that snapshot too: "
                            f"this page cannot tell a free agent from a player on "
                            f"waivers, and it does not know whether "
                            f"{u.add.name} is still unrostered."),
            evidence=elig.basis, withheld_reasons=w_why,
            verify=tuple(w_verify) + (elig.verify,),
            delta_points=u.lineup_gain or u.depth_gain, order=i,
            player_ids=(u.add.sleeper_id, u.drop.sleeper_id)))

    out.sort(key=lambda a: a.rank)
    return tuple(out)


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
_CSS = """
:root{--bg:#fafaf7;--fg:#1c1c1c;--muted:#5d5d5d;--line:#d9d6ce;--card:#ffffff;
--ok:#2f7d32;--warn:#b26a00;--bad:#b3261e;--info:#2a5db0;--chip:#eeece6;}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#15161a;--fg:#e8e6e1;
--muted:#a3a19b;--line:#33363d;--card:#1d1f25;--chip:#2a2d34;--ok:#6fbf73;--warn:#e0a24a;
--bad:#ef6f66;--info:#7fa6e8;}}
:root[data-theme="dark"]{--bg:#15161a;--fg:#e8e6e1;--muted:#a3a19b;--line:#33363d;
--card:#1d1f25;--chip:#2a2d34;--ok:#6fbf73;--warn:#e0a24a;--bad:#ef6f66;--info:#7fa6e8;}
*{box-sizing:border-box}html,body{max-width:100%}
body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
overflow-wrap:anywhere;word-break:break-word}
main{max-width:1180px;margin:0 auto}h1{font-size:22px;margin:0 0 4px}h2{font-size:17px;
margin:28px 0 8px;border-bottom:1px solid var(--line);padding-bottom:4px}
.sub{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);
border-radius:8px;padding:12px 14px;margin:10px 0}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;
font-weight:600;border:1px solid var(--line);background:var(--chip)}
.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}.info{color:var(--info)}
.banner{border-left:5px solid var(--bad);padding:8px 12px;margin:10px 0;background:var(--card)}
.banner.ok{border-left-color:var(--ok)}
table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;
padding:5px 6px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);white-space:nowrap}
td{overflow-wrap:anywhere}td.num{text-align:right;
font-variant-numeric:tabular-nums;white-space:nowrap}
.wrap{overflow-x:auto}details{margin:4px 0}summary{cursor:pointer;color:var(--info)}
code,pre{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
overflow-wrap:anywhere;word-break:break-all}
pre{background:var(--chip);padding:8px;border-radius:6px;overflow-x:auto;white-space:pre-wrap}
ul{margin:6px 0;padding-left:20px}.small{font-size:12px}
.kpi{display:flex;flex-wrap:wrap;gap:10px}.kpi div{flex:1 1 160px;background:var(--chip);
border-radius:6px;padding:8px 10px}.kpi b{display:block;font-size:18px}
.act{border:1px solid var(--line);border-left:5px solid var(--info);border-radius:8px;
background:var(--card);padding:12px 14px;margin:10px 0}
.act h3{font-size:16px;margin:6px 0 4px;line-height:1.3}
.act p{margin:4px 0}.act .why{color:var(--muted);font-size:12px}
.act-NOW{border-left-color:var(--bad)}.act-TODAY{border-left-color:var(--warn)}
.act-UNKNOWN{border-left-color:var(--warn)}.act-INFO{border-left-color:var(--line)}
.act.withheld{border-left-style:dashed;background:var(--bg)}
.bar{display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:2px}
.badge.now{background:var(--bad);color:#fff;border-color:transparent}
.badge.today{background:var(--warn);color:#fff;border-color:transparent}
.badge.held{background:transparent;color:var(--bad);border-color:var(--bad)}
.badge.go{background:transparent;color:var(--ok);border-color:var(--ok)}
.deadline{font-weight:600}.backup{color:var(--muted);font-size:13px}
.gatebox{border-left:5px solid var(--warn);padding:8px 12px;margin:8px 0;background:var(--chip)}
@media (max-width:560px){
 body{padding:10px 12px;font-size:15px}
 main{max-width:100%}
 h1{font-size:19px}h2{font-size:16px;margin:22px 0 6px}
 .card{padding:10px 11px;border-radius:6px}
 .act{padding:10px 11px}.act h3{font-size:15px}
 .kpi{gap:6px}.kpi div{flex:1 1 calc(50% - 6px);padding:6px 8px}.kpi b{font-size:16px}
 table{font-size:12px}th,td{padding:4px 5px}
 summary{padding:6px 0}
}
@media print{.act{break-inside:avoid}details{display:block}details>*{display:block}}
"""


def _e(x: object) -> str:
    return html.escape("" if x is None else str(x))


def _num(v: float | None, nd: int = 1, plus: bool = False) -> str:
    if v is None:
        return "—"
    return f"{v:+.{nd}f}" if plus else f"{v:.{nd}f}"


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]],
           numeric: Sequence[int] = ()) -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = []
    for r in rows:
        cells = "".join(
            f"<td class=\"num\">{c}</td>" if i in numeric else f"<td>{c}</td>"
            for i, c in enumerate(r))
        body.append(f"<tr>{cells}</tr>")
    return f"<div class=\"wrap\"><table><thead><tr>{head}</tr></thead><tbody>{''.join(body)}</tbody></table></div>"


def _status_class(s: Status) -> str:
    return {"fresh": "ok", "stale": "warn", "missing": "bad"}[s.value]


def _proj_cell(p: Player) -> str:
    pr = p.projection
    if not pr.usable:
        return f"<span class=\"bad\">abstain</span><div class=\"small sub\">{_e('; '.join(pr.reasons))}</div>"
    txt = f"<b>{_num(pr.mean)}</b> ± {_num(pr.sd)}"
    if "model_mean" in pr.inputs:
        mm = pr.inputs.get("model_mean")
        txt += f" <span class=\"warn\">(withheld; model {_num(mm)})</span>"
    return txt + f"<details><summary>why</summary><pre>{_e(pr.explain())}</pre></details>"


def _player_rows(players: Sequence[Player], slots_of: Mapping[str, str] | None = None
                 ) -> list[list[str]]:
    rows = []
    for p in players:
        lock = (f"<span class=\"warn\">{_e(p.lock_note)}</span>" if p.locked
                else f"<span class=\"small sub\">{_e(p.lock_note)}</span>")
        flags = "".join(f"<div class=\"warn small\">{_e(f)}</div>" for f in p.flags)
        slot = (slots_of or {}).get(p.sleeper_id, p.lineup)
        rows.append([_e(slot), _e(p.name), _e(p.position), _e(p.team),
                     _proj_cell(p), _e(p.availability) + flags, lock])
    return rows


def _action_card(a: "Action") -> str:
    held = not a.actionable
    bar = [f"<span class=\"badge {'now' if a.urgency == 'NOW' else 'today' if a.urgency in ('TODAY', 'UNKNOWN') else ''}\">"
           f"{_e(a.urgency)}</span>"]
    bar.append(f"<span class=\"badge {'held' if held else 'go'}\">{_e(a.status)}</span>")
    out = [f"<div class=\"act act-{_e(a.urgency.replace(' ', '-'))}"
           f"{' withheld' if held else ''}\">",
           "<div class=\"bar\">" + "".join(bar) + "</div>"]
    if held:
        # Said before the card's own wording, so the frame is set even for a
        # reader who never reaches the explanation underneath.
        out.append("<p class=\"why\"><b>Last known picture — no action is being "
                   "recommended.</b></p>")
    out += [
           f"<h3>{_e(a.title)}</h3>",
           f"<p>{_e(a.body)}</p>",
           f"<p class=\"deadline\">{_e(a.deadline_note)}</p>"]
    if a.backup:
        out.append(f"<p class=\"backup\">Backup — {_e(a.backup)}</p>")
    if held:
        out.append("<p class=\"why\"><b class=\"bad\">Not advice right now.</b> "
                   + _e("; ".join(a.withheld_reasons))
                   + ". The comparison above is the last known picture, kept so it is "
                     "not lost; it is not a statement about the situation now.</p>")
    if a.verify:
        out.append("<p class=\"why\">Verify first: "
                   + _e("; ".join(a.verify)) + ".</p>")
    if a.evidence:
        out.append("<details><summary>what this rests on</summary><ul class=\"small\">"
                   + "".join(f"<li>{_e(x)}</li>" for x in a.evidence) + "</ul></details>")
    out.append("</div>")
    return "".join(out)


def render_html(d: Dashboard, *, include_names: bool = True) -> str:
    ctx = d.context
    m = d.matchup
    title = f"Week {ctx.report_week} decision dashboard"
    if include_names:
        title += f" — {d.league_name}"
    out: list[str] = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        f"<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"><title>{_e(title)}</title>",
        f"<style>{_CSS}</style></head><body><main>",
        f"<h1>{_e(title)}</h1>",
        f"<div class=\"sub\">{_e(ctx.headline())} · evidence boundary week {ctx.evidence_boundary} · "
        f"generated {_e(d.generated.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC'))}</div>",
        f"<div class=\"sub small\">{_e(BASELINE_LABEL)}</div>",
        # The one Game Day entry in the product: the live-scoring page is
        # built beside this file by scripts/weekly/gameday.py.
        "<p><a href=\"gameday_latest.html\"><b>Game Day →</b></a> <span class=\"small sub\">"
        "the Sunday screen: platform score, who is yet to play, what is still legal, "
        "what changed, and what this board advised (opens the file built beside "
        "this one)</span></p>",
    ]
    if d.degraded:
        out.append("<div class=\"banner\"><b class=\"bad\">DEGRADED</b> — one or more inputs "
                   "are stale, missing or withheld. Every affected number is blank or "
                   "labelled below; nothing is filled in.<ul>"
                   + "".join(f"<li>{_e(n)}</li>" for n in d.notes) + "</ul></div>")
    else:
        out.append("<div class=\"banner ok\"><b class=\"ok\">All inputs current.</b></div>")

    # ---------------------------------------------------------------- 1. do
    live = [a for a in d.actions if a.actionable]
    out.append("<h2>1. This week — what to do</h2>")
    if not d.actions:
        out.append("<div class=\"card\"><p class=\"ok\">Nothing to do. The current lineup "
                   "is already the best legal one the projections can find, and no "
                   "available player beats a droppable roster player.</p></div>")
    else:
        if not live:
            out.append("<div class=\"gatebox\"><b class=\"bad\">No action is endorsed on "
                       "this data.</b> Every item below is WITHHELD: the inputs behind it "
                       "are stale or unreadable, so what follows is the last known "
                       "picture rather than current advice. The comparisons are kept "
                       "deliberately — old information is still information, as long as "
                       "it is labelled as old.</div>")
        for a in d.actions:
            out.append(_action_card(a))
    out.append("<p class=\"small sub\">Nothing on this page is ever submitted to Sleeper. "
               "Every action is a move the owner makes by hand, and every deadline is "
               "read from the schedule — where the schedule could not be read, the "
               "deadline says UNKNOWN rather than guessing a kickoff.</p>")

    # ---------------------------------------------------- 2. inputs & gating
    out.append("<h2>2. Inputs, and what they are good enough for</h2><div class=\"card\">")
    rows = [[f"<span class=\"{_status_class(s.status)}\">{_e(s.status.value.upper())}</span>",
             _e(s.name), _e(s.as_of.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if s.as_of else 'never'),
             _e(s.coverage()), _e(s.reason)] for s in d.sources]
    out.append(_table(["status", "source", "as-of", "covers", "reason"], rows))
    grows = []
    for action in ACTIONS:
        g = d.gate.gate(action)
        grows.append([_e(action),
                      ("<span class=\"ok\">SUPPORTED</span>" if g.allowed
                       else "<span class=\"bad\">WITHHELD</span>"),
                      _e(g.why() or "every input this rests on is current"),
                      _e("; ".join(g.verify()) or "—")])
    out.append("<p class=\"small sub\">A freshness label is not a gate. This is the gate:</p>")
    out.append(_table(["action", "state", "because", "verify"], grows))
    out.append("<p class=\"small sub\">Box-score sources (weekly_stats, snap_counts) are "
               "deliberately not gates: they are stale by construction for most of every "
               "week, which the evidence boundary already states. A scheduled cloud "
               "refresh keeps the SNAPSHOT current; it does not make injury news timely, "
               "and nothing here should be read as a claim that it does.</p></div>")

    # -------------------------------------------------------- 3. what changed
    out.append("<h2>3. Since the last snapshot</h2><div class=\"card\">")
    ch = d.changes
    if ch is None:
        out.append("<p class=\"sub\">No earlier snapshot to compare against — this is the "
                   "first page built for this season.</p>")
    elif not ch.any:
        out.append(f"<p class=\"ok\">{_e(ch.note)} (previous: {_e(ch.previous)}).</p>")
    else:
        out.append(f"<p class=\"small sub\">against the snapshot of {_e(ch.previous)}"
                   + (f", week {ch.previous_week}" if ch.previous_week else "") + "</p><ul>")
        for c in ch.items[:40]:
            cls = {"availability": "warn", "lock": "warn", "roster": "info",
                   "freshness": "sub"}.get(c.kind, "")
            out.append(f"<li><span class=\"badge\">{_e(c.kind)}</span> "
                       f"<span class=\"{cls}\">{_e(c.subject)}</span>: {_e(c.detail)}</li>")
        out.append("</ul>")
        if len(ch.items) > 40:
            out.append(f"<p class=\"small sub\">{len(ch.items) - 40} further change(s) in "
                       f"the archive record.</p>")
    out.append("<p class=\"small sub\">Transitions only (rule #8): a projection has to move "
               f"at least {MOVE_POINTS} points to be listed, so one more box score does not "
               "read as news.</p></div>")

    # ------------------------------------------------------------ 4. start/sit
    out.append("<h2>4. Start / sit — the comparisons behind those actions</h2><div class=\"card\">")
    plan = d.plan
    if not d.gate.allows("lineup"):
        out.append(f"<div class=\"gatebox\">{_e(d.gate.banner('lineup'))}</div>")
    if plan.abstained:
        out.append(f"<p class=\"bad\"><b>ABSTAINED:</b> {_e(plan.abstained)}</p>")
    else:
        out.append("<div class=\"kpi\">"
                   f"<div>current lineup<b>{_num(plan.current_points)}</b></div>"
                   f"<div>best legal lineup<b>{_num(plan.best_points)}</b></div>"
                   f"<div>improvement<b>{_num(plan.improvement, 1, True)}</b></div></div>")
        changes = plan.changes()
        if changes:
            out.append("<p><b>Best legal lineup differs in:</b></p><ul>")
            for i, c, b in changes:
                out.append(f"<li><b>{_e(d.slots[i])}</b>: {_e(c.name if c else 'EMPTY')} → "
                           f"{_e(b.name if b else 'EMPTY')} "
                           f"({_num(c.value if c else 0.0)} → {_num(b.value if b else 0.0)})</li>")
            out.append("</ul>")
        else:
            out.append("<p class=\"ok\">The current lineup is already the best legal lineup "
                       "the projections can find.</p>")
        if plan.alternatives:
            rows = []
            for a in plan.alternatives:
                verdict = ("within noise" if a.within_noise else
                           "favoured" if a.delta_points > 0 else "not favoured")
                cls = "sub" if a.within_noise else ("ok" if a.delta_points > 0 else "bad")
                rows.append([_e(a.bench.name) + f" <span class=\"sub\">({_e(a.bench.position)})</span>",
                             _e(a.slot), _e(a.starter.name if a.starter else "EMPTY"),
                             _num(a.delta_points, 2, True), _num(a.z, 2, True),
                             f"<span class=\"{cls}\">{verdict}</span>"])
            out.append(_table(["bench player", "into", "for", "Δ pts", "z", "read"],
                              rows, numeric=(3, 4)))
        else:
            out.append("<p class=\"sub\">No bench player can be shown to legally enter a "
                       "slot: each is unprojected, on IR, locked, or his kickoff could "
                       "not be established.</p>")
    if plan.frozen:
        out.append("<details><summary>frozen (not moved) and why</summary><ul>"
                   + "".join(f"<li>{_e(p.name)} ({_e(p.position)}, {_e(p.lineup)}): {_e(r)}</li>"
                             for p, r in plan.frozen) + "</ul></details>")
    out.append("<p class=\"small sub\">z = Δ / √(SD²+SD²) — the edge expressed in "
               "multiples of the combined uncertainty of the two projections. "
               "|z| &lt; 0.5 is read as within noise here; note that anything below "
               "|z| = 1 still means the edge is SMALLER than that uncertainty, so a "
               "\"favoured\" row at z = 0.7 is a lean, not a finding. Both "
               "projections are UNVALIDATED. A swap is listed only if Sleeper would "
               "accept it now: position-eligible, both players proven unlocked, nobody "
               "promoted off IR.</p></div>")

    # --------------------------------------------------------- 5. acquisitions
    out.append("<h2>5. Acquisitions — shortlist, with what each one costs</h2><div class=\"card\">")
    b = d.board
    if not d.gate.allows("waiver"):
        out.append(f"<div class=\"gatebox\">{_e(d.gate.banner('waiver'))}</div>")
    out.append(f"<p class=\"small sub\">pool {b.pool_size} unrostered players; {b.evaluated} "
               f"evaluated for a lineup change; {b.unprojected} without a projection "
               f"(counted, never ranked).</p>")
    if b.abstained:
        out.append(f"<p class=\"bad\"><b>ABSTAINED:</b> {_e(b.abstained)}</p>")
    elif not b.upgrades:
        out.append("<p class=\"ok\">No available player projects above a droppable roster "
                   "player this week.</p>")
    else:
        rows = []
        for u in b.upgrades[:12]:
            rows.append([_e(u.add.name) + f" <span class=\"sub\">({_e(u.add.position)}, {_e(u.add.team)})</span>",
                         _num(u.add.value),
                         "<span class=\"badge held\">UNVERIFIED</span>",
                         _e(u.drop.name) + f" <span class=\"sub\">({_e(u.drop.position)}, {_e(u.drop.lineup)})</span>",
                         _num(u.drop.value), _e(u.kind.upper()), _e(u.slot or "—"),
                         _num(u.lineup_gain, 2, True), _num(u.depth_gain, 2, True)])
        out.append(_table(["add", "proj", "addable?", "drop (the cost)", "proj", "kind",
                           "enters", "Δ lineup", "Δ depth"], rows, numeric=(1, 4, 7, 8)))
        out.append("<p class=\"small warn\">\"Addable?\" is UNVERIFIED for every row and "
                   "cannot be anything else from this cache: the snapshot proves only that "
                   "the player is on no roster at its as-of. Whether he is a free agent or "
                   "sitting on waivers, and when a claim would process, live in Sleeper\u2019s "
                   "transactions feed, which this repo does not pull. Check in the app "
                   "before bidding.</p>")
        if b.droppable:
            out.append("<p class=\"small sub\">Drop candidates, cheapest to lose first: "
                       + ", ".join(f"{_e(p.name)} ({_num(p.value)})" for p in b.droppable[:5])
                       + " — one week of projected points, not roster value.</p>")
    if b.protected:
        out.append("<details open><summary><b>Protected from the drop list ("
                   + str(len(b.protected)) + ")</b></summary><ul class=\"small\">"
                   + "".join(f"<li><b>{_e(p.name)}</b> ({_e(p.position)}, {_e(p.lineup)}): {_e(r)}</li>"
                             for p, r in b.protected) + "</ul>"
                   "<p class=\"small sub\">These are never offered as an automatic drop. A "
                   "player projected 0 because he is hurt, suspended or on a bye is not a "
                   "player worth 0, and pricing him properly needs a rest-of-season model "
                   "this repo does not have and will not fake (rule #5).</p></details>")
    for n in b.notes:
        out.append(f"<p class=\"small sub\">{_e(n)}</p>")
    out.append("</div>")

    # ------------------------------------------------------------- 6. roster
    slot_of = {}
    for i, p in enumerate(d.plan.current):
        if p is not None:
            slot_of[p.sleeper_id] = f"{d.slots[i]}"
    out.append("<h2>6. Roster projections</h2><div class=\"card\">")
    order = sorted(d.roster, key=lambda p: ({"START": 0, "BENCH": 1, "IR": 2}.get(p.lineup, 3),
                                            -(p.value or -1)))
    out.append(_table(["slot", "player", "pos", "nfl", "projection (mean ± SD)",
                       "availability (designation + source)", "lock"],
                      _player_rows(order, slot_of)))
    if d.unresolved_ids:
        out.append("<p class=\"small warn\">Unresolved sleeper ids (no projection, never name-matched): "
                   + ", ".join(f"<code>{_e(i)}</code>" for i in d.unresolved_ids) + "</p>")
    out.append("<p class=\"small sub\">SD = mean × positional CV from QUANT_FOUNDATIONS §2.1 "
               "(a literature recommendation, not a fit). Questionable/Doubtful are flagged, "
               "never zeroed (rule #11). Withheld = will not play (bye / Out / IR), and the "
               "model's own number is kept next to it.</p></div>")

    # ------------------------------------------------------------ 7. matchup
    out.append("<h2>7. Matchup — context, not advice</h2><div class=\"card\">")
    if m is None:
        out.append(f"<p class=\"warn\"><b>No matchup view:</b> {_e(d.matchup_reason)}</p>")
    else:
        out.append("<div class=\"kpi\">"
                   f"<div>my lineup<b>{_num(m.my_mean)} ± {_num(m.my_sd)}</b></div>"
                   f"<div>roster #{m.opponent_roster_id}<b>{_num(m.opp_mean)} ± {_num(m.opp_sd)}</b></div>"
                   f"<div>margin<b>{_num(m.margin, 1, True)}</b></div></div>")
        out.append("<details><summary>uncalibrated win probability (not used to rank any "
                   "action above)</summary>")
        pw = ("<span class=\"bad\">abstained</span>" if m.pwin is None
              else f"<b>{m.pwin:.0%}</b>")
        out.append("<div class=\"kpi\">"
                   f"<div>P(win) <span class=\"badge warn\">UNCALIBRATED</span>{pw}</div>"
                   f"<div>leverage<b>{'—' if m.leverage is None else f'{m.leverage*100:.2f} pp/pt'}</b></div>"
                   "</div>")
        out.append(f"<p class=\"small sub\">{_e(m.pwin_reason)}. {_e(m.label)}.</p>")
        out.append("<p class=\"small warn\">Rule #7 denominates decisions in ΔP(win), and "
                   "this page deliberately does not yet: the number below has never been "
                   "checked against a resolved matchup, so ranking start/sit calls by it "
                   "would dress an unvalidated projection in a second unvalidated layer. "
                   "The actions above are ranked by deadline and by projected points.</p>")
        if d.plan.alternatives and m.leverage is not None:
            rows = [[_e(a.bench.name), _e(a.slot), _num(a.delta_points, 2, True),
                     f"{(_dpwin(m, a.delta_points) or 0.0)*100:+.2f} pp"]
                    for a in d.plan.alternatives[:10]]
            out.append(_table(["bench player", "into", "Δ pts", "ΔP(win) (uncalibrated)"],
                              rows, numeric=(2, 3)))
        out.append("</details>")
        if m.my_unprojected or m.opp_unprojected:
            out.append("<p class=\"small warn\">Unprojected starters — mine: "
                       f"{_e(', '.join(m.my_unprojected) or 'none')}; theirs: "
                       f"{_e(', '.join(m.opp_unprojected) or 'none')}</p>")
        out.append("<details><summary>opponent lineup (roster #%s)</summary>" % _e(m.opponent_roster_id))
        opp_rows = _player_rows([p for p in m.opp_starters if p is not None],
                                {p.sleeper_id: d.slots[i] for i, p in enumerate(m.opp_starters) if p})
        out.append(_table(["slot", "player", "pos", "nfl", "projection", "availability", "lock"], opp_rows))
        out.append("</details>")
    out.append("</div>")

    # --------------------------------------------------------- 8. track record
    ev = d.evaluation
    out.append("<h2>8. How the baseline has fared (chronological, out-of-sample)</h2><div class=\"card\">")
    out.append(f"<p><b>{_e(ev.verdict())}</b></p>")
    if ev.n:
        out.append(_table(["predictor", "n", "MAE", "RMSE", "bias"],
                          [[_e(s.name), str(s.n), _num(s.mae, 2), _num(s.rmse, 2), _num(s.bias, 2, True)]
                           for s in ev.scores], numeric=(1, 2, 3, 4)))
        if ev.by_position:
            out.append("<details><summary>baseline by position</summary>"
                       + _table(["position", "n", "MAE", "RMSE", "bias"],
                                [[_e(k), str(v.n), _num(v.mae, 2), _num(v.rmse, 2), _num(v.bias, 2, True)]
                                 for k, v in sorted(ev.by_position.items())], numeric=(1, 2, 3, 4))
                       + "</details>")
    out.append("<p class=\"small sub\">P(win) calibration: "
               f"<b class=\"warn\">{'validated' if ev.pwin_calibrated else 'NOT validated'}</b> — "
               "every P(win) on this page is an uncalibrated closed-form number.</p>")
    for n in ev.notes:
        out.append(f"<p class=\"small sub\">{_e(n)}</p>")
    out.append("</div>")

    # ------------------------------------------------------------- 9. archive
    out.append("<h2>9. Decision-time archive</h2><div class=\"card\">")
    if d.archive:
        out.append(f"<p>Written to <code>{_e(d.archive)}</code> — the projections, lineup, "
                   "alternatives, upgrades, the freshness gate and which actions were "
                   "actually endorsed, as they were on this page. Grading reads that file "
                   "and the week\u2019s actuals only "
                   "(<code>gridiron.decisions.grade_archive</code>), so a grade can never "
                   "see data that arrived after the decision.</p>")
    else:
        out.append("<p class=\"sub\">Archive not written (dry run).</p>")
    out.append("</div></main></body></html>")
    return "\n".join(out)
