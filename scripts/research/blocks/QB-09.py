"""QB-09: goal-line sneak usage, the quarterback's QB sneaks per game (FTN charting).

Pre-registration: docs/research/blocks/QB-09.md. FTN charting starts in 2022,
so the block is scored on the 2022-2025 folds; 2019-2021 still train, with
the features unknown.
"""
from __future__ import annotations

import _common as C
import _p1b_ftn_pfr_opponent as H

ID = "QB-09"
TITLE = "QB sneaks per game, all and inside the opponent's 5 (FTN charting)"
COLUMNS = {"QB": ["sneaks_pg", "gl_sneaks_pg"]}
FOLDS = (2022, 2023, 2024, 2025)
GOAL_LINE = 5


def build(ctx):
    keys = ctx.keys_for("QB")[["season", "week", "gsis_id"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    plays = H.ftn_plays(ctx, seasons, ["play_type", "rusher_player_id", "yardline_100",
                                       "two_point_attempt"], ["is_qb_sneak"])
    sn = plays.loc[(plays["play_type"] == "run") & plays["is_qb_sneak"].fillna(False).astype(bool)
                   & (plays["two_point_attempt"].fillna(0) == 0)
                   & plays["rusher_player_id"].notna()]
    sn = sn.assign(gsis_id=C.ids(sn["rusher_player_id"]), n=1.0,
                   gl=(sn["yardline_100"] <= GOAL_LINE).astype(float))
    mine = (sn.groupby(["season", "week", "gsis_id"], as_index=False)
            .agg(sneaks=("n", "sum"), gl_sneaks=("gl", "sum")))
    hist = H.charted_history(ctx, seasons, plays, mine)
    r = C.as_of(keys, hist, ["gsis_id"], ["sneaks", "gl_sneaks", "charted"])
    r["sneaks_pg"] = C.share(r["sneaks_std"], r["charted_std"])
    r["gl_sneaks_pg"] = C.share(r["gl_sneaks_std"], r["charted_std"])
    return r[["season", "week", "gsis_id", *COLUMNS["QB"]]]
