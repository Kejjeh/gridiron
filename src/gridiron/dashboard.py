"""The weekly decision dashboard: projections, matchup, start/sit, upgrades.

This module assembles the pieces — `projection`, `lineup`, `waivers`,
`evaluate`, `decisions` — into one offline-renderable page and one archive
record. It reads only what the caller hands it (frames from the cache, the
league snapshot, the player dump, the crosswalk) and never opens a socket.

What the page says, in the order it says it:

  1. Context and freshness FIRST — the week, the phase, the evidence
     boundary, and one line per source with its status and reason. Every
     degradation is listed before any number, and the page carries a
     DEGRADED banner whenever anything is stale or missing.
  2. The evaluation verdict, so the reader sees how the baseline has fared
     out-of-sample before reading a single projection, and its n.
  3. The roster, projected: mean ± SD, the components that produced them,
     the availability designation and its source, the lock state, and the
     reason for every abstention or withholding.
  4. The matchup: both lineups' projected totals and SDs, the margin, and
     P(win) from the closed form (`gridiron.winprob`) labelled UNCALIBRATED
     — or abstained, with the reason, when either lineup has an unprojected
     non-DST starter.
  5. Start/sit: the best legal lineup reachable from the current one under
     the kickoff locks, every single-swap alternative with Δpoints, a
     z-score and the ΔP(win) exchange rate (also labelled uncalibrated),
     and the frozen slots with their reasons — or an abstention when the
     lock state is unknown.
  6. Available-player upgrades, each with its explicit drop.
  7. Where the decision-time archive was written.

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

from gridiron.decisions import archive_path, write_archive
from gridiron.evaluate import EvaluationReport, chronological_evaluation
from gridiron.freshness import SourceFreshness, Status, WeekContext, degradations
from gridiron.ids import Crosswalk, is_dst_id, nflverse_team, normalize_id
from gridiron.league_config import DEFAULT_SCORING, LEAGUE_NAME, ScoringRules
from gridiron.lineup import (
    Alternative, LineupPlan, Player, kickoff_index, lock_state, plan_lineup,
    slot_order,
)
from gridiron.projection import (
    BASELINE_LABEL, Evidence, Projection, abstain, build_evidence, project,
)
from gridiron.scoring import ScoringCoverage
from gridiron.waivers import WaiverBoard, available_ids, build_board, pool_players
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
    archive: Path | None = None
    league_name: str = LEAGUE_NAME

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
                    "withheld": "model_mean" in p.projection.inputs,
                    "reasons": list(p.projection.reasons), "locked": p.locked,
                    "availability": p.availability, "flags": list(p.flags),
                    "explain": p.projection.explain()}
        m = self.matchup
        return {
            "generated": self.generated.isoformat(timespec="seconds"),
            "season": self.context.season, "week": self.context.report_week,
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
        locked, lock_note = lock_state(team, kickoffs, now)
        return Player(sid, name, pos, team, proj, lineup, locked, lock_note, avail,
                      tuple(flags), gid)

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
    plan = plan_lineup(roster, starters, slots, locks_known=kickoffs is not None)

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
    board = build_board(roster, pool, starters, slots, locks_known=kickoffs is not None)

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

    dash = Dashboard(context, tuple(sources), tuple(notes), now, tuple(roster), slots,
                     plan, matchup, matchup_reason, board, evaluation,
                     tuple(unresolved), my_roster_id)
    if write_archive_file:
        path = archive_path(context.season, week, now, archive_root)
        write_archive(dash.record(), path)
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
*{box-sizing:border-box}body{margin:0;padding:16px;background:var(--bg);color:var(--fg);
font:14px/1.45 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif}
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
th{font-weight:600;color:var(--muted);white-space:nowrap}td.num{text-align:right;
font-variant-numeric:tabular-nums;white-space:nowrap}
.wrap{overflow-x:auto}details{margin:4px 0}summary{cursor:pointer;color:var(--info)}
code,pre{font:12px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
pre{background:var(--chip);padding:8px;border-radius:6px;overflow-x:auto;white-space:pre-wrap}
ul{margin:6px 0;padding-left:20px}.small{font-size:12px}
.kpi{display:flex;flex-wrap:wrap;gap:10px}.kpi div{flex:1 1 160px;background:var(--chip);
border-radius:6px;padding:8px 10px}.kpi b{display:block;font-size:18px}
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


def render_html(d: Dashboard, *, include_names: bool = True) -> str:
    ctx = d.context
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
    ]
    if d.degraded:
        out.append("<div class=\"banner\"><b class=\"bad\">DEGRADED</b> — one or more inputs "
                   "are stale, missing or withheld. Every affected number is blank or "
                   "labelled below; nothing is filled in.<ul>"
                   + "".join(f"<li>{_e(n)}</li>" for n in d.notes) + "</ul></div>")
    else:
        out.append("<div class=\"banner ok\"><b class=\"ok\">All inputs current.</b></div>")

    # 1. freshness
    out.append("<h2>1. Input freshness</h2><div class=\"card\">")
    rows = [[f"<span class=\"{_status_class(s.status)}\">{_e(s.status.value.upper())}</span>",
             _e(s.name), _e(s.as_of.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M UTC') if s.as_of else 'never'),
             _e(s.coverage()), _e(s.reason)] for s in d.sources]
    out.append(_table(["status", "source", "as-of", "covers", "reason"], rows))
    out.append("</div>")

    # 2. evaluation
    ev = d.evaluation
    out.append("<h2>2. How the baseline has fared (chronological, out-of-sample)</h2><div class=\"card\">")
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

    # 3. roster
    slot_of = {}
    for i, p in enumerate(d.plan.current):
        if p is not None:
            slot_of[p.sleeper_id] = f"{d.slots[i]}"
    out.append("<h2>3. Roster projections</h2><div class=\"card\">")
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
               "never zeroed (rule #11). Withheld = will not play (bye / Out / IR).</p></div>")

    # 4. matchup
    out.append("<h2>4. Matchup</h2><div class=\"card\">")
    m = d.matchup
    if m is None:
        out.append(f"<p class=\"warn\"><b>No matchup view:</b> {_e(d.matchup_reason)}</p>")
    else:
        pw = ("<span class=\"bad\">abstained</span>" if m.pwin is None
              else f"<b>{m.pwin:.0%}</b>")
        out.append("<div class=\"kpi\">"
                   f"<div>my lineup<b>{_num(m.my_mean)} ± {_num(m.my_sd)}</b></div>"
                   f"<div>roster #{m.opponent_roster_id}<b>{_num(m.opp_mean)} ± {_num(m.opp_sd)}</b></div>"
                   f"<div>margin<b>{_num(m.margin, 1, True)}</b></div>"
                   f"<div>P(win) <span class=\"badge warn\">UNCALIBRATED</span>{pw}</div>"
                   f"<div>leverage<b>{'—' if m.leverage is None else f'{m.leverage*100:.2f} pp/pt'}</b></div>"
                   "</div>")
        out.append(f"<p class=\"small sub\">{_e(m.pwin_reason)}. {_e(m.label)}.</p>")
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

    # 5. start/sit
    out.append("<h2>5. Start / sit — legal alternatives under kickoff locks</h2><div class=\"card\">")
    plan = d.plan
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
                dp = _dpwin(m, a.delta_points)
                verdict = ("within noise" if a.within_noise else
                           "favoured" if a.delta_points > 0 else "not favoured")
                cls = "sub" if a.within_noise else ("ok" if a.delta_points > 0 else "bad")
                rows.append([_e(a.bench.name) + f" <span class=\"sub\">({_e(a.bench.position)})</span>",
                             _e(a.slot), _e(a.starter.name if a.starter else "EMPTY"),
                             _num(a.delta_points, 2, True), _num(a.z, 2, True),
                             "—" if dp is None else f"{dp*100:+.2f} pp",
                             f"<span class=\"{cls}\">{verdict}</span>"])
            out.append(_table(["bench player", "into", "for", "Δ pts", "z", "ΔP(win) (uncal.)", "read"],
                              rows, numeric=(3, 4, 5)))
        else:
            out.append("<p class=\"sub\">No unlocked, projected bench player can legally enter a slot.</p>")
    if plan.frozen:
        out.append("<details><summary>frozen (not moved) and why</summary><ul>"
                   + "".join(f"<li>{_e(p.name)} ({_e(p.position)}, {_e(p.lineup)}): {_e(r)}</li>"
                             for p, r in plan.frozen) + "</ul></details>")
    out.append("<p class=\"small sub\">z = Δ / √(SD²+SD²); |z| &lt; 0.5 is within noise. "
               "ΔP(win) = leverage × Δpts, uncalibrated. A swap is listed only if Sleeper "
               "would accept it now (position-eligible, both players unlocked, nobody off IR).</p></div>")

    # 6. waivers
    out.append("<h2>6. Available-player upgrades (with the drop)</h2><div class=\"card\">")
    b = d.board
    out.append(f"<p class=\"small sub\">pool {b.pool_size} available players; {b.evaluated} "
               f"evaluated for a lineup change; {b.unprojected} without a projection (never ranked).</p>")
    if b.abstained:
        out.append(f"<p class=\"bad\"><b>ABSTAINED:</b> {_e(b.abstained)}</p>")
    elif not b.upgrades:
        out.append("<p class=\"ok\">No available player projects above a droppable roster "
                   "player this week.</p>")
    else:
        rows = []
        for u in b.upgrades[:15]:
            dp = _dpwin(m, u.lineup_gain)
            rows.append([_e(u.add.name) + f" <span class=\"sub\">({_e(u.add.position)}, {_e(u.add.team)})</span>",
                         _num(u.add.value), _e(u.drop.name) + f" <span class=\"sub\">({_e(u.drop.position)}, {_e(u.drop.lineup)})</span>",
                         _num(u.drop.value), _e(u.kind.upper()), _e(u.slot or "—"),
                         _num(u.lineup_gain, 2, True), _num(u.depth_gain, 2, True),
                         "—" if dp is None else f"{dp*100:+.2f} pp"])
        out.append(_table(["add", "proj", "drop", "proj", "kind", "enters", "Δ lineup", "Δ depth",
                           "ΔP(win) (uncal.)"], rows, numeric=(1, 3, 6, 7, 8)))
        if b.droppable:
            out.append("<p class=\"small sub\">Drop candidates, cheapest to lose first: "
                       + ", ".join(f"{_e(p.name)} ({_num(p.value)})" for p in b.droppable[:5]) + "</p>")
    for n in b.notes:
        out.append(f"<p class=\"small sub\">{_e(n)}</p>")
    out.append("</div>")

    # 7. archive
    out.append("<h2>7. Decision-time archive</h2><div class=\"card\">")
    if d.archive:
        out.append(f"<p>Written to <code>{_e(d.archive)}</code> — the projections, lineup, "
                   "alternatives and upgrades as they were on this page. Grading reads that "
                   "file and the week's actuals only (<code>gridiron.decisions.grade_archive</code>).</p>")
    else:
        out.append("<p class=\"sub\">Archive not written (dry run).</p>")
    out.append("</div></main></body></html>")
    return "\n".join(out)
