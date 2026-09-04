"""Vegas line algebra: the one identity everything downstream leans on.

A game with total T and a team quoted at spread S (negative = favored, the
sportsbook convention) implies

    team_total = T/2 - S/2        (expected points for that team)

because E[team + opp] = T and E[team - opp] = -S. The two team totals sum
to T and differ by |S| by construction.

Team-total -> touchdowns/volume mappings need empirical constants; those
live in docs/research/QUANT_FOUNDATIONS.md and enter as parameters when the
projection pipeline is built (build step 3). Only the exact identity lives
here.
"""
from __future__ import annotations

from dataclasses import dataclass

from gridiron.league_config import DEFAULT_SCORING, ScoringRules


def implied_team_total(game_total: float, team_spread: float) -> float:
    """Implied points for the team quoted at team_spread (negative = favored).

    implied_team_total(47, -3) -> 25.0 ; implied_team_total(47, +3) -> 22.0
    """
    if game_total <= 0:
        raise ValueError("game_total must be positive")
    return game_total / 2.0 - team_spread / 2.0


def implied_totals(game_total: float, favorite_spread: float) -> tuple[float, float]:
    """(favorite_total, underdog_total). favorite_spread is the favorite's
    quoted line and must be <= 0 (a pick'em is 0)."""
    if favorite_spread > 0:
        raise ValueError("favorite_spread is the favorite's line; must be <= 0")
    fav = implied_team_total(game_total, favorite_spread)
    return fav, game_total - fav


def implied_totals_from_nflverse(total_line: float, spread_line: float) -> tuple[float, float]:
    """(home_total, away_total) from nflverse schedule columns.

    nflverse's `spread_line` is the OPPOSITE sign convention from sportsbooks:
    positive means the HOME team is favored by that many points (verified
    empirically on 2024 games: corr(spread_line, home margin) = +0.49; the
    2024 Super Bowl row has spread_line -1.5 with the away team favored).
    So home_total = (T + spread_line)/2, i.e. team_spread = -spread_line.

    Use this at the ingest boundary and never hand a raw nflverse spread to
    implied_team_total, which expects the sportsbook sign.
    """
    home = implied_team_total(total_line, -spread_line)
    return home, total_line - home


# ---------------------------------------------------------------------------
# Verified line -> scoring / efficiency coefficients (docs/research/
# QUANT_FOUNDATIONS.md §1; reproduced by scripts/research/verify_vegas.py).
# Pooled OLS on 1,632 nflverse team-games, 2023-25 regular season, closing
# lines. Every scoring/efficiency fit below passed leave-one-season-out vs a
# mean-only baseline (rule #5). The VOLUME functions did not (OOS skill ~0):
# they are league means at a line, never per-game predictors.
# ---------------------------------------------------------------------------

I_REF = 22.0  # league-mean closing implied team total, 2023-25 (22.03)


def expected_team_points(implied_total: float) -> float:
    """E[points | I]; r=0.415, resid SD 9.0, LOSO skill 0.175."""
    return -1.928 + 1.1119 * implied_total


def expected_offensive_tds(implied_total: float) -> float:
    """E[offensive TDs | I]; r=0.396, LOSO skill 0.159."""
    return -0.9448 + 0.15054 * implied_total


def expected_pass_tds(implied_total: float) -> float:
    return -0.5106 + 0.08921 * implied_total


def expected_rush_tds(implied_total: float) -> float:
    return -0.4303 + 0.06101 * implied_total


def expected_fg_made(implied_total: float) -> float:
    """Flat in I (r=0.04): field goals do not scale with the line."""
    return 1.3965 + 0.01390 * implied_total


def league_mean_pass_attempts(team_spread: float, game_total: float) -> float:
    """League-MEAN pass attempts at a line (sportsbook sign). OOS skill ~0 —
    the spread barely moves passing volume; use as a prior, not a forecast."""
    return 24.70 + 0.068 * team_spread + 0.184 * game_total


def league_mean_targets(team_spread: float, game_total: float) -> float:
    """0.954 targets per official pass attempt (throwaways/spikes excluded)."""
    return 0.954 * league_mean_pass_attempts(team_spread, game_total)


def league_mean_rush_attempts(team_spread: float, game_total: float) -> float:
    """League-mean rush attempts, KNEEL-DOWNS EXCLUDED (scrambles included).
    The kneel-inclusive fit has slope -0.289/pt; ~0.04/pt of that is
    victory formation no player is credited with."""
    return 29.68 - 0.250 * team_spread - 0.081 * game_total


def receiving_efficiency_multiplier(implied_total: float) -> float:
    """Scale a receiver's prior points-per-target by the line: the Vegas
    signal reaches receivers ~92% through efficiency, ~8% through volume.
    Pooled fit PPR/target = 0.791 + 0.0420*I, normalised to 1.0 at I_REF
    (1.07 at 25, 0.93 at 19). Beats team-prior-plus-within-adjustment
    out of sample mid-season."""
    return (0.791 + 0.0420 * implied_total) / (0.791 + 0.0420 * I_REF)


def rushing_efficiency_multiplier(implied_total: float) -> float:
    """Same for points-per-carry: 0.210 + 0.0196*I, normalised at I_REF."""
    return (0.210 + 0.0196 * implied_total) / (0.210 + 0.0196 * I_REF)


@dataclass(frozen=True)
class ReceivingEfficiency:
    """Per-target components, nflverse 2023-25 pooled. Stored as components
    (not points) so the ONE scoring implementation supplies the weights."""

    catch_rate: float
    yards_per_target: float
    tds_per_target: float
    adot: float
    targets_n: int


RECEIVING_EFFICIENCY: dict[str, ReceivingEfficiency] = {
    "WR": ReceivingEfficiency(0.630, 7.93, 0.0493, 10.8, 30_251),
    "TE": ReceivingEfficiency(0.720, 7.30, 0.0505, 6.3, 11_512),
    "RB": ReceivingEfficiency(0.784, 5.79, 0.0304, -0.15, 8_991),
}

RB_YARDS_PER_CARRY = 4.29
RB_TDS_PER_CARRY = 0.0309


def points_per_target(position: str, rules: ScoringRules = DEFAULT_SCORING) -> float:
    """Positional prior for fantasy points per target under the league's
    scoring (1.72 WR / 1.75 TE / 1.55 RB at full PPR; 1.40 / 1.39 / 1.15 at
    half). Nearly flat across WR aDOT bands — aDOT moves variance, not mean."""
    e = RECEIVING_EFFICIENCY[position]
    return (
        rules.reception * e.catch_rate
        + rules.rec_yd * e.yards_per_target
        + rules.rec_td * e.tds_per_target
    )


def points_per_carry(rules: ScoringRules = DEFAULT_SCORING) -> float:
    """RB prior: 0.614 at 0.1/yd, 6/TD (35,363 carries)."""
    return rules.rush_yd * RB_YARDS_PER_CARRY + rules.rush_td * RB_TDS_PER_CARRY
