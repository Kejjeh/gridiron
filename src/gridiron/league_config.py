"""SEASON_YEAR + league constants. Season rollover = bump ONE number here.

STATUS: PLACEHOLDER. Roster slots and scoring below are a standard 12-team
PPR guess. Before build step 3 (first projection model), pull the REAL
league settings from the platform and flip SETTINGS_VERIFIED to True in the
same commit that corrects these values. Downstream engines must refuse to
ship outputs while SETTINGS_VERIFIED is False.
"""
from __future__ import annotations

from dataclasses import dataclass

SEASON_YEAR = 2026

# Which platform hosts the league. "espn" or "sleeper" — verify, don't guess
# from habit (bootstrap doc §3: Sleeper needs no auth and is much nicer).
PLATFORM = "espn"  # TODO: verify

SETTINGS_VERIFIED = False  # flip only after checking the platform's settings

NUM_TEAMS = 12

# Starting lineup slots. FLEX eligibility matters for replacement level
# (bootstrap doc §2) — keep it explicit, never inferred.
ROSTER_SLOTS: dict[str, int] = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 1,  # RB/WR/TE
    "DST": 1,
    "K": 1,
    "BENCH": 7,
}
FLEX_ELIGIBLE: tuple[str, ...] = ("RB", "WR", "TE")


@dataclass(frozen=True)
class ScoringRules:
    """Point weights for offensive stats. Frozen so nothing mutates scoring
    mid-pipeline; a rules change is a new instance and a new commit."""

    pass_yd: float = 0.04          # 1 pt / 25 yards
    pass_td: float = 4.0
    interception: float = -2.0
    rush_yd: float = 0.1           # 1 pt / 10 yards
    rush_td: float = 6.0
    reception: float = 1.0         # PPR — verify (0.5 leagues are common)
    rec_yd: float = 0.1
    rec_td: float = 6.0
    fumble_lost: float = -2.0
    two_pt: float = 2.0


DEFAULT_SCORING = ScoringRules()
