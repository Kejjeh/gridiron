"""WR-03: a teammate's absence this week, redistributed to the receiver.

Vacated targets: the season-to-date target shares of his teammates (any
position: whoever holds targets, rule #4) who are unavailable this week (Out
or Doubtful on the final injury report, on a reserve list, or gone from the
team per the weekly roster). Three columns:

  wr_vac_tgt_new    the vacated share of teammates who played the team's
                    previous game (an absence his own recent games do not
                    show yet) x his target share / the summed target shares
                    of the receivers still available
  wr_vac_route_new  the same vacated share, split by LAST season's route
                    share instead (routes from participation, which stops at
                    2025: a prior, the only route data the 2026 board has)
  wr_vac_tgt_all    as wr_vac_tgt_new, with every unavailable teammate,
                    however long he has been out

Definitions: _p2a_events_offense.py. Pre-registration:
docs/research/blocks/WR-03.md.
"""
from __future__ import annotations

import _common as C
import _p2a_events_offense as E

ID = "WR-03"
TITLE = "Teammate absence redistributed by target share and last season's route share"
COLUMNS = {"WR": ["wr_vac_tgt_new", "wr_vac_route_new", "wr_vac_tgt_all"]}


def build(ctx):
    rows = E.target_rows(ctx, "WR")
    seasons = sorted(rows["season"].unique())
    hist = E.target_history(ctx, seasons)
    mates = E.teammates(ctx, seasons, hist, ["tgt", "tgt_team"])
    mates["tgt_share"] = C.share(mates["tgt_std"], mates["tgt_team_std"]).to_numpy()
    mates["receiver"] = mates["tgt_std"] > 0
    mates["out_new"] = mates["receiver"] & mates["unavailable"] & mates["played_last"]
    mates["out_all"] = mates["receiver"] & mates["unavailable"]
    prior = E.route_shares(ctx, [s - 1 for s in seasons])
    mates = C.prior_season(mates, prior, ["gsis_id"], ["route_share"])
    out = rows[E.KEY].copy()
    out["wr_vac_tgt_new"] = E.pickup(rows, mates, "tgt_share", "out_new", "receiver")
    out["wr_vac_route_new"] = E.pickup(rows, mates, "tgt_share", "out_new", "receiver",
                                       split="route_share")
    out["wr_vac_tgt_all"] = E.pickup(rows, mates, "tgt_share", "out_all", "receiver")
    return out
