"""Weekly usage: opportunity and efficiency, kept explicitly apart (rule #6).

Opportunity (snaps, carries, targets, target share) is what we model fast —
in-season deltas in volume are real signal and move week to week. Efficiency
(yards per target, catch rate, yards per carry, TD rate) is a slow-moving
prior; a two-game efficiency delta is mostly noise. The two never share a
column group here, and `VOLUME_COLUMNS` / `EFFICIENCY_COLUMNS` are exported so
anything downstream has to say which kind of number it is using.

Role comes from usage, not the roster tag (rule #4): the snap/target/carry
share columns are the role, `position` only says which lineup slots a player
is eligible for.

Every join is anchored on `gsis_id` (rule #3). Snap counts are PFR-keyed, so
they come in through the crosswalk's gsis->pfr edge — never on name.

Points are computed by `gridiron.scoring` and nothing else (rule #2).
"""
from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from gridiron.ids import Crosswalk, normalize_id
from gridiron.league_config import DEFAULT_SCORING, ScoringRules
from gridiron.scoring import fantasy_points, kicker_points

#: Opportunity. Fast-moving, modelled explicitly.
VOLUME_COLUMNS: tuple[str, ...] = (
    "offense_snaps", "offense_pct", "carries", "targets", "target_share",
    "air_yards_share", "wopr", "opportunities",
)
#: Efficiency. Slow-moving prior — do NOT read a two-week delta as signal.
EFFICIENCY_COLUMNS: tuple[str, ...] = (
    "catch_rate", "yards_per_target", "yards_per_carry", "td_rate",
    "points_per_opportunity",
)

SKILL_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")


def score_row(row: pd.Series, rules: ScoringRules = DEFAULT_SCORING) -> float:
    """League points for one player-week, routed by position."""
    if str(row.get("position") or "").upper() == "K":
        return kicker_points(row)
    return fantasy_points(row, rules)


def score_frame(weekly: pd.DataFrame,
                rules: ScoringRules = DEFAULT_SCORING) -> pd.DataFrame:
    """Add `league_points` to an nflverse weekly frame. Non-destructive."""
    out = weekly.copy()
    out["league_points"] = [
        score_row(row, rules) for _, row in out.iterrows()
    ]
    return out


def _safe_div(num: pd.Series, den: pd.Series) -> pd.Series:
    den = den.astype("float64")
    return (num.astype("float64") / den.where(den > 0)).astype("float64")


def attach_snaps(weekly: pd.DataFrame, snaps: pd.DataFrame,
                 crosswalk: Crosswalk) -> pd.DataFrame:
    """Join PFR snap counts onto gsis-keyed weekly rows through the crosswalk.

    Returns the weekly frame with `offense_snaps` / `offense_pct` added; both
    are NaN (never 0) for a player whose snaps we could not anchor, because
    "no snap row" and "did not play a snap" are different facts.
    """
    out = weekly.copy()
    out["gsis_id"] = out["player_id"].map(normalize_id)
    out["pfr_id"] = out["gsis_id"].map(lambda g: crosswalk.pfr(g) or "")
    if snaps is None or len(snaps) == 0:
        out["offense_snaps"] = pd.NA
        out["offense_pct"] = pd.NA
        return out
    s = snaps.copy()
    s["pfr_id"] = s["pfr_player_id"].map(normalize_id)
    s = s.loc[s["pfr_id"] != "", ["pfr_id", "season", "week",
                                  "offense_snaps", "offense_pct"]]
    s = s.drop_duplicates(subset=["pfr_id", "season", "week"])
    return out.merge(s, on=["pfr_id", "season", "week"], how="left")


def player_weeks(weekly: pd.DataFrame, snaps: pd.DataFrame,
                 crosswalk: Crosswalk,
                 rules: ScoringRules = DEFAULT_SCORING) -> pd.DataFrame:
    """One scored, snap-joined row per player-week, gsis-anchored."""
    frame = attach_snaps(score_frame(weekly, rules), snaps, crosswalk)
    for col in ("carries", "targets", "receptions", "receiving_yards",
                "rushing_yards", "target_share", "air_yards_share", "wopr",
                "rushing_tds", "receiving_tds", "passing_tds"):
        if col not in frame.columns:
            frame[col] = 0.0
    frame["opportunities"] = (frame["carries"].fillna(0)
                              + frame["targets"].fillna(0))
    return frame


def season_to_date(frame: pd.DataFrame,
                   through_week: int | None = None) -> pd.DataFrame:
    """Aggregate player-weeks into one row per player.

    `through_week` truncates CHRONOLOGICALLY — nothing after it is read. That
    is the boundary that keeps a backtest honest, so it lives here rather
    than being re-implemented per caller.
    """
    f = frame
    if through_week is not None:
        f = f.loc[f["week"] <= int(through_week)]
    if len(f) == 0:
        return pd.DataFrame(columns=["gsis_id", "games"])

    g = f.groupby("gsis_id", dropna=False)
    agg = g.agg(
        player=("player_display_name", "last"),
        position=("position", "last"),
        team=("team", "last"),
        games=("week", "nunique"),
        last_week=("week", "max"),
        points=("league_points", "sum"),
        offense_snaps=("offense_snaps", "sum"),
        offense_pct=("offense_pct", "mean"),
        carries=("carries", "sum"),
        targets=("targets", "sum"),
        receptions=("receptions", "sum"),
        receiving_yards=("receiving_yards", "sum"),
        rushing_yards=("rushing_yards", "sum"),
        target_share=("target_share", "mean"),
        air_yards_share=("air_yards_share", "mean"),
        wopr=("wopr", "mean"),
        opportunities=("opportunities", "sum"),
    ).reset_index()

    tds = g[["rushing_tds", "receiving_tds"]].sum().sum(axis=1)
    agg = agg.merge(tds.rename("scrimmage_tds").reset_index(), on="gsis_id",
                    how="left")

    agg["ppg"] = _safe_div(agg["points"], agg["games"])
    # Efficiency block — slow-moving prior, never a two-week read.
    agg["catch_rate"] = _safe_div(agg["receptions"], agg["targets"])
    agg["yards_per_target"] = _safe_div(agg["receiving_yards"], agg["targets"])
    agg["yards_per_carry"] = _safe_div(agg["rushing_yards"], agg["carries"])
    agg["td_rate"] = _safe_div(agg["scrimmage_tds"], agg["opportunities"])
    agg["points_per_opportunity"] = _safe_div(agg["points"], agg["opportunities"])
    return agg.sort_values("points", ascending=False).reset_index(drop=True)


def last_n_weeks(frame: pd.DataFrame, through_week: int, n: int = 3
                 ) -> pd.DataFrame:
    """Trailing-window usage, for the volume trend. Chronological by
    construction: it reads weeks (through_week - n, through_week] and no
    later week can leak in."""
    lo = int(through_week) - int(n)
    window = frame.loc[(frame["week"] > lo) & (frame["week"] <= int(through_week))]
    return season_to_date(window)


def weeks_present(frame: pd.DataFrame) -> Sequence[int]:
    if frame is None or len(frame) == 0 or "week" not in frame.columns:
        return ()
    return tuple(sorted({int(w) for w in frame["week"].dropna().unique()}))
