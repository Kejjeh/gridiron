"""Closed-form head-to-head win probability and its decision derivatives.

The decision layer denominates every move in ΔP(win) (rule #7). Under a
normal approximation of the weekly margin, everything reduces to:

    D = X_a - X_b ~ Normal(mu_a - mu_b, s^2),
    s^2 = sigma_a^2 + sigma_b^2 - 2*rho*sigma_a*sigma_b,
    P(win) = Phi((mu_a - mu_b) / s).

Two derivatives drive strategy:

  dP/dmu_a  = phi(z)/s                      (the "leverage per point" — how
                                             much one expected point is worth
                                             THIS week, peaks in even matchups)
  dP/dsigma_a = -phi(z) * d * (sigma_a - rho*sigma_b) / s^3, d = mu_a - mu_b
                                            (the variance-sign-flip: positive
                                             when trailing — underdogs chase
                                             ceiling; negative when favored —
                                             favorites chase floor)

Verified numbers (docs/research/QUANT_FOUNDATIONS.md §2, reproduced by
scripts/research/verify_winprob.py, N=1e6 Monte Carlo):
  - 12-team full-PPR 9-slot lineup SD ~22.9 -> margin SD s ~32.3 (29-34)
  - leverage at an even matchup = 1.23 pp per projected point
  - Phi vs MC error for P(win): <= 0.25 pp (similar lineups), <= 0.5 pp
    (stacked vs unstacked with the correlated SD)
  - the variance derivative OVERSTATES the underdog's gain under
    right-skewed (gamma) marginals: raising SD at fixed mean lowers the
    lineup median, so the practical breakeven for chasing variance is a
    deficit of ~6 pts (~0.19 s), not 0. Use full MC for variance/skew
    trades at |d| < 15; use these closed forms for mean-leverage.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from math import erf, exp, pi, sqrt
from pathlib import Path

#: The tested spread factor (scripts/research/pwin_calibration.py,
#: docs/research/PWIN_CALIBRATION.md). Absent or not validated, the page
#: shows the raw closed form and says it is UNCALIBRATED; tested and found
#: not to need a factor, it shows the raw closed form labelled TESTED.
CALIBRATION_PATH = Path(__file__).with_name("models") / "pwin_calibration.json"
#: A factor outside this range is not a calibration, it is a broken file.
K_RANGE = (0.8, 2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def norm_pdf(x: float) -> float:
    return exp(-0.5 * x * x) / sqrt(2.0 * pi)


def _margin_sd(sigma_a: float, sigma_b: float, rho: float) -> float:
    var = sigma_a * sigma_a + sigma_b * sigma_b - 2.0 * rho * sigma_a * sigma_b
    # Numerical guard: rho near +/-1 with equal sigmas can round negative.
    return sqrt(max(var, 0.0))


def matchup_win_prob(
    mu_a: float, sigma_a: float, mu_b: float, sigma_b: float, rho: float = 0.0
) -> float:
    """P(team A outscores team B) under the normal margin approximation.

    rho is the correlation between the two LINEUP totals (nonzero when the
    lineups share game environments, e.g. opposing stacks in the same NFL
    game); it is not a player-level correlation.
    """
    s = _margin_sd(sigma_a, sigma_b, rho)
    d = mu_a - mu_b
    if s == 0.0:
        return 0.5 if d == 0.0 else (1.0 if d > 0.0 else 0.0)
    return norm_cdf(d / s)


def leverage_per_point(
    mu_a: float, sigma_a: float, mu_b: float, sigma_b: float, rho: float = 0.0
) -> float:
    """dP(win)/dmu_a: win-probability gained per expected point added to A.

    This is the exchange rate that turns projected points into ΔP(win) —
    the denominator of every start/sit and waiver decision (rule #7).
    """
    s = _margin_sd(sigma_a, sigma_b, rho)
    if s == 0.0:
        return 0.0
    return norm_pdf((mu_a - mu_b) / s) / s


def dpwin_dsigma_a(
    mu_a: float, sigma_a: float, mu_b: float, sigma_b: float, rho: float = 0.0
) -> float:
    """dP(win)/dsigma_a: the variance-sign-flip derivative.

    Positive when A is the underdog (mu_a < mu_b): more lineup variance
    HELPS. Negative when A is favored. Zero in a dead-even matchup.
    """
    s = _margin_sd(sigma_a, sigma_b, rho)
    if s == 0.0:
        return 0.0
    d = mu_a - mu_b
    return -norm_pdf(d / s) * d * (sigma_a - rho * sigma_b) / (s * s * s)


# ------------------------------------------------------------ calibration

@dataclass(frozen=True)
class Calibration:
    """One tested parameter: the factor `k` that widens the margin SD.

    P(win) = Phi(d / (k * s)). The independent-player SD understates how far
    real weekly totals swing (teammates move together, starters get hurt in
    the game, scores are skewed), so `k` > 1 pulls every probability toward
    50%. It is fit on earlier seasons and kept only if it beats k = 1 on
    later ones (rule #5); `validated` False means the page shows the raw
    closed form and labels it UNCALIBRATED. Validated with k = 1 means the
    test's answer was "no correction": the raw closed form, labelled TESTED.
    """

    k: float = 1.0
    validated: bool = False
    evidence: Mapping[str, object] = field(default_factory=dict)
    reason: str = "no tested calibration on file"

    def win_prob(self, mu_a: float, sigma_a: float, mu_b: float, sigma_b: float) -> float:
        return matchup_win_prob(mu_a, self.k * sigma_a, mu_b, self.k * sigma_b)

    def leverage(self, mu_a: float, sigma_a: float, mu_b: float, sigma_b: float) -> float:
        return leverage_per_point(mu_a, self.k * sigma_a, mu_b, self.k * sigma_b)

    @property
    def widens(self) -> bool:
        """A tested factor other than 1 is applied to the spread."""
        return self.validated and abs(self.k - 1.0) > 1e-9

    @property
    def badge(self) -> str:
        return "CALIBRATED" if self.widens else ("TESTED" if self.validated else "UNCALIBRATED")

    @property
    def label(self) -> str:
        if not self.validated:
            return ("UNCALIBRATED — closed-form Φ(margin/SD) on independent, "
                    f"literature-CV projections; {self.reason}")
        ev = self.evidence
        syn = ev.get("synthetic") or {}
        lg = ev.get("league_history") or {}
        if not self.widens:
            return _tested_label(syn, lg)
        parts = [f"CALIBRATED — Φ(margin / ({self.k:.2f} × SD)): the independent-player "
                 f"spread widened {round((self.k - 1) * 100):d}%"]
        if syn:
            parts.append(f"fit on {syn.get('n_fit', '?')} synthetic matchups "
                         f"{_span(syn.get('fit_seasons'))}, tested on {syn.get('n_test', '?')} "
                         f"from {_span(syn.get('test_seasons'))} (Brier "
                         f"{_f3(syn.get('test_brier'))} vs {_f3(syn.get('test_brier_raw'))} "
                         f"uncalibrated)")
        if lg.get("n"):
            parts.append(f"checked on this league's {lg['n']} matchups "
                         f"{_span(lg.get('seasons'))} (Brier {_f3(lg.get('brier'))} vs "
                         f"{_f3(lg.get('brier_raw'))})")
        return "; ".join(parts)

    @property
    def short(self) -> str:
        if self.widens:
            return f"calibrated, SD x{self.k:.2f}"
        return "tested, no correction" if self.validated else "UNCALIBRATED"

    def record(self) -> dict:
        return {"k": self.k, "validated": self.validated, "badge": self.badge,
                "short": self.short, "label": self.label,
                "evidence_doc": (self.evidence or {}).get("doc")}


RAW = Calibration()


def _span(seasons) -> str:
    try:
        s = sorted(int(x) for x in seasons)
    except (TypeError, ValueError):
        return ""
    return f"{s[0]}-{s[-1]}" if s else ""


def _f3(x) -> str:
    try:
        return f"{float(x):.3f}"
    except (TypeError, ValueError):
        return "?"


def _f4(x) -> str:
    try:
        return f"{float(x):.4f}"
    except (TypeError, ValueError):
        return "?"


def _pct(x) -> str:
    try:
        return f"{float(x):.0%}"
    except (TypeError, ValueError):
        return "?"


def _tested_label(syn: Mapping, lg: Mapping) -> str:
    parts = ["TESTED — Φ(margin/SD) as computed, no correction applied"]
    if syn:
        parts.append(f"a spread factor fit on {syn.get('n_fit', '?')} synthetic matchups "
                     f"{_span(syn.get('fit_seasons'))} did not beat it on "
                     f"{syn.get('n_test', '?')} held-out from {_span(syn.get('test_seasons'))} "
                     f"(log loss {_f4(syn.get('test_logloss_raw'))} as is vs "
                     f"{_f4(syn.get('test_logloss'))} with the factor)")
    if lg.get("n"):
        try:
            se = math.sqrt(float(lg["fav_won"]) * (1 - float(lg["fav_won"])) / int(lg["n"]))
            noise = f" (sampling noise ±{se * 100:.0f} points)"
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            noise = ""
        parts.append(f"on this league's {lg['n']} real matchups {_span(lg.get('seasons'))} "
                     f"the favourite was given {_pct(lg.get('fav_stated'))} on average and "
                     f"won {_pct(lg.get('fav_won'))}{noise}")
    return "; ".join(parts)


def load_calibration(path: Path | None = None) -> Calibration:
    """The tested calibration, or `RAW` with the reason it is not used.

    Validated when the file says the out-of-sample bar was cleared
    (`bar_cleared`: better log loss AND Brier than k = 1 on held-out
    seasons) and `k` is a sane number, or when it records that the test
    found no factor worth applying (`raw_tested`, k exactly 1: the raw
    closed form, labelled TESTED). Anything else falls back to the raw
    closed form, labelled UNCALIBRATED, rather than half-trusting a file."""
    path = CALIBRATION_PATH if path is None else path
    try:
        blob = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RAW
    except (OSError, ValueError) as exc:
        return Calibration(reason=f"the calibration file could not be read ({type(exc).__name__})")
    try:
        k = float(blob.get("k"))
    except (TypeError, ValueError):
        return Calibration(reason="the calibration file carries no numeric k")
    if not math.isfinite(k) or not (K_RANGE[0] <= k <= K_RANGE[1]):
        return Calibration(reason=f"the calibration factor {k} is outside {K_RANGE}")
    if blob.get("kind") != "sd_scale":
        return Calibration(reason=f"unknown calibration kind {blob.get('kind')!r}")
    if blob.get("bar_cleared") is True:
        return Calibration(k=k, validated=True, evidence=blob, reason="")
    if blob.get("raw_tested") is True:
        if k != 1.0:
            return Calibration(reason=f"the file says no correction yet carries k = {k}")
        return Calibration(k=1.0, validated=True, evidence=blob, reason="")
    return Calibration(k=k, evidence=blob,
                       reason="the tested factor did not beat the raw formula out of sample")
