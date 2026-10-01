"""Rest-of-season (ROS) projections: every remaining fantasy week, summed.

A ROS number answers "what is this player worth from now to the end of the
fantasy season" — the question behind a trade, a drop, or an add you hold
for weeks. It is built from the weekly projection the page already trusts
(`gridiron.models.advanced`, the baseline when the model is inactive) and the
schedule:

  * the RATE is the player's projection for the next week — usage-driven
    (rule #6), from box scores before that week only;
  * each remaining week `v` through `HORIZON_END` is a game or a bye: a team
    missing from a week the schedule HAS games for is on bye; a week with no
    games at all is not covered — never a bye — and is counted as played at
    the plain rate, with the gap stated;
  * optionally each game is nudged by its matchup, through the weekly
    model's OWN coefficients on the matchup features (implied total, the
    opponent's points allowed to the position; for kickers dome and spread;
    for defenses the opponent's implied total). Future lines are not posted,
    so every week's implied total — including the next one, for the
    difference — is ESTIMATED from points scored and allowed so far, shrunk
    toward the league average (`team_strength`).

Whether the matchup nudge earns its place is decided out of sample
(scripts/research/ros_backtest.py, docs/research/ROS_BACKTEST_2025.md); the
shipped setting is `SCHEDULE_ADJUST`.

The playoff weeks (league_config.PLAYOFF_START_WEEK through HORIZON_END) are
also summed on their own: a player who is idle in week 16 is worth less to a
contender than his season total says.
"""
from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.league_config import PLAYOFF_START_WEEK

#: The last fantasy week (the league's playoffs run weeks 15-17).
HORIZON_END = 17
PLAYOFF_WEEKS = tuple(range(PLAYOFF_START_WEEK, HORIZON_END + 1))
#: Team scoring is shrunk toward the league average with this many games of
#: prior weight — four games in, a team's own record counts half.
SHRINK_GAMES = 4.0
#: Home field, in implied points (about half the customary ~1.8-point spread).
HOME_EDGE = 0.9
#: The matchup features, per position, whose coefficients move a future week.
MATCHUP_FEATURES: dict[str, tuple[str, ...]] = {
    "QB": ("implied", "def_allowed"), "RB": ("implied", "def_allowed"),
    "WR": ("implied", "def_allowed"), "TE": ("implied", "def_allowed"),
    "K": ("implied", "spread", "dome"),
    "DEF": ("opp_implied", "home", "spread"),
}
#: Set by the out-of-sample test (docs/research/ROS_BACKTEST.md).
SCHEDULE_ADJUST = True
#: The chosen method per position and the learned combination, written by
#: scripts/research/ros_backtest.py --save.
WEIGHTS_PATH = Path(__file__).resolve().parent / "models" / "ros_weights.json"


def save_weights(blob: Mapping, path: Path = WEIGHTS_PATH) -> None:
    Path(path).write_text(json.dumps(dict(blob), indent=1), encoding="utf-8")


def load_weights(path: Path = WEIGHTS_PATH) -> dict | None:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ------------------------------------------------------------- schedule

def _regular(schedule: pd.DataFrame) -> pd.DataFrame:
    return schedule.loc[schedule["game_type"] == "REG"] if "game_type" in schedule \
        else schedule


@dataclass(frozen=True)
class Strength:
    """Points scored and allowed per game so far, as shrunk deviations from
    the league average `league` (per team-game)."""

    league: float
    offense: Mapping[str, float]
    defense: Mapping[str, float]          # + = allows more than average


def team_strength(schedule: pd.DataFrame | None, before_week: int) -> Strength:
    """Scoring strength from FINAL scores of games before `before_week`."""
    if schedule is None or len(schedule) == 0 \
            or not {"home_score", "away_score"} <= set(schedule.columns):
        return Strength(22.0, {}, {})       # no final scores: every team average
    s = _regular(schedule)
    s = s.loc[(s["week"] < int(before_week)) & s["home_score"].notna() & s["away_score"].notna()]
    scored: dict[str, list[float]] = {}
    allowed: dict[str, list[float]] = {}
    for r in s.itertuples():
        h, a = str(r.home_team), str(r.away_team)
        hs, as_ = float(r.home_score), float(r.away_score)
        scored.setdefault(h, []).append(hs)
        scored.setdefault(a, []).append(as_)
        allowed.setdefault(h, []).append(as_)
        allowed.setdefault(a, []).append(hs)
    every = [x for v in scored.values() for x in v]
    league = float(np.mean(every)) if every else 22.0

    def shrunk(values: list[float]) -> float:
        n = len(values)
        return (float(np.mean(values)) - league) * n / (n + SHRINK_GAMES) if n else 0.0
    return Strength(league, {t: shrunk(v) for t, v in scored.items()},
                    {t: shrunk(v) for t, v in allowed.items()})


def week_context(schedule: pd.DataFrame | None, week: int,
                 strength: Strength) -> dict[str, dict]:
    """team -> the ESTIMATED matchup context of its game in `week` (absent =
    no game that week): opponent, home, dome, implied, opp_implied, spread
    (+ = favoured). Never reads a posted line, so a future week and the next
    one are measured the same way."""
    if schedule is None or len(schedule) == 0:
        return {}
    s = _regular(schedule)
    s = s.loc[s["week"] == int(week)]
    out: dict[str, dict] = {}
    for r in s.to_dict("records"):
        home, away = str(r["home_team"]), str(r["away_team"])
        dome = 1.0 if str(r.get("roof") or "").lower() in ("dome", "closed") else 0.0
        imp = {}
        for team, opp, edge in ((home, away, HOME_EDGE), (away, home, -HOME_EDGE)):
            imp[team] = (strength.league + strength.offense.get(team, 0.0)
                         + strength.defense.get(opp, 0.0) + edge)
        for team, opp, is_home in ((home, away, 1.0), (away, home, 0.0)):
            out[team] = {"opponent": opp, "home": is_home, "dome": dome,
                         "implied": imp[team], "opp_implied": imp[opp],
                         "spread": imp[team] - imp[opp]}
    return out


def allowed_to_position(history: pd.DataFrame, before_week: int) -> dict[tuple[str, str], float]:
    """(defense team, position) -> points it allowed to the position per game
    minus the league's — the weekly model's `def_allowed`, before a week."""
    past = history.loc[history["week"] < int(before_week)]
    if len(past) == 0 or "opponent_team" not in past:
        return {}
    per = (past.groupby(["opponent_team", "position", "week"])["league_points"].sum()
           .reset_index())
    allowed = per.groupby(["opponent_team", "position"])["league_points"].mean()
    league = per.groupby("position")["league_points"].mean()
    return {(str(t), str(p)): float(v - league.get(p, np.nan))
            for (t, p), v in allowed.items() if v == v}


def matchup_values(position: str, ctx: Mapping[str, object] | None,
                   allowed: Mapping[tuple[str, str], float]) -> dict[str, float]:
    """The matchup features of one game (NaN where unknown)."""
    if ctx is None:
        return {c: math.nan for c in MATCHUP_FEATURES.get(position, ())}
    vals = {k: float(ctx[k]) for k in ("implied", "opp_implied", "spread", "dome", "home")}
    vals["def_allowed"] = allowed.get((str(ctx["opponent"]), position), math.nan)
    return {c: vals[c] for c in MATCHUP_FEATURES.get(position, ())}


def slopes(ridge, position: str) -> dict[str, float]:
    """Raw-unit slope of each matchup feature in a fitted `Ridge` (0 when the
    model does not use it)."""
    out = {}
    for c in MATCHUP_FEATURES.get(position, ()):
        if ridge is not None and c in ridge.cols:
            i = ridge.cols.index(c)
            out[c] = float(ridge.beta[i + 1]) / float(ridge.sd[i])
        else:
            out[c] = 0.0
    return out


# ------------------------------------------------------------ projection

@dataclass(frozen=True)
class RosLine:
    rate: float
    ros: float
    playoff: float
    games: int
    byes: tuple[int, ...]
    weeks: tuple[tuple[int, str, float], ...] = field(default=())   # (week, opp, points)


class Schedule:
    """The remaining schedule as of `week`, computed once and shared by
    every player: per-week estimated contexts and positional allowances."""

    def __init__(self, schedule: pd.DataFrame | None, week: int,
                 history: pd.DataFrame | None = None, *, end: int = HORIZON_END):
        self.week, self.end = int(week), int(end)
        self.strength = team_strength(schedule, week)
        self.contexts = {v: week_context(schedule, v, self.strength)
                         for v in range(self.week, self.end + 1)}
        self.allowed = allowed_to_position(history, week) if history is not None else {}
        #: weeks the schedule has NO games for: not declared, so not a bye —
        #: every team is counted as playing at its plain rate, and said so
        self.uncovered = tuple(v for v, c in self.contexts.items() if not c)

    def project(self, position: str, team: str, rate: float, *, ridge=None,
                adjust: bool = SCHEDULE_ADJUST, skip: Iterable[int] = (),
                clip: bool = True, ref_week: int | None = None) -> RosLine:
        """ROS for one player whose projection for `ref_week` (default: the
        next week) is `rate`. `skip` = weeks known to be missed."""
        skip = {int(w) for w in skip}
        pos = "DEF" if position in ("DEF", "DST") else position
        slope = slopes(ridge, pos) if adjust else {}
        ref_ctx = self.contexts.get(int(ref_week or self.week), {}).get(team)
        ref = matchup_values(pos, ref_ctx, self.allowed)
        if ridge is not None:
            # no game in the rate's week: it was predicted at the training means
            ref = {c: (v if v == v else (ridge.fill[ridge.cols.index(c)]
                                         if c in ridge.cols else math.nan))
                   for c, v in ref.items()}
        weeks, byes = [], []
        for v in range(self.week, self.end + 1):
            ctx = self.contexts[v].get(team)
            if v in self.uncovered:
                weeks.append((v, "?", 0.0 if v in skip else
                              (max(0.0, rate) if clip else rate)))
                continue
            if ctx is None:
                byes.append(v)                   # the schedule declares the week
                continue
            if v in skip:
                weeks.append((v, str(ctx["opponent"]), 0.0))
                continue
            x = matchup_values(pos, ctx, self.allowed)
            delta = sum(s * (x[c] - ref[c]) for c, s in slope.items()
                        if s and x[c] == x[c] and ref.get(c) == ref.get(c))
            pts = rate + delta
            weeks.append((v, str(ctx["opponent"]), max(0.0, pts) if clip else pts))
        ros = sum(p for _, _, p in weeks)
        playoff = sum(p for v, _, p in weeks if v in PLAYOFF_WEEKS)
        return RosLine(round(float(rate), 3), round(ros, 2), round(playoff, 2),
                       sum(1 for v, _, _ in weeks if v not in skip), tuple(byes),
                       tuple((v, o, round(p, 2)) for v, o, p in weeks))


def rank(frame: pd.DataFrame, *, by: str = "ros") -> pd.DataFrame:
    """Add `pos_rank` (1 = best) within each position, sorted."""
    out = frame.sort_values(["position", by], ascending=[True, False]).copy()
    out["pos_rank"] = out.groupby("position").cumcount() + 1
    return out


# ------------------------------------------------------------ the table

def build_table(*, weeks: pd.DataFrame | None, schedule: pd.DataFrame | None,
                injuries: pd.DataFrame | None, inputs: Mapping | None, week: int,
                positions: Mapping[str, str] | None = None,
                teams: Mapping[str, str] | None = None, model=None,
                weights: Mapping | None = None,
                out: Iterable[str] = ()) -> tuple[pd.DataFrame, str]:
    """Every projectable player's ROS line, keyed by gsis id (team
    defenses by `DEF:<team>`).

    Per player it computes each candidate per-game rate the backtest scored
    — points per game so far (`ppg`), the baseline weekly projection
    (`base`), the page's weekly number (`adv`: `gridiron.projection.project`
    refined by `advanced_v1` where it applies, before withholding) and its
    schedule-nudged average (`sched`) — and ranks by the method the
    cross-validation chose for that position (`weights["choice"]`; the
    learned combination is `ros_model`). `positions` (gsis -> the platform's
    eligibility tag) overrides the box-score position; `teams` overrides the
    last team seen (a trade); `out` (gsis ids ruled Out for `week`) are
    credited 0 that week — never beyond it. Returns (table, status)."""
    from gridiron.models import advanced as A
    from gridiron.projection import build_evidence, project
    from gridiron.weekly import schedule_index

    cols = ["gsis_id", "position", "team", "method", "rate", "next_week", "source",
            "ppg", "games_played", "games_left", "byes", "ros", "ros_pg", "playoff",
            "depth_rank", "weeks"]
    if weeks is None or len(weeks) == 0:
        return pd.DataFrame(columns=cols), "no box scores to project from"
    weights = weights if weights is not None else (load_weights() or {})
    choice = dict(weights.get("choice") or {})
    stack = {p: A.Ridge.from_json(v) for p, v in (weights.get("stack") or {}).items()}
    ev = build_evidence(weeks, through_week=int(week) - 1)
    games = schedule_index(schedule, int(week)) if schedule is not None else {}
    ctx = A.build_context(weeks=weeks, inputs=inputs, injuries=injuries, schedule=schedule,
                          week=int(week), teams=teams, model=model)
    sched = Schedule(schedule, int(week), weeks)
    out_ids = {str(g) for g in out}
    positions, teams = positions or {}, teams or {}

    def finish(gid, pos, team, cand, *, ridge, source, played, clip=True, ref_week=None):
        skip = (int(week),) if gid in out_ids else ()
        sch = sched.project(pos, team, cand["adv"], ridge=ridge, adjust=True, clip=clip,
                            ref_week=ref_week)
        cand["sched"] = (sch.ros / sch.games) if sch.games else cand["adv"]
        method = choice.get(pos, "adv_sched" if SCHEDULE_ADJUST else "adv")
        if method == "adv_sched":
            line = sched.project(pos, team, cand["adv"], ridge=ridge, adjust=True, skip=skip,
                                 clip=clip, ref_week=ref_week)
            rate = cand["sched"]
        else:
            if method == "ros_model" and pos in stack:
                rate = stack[pos].predict_row({**cand, "games_before": played})
            else:
                method = method if method in cand and cand[method] is not None else "adv"
                rate = cand[method]
            rate = max(0.0, rate) if clip else rate
            line = sched.project(pos, team, rate, adjust=False, skip=skip, clip=clip)
        return {**_row(gid, pos, team, line, source, cand.get("ppg"), played),
                "method": method, "rate": round(float(rate), 3),
                "next_week": round(float(cand["adv"]), 3)}

    rows = []
    for gid, pe in ev.players.items():
        pos = str(positions.get(gid) or pe.position or "").upper()
        team = str(teams.get(gid) or pe.team or "")
        if pos not in ("QB", "RB", "WR", "TE", "K") or not team:
            continue
        g = games.get(team)
        base = project(pe, position=pos, week=int(week),
                       implied_total=getattr(g, "implied_total", None), evidence=ev)
        if not base.usable:
            continue
        ppg = pe.points / pe.games if pe.games else None
        hit = ctx.refine(gid, pos, float(base.mean), ppg) if ctx.active else None
        cand = {"ppg": ppg if ppg is not None else float(base.mean),
                "base": float(base.mean), "adv": float(hit[0]) if hit else float(base.mean)}
        ridge = ctx.model.adv.get(pos) if hit and ctx.model is not None else None
        row = finish(gid, pos, team, cand, ridge=ridge,
                     source=A.NAME if hit else "baseline", played=pe.games)
        # the depth chart before the week (shown, never priced: who keeps a
        # shared job is not something a ROS number can know)
        dr = (ctx.features.get(gid) or {}).get("depth_rank")
        row["depth_rank"] = None if dr is None or dr != dr else int(dr)
        rows.append(row)
    if ctx.model is not None and "DEF" in ctx.model.adv:
        dh = (inputs or {}).get("defense")
        if dh is not None and len(dh):
            dridge = ctx.model.adv["DEF"]
            done: set[str] = set()
            for v in range(int(week), sched.end + 1):
                for r in A.defense_features_as_of(dh, v, schedule).to_dict("records"):
                    t = str(r["team"])
                    if t in done:
                        continue
                    done.add(t)
                    past = dh.loc[(dh["team"] == t) & (dh["week"] < int(week))]
                    ppg = float(past["dst_points"].mean()) if len(past) else None
                    adv = dridge.predict_row(r)
                    cand = {"ppg": ppg if ppg is not None else adv, "adv": adv}
                    rows.append(finish(f"DEF:{t}", "DEF", t, cand, ridge=dridge,
                                       source=A.NAME, played=int(len(past)), clip=False,
                                       ref_week=v))
    table = pd.DataFrame(rows, columns=cols)
    status = (ctx.status if ctx.active else f"baseline rates ({ctx.status})")
    if sched.uncovered:
        status += (f"; the schedule has no games for week(s) "
                   f"{', '.join(map(str, sched.uncovered))} — counted as played, NOT as byes")
    if choice:
        status += "; ROS method by position: " + ", ".join(f"{p} {m}" for p, m in choice.items())
    return rank(table), status


def _row(gid, pos, team, line: RosLine, source, ppg, played) -> dict:
    return {"gsis_id": gid, "position": pos, "team": team, "source": source,
            "ppg": None if ppg is None else round(float(ppg), 2),
            "games_played": int(played), "games_left": line.games,
            "byes": ",".join(str(b) for b in line.byes), "ros": line.ros,
            "ros_pg": round(line.ros / line.games, 2) if line.games else 0.0,
            "playoff": line.playoff,
            "weeks": " ".join(f"{v}:{o}:{p:g}" for v, o, p in line.weeks)}


def ownership(table: pd.DataFrame, *, snapshot: Mapping, players: Mapping[str, Mapping],
              crosswalk, owner_id: str | None, manager_names: bool = False) -> pd.DataFrame:
    """Join each ROS row to the league: Sleeper id (through the crosswalk —
    rule #3, never a name), display name, and who holds him: MINE, FA, or
    another roster (its manager's display name only when `manager_names`;
    the published record never carries other managers' names)."""
    from gridiron.ids import is_dst_id, nflverse_team, normalize_id
    g2s: dict[str, str] = {}
    dst: dict[str, str] = {}
    for sid in players:
        sid = normalize_id(sid)
        if is_dst_id(sid):
            dst[nflverse_team(sid)] = sid
            continue
        gid = crosswalk.gsis(sid)
        if gid and gid not in g2s:
            g2s[gid] = sid
    users = {str(u.get("user_id")): str(u.get("display_name") or "")
             for u in snapshot.get("users") or []}
    held: dict[str, str] = {}
    for r in snapshot.get("rosters") or []:
        owners = {str(r.get("owner_id"))} | {str(c) for c in (r.get("co_owners") or [])}
        label = ("MINE" if owner_id is not None and str(owner_id) in owners else
                 (users.get(str(r.get("owner_id"))) or f"roster {r.get('roster_id')}")
                 if manager_names else "rostered")
        for sid in r.get("players") or []:
            held[normalize_id(sid)] = label
    out = table.copy()
    sids, names, holders, status = [], [], [], []
    for gid, pos, team in zip(out["gsis_id"], out["position"], out["team"]):
        sid = dst.get(str(team), str(team)) if pos == "DEF" else g2s.get(str(gid), "")
        rec = players.get(sid) or {}
        sids.append(sid)
        names.append(f"{team} DST" if pos == "DEF" else
                     (str(rec.get("full_name") or "") or crosswalk.display_name(gid) or str(gid)))
        holders.append(held.get(sid, "FA") if sid else "unresolved")
        status.append(str(rec.get("injury_status") or ""))
    out["sleeper_id"], out["name"], out["held_by"], out["injury"] = sids, names, holders, status
    return out
