"""TE-08: game script for tight ends (blocks when leading, routes when trailing).

No in-season route data exists (participation stops at 2025, its routes are
about 42% filled), so this measures what play-by-play can: the tight end's
share of his team's second-half targets while it trails and while it leads,
season to date, and that split weighted by this week's pre-game spread (the
expected script). Pre-registration: docs/research/blocks/TE-08.md. Play
definitions: _p1a_pbp_roles.py.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
import _p1a_pbp_roles as R

ID = "TE-08"
TITLE = "Script: second-half target share trailing vs leading, by this week's spread"
COLUMNS = {"TE": ["trail_tgt_share_std", "lead_tgt_share_std", "script_tilt"]}
#: Each score-state share is shrunk toward his overall second-half share by
#: this many team targets: a team's second-half targets while leading are
#: under 10 for 11% of the rows, and a share of 1 from a single target is noise.
PRIOR_TARGETS = 10


def favored_by(ctx, keys: pd.DataFrame) -> pd.Series:
    """Points his team is favored by in the row's game (negative: underdog),
    from the pre-game schedule's closing line (`spread_line` is the home
    side's), aligned to `keys`. NaN when the game or its line is missing."""
    seasons = sorted(keys["season"].unique())
    s = ctx.schedule(seasons)[["season", "week", "game_id", "home_team", "away_team",
                               "spread_line"]].copy()
    s["home_team"], s["away_team"] = R.team_code(s["home_team"]), R.team_code(s["away_team"])
    k = keys[["season", "week", "game_id", "team"]].reset_index(drop=True).merge(
        s.drop_duplicates("game_id"), on=["season", "week", "game_id"], how="left")
    team = R.team_code(k["team"])
    line = pd.to_numeric(k["spread_line"], errors="coerce").to_numpy()
    out = np.where(team == k["home_team"], line, np.where(team == k["away_team"], -line, np.nan))
    return pd.Series(out, dtype="float64")


def build(ctx):
    rows = ctx.keys_for("TE")[[*R.KEY, "team", "game_id"]].reset_index(drop=True)
    seasons = sorted(rows["season"].unique())
    pbp = ctx.pbp(seasons, [*R.TARGET_COLUMNS, "score_differential", "qtr"])
    second_half = pd.to_numeric(pbp["qtr"], errors="coerce") >= 3       # overtime included
    margin = pd.to_numeric(pbp["score_differential"], errors="coerce")
    tgt = R.is_target(pbp) & second_half
    hist = R.event_history(ctx, seasons, pbp, {
        "half2_tgt": (tgt, "receiver_player_id"),
        "trail_tgt": (tgt & (margin < 0), "receiver_player_id"),
        "lead_tgt": (tgt & (margin > 0), "receiver_player_id")})
    names = ["half2_tgt", "trail_tgt", "lead_tgt"]
    r = C.as_of(rows[R.KEY], hist, ["gsis_id"], [c for n in names for c in (n, f"{n}_team")])
    overall = C.share(r["half2_tgt_std"], r["half2_tgt_team_std"])
    for state in ("trail", "lead"):
        r[f"{state}_tgt_share_std"] = ((r[f"{state}_tgt_std"] + PRIOR_TARGETS * overall)
                                       / (r[f"{state}_tgt_team_std"] + PRIOR_TARGETS))
    # as_of returns the target rows in their order: the spread aligns by position
    r["script_tilt"] = ((r["trail_tgt_share_std"] - r["lead_tgt_share_std"])
                        * -favored_by(ctx, rows))
    return r[[*R.KEY, *COLUMNS["TE"]]]
