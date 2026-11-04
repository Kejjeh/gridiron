"""K-11: the opponent's "bend but don't break", drives it lets reach its 30 that end without a touchdown.

Pre-registration: docs/research/blocks/K-11.md. Shared definitions:
_p6_kicker_defense_pbp.py.
"""
from __future__ import annotations

import _common as C
import _p6_kicker_defense_pbp as H

ID = "K-11"
TITLE = ("Opponent bend-but-don't-break: drives it allowed to its 30 that ended without a "
         "touchdown, per game (season to date, last three games)")
COLUMNS = {"K": ["opp_bend_pg", "opp_bend_l3"]}


def build(ctx):
    keys = H.kicker_keys(ctx)
    p = ctx.pbp(H.seasons_of(keys), H.DRIVE_COLUMNS)
    games = H.team_games(p)
    d = H.drives(p)
    bend = d.loc[(d["best"] <= H.FG_RANGE) & (d["td"] == 0)].assign(bend=1.0)
    defense = H.per_team_game(games, bend, ["bend"], side="opponent")
    opp = C.as_of(H.opponent_view(keys), defense, ["team"], ["bend"])
    keys["opp_bend_pg"] = H.per_game(opp["bend_std"], opp["games_std"])
    keys["opp_bend_l3"] = H.per_game(opp["bend_l3"], opp["games_std"].clip(upper=3))
    return keys[["season", "week", "gsis_id", *COLUMNS["K"]]]
