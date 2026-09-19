"""Chronological, out-of-sample evaluation of the baseline projection.

For every week w the cache holds box scores for (w ≥ 2), the evidence is
cut at w−1, the projection is made for w from that evidence and the market
line posted for w, and the result is scored against what actually happened
in w. The cut is `build_evidence(through_week=w-1)`; nothing here can see
week w while predicting it, and `test_evaluate.py` proves it by perturbing
later weeks and asserting earlier predictions do not move.

Three predictors are scored side by side, on the SAME player-weeks:

  baseline        the projection this repo ships (gridiron.projection)
  ppg_to_date     season points-per-game through w−1 — the naive number
                  every fantasy site shows
  last_week       last week's points — the recency heuristic

plus a calibration check on the baseline's SD: the share of actuals that
landed within ±1 SD of the mean (a well-calibrated symmetric interval holds
about 68%; under the right-skewed gamma marginal the literature recommends,
somewhat more).

What this is NOT. It is not the rule #5 gate clearing: the baseline is what
candidate features are measured against, and this report says how the
baseline itself fares against naive alternatives so a reader can see
whether "shrunk usage × efficiency prior × line" is worth more than a PPG
column. With one or two weeks of box scores it says almost nothing and the
page states the n.

P(win) calibration is a separate, harder question — it needs many resolved
matchups — and nothing here claims it. `EvaluationReport.pwin_calibrated`
is False by construction until that evaluation exists.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from math import sqrt

import pandas as pd

from gridiron.league_config import DEFAULT_SCORING, ScoringRules
from gridiron.projection import PROJECTABLE, build_evidence, project
from gridiron.weekly import schedule_index

PREDICTORS: tuple[str, ...] = ("baseline", "ppg_to_date", "last_week")


@dataclass(frozen=True)
class PredictorScore:
    name: str
    n: int
    mae: float | None
    rmse: float | None
    bias: float | None          # mean(pred - actual); + = over-projects

    def line(self) -> str:
        if not self.n or self.mae is None:
            return f"{self.name:<12} n=0"
        return (f"{self.name:<12} n={self.n:<4} MAE {self.mae:5.2f}  "
                f"RMSE {self.rmse:5.2f}  bias {self.bias:+5.2f}")


@dataclass(frozen=True)
class EvaluationReport:
    weeks_evaluated: tuple[int, ...]
    scores: tuple[PredictorScore, ...]
    within_1sd: float | None            # baseline SD coverage, [0,1]
    n_calibration: int
    by_position: Mapping[str, PredictorScore] = field(default_factory=dict)
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)
    pwin_calibrated: bool = False       # never True here; see module docstring
    notes: tuple[str, ...] = field(default=())

    @property
    def n(self) -> int:
        return self.scores[0].n if self.scores else 0

    def score(self, name: str) -> PredictorScore | None:
        return next((s for s in self.scores if s.name == name), None)

    @property
    def baseline_beats_ppg(self) -> bool | None:
        b, p = self.score("baseline"), self.score("ppg_to_date")
        if not b or not p or b.mae is None or p.mae is None:
            return None
        return b.mae < p.mae

    def verdict(self) -> str:
        if self.n == 0:
            return ("NOT EVALUATED — the cache holds no week that can be predicted "
                    "from an earlier one (needs box scores for at least two weeks)")
        wk = ", ".join(str(w) for w in self.weeks_evaluated)
        head = f"evaluated chronologically on {self.n} player-weeks (week(s) {wk})"
        b = self.baseline_beats_ppg
        tail = ("baseline MAE beats season PPG" if b else
                "baseline MAE does NOT beat season PPG" if b is False else
                "no comparison possible")
        cov = (f"; {self.within_1sd:.0%} of actuals within ±1 SD (n={self.n_calibration})"
               if self.within_1sd is not None else "")
        return f"{head}: {tail}{cov}. UNVALIDATED for decisions — n is small."


def _score(name: str, pred: Sequence[float], actual: Sequence[float]) -> PredictorScore:
    n = len(pred)
    if n == 0:
        return PredictorScore(name, 0, None, None, None)
    err = [p - a for p, a in zip(pred, actual)]
    mae = sum(abs(e) for e in err) / n
    rmse = sqrt(sum(e * e for e in err) / n)
    bias = sum(err) / n
    return PredictorScore(name, n, round(mae, 3), round(rmse, 3), round(bias, 3))


def chronological_evaluation(weeks: pd.DataFrame, schedule: pd.DataFrame | None,
                             *, through_week: int | None = None,
                             rules: ScoringRules = DEFAULT_SCORING) -> EvaluationReport:
    """Walk the weeks in order; predict each from the ones before it.

    `weeks` is the scored player-weeks frame (gridiron.usage.player_weeks).
    `through_week` caps the evaluation at the report's own evidence boundary
    so a dashboard rendered mid-week never scores a week it may not read.
    """
    if weeks is None or len(weeks) == 0 or "week" not in weeks.columns:
        return EvaluationReport((), tuple(PredictorScore(p, 0, None, None, None)
                                          for p in PREDICTORS), None, 0,
                                notes=("no box scores in the cache",))
    f = weeks.copy()
    if "gsis_id" not in f.columns:
        f["gsis_id"] = f["player_id"].astype(str)
    present = sorted({int(w) for w in f["week"].dropna().unique()})
    if through_week is not None:
        present = [w for w in present if w <= int(through_week)]
    targets = [w for w in present if any(v < w for v in present)]

    rows: list[dict] = []
    for w in targets:
        ev = build_evidence(f, through_week=w - 1, rules=rules)
        games = schedule_index(schedule, w) if schedule is not None else {}
        this = f.loc[f["week"] == w]
        for r in this.itertuples():
            pos = str(getattr(r, "position", "") or "").upper()
            if pos not in PROJECTABLE:
                continue
            gid = str(r.gsis_id)
            pe = ev.players.get(gid)
            if pe is None or pe.games <= 0:
                continue                         # nothing to predict FROM
            team = str(getattr(r, "team", "") or "")
            implied = games.get(team).implied_total if team in games else None
            p = project(pe, position=pos, week=w, implied_total=implied, evidence=ev)
            if not p.usable:
                continue
            actual = float(getattr(r, "league_points", 0.0) or 0.0)
            rows.append({
                "week": w, "gsis_id": gid, "position": pos, "actual": actual,
                "baseline": float(p.mean), "sd": float(p.sd),
                "ppg_to_date": pe.points / pe.games,
                "last_week": pe.last_week_points,
                "games_before": pe.games,
            })
    frame = pd.DataFrame(rows)
    if len(frame) == 0:
        return EvaluationReport(tuple(targets), tuple(
            PredictorScore(p, 0, None, None, None) for p in PREDICTORS), None, 0,
            rows=frame, notes=("no player-week could be predicted from an earlier week",))
    frame = frame.dropna(subset=list(PREDICTORS))
    scores = tuple(_score(p, frame[p].tolist(), frame["actual"].tolist())
                   for p in PREDICTORS)
    inside = ((frame["actual"] - frame["baseline"]).abs() <= frame["sd"])
    by_pos = {pos: _score("baseline", g["baseline"].tolist(), g["actual"].tolist())
              for pos, g in frame.groupby("position")}
    return EvaluationReport(
        tuple(sorted(frame["week"].unique().tolist())), scores,
        round(float(inside.mean()), 3) if len(frame) else None, int(len(frame)),
        by_position=by_pos, rows=frame,
        notes=("scored on player-weeks where all three predictors exist; a "
               "player's first week is never scored (nothing to predict from)",))
