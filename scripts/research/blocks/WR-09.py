"""WR-09: screens, the receiver's screen targets per game and his share of the team's.

Pre-registration: docs/research/blocks/WR-09.md. FTN charting starts in 2022,
so the block is scored on the 2022-2025 folds; 2019-2021 still train, with
the features unknown.
"""
from __future__ import annotations

import _common as C
import _p1b_ftn_pfr_opponent as H

ID = "WR-09"
TITLE = "Screen targets per game and share of the team's screen targets (FTN charting)"
COLUMNS = {"WR": ["screen_tgt_pg", "screen_tgt_share"]}
FOLDS = (2022, 2023, 2024, 2025)


def build(ctx):
    keys = ctx.keys_for("WR")[["season", "week", "gsis_id"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    plays = H.ftn_plays(ctx, seasons, ["play_type", "pass_attempt", "receiver_player_id",
                                       "two_point_attempt"], ["is_screen_pass"])
    scr = plays.loc[(plays["play_type"] == "pass") & (plays["pass_attempt"] == 1)
                    & plays["is_screen_pass"].fillna(False).astype(bool)
                    & (plays["two_point_attempt"].fillna(0) == 0)
                    & plays["receiver_player_id"].notna()]
    team = (scr.groupby(["season", "week", "posteam"]).size().rename("team_scr").reset_index()
            .rename(columns={"posteam": "team"}))
    mine = (scr.assign(gsis_id=C.ids(scr["receiver_player_id"]))
            .groupby(["season", "week", "gsis_id"]).size().rename("scr").reset_index())
    hist = H.charted_history(ctx, seasons, plays, mine, team)
    r = C.as_of(keys, hist, ["gsis_id"], ["scr", "team_scr", "charted"])
    r["screen_tgt_pg"] = C.share(r["scr_std"], r["charted_std"])
    r["screen_tgt_share"] = C.share(r["scr_std"], r["team_scr_std"])
    return r[["season", "week", "gsis_id", *COLUMNS["WR"]]]
