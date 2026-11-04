"""RB-06: a teammate back's role, vacated this week, handed out by role.

When a back who played his team's previous game is unavailable this week
(Out or Doubtful on the final injury report, on a reserve list, or gone from
the team per the weekly roster), three parts of his role are vacated, each
his season-to-date share of the team's plays of that kind:

  early  designed carries on 1st and 2nd down
  pd     targets on 3rd or 4th down or in a half's last two minutes
  gl     designed carries from the opponent's 5 or closer

Each vacated share goes to the remaining backs by their own share of it:
this back's column is (vacated share) x (his share) / (the summed shares of
the backs still available). "Back" is the box-score position (RB, FB) of the
player's latest game for the team; how much of each role he holds is usage
(rule #4). Play definitions: _p1a_pbp_roles.py (as RB-05 and RB-03 count
them). Pre-registration: docs/research/blocks/RB-06.md.
"""
from __future__ import annotations

import pandas as pd

import _common as C
import _p1a_pbp_roles as R
import _p2a_events_offense as E

ID = "RB-06"
TITLE = "A teammate back's early-down, passing-down and goal-line role, vacated this week"
COLUMNS = {"RB": ["rb_vac_early", "rb_vac_pd", "rb_vac_gl"]}
GOAL_LINE = 5


def build(ctx):
    rows = E.target_rows(ctx, "RB")
    seasons = sorted(rows["season"].unique())
    pbp = ctx.pbp(seasons, sorted({*R.RUN_COLUMNS, *R.TARGET_COLUMNS, "down",
                                   "half_seconds_remaining", "yardline_100"}))
    down = pd.to_numeric(pbp["down"], errors="coerce")
    clock = pd.to_numeric(pbp["half_seconds_remaining"], errors="coerce")
    yardline = pd.to_numeric(pbp["yardline_100"], errors="coerce")
    run = R.is_designed_run(pbp)
    events = {"early": (run & down.isin([1, 2]), "rusher_player_id"),
              "pd": (R.is_target(pbp) & (down.isin([3, 4]) | (clock <= R.TWO_MINUTES)),
                     "receiver_player_id"),
              "gl": (run & (yardline <= GOAL_LINE), "rusher_player_id")}
    hist = E.history(ctx, seasons, pbp, events)
    mates = E.teammates(ctx, seasons, hist, [c for n in events for c in (n, f"{n}_team")])
    mates["back"] = mates["position"].isin(E.BACKS)
    mates["out_new"] = mates["back"] & mates["unavailable"] & mates["played_last"]
    out = rows[E.KEY].copy()
    for n in events:
        mates[f"{n}_share"] = C.share(mates[f"{n}_std"], mates[f"{n}_team_std"]).to_numpy()
        out[f"rb_vac_{n}"] = E.pickup(rows, mates, f"{n}_share", "out_new", "back")
    return out
