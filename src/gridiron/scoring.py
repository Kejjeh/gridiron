"""Canonical fantasy-point formula. Import this everywhere; never re-derive.

The plv_clone scar (test_sp_fp_formula_copies / test_no_hardcoded_scoring
_weights): scoring formulas copied into scripts drift silently. There is
exactly one implementation, parameterized by league_config.ScoringRules.

Stat keys follow nflverse weekly-data column names so scored frames need no
renaming at the join boundary. nflverse renamed/split several of those
columns in the stats rewrite shipped with nflreadpy 0.1.x:

    interceptions          -> passing_interceptions
    fumbles_lost           -> sack_ + rushing_ + receiving_fumbles_lost
    two_point_conversions  -> passing_ + rushing_ + receiving_2pt_conversions

so each scoring term names the CURRENT columns it sums plus the legacy key it
replaced. A term reads the legacy key only when no current component is
present, so a frame carrying both never double-counts.

Verified 2026-09-17 against two independent ground truths on the 2026 week-1
frame (see tests/test_scoring_nflverse.py, which pins the same numbers on a
committed fixture):
  * standard + full-PPR: `fantasy_points(row, ScoringRules(interception=-2.0,
    reception=0.5))` reproduces (fantasy_points + fantasy_points_ppr) / 2
    exactly for all 357 offensive player-weeks (max abs error 0.0).
  * this league: the same call at DEFAULT_SCORING reproduces Sleeper's own
    `players_points` for all 147 rostered offensive player-weeks, and
    `kicker_points` reproduces it for all 12 rostered kickers.

Team defense (DEFENSE_SCORING) has no implementation yet — nflverse weekly
data is player-level, so DST points have to be aggregated from team stats.
Until that lands there is NO DST scoring here at all: a DST stat line scores
nothing, and a caller must render it as absent rather than as zero points.
Nothing in this module reads Sleeper's `players_points`; that endpoint was
used once, offline, to reconcile the weights above, and wiring it in as a
points source is an open decision, not current behavior.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from gridiron.league_config import DEFAULT_SCORING, KICKING_SCORING, ScoringRules


@dataclass(frozen=True)
class _Term:
    """One scoring line: a ScoringRules weight and the columns it multiplies."""

    rule_field: str
    #: current nflverse weekly columns, summed
    components: tuple[str, ...]
    #: pre-rewrite column names; read ONLY when no component key is present
    legacy: tuple[str, ...] = field(default=())


_TERMS: tuple[_Term, ...] = (
    _Term("pass_yd", ("passing_yards",)),
    _Term("pass_td", ("passing_tds",)),
    _Term("interception", ("passing_interceptions",), ("interceptions",)),
    _Term("rush_yd", ("rushing_yards",)),
    _Term("rush_td", ("rushing_tds",)),
    _Term("reception", ("receptions",)),
    _Term("rec_yd", ("receiving_yards",)),
    _Term("rec_td", ("receiving_tds",)),
    # nflverse counts only offensive lost fumbles here. fumbles_lost_total
    # also carries return fumbles and does NOT reconcile (2 mismatches on the
    # 2026 week-1 frame) — do not substitute it.
    _Term(
        "fumble_lost",
        ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"),
        ("fumbles_lost",),
    ),
    _Term(
        "two_pt",
        ("passing_2pt_conversions", "rushing_2pt_conversions",
         "receiving_2pt_conversions"),
        ("two_point_conversions",),
    ),
)

#: Sleeper kicking stat -> nflverse weekly columns that feed it. fgm_50p is
#: one Sleeper bucket over two nflverse buckets. Blocked kicks (fg_blocked,
#: pat_blocked) are deliberately absent: whether Sleeper scores a blocked
#: attempt as a miss is UNVERIFIED (no blocked kick in the 2026 week-1
#: reconciliation), and guessing would silently change kicker points.
_KICK_COMPONENTS: dict[str, tuple[str, ...]] = {
    "fgm_0_19": ("fg_made_0_19",),
    "fgm_20_29": ("fg_made_20_29",),
    "fgm_30_39": ("fg_made_30_39",),
    "fgm_40_49": ("fg_made_40_49",),
    "fgm_50p": ("fg_made_50_59", "fg_made_60_"),
    "xpm": ("pat_made",),
    "fgmiss": ("fg_missed",),
    "xpmiss": ("pat_missed",),
}


def _num(value: object) -> float:
    """nflverse frames carry nulls/NaN; both score as zero."""
    if value is None:
        return 0.0
    f = float(value)  # type: ignore[arg-type]
    return 0.0 if f != f else f  # NaN


def _term_value(stats: Mapping[str, float], term: _Term) -> float:
    keys = [k for k in term.components if k in stats]
    if not keys:
        keys = [k for k in term.legacy if k in stats]
    return sum(_num(stats[k]) for k in keys)


def fantasy_points(
    stats: Mapping[str, float],
    rules: ScoringRules = DEFAULT_SCORING,
) -> float:
    """Score one OFFENSIVE player-week stat line (QB/RB/WR/TE).

    Missing stat keys count as zero; unknown extra keys are ignored (frames
    carry many non-scoring columns). Kickers go through `kicker_points`.
    """
    return sum(
        _term_value(stats, term) * getattr(rules, term.rule_field)
        for term in _TERMS
    )


def kicker_points(
    stats: Mapping[str, float],
    weights: Mapping[str, float] = KICKING_SCORING,
) -> float:
    """Score one KICKER player-week from nflverse weekly columns.

    `weights` is keyed by Sleeper stat name (league_config.KICKING_SCORING);
    this function owns the mapping from those names onto nflverse columns so
    no script has to know it.
    """
    total = 0.0
    for sleeper_key, cols in _KICK_COMPONENTS.items():
        w = weights.get(sleeper_key)
        if not w:
            continue
        total += w * sum(_num(stats[c]) for c in cols if c in stats)
    return total


def scoring_inputs() -> frozenset[str]:
    """Every column name this module will read. A caller that pulls a frame
    uses it to assert the frame can actually be scored before anything
    downstream trusts the numbers. Legacy aliases are excluded on purpose:
    they are a read-side fallback, not a contract an ingest must satisfy."""
    cols: set[str] = set()
    for term in _TERMS:
        cols.update(term.components)
    for comps in _KICK_COMPONENTS.values():
        cols.update(comps)
    return frozenset(cols)
