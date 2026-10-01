"""Rule #5, as an import-time assert rather than a habit.

No model feature ships without beating a baseline that contains ALL existing
features, out-of-sample. This module is the registry that gate reads:

  * `BASELINE` names the one projection that everything is measured against.
    It is the ARCHITECTURE step-3 shape — usage prior × positional efficiency
    × line multiplier (docs/research/QUANT_FOUNDATIONS.md §1.4) — and it is
    labelled a BASELINE, never a validated model, everywhere it is rendered.
  * `FEATS` is the list of signals a projection may use ON TOP of the
    baseline. It is empty. Adding a name here without a matching entry in
    `VALIDATED` fails every import of `gridiron.projection`, which is what
    smoke.py runs first.
  * `VALIDATED` maps a feature name to the out-of-sample evidence that it
    beat the full baseline: the chronological evaluation's file, the weeks
    it covered and the metric delta. `gridiron.evaluate` is the tool that
    produces that evidence; a feature is not validated because someone
    believes in it.

The plv_clone scar (rh3/rp3-v2): a candidate signal evaluated against a
stripped-down baseline over-claimed its lift four-fold. The gate exists so
that the comparison is always against everything already in.
"""
from __future__ import annotations

BASELINE = "usage_prior x positional_efficiency x line_multiplier"

#: Signals layered on the baseline.
FEATS: tuple[str, ...] = ("advanced_v1", "advanced_v1_k", "advanced_v1_def", "ros_v1")

#: feature name -> {"evidence": path, "weeks": (lo, hi), "mae_delta": float}
VALIDATED: dict[str, dict] = {
    # gridiron.models.advanced: the full baseline as an input plus expected
    # fantasy points, snap/target/air-yards share, NGS, opponent, vacated
    # opportunity and depth-chart rank. Trained 2023-2024, scored on 2025
    # weeks 4-18 (4,798 player-weeks, 60,813 start/sit pairs): start/sit
    # 64.0% vs the baseline's 63.1%, better in 13 of 15 weeks.
    "advanced_v1": {
        "evidence": "docs/research/ADVANCED_STATS_BACKTEST_2025.md",
        "script": "scripts/research/advanced_model_backtest.py",
        "season": 2025, "weeks": (4, 18),
        "mae_delta": -0.074, "pairwise_delta": 0.0088,
    },
    # Kickers: the baseline kicker projection as an input plus the kicker's
    # own points per game, his team's FG/PAT attempts per game, implied
    # total, spread and dome. 2025 weeks 4-18 (427 kicker-weeks, 5,526
    # pairs): start/sit 56.8% vs the baseline's 53.6%, better in 11 of 15.
    "advanced_v1_k": {
        "evidence": "docs/research/K_DEF_BACKTEST_2025.md",
        "script": "scripts/research/k_def_backtest.py",
        "season": 2025, "weeks": (4, 18),
        "mae_delta": -0.097, "pairwise_delta": 0.0323,
    },
    # Team defenses had NO projection (the board abstained), so the full
    # baseline is the defense's own points per game. Pressure, takeaways,
    # points allowed, the opponent's implied total, sacks allowed and
    # giveaways. 2025 weeks 4-18 (448 team-weeks, 6,138 pairs): start/sit
    # 61.5% vs 54.0%, better in 13 of 15 weeks.
    "advanced_v1_def": {
        "evidence": "docs/research/K_DEF_BACKTEST_2025.md",
        "script": "scripts/research/k_def_backtest.py",
        "season": 2025, "weeks": (4, 18),
        "mae_delta": -0.344, "pairwise_delta": 0.0751,
    },
    # Rest-of-season rankings (gridiron.ros): per position, the method that
    # ordered players best in season-fold cross-validation over 2023-2025
    # (cuts at weeks 4-14, through week 17) — advanced_v1 rates, schedule-
    # nudged or combined; points per game so far was weakest everywhere.
    # Spearman gain over ppg: QB +.059, RB +.019, WR +.052, TE +.075,
    # K +.058, DEF +.077.
    "ros_v1": {
        "evidence": "docs/research/ROS_BACKTEST.md",
        "script": "scripts/research/ros_backtest.py",
        "season": (2023, 2025), "weeks": (4, 14),
        "mae_delta": None, "pairwise_delta": 0.024,
    },
}

_unvalidated = [f for f in FEATS if f not in VALIDATED]
assert not _unvalidated, (
    f"rule #5: feature(s) {_unvalidated} are in FEATS without out-of-sample "
    f"evidence in VALIDATED. Run the chronological evaluation "
    f"(gridiron.evaluate) against the full baseline first."
)
