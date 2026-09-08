"""Pure draft math: snake order, ADP availability, replacement level, lineups.

Used by scripts/research/draft_board_2026.py and mirrored line-for-line in
the war-room page's JavaScript (scratchpad draftroom_logic.js); the tests in
tests/test_draft.py pin the numbers both must agree on.

Conventions
- Picks are 1-indexed overall pick numbers.
- ADP availability is a Normal(adp, sd) model of the pick at which a player
  goes, with a half-pick continuity correction.
"""
from __future__ import annotations

from math import erf, sqrt

import pandas as pd

from gridiron.league_config import FLEX_ELIGIBLE


def snake_picks(slot: int, teams: int, rounds: int) -> list[int]:
    """Overall pick numbers for draft slot `slot` (1-indexed) in a snake."""
    if not 1 <= slot <= teams:
        raise ValueError("slot must be within 1..teams")
    out = []
    for r in range(1, rounds + 1):
        out.append((r - 1) * teams + slot if r % 2 == 1 else r * teams - slot + 1)
    return out


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + erf(z / sqrt(2.0)))


def adp_sd(adp: float) -> float:
    """Spread of the pick at which a player goes. Linear in ADP, fitted on
    FantasyFootballCalculator's per-player stdev (2026-09-08, n=187), floored
    at one pick so the top of the board is not treated as deterministic."""
    return max(1.0, 0.57 + 0.11 * adp)


def p_available(adp: float, sd: float, pick: int) -> float:
    """Unconditional P(player is still on the board when `pick` comes up)."""
    return 1.0 - norm_cdf((pick - 0.5 - adp) / sd)


def p_survive(adp: float, sd: float, now: int, nxt: int) -> float:
    """P(still available at `nxt` | still available at `now`). Clamped to
    [0, 1]; a player who fell far past ADP gets ~0 (he goes immediately)."""
    if nxt <= now:
        return 1.0
    p_now = p_available(adp, sd, now)
    if p_now <= 1e-12:
        return 0.0
    return max(0.0, min(1.0, p_available(adp, sd, nxt) / p_now))


def replacement_levels(
    df: pd.DataFrame,
    starters: dict[str, int],
    flex_slots: int,
    flex_eligible: tuple[str, ...] = FLEX_ELIGIBLE,
) -> tuple[dict[str, float], dict[str, int]]:
    """Order-statistic fill of the league's starting lineups.

    Fills each position's league-wide starter count by projection, then the
    flex slots from the best remaining flex-eligible players. Returns
    (replacement projection per position = best player left over, and the
    position mix of the flex fill). Positions with nobody left get 0.0.
    """
    taken: set = set()
    for pos, n in starters.items():
        taken |= set(df[df.pos == pos].nlargest(n, "proj").index)
    if flex_slots:
        pool = df[df.pos.isin(flex_eligible) & ~df.index.isin(taken)].nlargest(flex_slots, "proj")
        taken |= set(pool.index)
        flex_mix = {k: int(v) for k, v in pool.pos.value_counts().items()}
    else:
        flex_mix = {}
    repl = {}
    for pos in starters:
        rest = df[(df.pos == pos) & ~df.index.isin(taken)]
        repl[pos] = float(rest.proj.max()) if len(rest) else 0.0
    return repl, flex_mix


def optimal_lineup(
    roster: list[tuple[str, float]],
    slots: dict[str, int],
    flex_eligible: tuple[str, ...] = FLEX_ELIGIBLE,
) -> tuple[float, list[int]]:
    """Greedy optimal starting lineup for a roster of (pos, proj) pairs.

    `slots` uses league_config keys: fixed positions, "FLEX" (flex_eligible),
    "DST" for team defense (roster pos "DEF"), "BENCH" ignored. Fixed slots
    take their best players first, then FLEX takes the best leftovers, which
    is optimal because every flex-eligible position is also a fixed slot.
    Returns (total projection, indices of the starters in roster order).
    """
    alias = {"DST": "DEF"}
    used: list[int] = []
    total = 0.0
    for slot, n in slots.items():
        if slot in ("FLEX", "BENCH"):
            continue
        pos = alias.get(slot, slot)
        cands = sorted((i for i, (p, _) in enumerate(roster) if p == pos and i not in used),
                       key=lambda i: -roster[i][1])[:n]
        used += cands
        total += sum(roster[i][1] for i in cands)
    n_flex = slots.get("FLEX", 0)
    if n_flex:
        cands = sorted((i for i, (p, _) in enumerate(roster) if p in flex_eligible and i not in used),
                       key=lambda i: -roster[i][1])[:n_flex]
        used += cands
        total += sum(roster[i][1] for i in cands)
    return total, sorted(used)
