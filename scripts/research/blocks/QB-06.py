"""QB-06: the target share of his receivers who will not play this week.

For his team this week: the season-to-date target shares of the teammates
who are unavailable (Out or Doubtful on the final injury report, on a
reserve list, or gone from the team per the weekly roster), summed; and the
part of that sum from receivers who played the team's previous game (an
absence his own recent games do not show yet). Definitions:
_p2a_events_offense.py. Pre-registration: docs/research/blocks/QB-06.md.
"""
from __future__ import annotations

import _common as C
import _p2a_events_offense as E

ID = "QB-06"
TITLE = "Target share of his receivers who will not play this week"
COLUMNS = {"QB": ["vac_tgt_share", "vac_tgt_share_new"]}


def build(ctx):
    rows = E.target_rows(ctx, "QB")
    seasons = sorted(rows["season"].unique())
    hist = E.target_history(ctx, seasons)
    mates = E.teammates(ctx, seasons, hist, ["tgt", "tgt_team"])
    mates["tgt_share"] = C.share(mates["tgt_std"], mates["tgt_team_std"]).to_numpy()
    mates["out_all"] = mates["unavailable"]
    mates["out_new"] = mates["unavailable"] & mates["played_last"]
    out = rows[E.KEY].copy()
    out["vac_tgt_share"] = E.vacated(rows, mates, "tgt_share", "out_all")
    out["vac_tgt_share_new"] = E.vacated(rows, mates, "tgt_share", "out_new")
    return out
