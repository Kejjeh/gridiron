"""DEF-10: short weeks and the opponent's travel, for team defenses.

The defense's and the opponent's days of rest, the time zones the opponent
crossed to the venue, and a Thursday flag, from the pre-game schedule and
the committed zone tables. Home is already in the shipped DEF set (`home`).
Pre-registration: docs/research/blocks/DEF-10.md. Transforms:
_p5_schedule_weather.py.
"""
from __future__ import annotations

import _p5_schedule_weather as H

ID = "DEF-10"
TITLE = "Rest days for both teams, the opponent's zones crossed, Thursday games"
COLUMNS = {"DEF": ["rest_days", "opp_rest_days", "opp_tz_crossed", "thursday"]}


def build(ctx):
    keys = ctx.keys_for("DEF")[["season", "week", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    tf = H.team_frame(ctx, seasons, with_weather=False)
    r = H.for_rows(keys, tf, ["rest_c", "opp_rest_c", "opp_tz_shift", "thursday"])
    r["rest_days"] = r["rest_c"]
    r["opp_rest_days"] = r["opp_rest_c"]
    r["opp_tz_crossed"] = r["opp_tz_shift"].abs()
    return r[["season", "week", "team", *COLUMNS["DEF"]]]
