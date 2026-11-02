"""TE-07: the tight end's target bump when a wide receiver is out.

Vacated wide-receiver targets: the season-to-date target shares of his
teammates whose box-score position is WR and who are unavailable this week
(Out or Doubtful on the final injury report, on a reserve list, or gone from
the team per the weekly roster). Each times his share of the team's non-WR
targets (his targets over the targets that did not go to a WR, over his
games this season):

  te_vac_wr_new  WRs who played the team's previous game (an absence his own
                 recent games do not show yet)
  te_vac_wr_all  every unavailable WR, however long he has been out

Definitions: _p2a_events_offense.py. Pre-registration:
docs/research/blocks/TE-07.md.
"""
from __future__ import annotations

import numpy as np

import _common as C
import _p2a_events_offense as E

ID = "TE-07"
TITLE = "Vacated wide-receiver target share x the tight end's share of non-WR targets"
COLUMNS = {"TE": ["te_vac_wr_new", "te_vac_wr_all"]}


def build(ctx):
    rows = E.target_rows(ctx, "TE")
    seasons = sorted(rows["season"].unique())
    hist = E.target_history(ctx, seasons)
    wr = (hist.loc[hist["position"] == "WR"].groupby(E.TEAM_WEEK, as_index=False)["tgt"].sum()
          .rename(columns={"tgt": "wrtgt_team"}))
    hist = hist.merge(wr, on=E.TEAM_WEEK, how="left").fillna({"wrtgt_team": 0.0})
    mates = E.teammates(ctx, seasons, hist, ["tgt", "tgt_team", "wrtgt_team"])
    mates["tgt_share"] = C.share(mates["tgt_std"], mates["tgt_team_std"]).to_numpy()
    mates["nonwr_share"] = C.share(mates["tgt_std"],
                                   mates["tgt_team_std"] - mates["wrtgt_team_std"]).to_numpy()
    wide = mates["position"] == "WR"
    mates["out_new"] = wide & mates["unavailable"] & mates["played_last"]
    mates["out_all"] = wide & mates["unavailable"]
    own = rows[E.MATE].merge(mates[[*E.MATE, "nonwr_share", "unavailable"]], on=E.MATE,
                             how="left")
    mine = own["nonwr_share"].to_numpy(dtype="float64")
    away = own["unavailable"].fillna(False).astype(bool).to_numpy()
    out = rows[E.KEY].copy()
    for name, flag in (("te_vac_wr_new", "out_new"), ("te_vac_wr_all", "out_all")):
        v = E.vacated(rows, mates, "tgt_share", flag)
        out[name] = np.where((v == 0) | away, 0.0, v * mine)
    return out
