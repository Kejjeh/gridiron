"""QB-02: the quarterback's designed runs and scrambles, per game, season to date.

The shipped set prices a quarterback's rushing through `carries_l3` (box-score
carries, kneels included) and expected points; this block splits his own
rush attempts into designed runs (a play call) and scrambles (a reaction),
with kneels and two-point tries out. Pre-registration:
docs/research/blocks/QB-02.md. Play definitions: _p1a_pbp_roles.py.
"""
from __future__ import annotations

import _common as C
import _p1a_pbp_roles as R

ID = "QB-02"
TITLE = "Designed runs and scrambles per game, season to date"
COLUMNS = {"QB": ["qb_designed_pg", "qb_scramble_pg"]}


def build(ctx):
    keys = ctx.keys_for("QB")[R.KEY]
    seasons = sorted(keys["season"].unique())
    pbp = ctx.pbp(seasons, R.RUN_COLUMNS)
    hist = R.event_history(ctx, seasons, pbp, {
        "designed": (R.is_designed_run(pbp), "rusher_player_id"),
        "scramble": (R.is_scramble(pbp), "rusher_player_id")})
    r = C.as_of(keys, hist, ["gsis_id"], ["designed", "scramble"])
    r["qb_designed_pg"] = r["designed_std"] / r["games_std"]
    r["qb_scramble_pg"] = r["scramble_std"] / r["games_std"]
    return r[[*R.KEY, *COLUMNS["QB"]]]
