"""QB-03: neutral pass rate over expected of the week's play-caller, per person.

The +team block (FEATURE_EXPANSION.md) gave the model the offense's pass rate
over its last 3 games and failed. This block gives the PERSON calling the
plays: his neutral pass rate over expected in his career before this season
(any team, following him across teams) and this season with this team so
far, each shrunk toward the league by its plays. Pre-registration:
docs/research/blocks/QB-03.md. Definitions: _tendencies.py.

The table starts in 2019, so no play-caller has a career there: the block is
scored on the 2020-2025 folds, and 2019 still trains (career unknown).
"""
from __future__ import annotations

import _tendencies as T

ID = "QB-03"
TITLE = "Play-caller's neutral pass rate over expected: career and this season"
COLUMNS = {"QB": ["pc_proe_career", "pc_proe_season"]}
FOLDS = (2020, 2021, 2022, 2023, 2024, 2025)


def build(ctx):
    keys = ctx.keys_for("QB")[[*T.KEY, "team"]]
    pbp = ctx.pbp(T.history_seasons("OPC", keys), T.PROE_COLUMNS)
    t = T.tendency(ctx, keys, T.proe_obs(pbp), "OPC")
    t["pc_proe_career"] = T.career(t, T.K["proe"])
    t["pc_proe_season"] = T.this_season(t, T.K["proe"])
    r = keys.merge(t[[*T.TEAM_WEEK, *COLUMNS["QB"]]], on=T.TEAM_WEEK, how="left")
    return r[[*T.KEY, *COLUMNS["QB"]]]
