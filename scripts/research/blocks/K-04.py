"""K-04: red-zone touchdown rate, the kicker's offense against the opponent's defense.

Pre-registration: docs/research/blocks/K-04.md. Shared definitions and the
prior strengths: _p6_kicker_defense_pbp.py.
"""
from __future__ import annotations

import _common as C
import _p6_kicker_defense_pbp as H

ID = "K-04"
TITLE = "Red-zone touchdown rate over the league, shrunk: his team's offense and the opponent's defense"
COLUMNS = {"K": ["rz_td_oe", "opp_rz_td_allowed_oe"]}


def build(ctx):
    keys = H.kicker_keys(ctx)
    p = ctx.pbp(H.seasons_of(keys), H.DRIVE_COLUMNS)
    games = H.team_games(p)
    d = H.drives(p)
    rz = d.loc[d["best"] <= H.RED_ZONE].assign(trips=1.0)
    offense = H.per_team_game(games, rz, ["trips", "td"], side="team")
    defense = H.per_team_game(games, rz, ["trips", "td"], side="opponent")
    own = C.as_of(keys, offense, ["team"], ["trips", "td"])
    opp = C.as_of(H.opponent_view(keys), defense, ["team"], ["trips", "td"])
    lg = H.league_as_of(keys, offense, ["trips", "td"])
    league = C.share(lg["td_lg"], lg["trips_lg"])
    keys["rz_td_oe"] = H.over_league(own["td_std"], own["trips_std"], league, H.N0_RZ_OFFENSE)
    keys["opp_rz_td_allowed_oe"] = H.over_league(opp["td_std"], opp["trips_std"], league,
                                                 H.N0_RZ_DEFENSE)
    return keys[["season", "week", "gsis_id", *COLUMNS["K"]]]
