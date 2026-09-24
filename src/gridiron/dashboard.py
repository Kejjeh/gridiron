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
import secrets
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
from gridiron.gating import (ACTIONS, ActionGate, box_score_blockers, valid_until,
                             build_gate)
from gridiron.lineup import (
    NOISE_Z, KickoffIndex, LineupPlan, Player, eligible, kickoff_index, lock_state,
    plan_lineup, slot_order,
)
from gridiron.projection import (
    BASELINE_LABEL, Projection, abstain, build_evidence, project,
)
from gridiron.radar import RadarChanges, diff_radar, move_deadline, radar_record
from gridiron.scoring import ScoringCoverage
from gridiron import theme
from gridiron.waivers import (
    BELOW, COVERAGE, LINEUP, RESEARCH, Candidate, WaiverBoard, available_ids,
    build_board, drop_rule, eligibility, pool_players,
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
    #: The week-transition view: what this page's advice is FOR, what can
    #: still be done, what waits for next week's inputs.
    next: "NextDecision | None" = None
    #: The whole available pool (projected or not) and every id any roster
    #: holds, so the radar block can be diffed by id later.
    pool: tuple[Player, ...] = field(default=())
    owned_ids: tuple[str, ...] = field(default=())
    designations: Mapping[str, str] = field(default_factory=dict)
    #: What the radar found since the previous record, like for like.
    radar_changes: RadarChanges | None = None
    #: The snapshot as-of, as the page states it.
    snapshot_as_of: str = ""

    @property
    def degraded(self) -> bool:
        return bool(self.notes) or any(s.status is not Status.FRESH for s in self.sources)

    # ------------------------------------------------------------ archive
    def desk(self) -> "Desk":
        """The Action Desk: this page's actions, prioritised for the first
        screen (see `action_desk`). Derived on demand, never archived."""
        until, _ = valid_until(self.sources, [a for a in ("lineup", "waiver")
                                              if self.gate.allows(a)], self.generated)
        src = next((x for x in self.sources if x.name == "sleeper_players"), None)
        des = (src.as_of.astimezone(timezone.utc).strftime("%a %d %b %H:%M UTC")
               if src is not None and src.as_of else "at an unknown time")
        return action_desk(self.actions, self.gate, valid_until=until,
                           designations_as_of=des, snapshot_as_of=self.snapshot_as_of)

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
                "kind": u.kind,
                "displaces_id": u.displaces.sleeper_id if u.displaces else None,
                "drop_alternatives": [{"drop_id": d.sleeper_id, "lineup_gain": g}
                                      for d, g in u.alternatives]}
                for u in self.board.upgrades],
            "waiver_abstained": self.board.abstained,
            "protected_from_drop": [
                {"sleeper_id": p.sleeper_id, "name": p.name, "reason": r}
                for p, r in self.board.protected],
            "actions": [a.record() for a in self.actions],
            "actionable": sum(1 for a in self.actions if a.actionable),
            "conditional": sum(1 for a in self.actions if a.conditional),
            "watchlist": [{"add": player(w.add), "versus_id": w.versus.sleeper_id,
                           "gap": w.gap} for w in self.board.watchlist],
            "coverage": list(self.board.coverage),
            "next": None if self.next is None else self.next.record(),
            "radar": radar_record(self.board, pool=self.pool, owned_ids=self.owned_ids,
                                  snapshot_as_of=self.snapshot_as_of,
                                  sources=self.sources, designations=self.designations),
            "radar_changes": (None if self.radar_changes is None
                              else self.radar_changes.record()),
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
                            snapshot_as_of=snapshot_as_of, roster=roster)
    nxt = next_decision(context=context, roster=roster, plan=plan, schedule=schedule,
                        sources=sources, snapshot_as_of=snapshot_as_of, now=now,
                        actions=actions)

    league_id = str(snapshot.get("league_id")
                    or (league.get("league_id") if isinstance(league, Mapping) else "")
                    or "")
    owned_ids = tuple(sorted({normalize_id(sid) for r in rosters
                              for sid in (r.get("players") or []) if normalize_id(sid)}))
    designations = {p.sleeper_id: str((sleeper_players.get(p.sleeper_id) or {})
                                      .get("injury_status") or "") for p in pool}
    dash = Dashboard(context, tuple(sources), tuple(notes), now, tuple(roster), slots,
                     plan, matchup, matchup_reason, board, evaluation,
                     tuple(unresolved), my_roster_id, gate, actions, None, kickoffs,
                     league_id=league_id, next=nxt, pool=tuple(pool), owned_ids=owned_ids,
                     designations=designations, snapshot_as_of=snapshot_as_of)

    # What changed since the previous frozen page. Read-only: the diff never
    # feeds a projection, so yesterday's numbers cannot enter today's evidence.
    prev = previous_archive(archive_root, context.season, now)
    if prev is not None:
        try:
            before = read_archive(prev)
            current = dash.record()
            dash = Dashboard(**{**dash.__dict__,
                                "changes": diff_archives(before, current),
                                "radar_changes": diff_radar(before, current)})
        except (OSError, ValueError) as exc:
            dash = Dashboard(**{**dash.__dict__, "changes": Changes(
                str(prev.name), None, (),
                note=f"previous snapshot could not be read ({type(exc).__name__}); "
                     f"no change list this run"),
                "radar_changes": RadarChanges(
                    False, f"the previous record could not be read "
                           f"({type(exc).__name__})", str(prev.name), None)})
    else:
        dash = Dashboard(**{**dash.__dict__, "radar_changes": diff_radar(None, {})})

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
    stand behind advice built on a five-day-old roster. CONDITIONAL sits
    between them and exists for acquisitions only: the inputs are current
    and the lineup arithmetic holds, but whether the player can be claimed
    at all is something this page never establishes, so the card is
    endorsed IF Sleeper shows him available and never wears an unqualified
    ACTIONABLE badge. Nothing here submits anything to Sleeper — every action
    is a description of a move the owner makes by hand.
    """

    kind: str
    status: str                       # ACTIONABLE | CONDITIONAL | WITHHELD
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
    #: The instant after which this card describes a move that can no longer
    #: be made this week (the first kickoff among the players involved). It
    #: is `deadline` for a lineup move; an acquisition carries no deadline of
    #: its own (waiver timing is unknown) but still lapses at kickoff. The
    #: page re-judges it on the reader's clock.
    lapses_at: datetime | None = None
    #: Short, structured restatements for the Action Desk, written by the
    #: producer that knows the players (never parsed out of `detail`). Not
    #: part of `record()`: the archive keeps the sentences it always had.
    why_now: str = ""
    benefit: str = ""
    cost: str = ""
    #: Display names parallel to `player_ids`, for the desk's check wording.
    names: tuple[str, ...] = field(default=())
    #: A pickup's unverified-drop check (`gridiron.waivers.drop_rule`); "" when
    #: the drop is known to be allowed or the action drops nobody.
    drop_check: str = ""

    @property
    def lapse(self) -> datetime | None:
        return self.lapses_at or self.deadline

    @property
    def actionable(self) -> bool:
        return self.status == "ACTIONABLE"

    @property
    def conditional(self) -> bool:
        return self.status == "CONDITIONAL"

    @property
    def withheld(self) -> bool:
        return self.status == "WITHHELD"

    @property
    def title(self) -> str:
        """What the card actually says. Imperative only when endorsed
        (a conditional card keeps its wording: the condition is in it)."""
        if self.withheld and self.neutral_headline:
            return self.neutral_headline
        return self.headline

    @property
    def body(self) -> str:
        if self.withheld and self.neutral_detail:
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


def _coverage_after(roster: Sequence[Player], u) -> str:
    """Roster count by position once the pair is made, for the positions it
    touches, naming who is retained. Roster composition is known; what the
    retained players are worth beyond this week is not, and this says
    nothing about that."""
    seen: dict[str, Player] = {}
    for p in roster:
        seen.setdefault(p.sleeper_id, p)
    seen.pop(u.drop.sleeper_id, None)
    seen[u.add.sleeper_id] = u.add
    bits = []
    for pos in sorted({u.add.position, u.drop.position}):
        held = [p for p in seen.values() if p.position == pos]
        names = ", ".join(sorted(p.name for p in held))
        bits.append(f"{pos} {len(held)} ({names or 'none'})")
    return "; ".join(bits)


def build_actions(*, plan: LineupPlan, board: WaiverBoard, gate: ActionGate,
                  now: datetime, slots: Sequence[str],
                  snapshot_as_of: str, roster: Sequence[Player] = ()) -> tuple[Action, ...]:
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
                player_ids=(best.sleeper_id,) if best else (), slot=slots[i],
                why_now=note, benefit=(f"an empty {slots[i]} scores 0; {best.name} "
                                       f"projects {_num(best.value, 2)}" if best else
                                       f"an empty {slots[i]} scores 0"),
                cost="nobody leaves the lineup", names=(best.name,) if best else ()))
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
                            else (cur.sleeper_id,)), slot=slots[i],
                why_now=note,
                benefit=(f"{replacement.name} projects {_num(replacement.value, 2)} where "
                         f"{cur.name} projects 0 ({why})" if replacement else
                         f"{cur.name} projects 0 ({why})"),
                cost=(f"{cur.name} goes to the bench" if replacement else
                      "no eligible bench player — the slot stays as it is"),
                names=((replacement.name, cur.name) if replacement else (cur.name,))))

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
            player_ids=(a.bench.sleeper_id, a.starter.sleeper_id), slot=a.slot,
            why_now=note,
            benefit=(f"{_num(a.delta_points, 2, True)} projected pts this week"
                     + (f" (z {_num(a.z, 2)}: inside the noise)" if noise else
                        f" (z {_num(a.z, 2)})" if a.z is not None else "")),
            cost=f"{a.starter.name} goes to the bench",
            names=(a.bench.name, a.starter.name)))

    # 3. Acquisitions. Only a pair that improves THIS WEEK's best legal
    # lineup gets a card, and the card is CONDITIONAL at best: the lineup
    # arithmetic is current, the player's availability is not established
    # here and never will be from a cache. The board is ranked by lineup
    # gain; two cards that want the same drop are shown as the either/or
    # they are, with the next feasible drop for each named or ruled out.
    w_status, w_why, w_verify = status_of(waiver_gate)
    if w_status == "ACTIONABLE":
        w_status = "CONDITIONAL"
    shown = list(board.upgrades[:2])
    for i, u in enumerate(shown):
        elig = eligibility(snapshot_as_of=snapshot_as_of)
        kick, kick_note = _deadline_for([u.add, u.displaces], now)
        deadline, note = (None, "waiver timing NOT established — " + elig.verify
                          + (f" To count this week the claim has to clear before "
                             f"{kick.astimezone(timezone.utc):%a %d %b %H:%M} UTC "
                             f"(first kickoff among the players involved)."
                             if kick is not None else f" {kick_note}."))
        rivals = [o for o in shown if o is not u and o.drop.sleeper_id == u.drop.sleeper_id]
        # Fallback drops are counted for THIS move on its own, verified ones
        # first (`build_board` orders them so): a bench player whose game has
        # started is named as unverified, never as "the next feasible drop".
        legal = [(d, g) for d, g in u.alternatives if not drop_rule(d)]
        unverified = [d for d, _ in u.alternatives if drop_rule(d)]
        nxt = legal[0] if legal else None
        count = (f"{len(legal)} verified fallback drop(s) for {u.add.name}"
                 + (f"; {', '.join(d.name for d in unverified)} would also work but "
                    f"{'has' if len(unverified) == 1 else 'have'} already played "
                    f"(or kickoff unknown) — unverified whether Sleeper allows that drop"
                    if unverified else ""))
        if rivals:
            either = (f"Either/or with {', '.join(r.add.name for r in rivals)}: both cost "
                      f"the same drop, {u.drop.name}, so they are not both possible with "
                      f"it. If that one is made first, "
                      + (f"the next verified drop for {u.add.name} is {nxt[0].name} "
                         f"({nxt[0].position}, {_num(nxt[0].value, 2)} pts), and the "
                         f"lineup gain with that drop is {_num(nxt[1], 2, True)}"
                         if nxt else
                         f"there is no other verified drop for {u.add.name}: the move "
                         f"is off") + f". {count}.")
        else:
            either = (f"if {u.drop.name} cannot be dropped, the next verified drop is "
                      f"{nxt[0].name} ({nxt[0].position}, {_num(nxt[0].value, 2)} pts) "
                      f"with a lineup gain of {_num(nxt[1], 2, True)}. {count}"
                      if nxt else
                      f"{u.drop.name} is the only verified drop; without it the move is off"
                      + (f". {count}" if unverified else ""))
        drop_note = (f" Drop legality UNVERIFIED: {u.drop_check}." if u.drop_check else "")
        after = _coverage_after(roster or [p for p in plan.current if p is not None]
                                + list(plan.bench_pool), u)
        displ = (f"{u.displaces.name} leaves the lineup"
                 if u.displaces is not None and u.displaces.sleeper_id != u.drop.sleeper_id
                 else f"{u.drop.name} leaves the roster")
        out.append(Action(
            "acquire", w_status, "INFO",
            f"If available, claim {u.add.name} ({u.add.position}) — this week's lineup "
            f"{_num(u.lineup_gain, 2, True)} via {u.slot}",
            (f"Benefit: {u.add.name} enters {u.slot} ({_num(u.add.value, 2)} projected), "
             f"{displ}; best legal lineup {_num(u.lineup_gain, 2, True)} pts THIS WEEK. "
             f"Cost: drop {u.drop.name} ({u.drop.position}, {_num(u.drop.value, 2)} "
             f"projected this week).{drop_note} Coverage after the move: {after}. "
             f"Availability {elig.state}: this page cannot tell a free agent from a "
             f"player on waivers, so this is endorsed only if Sleeper shows him "
             f"available. Limits: projections are the UNVALIDATED baseline; FAAB, "
             f"rest-of-season value and the waiver order are not modelled."),
            deadline, note,
            either,
            neutral_headline=(f"The last snapshot found {u.add.name} ({u.add.position}) "
                              f"would have improved that week's lineup"),
            # Deliberately NOT `u.describe()`. That sentence opens "add X,
            # drop Y", which is an instruction, and an instruction inside a
            # withheld card is the exact failure the neutral wording exists
            # to prevent — the badge says no advice is being given while the
            # first words of the body give some.
            neutral_detail=(f"{seen}, {u.add.name} ({u.add.position}) projected "
                            f"{_num(u.add.value, 2)} and would have entered {u.slot} for "
                            f"{_num(u.lineup_gain, 2, True)} pts, at the cost of "
                            f"{u.drop.name} ({u.drop.position}, {_num(u.drop.value, 2)}). "
                            f"Availability {elig.state} in that snapshot too: this page "
                            f"cannot tell a free agent from a player on waivers, and it "
                            f"does not know whether {u.add.name} is still unrostered."),
            evidence=(f"evidence: league snapshot {snapshot_as_of}; projections built "
                      f"from box scores up to the evidence boundary stated at the top",)
                     + elig.basis,
            withheld_reasons=w_why,
            verify=tuple(w_verify) + ((u.drop_check,) if u.drop_check else ()) + (
                                      elig.verify,
                                      f"confirm {u.drop.name} is the player you would "
                                      f"drop and that no injured or bye player is a better "
                                      f"drop — the protected list names the ones this "
                                      f"page will not rank"),
            delta_points=u.lineup_gain, order=i, lapses_at=kick,
            player_ids=(u.add.sleeper_id, u.drop.sleeper_id), slot=u.slot,
            why_now=("claims process on Sleeper's clock, not known here; to count this "
                     "week it must clear before " + (f"{kick.astimezone(timezone.utc):%a %d %b %H:%M} UTC"
                                                      if kick is not None else "an UNKNOWN kickoff")),
            benefit=(f"{_num(u.lineup_gain, 2, True)} to this week's best legal lineup: "
                     f"{u.add.name} ({u.add.position}, {_num(u.add.value, 2)}) enters {u.slot}"
                     + (f", {u.displaces.name} leaves the lineup"
                        if u.displaces is not None and u.displaces.sleeper_id != u.drop.sleeper_id
                        else "")),
            cost=f"drop {u.drop.name} ({u.drop.position}, {_num(u.drop.value, 2)} projected)",
            names=(u.add.name, u.drop.name), drop_check=u.drop_check))

    out.sort(key=lambda a: a.rank)
    return tuple(out)


# --------------------------------------------------------------------------
# The Action Desk: the actions above, prioritised for the first screen
# --------------------------------------------------------------------------
#: How many supported cards the desk leads with. More than three and the
#: first phone screen is a list again, not a decision.
DESK_TOP = 3
#: Sources whose staleness only means "a status tag may have changed": a
#: move withheld on these alone is a check the owner can make in Sleeper in
#: seconds, not a page that has lost track of the roster.
_DESIGNATION_SOURCES = frozenset({"sleeper_players", "injuries"})


@dataclass(frozen=True)
class DeskItem:
    """One card on the desk. `rows` are the six answers, in order; every
    sentence in them was written by the producer of the action(s)."""

    label: str        # LINEUP MOVE | IF AVAILABLE | CHECK IN SLEEPER | WITHHELD | OPTIONAL
    tone: str         # go | cond | check | held | opt
    title: str
    rows: tuple[tuple[str, str], ...]
    actions: tuple[Action, ...]
    lapse: datetime | None
    link: str
    link_text: str
    #: The earlier of `lapse` and the page's evidence expiry: when this card
    #: stops being true. None = UNKNOWN (never invented).
    until: datetime | None = None


@dataclass(frozen=True)
class Desk:
    headline: str
    top: tuple[DeskItem, ...]          # supported, best first, at most DESK_TOP
    checks: tuple[DeskItem, ...]       # CHECK IN SLEEPER
    more: tuple[DeskItem, ...]         # supported overflow, then optional
    withheld: tuple[DeskItem, ...]     # last known picture, inputs stale
    hold: bool


def _when(t: datetime) -> str:
    return f"{t.astimezone(timezone.utc):%a %d %b %H:%M} UTC"


def _first(*ts: datetime | None) -> datetime | None:
    known = [t for t in ts if t is not None]
    return min(known) if known else None


def _valid_row(lapse: datetime | None, until: datetime | None, evidence: str) -> str:
    known = [(t, w) for t, w in ((lapse, "the first kickoff it involves"),
                                 (until, "the evidence behind it passes its limit"))
             if t is not None]
    if not known:
        head = "UNKNOWN — no kickoff time or evidence expiry could be established"
    else:
        t, why = min(known, key=lambda x: x[0])
        head = f"{_when(t)}, when {why}"
    return head + (f" · evidence: {evidence}" if evidence else "")


def action_desk(actions: Sequence[Action], gate: ActionGate, *,
                valid_until: datetime | None, designations_as_of: str,
                snapshot_as_of: str = "") -> Desk:
    """Sort the page's actions into the desk. Nothing is re-judged here: the
    status each action carries (from the gate, the lock and the drop rule)
    decides where it goes, and only the wording is condensed."""
    evidence = f"league snapshot {snapshot_as_of}" if snapshot_as_of else ""

    def gate_of(a: Action):
        return gate.gate("waiver" if a.kind == "acquire" else "lineup")

    def link(a: Action) -> tuple[str, str]:
        if a.kind == "acquire" and a.player_ids:
            return f"#fa-{a.player_ids[0]}", "Open in Free Agent Radar"
        return "#startsit", "Open the start/sit comparison"

    def availability(a: Action) -> str:
        return next((v for v in a.verify if "FREE AGENT" in v),
                    "open the player in Sleeper: it shows FREE AGENT or a waiver clear time")

    def designation_check(a: Action) -> str:
        who = ", ".join(a.names) or "the players in this move"
        return (f"open {who} in Sleeper and read the status tag beside each name "
                f"(Q, D, O, IR or none): this page's designations come from the "
                f"once-a-day player map pulled {designations_as_of}, older than the "
                f"gate allows")

    def item(a: Action, label: str, tone: str) -> DeskItem:
        if label == "LINEUP MOVE":
            check = (f"none open — every input it rests on was current when built "
                     f"(designations {designations_as_of})")
        elif label == "IF AVAILABLE":
            check = availability(a)
        elif label == "CHECK IN SLEEPER":
            parts = []
            if a.withheld:
                parts.append(designation_check(a))
            if a.drop_check:
                parts.append(a.drop_check)
            if a.kind == "acquire":
                parts.append(availability(a))
            check = "; then ".join(parts)
        elif label == "WITHHELD":
            check = "; ".join(gate_of(a).verify() or a.verify) or "see the inputs section"
        else:
            check = "none — optional"
        title = a.title
        valid = ("not supported now: " + "; ".join(a.withheld_reasons)
                 if label == "WITHHELD" else _valid_row(a.lapse, valid_until, evidence))
        href, text = link(a)
        rows = (("Why now", a.why_now or a.deadline_note), ("Benefit", a.benefit or a.body),
                ("Cost", a.cost or "—"), ("If not", a.backup or "—"),
                ("Check", check), ("Valid until", valid))
        return DeskItem(label, tone, title, rows, (a,), a.lapse, href, text,
                        _first(a.lapse, valid_until) if label != "WITHHELD" else None)

    def group(items: list[DeskItem]) -> list[DeskItem]:
        """Pickups that cost the same drop are one either/or card."""
        out: list[DeskItem] = []
        by_drop: dict[str, list[DeskItem]] = {}
        for it in items:
            a = it.actions[0]
            if a.kind == "acquire" and len(a.player_ids) > 1:
                by_drop.setdefault(a.player_ids[1], []).append(it)
        done: set[int] = set()
        for it in items:
            if id(it) in done:
                continue
            a = it.actions[0]
            peers = by_drop.get(a.player_ids[1], []) if a.kind == "acquire" and len(a.player_ids) > 1 else []
            if len(peers) < 2:
                out.append(it)
                continue
            done.update(id(x) for x in peers)
            acts = tuple(x.actions[0] for x in peers)
            drop = a.names[1] if len(a.names) > 1 else "the same player"
            rows = dict(it.rows)
            rows["Benefit"] = ("; ".join(f"{x.names[0] if x.names else '?'} "
                                         f"{_num(x.delta_points, 2, True)} via {x.slot}"
                                         for x in acts)
                               + " — this week's best legal lineup; one or the other, not both")
            rows["If not"] = a.backup or "—"
            lapses = [x.lapse for x in acts if x.lapse is not None]
            rows["Valid until"] = (_valid_row(min(lapses) if lapses else None, valid_until,
                                              evidence) if it.label != "WITHHELD"
                                   else rows["Valid until"])
            names = " or ".join(x.names[0] if x.names else "?" for x in acts)
            # Only a supported group may say "pick": a withheld or unchecked
            # one describes what the last snapshot showed, never an order.
            title = (f"Pick one — {names}; both cost dropping {drop}"
                     if it.label == "IF AVAILABLE" else
                     f"Either/or in the last snapshot — {names}; both would have cost {drop}")
            out.append(DeskItem(it.label, it.tone, title,
                                tuple((k, rows[k]) for k, _ in it.rows), acts,
                                min(lapses) if lapses else None, it.link, it.link_text,
                                _first(min(lapses) if lapses else None, valid_until)
                                if it.label != "WITHHELD" else None))
        return out

    supported: list[DeskItem] = []
    checks: list[DeskItem] = []
    optional: list[DeskItem] = []
    withheld: list[DeskItem] = []
    for a in sorted(actions, key=lambda a: a.rank):
        g = gate_of(a)
        if a.kind == "swap" and a.urgency == "INFO":
            # inside the noise: never worth a top slot or a check in Sleeper,
            # whatever its status (a withheld one keeps its neutral title)
            optional.append(item(a, "OPTIONAL", "opt"))
        elif a.actionable:
            supported.append(item(a, "LINEUP MOVE", "go"))
        elif a.conditional and a.drop_check:
            checks.append(item(a, "CHECK IN SLEEPER", "check"))
        elif a.conditional:
            supported.append(item(a, "IF AVAILABLE", "cond"))
        elif (a.withheld and a.kind != "verify" and g.blockers
              and set(g.sources) <= _DESIGNATION_SOURCES):
            checks.append(item(a, "CHECK IN SLEEPER", "check"))
        else:
            withheld.append(item(a, "WITHHELD", "held"))
    supported, checks, withheld = group(supported), group(checks), group(withheld)
    top, overflow = supported[:DESK_TOP], supported[DESK_TOP:]

    def n(k: int, word: str) -> str:
        return f"{k} {word}{'' if k == 1 else 's'}"

    moves = sum(1 for i in supported if i.label == "LINEUP MOVE")
    picks = sum(1 for i in supported if i.label == "IF AVAILABLE")
    parts = ([n(moves, "lineup move")] if moves else []) + \
            ([n(picks, "pickup") + " if available"] if picks else [])
    if parts:
        headline = " · ".join(parts + ([f"{len(checks)} to check in Sleeper"] if checks else []))
    elif checks:
        headline = "Check Sleeper before acting"
    elif withheld:
        headline = "Hold — inputs are stale"
    else:
        headline = "Hold — no change needed"
    return Desk(headline, tuple(top), tuple(checks), tuple(overflow + optional),
                tuple(withheld), hold=not supported)


# --------------------------------------------------------------------------
# The week transition: what this advice is for, and what waits
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class NextDecision:
    """What the page's advice is FOR, what can still be done, what waits.

    Everything here is read off inputs the page already holds — the phase,
    each starter's lock, the dated sources, and the schedule's rows for the
    week after this one. Nothing is projected for next week: this week's
    lineup gains are this week's, and a next-week projection would need
    box scores that do not exist yet. Where the schedule has no rows for
    next week the preview says UNAVAILABLE rather than guessing.
    """

    week: int
    phase: str
    evidence: tuple[str, ...]            # dated, one line per input class
    open_starters: tuple[str, ...]       # "name (POS) — kicks off ..." for unlocked starters
    locked_starters: int
    total_starters: int
    still_possible: tuple[str, ...]
    must_wait: tuple[str, ...]
    next_week: int | None
    next_week_lines: tuple[str, ...]     # schedule coverage from admissible data, or why not

    @property
    def all_locked(self) -> bool:
        return self.total_starters > 0 and self.locked_starters >= self.total_starters

    def record(self) -> dict:
        return {"week": self.week, "phase": self.phase, "evidence": list(self.evidence),
                "open_starters": list(self.open_starters),
                "locked_starters": self.locked_starters,
                "total_starters": self.total_starters,
                "still_possible": list(self.still_possible),
                "must_wait": list(self.must_wait), "next_week": self.next_week,
                "next_week_lines": list(self.next_week_lines)}


def next_decision(*, context: WeekContext, roster: Sequence[Player], plan: LineupPlan,
                  schedule: pd.DataFrame | None, sources: Sequence[SourceFreshness],
                  snapshot_as_of: str, now: datetime,
                  actions: Sequence[Action]) -> NextDecision:
    week = context.report_week
    phase = context.phase.value

    def as_of(name: str) -> str:
        src = next((s for s in sources if s.name == name), None)
        if src is None or src.as_of is None:
            return f"{name}: never pulled"
        return (f"{name}: as-of {src.as_of.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC "
                f"({src.status.value.upper()})")

    evidence = (
        f"advice is for WEEK {week} ({phase})",
        f"league snapshot (roster, lineup): {snapshot_as_of}",
        f"projections: box scores through week {context.stats_through} "
        f"(evidence boundary week {context.evidence_boundary}); a projection is this "
        f"week's, never next week's",
        as_of("injuries"), as_of("sleeper_players"),
    )

    starters = [p for p in plan.current if p is not None]
    total = len(plan.slots)
    locked = sum(1 for p in starters if p.locked)
    empty = total - len(starters)
    open_: list[str] = []
    for p in starters:
        if p.locked:
            continue
        when = (f"kicks off {p.kickoff.astimezone(timezone.utc):%a %d %b %H:%M} UTC"
                if p.kickoff is not None else (p.lock_note or "lock state UNKNOWN"))
        open_.append(f"{p.name} ({p.position}) — {when}")
    unknown = sum(1 for p in starters if not p.lock_known)

    live = [a for a in actions if a.actionable and a.kind != "acquire"]
    held_lineup = [a for a in actions if a.withheld and a.kind not in ("acquire", "verify")]
    cond = [a for a in actions if a.conditional]
    held_acq = [a for a in actions if a.withheld and a.kind == "acquire"]
    possible: list[str] = []
    waits: list[str] = []
    if plan.abstained:
        possible.append("no lineup change can be shown legal: " + plan.abstained)
    elif not starters:
        possible.append("the lineup is empty in the snapshot; nothing here can be judged")
    elif locked >= len(starters) and empty == 0:
        possible.append(f"nothing on this week's lineup can change: all {total} starters "
                        f"have kicked off")
        possible.append(f"roster moves for week {week + 1} (add/drop) are still yours to "
                        f"make in Sleeper, subject to its waiver processing, whose timing "
                        f"this page does not know; none is ranked here because no "
                        f"week-{week + 1} projection exists yet")
    else:
        possible.append(f"lineup changes for the {len(open_)} starter(s) not yet locked"
                        + (f" and the {empty} EMPTY slot(s)" if empty else "")
                        + (f"; {unknown} starter(s) have an UNKNOWN lock and are not moved"
                           if unknown else ""))
        if live:
            possible.append(f"{len(live)} supported lineup change(s) are on this page")
        elif held_lineup:
            possible.append(f"{len(held_lineup)} lineup comparison(s) are WITHHELD: the "
                            f"inputs behind them are stale, so they are the last known "
                            f"picture, not advice")
        else:
            possible.append("no supported lineup change: the current lineup is the best "
                            "legal one the projections find")
    if cond:
        possible.append(f"{len(cond)} conditional acquisition(s): each improves THIS "
                        f"WEEK's lineup only if the player is available and the claim "
                        f"clears before his kickoff — when a claim would process is not "
                        f"known here")
    elif held_acq:
        possible.append(f"{len(held_acq)} acquisition comparison(s) are WITHHELD on stale "
                        f"inputs; none is endorsed")
    else:
        possible.append("no acquisition improves this week's best legal lineup; a pickup "
                        "that would only sit on the bench is research, not a move")
    waits.append(f"week {week + 1} projections: they need week-{week} box scores, which "
                 f"arrive after the slate, and a week-{week + 1} league snapshot; this "
                 f"week's lineup gains are not next week's")
    waits.append("what a pickup is worth beyond this week (byes, injuries, role): "
                 "unpriced here, so it is never the reason for a move")

    nxt_lines: list[str] = []
    nxt_week = week + 1
    if schedule is None:
        nxt_lines.append(f"week {nxt_week} preview UNAVAILABLE: no schedule is loaded")
    else:
        idx = kickoff_index(schedule, nxt_week)
        if idx is None or (not idx.kickoffs and not idx.time_unknown):
            nxt_lines.append(f"week {nxt_week} preview UNAVAILABLE: the cached schedule "
                             f"carries no week-{nxt_week} game rows")
        else:
            timed, untimed, absent = [], [], []
            for p in sorted(roster, key=lambda p: p.name):
                t = nflverse_team(p.team)
                if t in idx.kickoffs:
                    timed.append(p)
                elif t in idx.time_unknown:
                    untimed.append(p.name)
                else:
                    absent.append(f"{p.name} ({t or 'no team'})")
            nxt_lines.append(f"week {nxt_week} schedule: {len(timed)} of {len(roster)} roster "
                             f"players have a timed game"
                             + (f"; first kickoff {min(idx.kickoffs[nflverse_team(p.team)] for p in timed).astimezone(timezone.utc):%a %d %b %H:%M} UTC"
                                if timed else ""))
            if untimed:
                nxt_lines.append("game found but no usable kickoff time: " + ", ".join(untimed))
            if absent:
                nxt_lines.append(f"no week-{nxt_week} row in the schedule for: "
                                 + ", ".join(absent)
                                 + " — UNKNOWN, not read as a bye (the schedule declares "
                                 "no byes; a missing row and a week off look the same)")
            nxt_lines.append(f"no week-{nxt_week} projection, lineup or pickup is made here: "
                             f"the inputs for it do not exist yet")
    return NextDecision(week, phase, evidence, tuple(open_), locked, total,
                        tuple(possible), tuple(waits), nxt_week, tuple(nxt_lines))


# --------------------------------------------------------------------------
# HTML
# --------------------------------------------------------------------------
_CSS = theme.CSS + """
.act p{margin:4px 0}.act-TODAY{border-left-color:var(--warn)}.act-UNKNOWN{border-left-color:var(--warn)}
.next h3{font-size:15px;margin:16px 0 4px;letter-spacing:.02em}.next ul{margin:4px 0}
.radar{list-style:none;margin:8px 0 0;padding:0}
.radar li{border-top:1px solid var(--line)}.radar li:first-child{border-top:0}
.radar summary{display:grid;grid-template-columns:1fr auto;gap:4px 12px;align-items:center;padding:10px 4px;
color:inherit;font-weight:500;list-style:none}
.radar summary::-webkit-details-marker{display:none}
.radar .rname{font-size:15.5px;font-weight:700}.radar .rmeta{color:var(--muted);font-size:12.5px;font-weight:500}
.radar .rnum{text-align:right;white-space:nowrap}.radar .rnum .stat{display:block;line-height:1.1}
.radar .rnum .lbl{font-size:11px;color:var(--dim);letter-spacing:.08em;text-transform:uppercase}
.radar .rbody{padding:2px 4px 14px;font-size:14px}
.radar .rbody dl{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;margin:6px 0}
.radar .rbody dt{color:var(--muted);font-size:12px;letter-spacing:.06em;text-transform:uppercase;padding-top:2px}
.radar .rbody dd{margin:0}
.badge.v-LINEUP{color:var(--lime-ink);background:var(--lime);border-color:transparent}
.badge.v-RESEARCH{color:var(--cyan);border-color:rgba(95,227,255,.45)}
.badge.v-COVERAGE{color:var(--warn);border-color:rgba(255,200,107,.45)}
.badge.v-BELOW,.badge.v-UNRANKED{color:var(--muted)}
.badge.v-LOCKED,.badge.v-UNKNOWN{color:var(--bad);border-color:rgba(255,128,128,.45)}
.radar li[hidden]{display:none}
.chg li{margin:3px 0}
.desk h2{border-top:0;margin-top:22px;padding-top:0}
.desk-grid{display:grid;grid-template-columns:minmax(0,1fr);gap:0 28px}
.deskh{font-size:var(--t-xs);letter-spacing:.12em;text-transform:uppercase;color:var(--warn);margin:22px 0 4px}
.dcard{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;margin:12px 0;
box-shadow:inset 3px 0 0 var(--line2)}
.dcard.first{background:var(--card2);border-color:var(--line2);padding:18px 20px}
.dcard.tone-go{box-shadow:inset 3px 0 0 var(--lime)}.dcard.tone-cond{box-shadow:inset 3px 0 0 var(--cyan)}
.dcard.tone-check{box-shadow:inset 3px 0 0 var(--warn)}.dcard.tone-hold{box-shadow:inset 3px 0 0 var(--muted)}
.dhead{display:flex;flex-wrap:wrap;align-items:center;gap:8px}
.dhead .rank{width:26px;height:26px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center;
font-weight:800;font-size:13px;background:var(--fg);color:var(--lime-ink)}
.dhead .best{font-size:var(--t-xs);letter-spacing:.12em;text-transform:uppercase;color:var(--fg);font-weight:800}
.dhead .until{margin-left:auto;font-size:var(--t-s);color:var(--muted)}
.dcard h3{font-size:18px;line-height:1.3;margin:10px 0 4px;max-width:60ch}
.dcard.first h3{font-size:21px;letter-spacing:-.01em}
.dcard .lead{font-size:var(--t-s);color:var(--muted);margin:6px 0 2px;max-width:75ch}
.facts{margin:10px 0 0}
.facts>div{display:grid;grid-template-columns:8.5em minmax(0,1fr);gap:12px;padding:9px 0;border-top:1px solid var(--line)}
.facts dt{font-size:var(--t-xs);letter-spacing:.09em;text-transform:uppercase;color:var(--dim);font-weight:700;padding-top:3px}
.facts dd{margin:0;font-size:14.5px;line-height:1.5;max-width:75ch}
.dfoot{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;margin-top:12px}
.dfoot .full{margin:0}.dfoot .full>summary{min-height:44px;display:flex;align-items:center}
.dfoot .full[open]{flex-basis:100%}.dfoot .full h4{font-size:14.5px;margin:12px 0 2px}.dfoot .full p,.dfoot .full li{font-size:var(--t-s);color:var(--muted);max-width:80ch}
.more{margin:14px 0}.more>summary{min-height:44px;display:flex;align-items:center}
.desk-side .panel details>summary{min-height:40px}
@media (min-width:1100px){.desk-grid{grid-template-columns:minmax(0,1fr) 340px}
.desk-side{position:sticky;top:72px;align-self:start;max-height:calc(100vh - 88px);overflow:auto;padding-top:0}}
@media (max-width:560px){.radar summary{padding:9px 2px}.radar .rname{font-size:15px}
.facts>div{grid-template-columns:1fr;gap:2px;padding:8px 0}.dcard,.dcard.first{padding:14px 15px}
.dcard.first h3{font-size:19px}.dhead .until{margin-left:0;flex-basis:100%}}
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


#: What an element says once the reader's clock passes its deadline or the
#: page's evidence expiry. Written here, shown by theme.VALIDITY_JS.
_LAPSE_MOVE = ("OFF — the first kickoff among the players involved ({when}) has passed "
               "since this page was built; this move can no longer be made this week.")
_LAPSE_ROW = ("LOCKED — his game kicked off {when}, after this page was built; he cannot "
              "enter this week's lineup.")
_EXPIRED = ("EXPIRED — the evidence behind this move has passed its freshness limit since "
            "this page was built; last known picture, not advice.")
_HELD = "WITHHELD — built on stale inputs; last known picture, not advice."


def _live_attrs(deadline: datetime | None, *, gated: bool, move: bool,
                held: bool = False) -> str:
    out = ""
    if deadline is not None:
        when = deadline.astimezone(timezone.utc)
        out += (f" data-deadline=\"{_e(when.isoformat(timespec='seconds'))}\""
                f" data-lapse-text=\"{_e((_LAPSE_MOVE if move else _LAPSE_ROW).format(when=f'{when:%a %d %b %H:%M} UTC'))}\"")
    if held:
        out += f" data-held=\"{_e(_HELD)}\""
    elif gated:
        out += f" data-gated=\"\" data-expire-text=\"{_e(_EXPIRED)}\""
    return out


def _action_card(a: "Action") -> str:
    held = a.withheld
    bar = [f"<span class=\"badge {'now' if a.urgency == 'NOW' else 'today' if a.urgency in ('TODAY', 'UNKNOWN') else ''}\">"
           f"{_e(a.urgency)}</span>"]
    cls = "held" if held else "cond" if a.conditional else "go"
    label = a.status + (" — if available" if a.conditional else "")
    bar.append(f"<span class=\"badge {cls}\">{_e(label)}</span>")
    out = [f"<div class=\"act act-{_e(a.urgency.replace(' ', '-'))}"
           f"{' withheld' if held else ' conditional' if a.conditional else ''}\""
           f"{_live_attrs(a.lapse, gated=not held, move=True)}>",
           "<div class=\"bar\">" + "".join(bar) + "</div>",
           "<span class=\"vstate\"></span>"]
    if held:
        # Said before the card's own wording, so the frame is set even for a
        # reader who never reaches the explanation underneath.
        out.append("<p class=\"why\"><b>Last known picture — no action is being "
                   "recommended.</b></p>")
    elif a.conditional:
        out.append("<p class=\"why\"><b>Conditional.</b> The lineup arithmetic is current; "
                   "whether the player can be claimed is NOT established here. Endorsed "
                   "only if Sleeper shows him available.</p>")
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




_DESK_LEAD = {
    "cond": ("<p class=\"lead\"><b>CONDITIONAL — if available.</b> The lineup arithmetic is "
             "current; whether the player can be claimed is NOT established here. Endorsed "
             "only if Sleeper shows him available.</p>"),
    "held": "<p class=\"lead\"><b>Last known picture — no action is being recommended.</b></p>",
    "check": ("<p class=\"lead\"><b>Not advice until checked.</b> This is the comparison as "
              "last computed; the check below decides whether it holds.</p>"),
    "opt": ("<p class=\"lead\">Optional: the edge is inside the noise of the two projections, "
            "so doing nothing is fine.</p>"),
}


def _action_detail(a: "Action", *, titled: bool = False) -> str:
    """Everything the action card used to say, for the desk card's
    "Full reasoning" disclosure. Wording unchanged. `titled` leads with the
    action's own headline, for a card that groups several."""
    out = ([f"<h4>{_e(a.title)}</h4>"] if titled else []) + [
        f"<p>{_e(a.body)}</p>", f"<p class=\"deadline\">{_e(a.deadline_note)}</p>"]
    if a.backup:
        out.append(f"<p class=\"backup\">Backup — {_e(a.backup)}</p>")
    if a.withheld:
        out.append("<p class=\"why\"><b class=\"bad\">Not advice right now.</b> "
                   + _e("; ".join(a.withheld_reasons))
                   + ". The comparison above is the last known picture, kept so it is "
                     "not lost; it is not a statement about the situation now.</p>")
    if a.verify:
        out.append("<p class=\"why\">Verify first: " + _e("; ".join(a.verify)) + ".</p>")
    if a.evidence:
        out.append("<ul class=\"small\">" + "".join(f"<li>{_e(x)}</li>" for x in a.evidence)
                   + "</ul>")
    return "".join(out)


def _desk_card(item: "DeskItem", rank: int | None, *, first: bool = False) -> str:
    """One Action Desk card: the verdict, the six answers, one link into the
    detail, and the full wording one tap away. Carries the same live
    attributes as every other move, so the reader's clock can lapse it."""
    endorsed = all(a.actionable or a.conditional for a in item.actions)
    attrs = _live_attrs(item.lapse, gated=item.tone in ("go", "cond", "opt") and endorsed,
                        move=True, held=item.tone == "held")
    head = ("<div class=\"dhead\">"
            + (f"<span class=\"rank\">{rank}</span>" if rank else "")
            + f"<span class=\"badge {_e(item.tone)}\">{_e(item.label)}</span>"
            + ("<span class=\"best\">Best next step</span>" if first else "")
            + (f"<span class=\"until\">until {theme.time_html(item.until, _when(item.until))}</span>"
               if item.until is not None else "")
            + "</div>")
    facts = "".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in item.rows)
    detail = "".join(_action_detail(a, titled=len(item.actions) > 1) for a in item.actions)
    return (f"<article class=\"dcard tone-{_e(item.tone)}{' first' if first else ''}\"{attrs}>"
            + head + f"<h3>{_e(item.title)}</h3><span class=\"vstate\">"
            + (_e(_HELD) if item.tone == "held" else "") + "</span>"
            + _DESK_LEAD.get(item.tone, "")
            + f"<dl class=\"facts\">{facts}</dl>"
            + "<div class=\"dfoot\">"
            + ("".join(f"<a class=\"btn{' primary' if first and i == 0 else ''}\" "
                       f"href=\"#fa-{_e(a.player_ids[0])}\">{_e(a.names[0] if a.names else 'Option')} "
                       f"in the Radar →</a>" for i, a in enumerate(item.actions))
               if len(item.actions) > 1 and all(a.kind == "acquire" and a.player_ids
                                                for a in item.actions) else
               f"<a class=\"btn{' primary' if first else ''}\" href=\"{_e(item.link)}\">"
               f"{_e(item.link_text)} →</a>")
            + f"<details class=\"full\"><summary>Full reasoning</summary>{detail}</details>"
            + "</div></article>")

# --------------------------------------------------------------------------
# The Free Agent Radar
# --------------------------------------------------------------------------
_VERDICT_WORD = {
    LINEUP: "improves this week's lineup", RESEARCH: "research — lineup unchanged",
    COVERAGE: "no like-for-like comparison", BELOW: "below your cheapest droppable",
    "LOCKED": "locked this week", "UNKNOWN": "lock unknown",
    "UNRANKED": "not compared (under the cap)",
}


def _dl(pairs: Sequence[tuple[str, str]]) -> str:
    return "<dl>" + "".join(f"<dt>{_e(k)}</dt><dd>{v}</dd>" for k, v in pairs if v) + "</dl>"


def _radar_row(d: Dashboard, c: Candidate, elig, waiver_ok: bool, order: int) -> str:
    a = c.add
    pr = a.projection
    proj = _num(pr.mean, 1) if pr.usable else "—"
    gain = c.lineup_gain if c.verdict == LINEUP else None
    desig = d.designations.get(a.sleeper_id, "")
    head_num = (f"<span class=\"stat plus\">{_num(gain, 1, True)}</span><span class=\"lbl\">Δ lineup</span>"
                if gain is not None else
                f"<span class=\"stat\">{proj}</span><span class=\"lbl\">proj</span>")
    badge = f"<span class=\"badge v-{_e(c.verdict)}\">{_e(c.verdict)}</span>"
    meta = f"{_e(a.position)} · {_e(a.team)} · proj {proj}"
    if pr.usable and pr.is_withheld:
        meta += " <span class=\"warn\">(withheld: will not play)</span>"
    if desig:
        meta += f" · <span class=\"warn\">{_e(desig)}</span>"
    pairs: list[tuple[str, str]] = [("Verdict", f"{badge} {_e(_VERDICT_WORD.get(c.verdict, ''))}"),
                                    ("Why", _e(c.reason))]
    if c.verdict == LINEUP and c.drop is not None:
        displ = (f"{_e(c.displaces.name)} ({_e(c.displaces.position)}, "
                 f"{_num(c.displaces.value, 2)}) leaves the lineup"
                 if c.displaces is not None and c.displaces.sleeper_id != c.drop.sleeper_id
                 else f"{_e(c.drop.name)} leaves the roster and the lineup")
        alts = ("; ".join(f"{_e(dp.name)} ({_e(dp.position)}, {_num(dp.value, 2)}) → "
                          f"lineup {_num(g, 2, True)}"
                          + (" <span class=\"warn\">(already played — drop UNVERIFIED)</span>"
                             if drop_rule(dp) else "") for dp, g in c.alternatives)
                or "none — this is the only feasible drop; without it the move is off")
        deadline, note = _deadline_for([a, c.displaces], d.generated)
        pairs += [
            ("Benefit", f"enters <b>{_e(c.slot)}</b>; best legal lineup "
                        f"<b class=\"lime\">{_num(gain, 2, True)}</b> pts THIS WEEK"),
            ("Displaces", displ),
            ("Cost", f"drop <b>{_e(c.drop.name)}</b> ({_e(c.drop.position)}, "
                     f"{_e(c.drop.lineup)}, {_num(c.drop.value, 2)} projected this week)"
                     + (f" — <span class=\"warn\">drop UNVERIFIED:</span> {_e(c.drop_check)}"
                        if c.drop_check else "")),
            ("Alternatives", alts),
            ("Coverage after", _e(_coverage_after(d.roster, c))),
            ("Deadline", _e(note)),
            ("Availability", f"<span class=\"badge held\">UNVERIFIED</span> " + _e(elig.verify)),
            ("Status", ("<span class=\"badge cond\">CONDITIONAL — if available</span> the arithmetic "
                        "is current; endorsed only if Sleeper shows him available" if waiver_ok else
                        "<span class=\"badge held\">WITHHELD</span> the inputs behind this "
                        "comparison are stale; it is the last known picture, not advice")),
        ]
    elif c.versus is not None:
        pairs += [
            ("Versus", f"your cheapest droppable {_e(c.versus.position)}: <b>{_e(c.versus.name)}</b> "
                       f"({_num(c.versus.value, 2)} this week); gap "
                       f"<b>{_num(c.gap, 2, True)}</b>"),
            ("Lineup", "unchanged this week — no starter is displaced, so there is no "
                       "gain to show and no drop is proposed"),
            ("Future value", "not priced here (byes, injuries, role): a rest-of-season model "
                             "does not exist in this repo and is not invented on this page"),
        ]
    kick = (f"kicks off {a.kickoff.astimezone(timezone.utc):%a %d %b %H:%M} UTC"
            if a.kickoff and not a.locked else (a.lock_note or "lock state unknown"))
    pairs.append(("Lock", _e(kick)))
    pairs.append(("Evidence", _e(f"league snapshot {d.snapshot_as_of}; projections from box "
                                 f"scores through week {d.context.stats_through}")))
    if pr.usable:
        pairs.append(("Projection", f"{_num(pr.mean, 2)} ± {_num(pr.sd, 2)} "
                                    f"<details><summary class=\"small\">why</summary>"
                                    f"<pre>{_e(pr.explain())}</pre></details>"))
    elif pr.reasons:
        pairs.append(("Projection", _e("abstained: " + "; ".join(pr.reasons))))
    move = c.verdict == LINEUP
    lapses = move_deadline(c)
    live = (_live_attrs(lapses, gated=move and waiver_ok, move=move,
                        held=move and not waiver_ok)
            + (f" data-verdict-built=\"{_e(c.verdict)}\" data-lapse-verdict=\"LOCKED\""
               if lapses is not None else ""))
    vstate = _e(_HELD) if move and not waiver_ok else ""
    attrs = (f"data-id=\"{_e(a.sleeper_id)}\" data-pos=\"{_e(a.position)}\" "
             f"data-verdict=\"{_e(c.verdict)}\" data-proj=\"{pr.mean if pr.usable else ''}\" "
             f"data-gain=\"{'' if gain is None else gain}\" data-gap=\"{'' if c.gap is None else c.gap}\" "
             f"data-name=\"{_e(a.name.lower())}\" data-team=\"{_e(a.team.lower())}\" data-order=\"{order}\""
             + live)
    return (f"<li class=\"rrow\" id=\"fa-{_e(a.sleeper_id)}\" {attrs}><details><summary>"
            f"<span><span class=\"rname\">{_e(a.name)}</span> {badge}<br>"
            f"<span class=\"rmeta\">{meta}</span><span class=\"vstate\">{vstate}</span></span>"
            f"<span class=\"rnum\">{head_num}</span></summary>"
            f"<div class=\"rbody\">{_dl(pairs)}</div></details></li>")


def _radar_html(d: Dashboard) -> str:
    b = d.board
    out = ["<h2 id=\"free-agents\">Free Agent Radar — every available player against your "
           "roster</h2><div class=\"card\">"]
    waiver_ok = d.gate.allows("waiver")
    if not waiver_ok:
        out.append(f"<div class=\"gatebox\">{_e(d.gate.banner('waiver'))}</div>")
    out.append(f"<p class=\"small sub\">Compared against your roster as of the league snapshot "
               f"{_e(d.snapshot_as_of)}, with projections from box scores through week "
               f"{d.context.stats_through}. Every verdict is for WEEK {d.context.report_week} "
               f"only. A player on any roster in that snapshot is never listed; one claimed "
               f"since it was taken still is, which is why availability stays UNVERIFIED.</p>")
    projected = sum(1 for p in d.pool if p.projected)
    lineup_n = sum(1 for c in b.candidates if c.verdict == LINEUP)
    out.append("<div class=\"kpi\">"
               f"<div>pool<b>{b.pool_size}</b></div>"
               f"<div>with a projection<b>{projected}</b></div>"
               f"<div>compared to lineup<b>{b.evaluated}</b></div>"
               f"<div>missing evidence<b>{b.unprojected}</b></div>"
               f"<div>lineup gains<b class=\"{'lime' if lineup_n else ''}\" "
               f"data-live-count=\"LINEUP\">{lineup_n}</b></div></div>")
    if b.positions:
        out.append("<p class=\"small sub\">By position: " + " · ".join(
            f"<b>{_e(pc.position)}</b> {pc.pool} in pool, {pc.projected} projected, "
            f"{pc.evaluated} compared, {pc.unprojected} without evidence"
            + (f", {pc.locked} locked" if pc.locked else "")
            + (f", {pc.unknown_lock} lock unknown" if pc.unknown_lock else "")
            for pc in b.positions) + ".</p>")
    if b.abstained:
        out.append(f"<p class=\"bad\"><b>ABSTAINED:</b> {_e(b.abstained)}</p>")

    rc = d.radar_changes
    out.append("<h3>Since the last record</h3>")
    if rc is None:
        out.append("<p class=\"sub small\">No comparison was attempted.</p>")
    else:
        out.append(f"<p class=\"small\"><b>{_e(rc.summary())}</b></p>")
        if rc.comparable and rc.refreshed:
            out.append("<p class=\"small sub\">Refreshed inputs: " + _e("; ".join(rc.refreshed))
                       + ". A newer timestamp is not a change in the numbers; the list below "
                         "is the numbers.</p>")
        if rc.items:
            out.append("<ul class=\"chg small\">" + "".join(
                f"<li><span class=\"badge\">{_e(c.kind)}</span> <b>{_e(c.subject)}</b>: "
                f"{_e(c.detail)}</li>" for c in rc.items[:30]) + "</ul>")
            if len(rc.items) > 30:
                out.append(f"<p class=\"small sub\">{len(rc.items) - 30} further change(s) in "
                           f"the archive record.</p>")
        out.append("<p class=\"small sub\">Like for like: the previous decision-time record's "
                   "radar block against this one, by player id. Nothing is recomputed; a "
                   "first run or a week rollover is reported as no comparison, never as "
                   "movement.</p>")

    positions = sorted({c.add.position for c in b.candidates}) or ["QB", "RB", "WR", "TE", "K"]
    out.append("<h3>Candidates</h3>")
    out.append("<div class=\"controls\">"
               "<div><label for=\"radar-q\">Search</label>"
               "<input type=\"search\" id=\"radar-q\" placeholder=\"player or team\" autocomplete=\"off\"></div>"
               "<div><label for=\"radar-v\">Show</label><select id=\"radar-v\">"
               "<option value=\"\">every verdict</option>"
               "<option value=\"LINEUP\">lineup gains</option>"
               "<option value=\"RESEARCH\">research (same position)</option>"
               "<option value=\"COVERAGE\">no comparison</option>"
               "<option value=\"BELOW\">below cheapest droppable</option>"
               "<option value=\"LOCKED\">locked this week</option>"
               "<option value=\"UNRANKED\">under the cap</option></select></div>"
               "<div><label for=\"radar-s\">Sort</label><select id=\"radar-s\">"
               "<option value=\"verdict\">verdict, then gain</option>"
               "<option value=\"gain\">lineup gain</option>"
               "<option value=\"proj\">projection</option>"
               "<option value=\"gap\">gap vs your cheapest droppable</option>"
               "<option value=\"name\">name</option></select></div></div>")
    out.append("<div class=\"chips\" id=\"radar-pos\" role=\"group\" aria-label=\"Position\">"
               "<button type=\"button\" data-pos=\"\" aria-pressed=\"true\">All</button>"
               + "".join(f"<button type=\"button\" data-pos=\"{_e(p)}\" aria-pressed=\"false\">{_e(p)}</button>"
                         for p in positions) + "</div>")
    out.append(f"<p class=\"small sub\" id=\"radar-count\">Showing {len(b.candidates)} of "
               f"{len(b.candidates)} listed candidates; {b.unprojected} more are in the pool "
               f"without a projection and are not listed.</p>")
    elig = eligibility(snapshot_as_of=d.snapshot_as_of)
    if b.candidates:
        out.append("<ol class=\"radar\" id=\"radar-list\">"
                   + "".join(_radar_row(d, c, elig, waiver_ok, i) for i, c in enumerate(b.candidates))
                   + "</ol>")
    else:
        out.append("<p class=\"sub\">No available player carries a projection this week, so "
                   "there is nothing to compare.</p>")
    out.append("<p class=\"small sub\" id=\"radar-empty\" hidden>No candidate matches these "
               "filters.</p>")
    out.append("<p class=\"small warn\">Availability is UNVERIFIED for every row and cannot be "
               "anything else from this cache: the snapshot proves only that the player is on "
               "no roster at its as-of. Whether he is a free agent or sitting on waivers, and "
               "when a claim would process, live in Sleeper\u2019s transactions feed, which this "
               "repo does not pull. Check in the app before bidding.</p>")
    out.append("<p class=\"small sub\">Only a LINEUP verdict is a move, and it is CONDITIONAL "
               "on availability. Raw points across positions rank nothing here; a same-position "
               "gap is research; a locked player cannot enter this week's lineup. Filters and "
               "sort are kept across a reload on this device.</p>")
    if b.droppable:
        out.append("<p class=\"small sub\">Drop candidates, cheapest to lose first: "
                   + ", ".join(f"{_e(p.name)} ({_num(p.value)})" for p in b.droppable[:5])
                   + " — one week of projected points, not roster value.</p>")
    if b.protected:
        out.append("<details><summary>Protected from the drop list ("
                   + str(len(b.protected)) + ")</summary><ul class=\"small\">"
                   + "".join(f"<li><b>{_e(p.name)}</b> ({_e(p.position)}, {_e(p.lineup)}): {_e(r)}</li>"
                             for p, r in b.protected) + "</ul>"
                   "<p class=\"small sub\">These are never offered as an automatic drop. A "
                   "player projected 0 because he is hurt, suspended or on a bye is not a "
                   "player worth 0, and pricing him properly needs a rest-of-season model "
                   "this repo does not have and will not fake (rule #5).</p></details>")
    for n in b.notes:
        out.append(f"<p class=\"small sub\">{_e(n)}</p>")
    out.append("</div>")
    return "".join(out)


#: Filters, search and sort over the server-rendered rows. No data is
#: fetched; the rows carry their numbers as data attributes. The chosen
#: filters live in sessionStorage so a reload (the snapshot banner's, or the
#: reader's) does not erase them. Without JavaScript every row is shown in
#: the build's order, LINEUP first.
_RADAR_JS = r"""
(function(){
'use strict';
var list=document.getElementById('radar-list'), q=document.getElementById('radar-q'), v=document.getElementById('radar-v'),
    s=document.getElementById('radar-s'), chips=document.getElementById('radar-pos'), count=document.getElementById('radar-count'),
    empty=document.getElementById('radar-empty');
if(!list||!q||!v||!s||!chips) return;
var KEY='gridiron:radar:'+(document.querySelector('meta[name="gridiron-build"]')||{getAttribute:function(){return '';}}).getAttribute('data-page');
var RANK={LINEUP:0,RESEARCH:1,COVERAGE:2,BELOW:3,LOCKED:4,UNKNOWN:5,UNRANKED:6};
var state={q:'',verdict:'',sort:'verdict',pos:''}, rows=Array.prototype.slice.call(list.querySelectorAll('li.rrow')), total=rows.length;
function num(x){ var n=parseFloat(x); return isNaN(n)?null:n; }
function load(){ try{ var raw=sessionStorage.getItem(KEY); if(raw){ var o=JSON.parse(raw); ['q','verdict','sort','pos'].forEach(function(k){ if(typeof o[k]==='string') state[k]=o[k]; }); } }catch(e){} }
function save(){ try{ sessionStorage.setItem(KEY,JSON.stringify(state)); }catch(e){} }
function cmp(a,b){ var k=state.sort, x, y;
  function d(el,n){ return el.getAttribute('data-'+n); }
  if(k==='name'){ x=d(a,'name'); y=d(b,'name'); return x<y?-1:x>y?1:0; }
  if(k==='gain'||k==='proj'||k==='gap'){ x=num(d(a,k)); y=num(d(b,k)); if(x===null&&y===null) return 0; if(x===null) return 1; if(y===null) return -1; return y-x; }
  x=RANK[d(a,'verdict')]; y=RANK[d(b,'verdict')]; if(x===undefined) x=9; if(y===undefined) y=9; if(x!==y) return x-y;
  return num(d(a,'order'))-num(d(b,'order')); }
function apply(){ var needle=state.q.trim().toLowerCase(), shown=0;
  rows.slice().sort(cmp).forEach(function(r){ list.appendChild(r); });
  rows.forEach(function(r){ var ok=true;
    if(state.pos&&r.getAttribute('data-pos')!==state.pos) ok=false;
    if(ok&&state.verdict&&r.getAttribute('data-verdict')!==state.verdict) ok=false;
    if(ok&&needle){ var hay=(r.getAttribute('data-name')||'')+' '+(r.getAttribute('data-team')||''); if(hay.indexOf(needle)<0) ok=false; }
    if(ok){ r.removeAttribute('hidden'); shown+=1; } else r.setAttribute('hidden',''); });
  if(count){ var extra=count.getAttribute('data-extra')||''; count.textContent='Showing '+shown+' of '+total+' listed candidates'+(extra?'; '+extra:'')+'.'; }
  if(empty){ if(shown) empty.setAttribute('hidden',''); else empty.removeAttribute('hidden'); }
  Array.prototype.forEach.call(chips.querySelectorAll('button'),function(b){ b.setAttribute('aria-pressed',(b.getAttribute('data-pos')||'')===state.pos?'true':'false'); });
  if(q.value!==state.q) q.value=state.q; if(v.value!==state.verdict) v.value=state.verdict; if(s.value!==state.sort) s.value=state.sort;
  save(); return shown; }
if(count){ var m=/;\s*(.*)\.$/.exec(count.textContent||''); if(m) count.setAttribute('data-extra',m[1]); }
load();
q.addEventListener('input',function(){ state.q=q.value; apply(); });
v.addEventListener('change',function(){ state.verdict=v.value; apply(); });
s.addEventListener('change',function(){ state.sort=s.value; apply(); });
chips.addEventListener('click',function(e){ var b=e.target&&e.target.closest?e.target.closest('button'):null; if(!b||!chips.contains(b)) return; state.pos=b.getAttribute('data-pos')||''; apply(); });
apply();
// A link from the Action Desk (#fa-<id>) must land on its row even when the
// saved filters hide it: the filters are cleared, the row opened and focused.
function reveal(id){ var r=document.getElementById(id); if(!r||!list.contains(r)) return false;
  if(r.hasAttribute('hidden')){ state.q=''; state.verdict=''; state.pos=''; apply(); }
  var dd=r.querySelector('details'); if(dd) dd.open=true;
  var sm=r.querySelector('summary'); if(sm&&sm.focus){ try{ sm.focus({preventScroll:true}); }catch(e){ sm.focus(); } }
  if(r.scrollIntoView) r.scrollIntoView({block:'start'});
  // The hash has done its job; left in the URL it would re-reveal on every
  // reload and wipe the filters the reader chose afterwards.
  try{ if(history.replaceState&&location.hash==='#'+id) history.replaceState(null,'',location.pathname+location.search); }catch(e){}
  return true; }
function fromHash(){ var h=(location.hash||'').slice(1); if(h.indexOf('fa-')===0) reveal(h); }
window.addEventListener('hashchange',fromHash); fromHash();
window.gridironRadar={apply:apply,reveal:reveal,state:function(){ return state; },set:function(o){ o=o||{}; ['q','verdict','sort','pos'].forEach(function(k){ if(typeof o[k]==='string') state[k]=o[k]; }); return apply(); },total:total};
})();
"""


_SOURCE_WORDS = {"sleeper_league": "league snapshot", "sleeper_players": "designations "
                 "(once-a-day player map)", "injuries": "injury report", "schedules": "schedule"}


def _valid_meta(d: Dashboard) -> str:
    """The instant this page's moves stop resting on fresh evidence, by
    the gate's own cadences; empty when nothing on it is endorsed (already
    withheld) or nothing gated can age out."""
    actions = [a for a in ("lineup", "waiver") if d.gate.allows(a)]
    until, name = valid_until(d.sources, actions, d.generated)
    if until is None:
        return ""
    src = next((x for x in d.sources if x.name == name), None)
    pulled = (src.as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
              if src is not None and src.as_of else "at an unknown time")
    why = (f"The {_SOURCE_WORDS.get(name, name)} pulled {pulled} passed its freshness "
           f"limit at {until:%a %d %b %H:%M} UTC, and every lineup move and pickup on "
           f"this page rests on it.")
    return theme.valid_meta(until.isoformat(timespec="seconds"), why)


def render_html(d: Dashboard, *, include_names: bool = True) -> str:
    ctx = d.context
    m = d.matchup
    title = f"Week {ctx.report_week} decision dashboard"
    if include_names:
        title += f" — {d.league_name}"
    nonce = secrets.token_urlsafe(16)
    generated_iso = d.generated.astimezone(timezone.utc).isoformat(timespec="seconds")
    src_by = {x.name: x for x in d.sources}

    def iso(name: str) -> str | None:
        x = src_by.get(name)
        return (x.as_of.astimezone(timezone.utc).isoformat(timespec="seconds")
                if x is not None and x.as_of else None)

    def when(name: str) -> str:
        x = src_by.get(name)
        if x is None or x.as_of is None:
            return "never pulled"
        return (x.as_of.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                + ("" if x.status is Status.FRESH else f" ({x.status.value.upper()})"))

    nd = d.next
    b = d.board
    desk = d.desk()
    until, _ = valid_until(d.sources, [a for a in ("lineup", "waiver") if d.gate.allows(a)],
                           d.generated)
    stale = [x for x in d.sources if x.name in _SOURCE_WORDS and x.status is not Status.FRESH]
    out: list[str] = [
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">",
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">",
        # The board fetches nothing but its own URL (the published-build
        # check) and runs only its own two scripts.
        f"<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; "
        f"connect-src 'self'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
        f"base-uri 'none'; form-action 'none'\">",
        theme.build_meta(generated_iso, "dashboard_latest.html"),
        _valid_meta(d),
        f"<title>{_e(title)}</title>",
        f"<style nonce=\"{nonce}\">{_CSS}</style></head><body><main>",
        theme.nav_html("board"),
        "<header class=\"hero\">",
        f"<div class=\"eyebrow\">Week {ctx.report_week} · Board"
        + (f" · {_e(d.league_name)}" if include_names and d.league_name else "") + "</div>",
        # The headline is the desk's verdict. It carries the evidence expiry
        # like any endorsed card, so a tab left open past it strikes it out.
        "<div class=\"headline\""
        + ("" if desk.hold else f" data-gated=\"\" data-expire-text=\"{_e(_EXPIRED)}\"")
        + f"><h1>{_e(desk.headline)}</h1><span class=\"vstate\"></span></div>",
        "<div class=\"statusbar\">",
    ]
    if d.degraded:
        out.append(f"<a class=\"chip bad\" href=\"#inputs\"><b>DEGRADED</b> "
                   + (f"{len(stale)} input(s) not current" if stale else
                      f"{len(d.notes)} note(s) below") + "</a>")
    else:
        out.append("<span class=\"chip ok\" data-gated=\"\" data-expire-text=\"No longer "
                   "true: these inputs were current when the page was built and have since "
                   "passed their freshness limit.\"><b class=\"ok\">All inputs current "
                   "when built.</b><span class=\"vstate\"></span></span>")
    if until is not None:
        out.append(f"<span class=\"chip\">Moves valid until&nbsp;<b>"
                   f"{theme.time_html(until, _when(until))}</b></span>")
    players_src = src_by.get("sleeper_players")
    warn = " warn" if players_src is not None and players_src.status is not Status.FRESH else ""
    out.append(f"<span class=\"chip{warn}\""
               + (f" data-asof=\"{_e(iso('sleeper_players'))}\"" if iso("sleeper_players") else "")
               + ">Designations&nbsp;<b>"
               + (theme.time_html(players_src.as_of, when("sleeper_players"))
                  if players_src is not None and players_src.as_of else "never pulled")
               + (f" {_e(players_src.status.value.upper())}" if warn else "")
               + "</b><span class=\"age\"></span></span>")
    out.append("</div></header>")
    out.append(theme.validity_html())
    out.append(theme.snapshot_banner_html())
    if d.degraded:
        # The warning and WHICH inputs it concerns stay in view; the verbatim
        # notes (the same facts, per action) fold under it.
        named = "; ".join(f"{_SOURCE_WORDS.get(x.name, x.name)} {x.status.value.upper()} "
                          f"({x.reason})" for x in d.sources if x.status is not Status.FRESH)
        out.append("<div class=\"banner\"><b class=\"bad\">DEGRADED</b> — one or more inputs "
                   "are stale, missing or withheld. Every affected number is blank or "
                   "labelled below; nothing is filled in."
                   + (f"<p class=\"small\">{_e(named)}.</p>" if named else "")
                   + (f"<details><summary>Every note ({len(d.notes)})</summary><ul>"
                      + "".join(f"<li>{_e(n)}</li>" for n in d.notes) + "</ul></details></div>"
                      if named and len(d.notes) > 2 else
                      # nothing to name, or little to say: the notes ARE the warning
                      "<ul>" + "".join(f"<li>{_e(n)}</li>" for n in d.notes) + "</ul></div>"))
    out.append(theme.meta_details(
        "<div class=\"ages\">"
        + theme.age_span("League snapshot", iso("sleeper_league"), when("sleeper_league"))
        + theme.age_span("Projections", iso("weekly_stats"),
                         f"box scores through week {ctx.stats_through}, pulled {when('weekly_stats')}")
        + theme.age_span("Designations", iso("sleeper_players"),
                         when("sleeper_players") + " (once-a-day player map)")
        + theme.age_span("Page built", generated_iso,
                         d.generated.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        + "</div>"
        + f"<p class=\"small sub\">{_e(ctx.headline())} · evidence boundary week "
          f"{ctx.evidence_boundary}</p>"
        + theme.snapshot_strip_html()
        + f"<p class=\"small sub\">{_e(BASELINE_LABEL)}</p>"
        + "<p class=\"small sub\"><a href=\"#inputs\">Every input and what it is good "
          "enough for ↓</a></p>"))

    # ------------------------------------------------- 1. the action desk
    rc = d.radar_changes
    ch0 = d.changes
    out.append(f"<section id=\"desk\" class=\"desk\"><h2>Action Desk — week {ctx.report_week}</h2>"
               "<div class=\"desk-grid\"><div class=\"desk-main\">")
    if d.actions and not any(a.actionable or a.conditional for a in d.actions):
        out.append("<div class=\"gatebox\"><b class=\"bad\">No action is endorsed on "
                   "this data.</b> Every item below is WITHHELD or waits on a check: the "
                   "inputs behind it are stale or unreadable, so what follows is the last "
                   "known picture rather than current advice.</div>")
    for i, item in enumerate(desk.top, 1):
        out.append(_desk_card(item, i, first=i == 1))
    if desk.checks:
        out.append("<h3 class=\"deskh\">Check in Sleeper first</h3>")
        for item in desk.checks:
            out.append(_desk_card(item, None))
    if desk.hold:
        if d.actions and all(a.withheld for a in d.actions) and not desk.checks:
            why = "the inputs behind every comparison are stale or unreadable (see Inputs)"
        elif nd is not None and nd.all_locked:
            why = ("Every starter has kicked off. No lineup change this week is possible, "
                   "and none is proposed; no available player can enter this week's lineup")
        elif d.plan.abstained:
            why = d.plan.abstained
        else:
            why = ("the current lineup is the best legal one the projections find, and no "
                   "available player improves it")
        out.append("<article class=\"dcard tone-hold first\"><div class=\"dhead\"><span "
                   "class=\"badge\">HOLD</span></div><h3>Hold — no supported change</h3>"
                   f"<dl class=\"facts\"><div><dt>Why</dt><dd>{_e(why)}.</dd></div>"
                   "<div><dt>What would change it</dt><dd>A fresh "
                   "league snapshot and player map within their game-day limits, a "
                   "projection edge outside noise, or an available player who enters "
                   "this week's lineup.</dd></div></dl>"
                   "<div class=\"dfoot\"><a class=\"btn\" href=\"#free-agents\">Open the "
                   "Free Agent Radar</a></div></article>")
    if desk.withheld:
        out.append(f"<details class=\"more\"><summary>Last known picture — {len(desk.withheld)} "
                   f"withheld item(s)</summary>"
                   + "".join(_desk_card(item, None) for item in desk.withheld) + "</details>")
    if desk.more:
        out.append(f"<details class=\"more\"><summary>More options ({len(desk.more)})</summary>"
                   + "".join(_desk_card(item, None) for item in desk.more) + "</details>")
    if any(a.kind == "acquire" for a in d.actions):
        out.append("<p class=\"small sub\">Each pickup names the ONE drop it costs and the next "
                   "verified drop. Two pickups that cost the same player are an either/or, "
                   "not two moves.</p>")
    out.append("</div><aside class=\"desk-side\" aria-label=\"This week\">")
    # --- the side panel: this week at a glance
    out.append("<div class=\"panel\"><h3>This week</h3>")
    if nd is not None:
        out.append(f"<p><b>Lineup lock:</b> {nd.locked_starters} of {nd.total_starters} "
                   f"starters locked"
                   + ("" if nd.open_starters else "; nothing on this week's lineup can still change")
                   + ".</p>")
        if nd.open_starters:
            out.append(f"<details><summary>{len(nd.open_starters)} still open</summary><ul>"
                       + "".join(f"<li>{_e(x)}</li>" for x in nd.open_starters) + "</ul></details>")
    roster_line = ("no earlier record to compare against" if ch0 is None else
                   (f"{len(ch0.items)} roster/lineup change(s) since {ch0.previous}"
                    if ch0.any else f"roster, lineup and projections unchanged since {ch0.previous}"))
    out.append("<p><b>What changed:</b> " + _e(roster_line) + " · "
               + (_e(rc.summary()) if rc is not None else "free-agent pool not compared")
               + " <a href=\"#free-agents\">Free Agents ↓</a></p>")
    if m is not None:
        out.append(f"<p><b>Matchup:</b> <span class=\"num\">{_num(m.my_mean)}</span> vs "
                   f"<span class=\"num\">{_num(m.opp_mean)}</span> projected "
                   f"({_num(m.margin, 1, True)}) — context, not advice. "
                   f"<a href=\"#matchup\">Matchup ↓</a></p>")
    lineup_n = sum(1 for c in b.candidates if c.verdict == LINEUP)
    out.append(f"<p><b>Free agents:</b> {b.pool_size} in the pool, {b.evaluated} compared, "
               f"<b class=\"{'lime' if lineup_n else ''}\">{lineup_n}</b> improve this week's "
               f"lineup. <a href=\"#free-agents\">Radar ↓</a></p>")
    if nd is not None:
        out.append("<p class=\"small sub\">" + " · ".join(_e(x) for x in nd.evidence) + "</p>")
    out.append("</div>")
    out.append("<details class=\"panel\"><summary>Watchlist and withheld — research, not moves"
               "</summary>")
    if b.watchlist:
        out.append("<ul class=\"small\">"
                   + "".join(f"<li>{_e(w.describe())}</li>" for w in b.watchlist) + "</ul>")
    for c in b.coverage:
        out.append(f"<p class=\"small warn\">{_e(c)}</p>")
    if not b.watchlist and not b.coverage:
        out.append("<p class=\"small sub\">No available player projects above your cheapest "
                   "droppable player at the same position this week.</p>")
    out.append("<p class=\"small sub\">Same position, this week's projection only, lineup "
               "unchanged. Nothing here is a ranked recommendation, and a player's value "
               "beyond this week is not priced — that would need a rest-of-season model "
               "this repo does not have (rule #5).</p></details>")
    if nd is not None:
        out.append("<details class=\"panel\"><summary>Week transition</summary>")
        out.append("<p><b>Still possible now:</b></p><ul class=\"small\">"
                   + "".join(f"<li>{_e(x)}</li>" for x in nd.still_possible) + "</ul>")
        out.append("<p><b>Must wait for next-week inputs:</b></p><ul class=\"small\">"
                   + "".join(f"<li>{_e(x)}</li>" for x in nd.must_wait) + "</ul>")
        out.append(f"<p><b>Week {nd.next_week} preview (schedule only):</b></p><ul class=\"small\">"
                   + "".join(f"<li>{_e(x)}</li>" for x in nd.next_week_lines) + "</ul></details>")
    out.append("<p class=\"small sub\">Nothing on this page is ever submitted to Sleeper. "
               "Every action is a move the owner makes by hand, and every deadline is "
               "read from the schedule — where the schedule could not be read, the "
               "deadline says UNKNOWN rather than guessing a kickoff.</p>")
    out.append("</aside></div></section>")

    # The sections below are built into their own lists and assembled after:
    # the decision surfaces first, provenance and diagnostics last and
    # folded (their warnings are already in the status bar and banner).
    main_out = out
    # ---------------------------------------------------- inputs & gating
    out = ["<div class=\"card\">"]
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

    sec_inputs = out
    # -------------------------------------------------------- what changed
    out = ["<div class=\"card\">"]
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

    sec_changes = out
    # ------------------------------------------------------------ start/sit
    out = ["<h2 id=\"startsit\">Start / sit — the comparisons behind those actions</h2>"
           "<div class=\"card\">"]
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

    sec_startsit = out
    # ------------------------------------------------ free agent radar
    sec_radar = [_radar_html(d)]
    out = []

    # ------------------------------------------------------------- 6. roster
    slot_of = {}
    for i, p in enumerate(d.plan.current):
        if p is not None:
            slot_of[p.sleeper_id] = f"{d.slots[i]}"
    out.append("<h2 id=\"roster\">Roster projections</h2><div class=\"card\">")
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
    out.append("<h2 id=\"matchup\">Matchup — context, not advice</h2><div class=\"card\">")
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

    sec_roster = out
    # --------------------------------------------------------- track record
    ev = d.evaluation
    out = ["<div class=\"card\">"]
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

    sec_baseline = out
    # ------------------------------------------------------------- archive
    out = ["<div class=\"card\">"]
    if d.archive:
        out.append(f"<p>Written to <code>{_e(d.archive.name)}</code> — the projections, lineup, "
                   "alternatives, upgrades, the freshness gate and which actions were "
                   "actually endorsed, as they were on this page. Grading reads that file "
                   "and the week\u2019s actuals only "
                   "(<code>gridiron.decisions.grade_archive</code>), so a grade can never "
                   "see data that arrived after the decision.</p>")
    else:
        out.append("<p class=\"sub\">Archive not written (dry run).</p>")
    out.append("</div>")
    sec_archive = out

    def sect(sid: str, title: str, hint: str, body: list[str], cls: str = "") -> str:
        return (f"<details class=\"sect\" id=\"{sid}\"><summary><h2>{_e(title)}</h2>"
                f"<span class=\"hint {cls}\">{_e(hint)}</span></summary>{''.join(body)}</details>")

    out = main_out + sec_radar + sec_startsit + sec_roster
    stale_n = sum(1 for x in d.sources if x.status is not Status.FRESH)
    out.append(sect("inputs", "Inputs, and what they are good enough for",
                    f"{stale_n} not current" if stale_n else "all current",
                    sec_inputs, "warn" if stale_n else ""))
    out.append(sect("changes", "Since the last snapshot",
                    "first page" if d.changes is None else
                    (f"{len(d.changes.items)} change(s)" if d.changes.any else "unchanged"),
                    sec_changes))
    out.append(sect("baseline", "How the baseline has fared (chronological, out-of-sample)",
                    "UNVALIDATED" if not ev.n else f"n={ev.n}", sec_baseline))
    out.append(sect("archive", "Decision-time archive",
                    "written" if d.archive else "not written", sec_archive))
    out.append(f"<script nonce=\"{nonce}\">{theme.AGES_JS}</script>")
    out.append(f"<script nonce=\"{nonce}\">{theme.SNAPSHOT_JS}</script>")
    out.append(f"<script nonce=\"{nonce}\">{_RADAR_JS}</script>")
    out.append(f"<script nonce=\"{nonce}\">{theme.VALIDITY_JS}</script>")
    out.append("</main></body></html>")
    return "\n".join(out)
