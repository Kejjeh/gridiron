"""The baseline weekly projection: transparent, shrunk, labelled, abstaining.

This is ARCHITECTURE step 3 and nothing more: the projection everything
else is measured against (rule #5). Its shape is the one QUANT_FOUNDATIONS
§1.4 says a baseline has to have — Vegas moves EFFICIENCY, not volume — so

    points = Σ_component  volume × positional efficiency × line multiplier

with the volume side coming from measured usage shrunk toward a role prior
(rule #6: usage stabilizes in ~3 games, efficiency in ~20, so the player's
own efficiency is never read — the positional prior stands in for it) and
the efficiency side being the pooled per-opportunity constants in
`gridiron.vegas`, scaled by the market's implied team total.

Every number that goes into a projection comes back out of it. `Projection`
carries its components (volume, rate, multiplier, points) and its `inputs`
(games of evidence, observed share, the prior it was shrunk toward, the
weight the data got), so a reader can recompute the mean by hand from the
page. That is the transparency requirement, and it is also the only way
a wrong projection can be caught by looking at it.

It ABSTAINS rather than guesses. A player with no admissible box score, a
position with no scoring implementation (DST), or a quarterback in a cache
that holds no quarterback box scores to pool a prior from gets `mean=None`
and a reason, never a league-average number that looks like evidence.

Efficiency priors used here — points per target, points per carry — come
from `gridiron.vegas` and are computed through the ONE scoring
implementation's weights (rule #2). Quarterback and kicker priors are
POOLED FROM THE ADMISSIBLE FRAME at run time (all QB / K player-weeks
through the evidence boundary), because QUANT_FOUNDATIONS carries no
verified constant for them under this league's scoring; the numbers are
stated in `inputs` so they are visible, not baked in.

Weekly SD is mean × positional CV from QUANT_FOUNDATIONS §2.1. That CV is a
literature recommendation, NOT a fit to this league's data, and the
dashboard says so; `gridiron.evaluate` measures how often actuals land
inside it.

UNVALIDATED. `gridiron.models.validated_signals` lists zero validated
features; this module imports it so the rule #5 assert runs at import time.
The chronological evaluation (`gridiron.evaluate`) is how the baseline is
scored against naive alternatives, and its result is rendered next to every
projection.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

from gridiron.league_config import DEFAULT_SCORING, ScoringRules
from gridiron.models import validated_signals as _gate
from gridiron.scoring import fantasy_points
from gridiron.shrinkage import (
    CARRY_SHARE_PRIORS,
    N0_IN_SEASON,
    N0_RB_CARRY_BY_WEEK,
    TARGET_SHARE_PRIORS,
    TEAM_CARRIES_PER_GAME,
    TEAM_TARGETS_PER_GAME,
    eb_shrink,
    shrink_weight,
)
from gridiron.vegas import (
    points_per_carry,
    points_per_target,
    receiving_efficiency_multiplier,
    rushing_efficiency_multiplier,
)

BASELINE_LABEL = f"BASELINE ({_gate.BASELINE}) — UNVALIDATED, rule #5"

PROJECTABLE: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K")

#: Weekly CV by position, QUANT_FOUNDATIONS §2.1/§2.3 slot model. A
#: recommendation from the literature, not a fit; see module docstring.
WEEKLY_CV: dict[str, float] = {
    "QB": 0.42, "RB": 0.55, "WR": 0.58, "TE": 0.65, "K": 0.55,
}
#: A projection under this many points still gets this much SD: a 1-point
#: projection with a 0.5-point SD would claim a precision nothing here has.
SD_FLOOR = 1.5

#: Pseudo-sample size, in GAMES, for the per-game rates that have no share
#: prior (QB passing points, QB carries, K points). Chosen by analogy with
#: the ~3-game stabilization of WR/TE target share (§5.3); stated, not fit.
N0_GAMES_RATE = 3.0

#: A team measured at less than this fraction of the league-mean volume is
#: not a complete team frame (a partial pull, a fixture, a week with one
#: box score in): the league mean stands in and the projection says so.
TEAM_VOLUME_PLAUSIBILITY = 0.5

#: The cutoff that makes a quarterback player-week a STARTER's game when the
#: pooled prior is measured: a mop-up appearance would drag the prior down.
QB_STARTER_MIN_PASS_YARDS = 100.0


# --------------------------------------------------------------------------
# Evidence: what the admissible frame says about each player, team and pool
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class PlayerEvidence:
    gsis_id: str
    position: str
    team: str
    games: int
    targets: float
    carries: float
    team_targets_in_games: float    # team targets in the weeks he played
    team_carries_in_games: float
    receiving_points: float
    rushing_points: float
    passing_points: float
    points: float                   # league points, all terms
    last_week: int | None
    last_week_points: float | None


@dataclass(frozen=True)
class TeamContext:
    team: str
    games: int
    targets: float
    carries: float

    @property
    def targets_per_game(self) -> float:
        return self.targets / self.games if self.games else TEAM_TARGETS_PER_GAME

    @property
    def carries_per_game(self) -> float:
        return self.carries / self.games if self.games else TEAM_CARRIES_PER_GAME


@dataclass(frozen=True)
class PooledPriors:
    """Priors measured from the admissible frame. None = not measurable."""

    qb_pass_ppg: float | None
    qb_carries_per_game: float | None
    qb_rush_pts_per_carry: float | None
    k_ppg: float | None
    qb_games: int
    k_games: int


@dataclass(frozen=True)
class Evidence:
    through_week: int | None
    players: Mapping[str, PlayerEvidence]
    teams: Mapping[str, TeamContext]
    roles: Mapping[str, str]            # gsis -> "WR1", "RB2", ...
    carry_roles: Mapping[str, str]      # gsis -> "RB1"/"RB2" by carries
    pooled: PooledPriors
    rules: ScoringRules = DEFAULT_SCORING


def _f(v: object) -> float:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if x != x else x


def _component_points(row: pd.Series, cols: tuple[str, ...], rules: ScoringRules) -> float:
    """Points from a subset of a stat line, via the ONE implementation."""
    return fantasy_points({c: _f(row.get(c)) for c in cols if c in row.index}, rules)


_PASS_COLS = ("passing_yards", "passing_tds", "passing_interceptions", "interceptions")
_RUSH_COLS = ("rushing_yards", "rushing_tds")
_REC_COLS = ("receptions", "receiving_yards", "receiving_tds")


def build_evidence(weeks: pd.DataFrame, *, through_week: int | None,
                   rules: ScoringRules = DEFAULT_SCORING) -> Evidence:
    """Aggregate scored player-weeks (gridiron.usage.player_weeks output)
    into per-player, per-team and pooled evidence.

    `through_week` is the chronological cut and is applied HERE, before any
    aggregation, so nothing after the evidence boundary can reach a
    projection (the same discipline as `usage.season_to_date`).
    """
    f = weeks
    if through_week is not None:
        f = f.loc[f["week"] <= int(through_week)]
    if f is None or len(f) == 0:
        return Evidence(through_week, {}, {}, {}, {},
                        PooledPriors(None, None, None, None, 0, 0), rules)
    f = f.copy()
    for col in ("targets", "carries", "league_points"):
        if col not in f.columns:
            f[col] = 0.0
    f["gsis_id"] = f["gsis_id"].astype(str) if "gsis_id" in f.columns else f["player_id"].astype(str)

    # Team-week opportunity totals, then attached to each player-week so a
    # player's share is measured over the games HE played.
    tw = (f.groupby(["team", "week"], dropna=False)
            .agg(tt=("targets", "sum"), tc=("carries", "sum")).reset_index())
    f = f.merge(tw, on=["team", "week"], how="left")

    f["_pass"] = [_component_points(r, _PASS_COLS, rules) for _, r in f.iterrows()]
    f["_rush"] = [_component_points(r, _RUSH_COLS, rules) for _, r in f.iterrows()]
    f["_rec"] = [_component_points(r, _REC_COLS, rules) for _, r in f.iterrows()]

    players: dict[str, PlayerEvidence] = {}
    last = f.sort_values("week").groupby("gsis_id").last()
    agg = f.groupby("gsis_id").agg(
        position=("position", "last"), team=("team", "last"),
        games=("week", "nunique"), targets=("targets", "sum"),
        carries=("carries", "sum"), tt=("tt", "sum"), tc=("tc", "sum"),
        rec=("_rec", "sum"), rush=("_rush", "sum"), pas=("_pass", "sum"),
        points=("league_points", "sum"), last_week=("week", "max"))
    for gid, r in agg.iterrows():
        players[str(gid)] = PlayerEvidence(
            gsis_id=str(gid), position=str(r["position"] or "").upper(),
            team=str(r["team"] or ""), games=int(r["games"]),
            targets=_f(r["targets"]), carries=_f(r["carries"]),
            team_targets_in_games=_f(r["tt"]), team_carries_in_games=_f(r["tc"]),
            receiving_points=_f(r["rec"]), rushing_points=_f(r["rush"]),
            passing_points=_f(r["pas"]), points=_f(r["points"]),
            last_week=int(r["last_week"]),
            last_week_points=_f(last.loc[gid, "league_points"]))

    teams: dict[str, TeamContext] = {}
    for team, g in tw.groupby("team", dropna=False):
        teams[str(team)] = TeamContext(str(team), int(g["week"].nunique()),
                                       _f(g["tt"].sum()), _f(g["tc"].sum()))

    # Role from usage, never from the tag (rule #4): rank within team by the
    # opportunity that defines the role.
    roles: dict[str, str] = {}
    carry_roles: dict[str, str] = {}
    pos_group = agg["position"].astype(str).str.upper()
    for (team, pos), g in agg.assign(_pos=pos_group).groupby(["team", "_pos"]):
        if pos in ("WR", "TE", "RB"):
            for rank, gid in enumerate(g.sort_values("targets", ascending=False).index, 1):
                roles[str(gid)] = f"{pos}{rank}"
        if pos == "RB":
            for rank, gid in enumerate(g.sort_values("carries", ascending=False).index, 1):
                carry_roles[str(gid)] = f"RB{rank}"

    qb = f.loc[f["position"].astype(str).str.upper() == "QB"]
    qb_start = qb.loc[qb.get("passing_yards", pd.Series(0.0, index=qb.index)).fillna(0) >= QB_STARTER_MIN_PASS_YARDS] \
        if "passing_yards" in qb.columns else qb.iloc[0:0]
    k = f.loc[f["position"].astype(str).str.upper() == "K"]
    pooled = PooledPriors(
        qb_pass_ppg=float(qb_start["_pass"].mean()) if len(qb_start) else None,
        qb_carries_per_game=float(qb_start["carries"].mean()) if len(qb_start) else None,
        qb_rush_pts_per_carry=(float(qb_start["_rush"].sum() / qb_start["carries"].sum())
                               if len(qb_start) and qb_start["carries"].sum() > 0 else None),
        k_ppg=float(k["league_points"].mean()) if len(k) else None,
        qb_games=int(len(qb_start)), k_games=int(len(k)))
    return Evidence(through_week, players, teams, roles, carry_roles, pooled, rules)


# --------------------------------------------------------------------------
# The projection
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class Component:
    name: str           # "receiving" | "rushing" | "passing" | "kicking"
    volume: float       # opportunities per game (or games, for rate models)
    rate: float         # points per opportunity
    multiplier: float   # line multiplier
    points: float
    note: str

    def describe(self) -> str:
        return (f"{self.name}: {self.volume:.2f} × {self.rate:.3f} × "
                f"{self.multiplier:.3f} = {self.points:.2f} ({self.note})")


@dataclass(frozen=True)
class Projection:
    """mean=None means ABSTAINED; `reasons` says why. When mean is a number,
    `reasons` carries caveats (thin evidence, no line) rather than refusals."""

    mean: float | None
    sd: float | None
    components: tuple[Component, ...] = field(default=())
    reasons: tuple[str, ...] = field(default=())
    inputs: dict = field(default_factory=dict)
    label: str = BASELINE_LABEL

    @property
    def usable(self) -> bool:
        return self.mean is not None

    @property
    def is_withheld(self) -> bool:
        """True when this 0.0 is 'he will not play', not 'he is worthless'.

        `withheld()` parks the model's own mean in `inputs['model_mean']`, so
        the distinction survives into every consumer and the archive. Callers
        that rank players by value MUST check this: a bye-week 0 and a
        replacement-level 4 are not comparable quantities.
        """
        return "model_mean" in self.inputs

    @property
    def model_mean(self) -> float | None:
        """What the model said before the withholding, if anything."""
        return self.inputs.get("model_mean")

    def withheld(self, reason: str) -> "Projection":
        """A zero-point projection for a player who will not play (bye, Out).
        The model mean survives in `inputs['model_mean']` so the page can
        still show what he would have projected to."""
        inputs = dict(self.inputs)
        inputs["model_mean"] = self.mean
        return Projection(0.0, 0.0, self.components, self.reasons + (reason,),
                          inputs, self.label)

    def explain(self) -> str:
        if self.mean is None:
            return "ABSTAINED: " + "; ".join(self.reasons)
        parts = [c.describe() for c in self.components]
        tail = f" | caveats: {'; '.join(self.reasons)}" if self.reasons else ""
        return " + ".join(parts) + f" = {self.mean:.2f} ± {self.sd:.2f}{tail}"


def abstain(*reasons: str) -> Projection:
    return Projection(None, None, (), tuple(reasons))


def _shrunk_rate(observed: float, games: int, prior: float | None,
                 n0_games: float) -> tuple[float, float]:
    """Per-game rate shrunk toward a prior with a games-denominated n0.
    Returns (rate, weight on the data)."""
    if prior is None:
        return observed, 1.0
    w = shrink_weight(float(games), n0_games)
    return w * observed + (1.0 - w) * prior, w


def project(ev: PlayerEvidence | None, *, position: str, week: int,
            implied_total: float | None, evidence: Evidence) -> Projection:
    """Project one player for `week` from evidence through the boundary.

    `position` is the LINEUP-ELIGIBILITY tag (from Sleeper); the role used
    for the prior is measured (`evidence.roles`). `week` selects the RB
    carry-share n0, which decays to nothing by week 6.
    """
    pos = str(position or "").upper()
    if pos not in PROJECTABLE:
        return abstain(f"no projection model for {pos or 'unknown position'}"
                       + (" — team defense has no scoring implementation" if pos in ("DST", "DEF") else ""))
    if ev is None or ev.games <= 0:
        tw = evidence.through_week
        return abstain("no admissible box scores"
                       + (f" through week {tw}" if tw is not None else ""))

    rules = evidence.rules
    reasons: list[str] = []
    inputs: dict = {"games": ev.games, "through_week": evidence.through_week,
                    "implied_total": implied_total}
    if implied_total is None:
        rec_mult = rush_mult = 1.0
        reasons.append("no market line for this week: line multiplier 1.0")
    else:
        rec_mult = receiving_efficiency_multiplier(float(implied_total))
        rush_mult = rushing_efficiency_multiplier(float(implied_total))
    if ev.games < 3:
        reasons.append(f"{ev.games} game(s) of evidence — prior-heavy")

    team = evidence.teams.get(ev.team)
    comps: list[Component] = []

    if pos in ("RB", "WR", "TE"):
        # Receiving: shrunk target share × team targets per game.
        role = evidence.roles.get(ev.gsis_id, "")
        prior = TARGET_SHARE_PRIORS.get(role)
        n_team = ev.team_targets_in_games
        obs_share = ev.targets / n_team if n_team > 0 else 0.0
        if prior is not None and n_team > 0:
            n0 = N0_IN_SEASON["rb_target_share" if pos == "RB" else "wr_te_target_share"]
            share = eb_shrink(ev.targets, n_team, prior[0], n0)
            w = shrink_weight(n_team, n0)
            note = f"tgt share {obs_share:.3f} shrunk to {role} prior {prior[0]:.3f}, w={w:.2f}"
        else:
            share, w = obs_share, 1.0
            note = f"tgt share {obs_share:.3f} unshrunk (no prior for role {role or '?'})"
            reasons.append(f"no target-share prior for role {role or '?'}: usage unshrunk")
        tpg = team.targets_per_game if team else TEAM_TARGETS_PER_GAME
        if tpg < TEAM_VOLUME_PLAUSIBILITY * TEAM_TARGETS_PER_GAME:
            reasons.append(f"team volume measured at {tpg:.1f} tgt/g, below the "
                           f"plausibility floor — league mean {TEAM_TARGETS_PER_GAME} "
                           f"used (frame may be incomplete)")
            tpg = TEAM_TARGETS_PER_GAME
        e_targets = share * tpg
        ppt = points_per_target(pos, rules)
        comps.append(Component("receiving", e_targets, ppt, rec_mult,
                               e_targets * ppt * rec_mult,
                               f"{note}; team {tpg:.1f} tgt/g"))
        inputs.update({"role": role, "target_share_obs": round(obs_share, 4),
                       "target_share_used": round(share, 4), "share_weight": round(w, 3),
                       "team_targets_per_game": round(tpg, 2)})

        # Rushing: RB carry share with the decaying prior; others unshrunk.
        n_c = ev.team_carries_in_games
        obs_c = ev.carries / n_c if n_c > 0 else 0.0
        cpg = team.carries_per_game if team else TEAM_CARRIES_PER_GAME
        if cpg < TEAM_VOLUME_PLAUSIBILITY * TEAM_CARRIES_PER_GAME:
            reasons.append(f"team volume measured at {cpg:.1f} car/g, below the "
                           f"plausibility floor — league mean {TEAM_CARRIES_PER_GAME} "
                           f"used (frame may be incomplete)")
            cpg = TEAM_CARRIES_PER_GAME
        if pos == "RB":
            crole = evidence.carry_roles.get(ev.gsis_id, "")
            cprior = CARRY_SHARE_PRIORS.get(crole)
            n0c = N0_RB_CARRY_BY_WEEK.get(min(max(int(week), 1), 6), 0.0)
            if cprior is not None and n_c > 0 and n0c > 0:
                cshare = eb_shrink(ev.carries, n_c, cprior[0], n0c)
                wc = shrink_weight(n_c, n0c)
                cnote = f"carry share {obs_c:.3f} shrunk to {crole} prior {cprior[0]:.3f}, w={wc:.2f}"
            else:
                cshare = obs_c
                cnote = f"carry share {obs_c:.3f} unshrunk"
            e_carries = cshare * cpg
            inputs.update({"carry_role": crole, "carry_share_obs": round(obs_c, 4),
                           "carry_share_used": round(cshare, 4)})
        else:
            e_carries = ev.carries / ev.games
            cnote = f"{e_carries:.2f} carries/g observed, unshrunk"
        if e_carries > 0:
            ppc = points_per_carry(rules)
            comps.append(Component("rushing", e_carries, ppc, rush_mult,
                                   e_carries * ppc * rush_mult,
                                   f"{cnote}; team {cpg:.1f} car/g"))

    elif pos == "QB":
        p = evidence.pooled
        if p.qb_pass_ppg is None:
            return abstain("no pooled quarterback prior: the admissible frame holds "
                           "no starter-sized QB box scores")
        pass_obs = ev.passing_points / ev.games
        pass_rate, w = _shrunk_rate(pass_obs, ev.games, p.qb_pass_ppg, N0_GAMES_RATE)
        comps.append(Component("passing", 1.0, pass_rate, rec_mult,
                               pass_rate * rec_mult,
                               f"pass pts/g {pass_obs:.2f} shrunk to pooled QB "
                               f"{p.qb_pass_ppg:.2f} (n={p.qb_games} starter-games), w={w:.2f}"))
        car_obs = ev.carries / ev.games
        car_rate, wc = _shrunk_rate(car_obs, ev.games, p.qb_carries_per_game, N0_GAMES_RATE)
        ppc = p.qb_rush_pts_per_carry or 0.0
        if car_rate > 0 and ppc > 0:
            comps.append(Component("rushing", car_rate, ppc, rush_mult,
                                   car_rate * ppc * rush_mult,
                                   f"carries/g {car_obs:.2f} shrunk to pooled QB "
                                   f"{p.qb_carries_per_game:.2f}, w={wc:.2f}; pooled QB "
                                   f"{ppc:.3f} pts/carry"))
        inputs.update({"pooled_qb_pass_ppg": round(p.qb_pass_ppg, 3),
                       "pooled_qb_games": p.qb_games, "pass_ppg_obs": round(pass_obs, 3),
                       "rate_weight": round(w, 3)})

    elif pos == "K":
        p = evidence.pooled
        if p.k_ppg is None:
            return abstain("no pooled kicker prior: the admissible frame holds no "
                           "kicker box scores")
        obs = ev.points / ev.games
        rate, w = _shrunk_rate(obs, ev.games, p.k_ppg, N0_GAMES_RATE)
        comps.append(Component("kicking", 1.0, rate, 1.0, rate,
                               f"pts/g {obs:.2f} shrunk to pooled K {p.k_ppg:.2f} "
                               f"(n={p.k_games}), w={w:.2f}; field goals are flat in "
                               f"the line (§1.3), no multiplier"))
        inputs.update({"pooled_k_ppg": round(p.k_ppg, 3), "ppg_obs": round(obs, 3),
                       "rate_weight": round(w, 3)})

    mean = float(sum(c.points for c in comps))
    sd = max(mean * WEEKLY_CV[pos], SD_FLOOR)
    inputs["cv"] = WEEKLY_CV[pos]
    return Projection(round(mean, 3), round(sd, 3), tuple(comps), tuple(reasons), inputs)
