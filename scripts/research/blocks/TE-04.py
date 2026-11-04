"""TE-04: two- and three-tight-end personnel rate of the week's play-caller, prior seasons only.

Participation's `offense_personnel` ends with 2025 (nflverse has no 2026), so
the play-caller's personnel habit can only be a prior: his rate of 2+ and 3+
tight-end personnel on neutral plays in his career through the season before,
following him across teams, shrunk toward the league. The shipped TE set has
his own snaps and targets this season, nothing about the play-caller's
personnel. Pre-registration: docs/research/blocks/TE-04.md. Definitions:
_tendencies.py.

The table starts in 2019, so no play-caller has a prior season there: the
block is scored on the 2020-2025 folds, and 2019 still trains (unknown).
"""
from __future__ import annotations

import _tendencies as T

ID = "TE-04"
TITLE = "Play-caller's 2+ and 3+ tight-end personnel rate, career through last season"
COLUMNS = {"TE": ["pc_te2_rate", "pc_te3_rate"]}
FOLDS = (2020, 2021, 2022, 2023, 2024, 2025)


def build(ctx):
    keys = ctx.keys_for("TE")[[*T.KEY, "team"]]
    seasons = T.history_seasons("OPC", keys)
    pbp = ctx.pbp(seasons, sorted(set(T.SCRIMMAGE_COLUMNS) | set(T.NEUTRAL_COLUMNS)))
    part = ctx.participation(seasons)
    r = keys.copy()
    for at, col in ((2, "pc_te2_rate"), (3, "pc_te3_rate")):
        t = T.tendency(ctx, keys, T.personnel_obs(pbp, part, at), "OPC")
        t[col] = T.career(t, T.K["personnel"])
        r = r.merge(t[[*T.TEAM_WEEK, col]], on=T.TEAM_WEEK, how="left")
    return r[[*T.KEY, *COLUMNS["TE"]]]
