"""QB-13: short weeks and west-to-east travel.

His team's days of rest, a Thursday flag, and the time zones it crossed
eastward when the kickoff is early (13:00 Eastern or before), from the
pre-game schedule and the committed team and stadium zone tables. The
shipped QB set has nothing from the calendar or travel. Pre-registration:
docs/research/blocks/QB-13.md. Transforms: _p5_schedule_weather.py.
"""
from __future__ import annotations

import _p5_schedule_weather as H

ID = "QB-13"
TITLE = "Rest days, Thursday games, and zones crossed eastward for an early kickoff"
COLUMNS = {"QB": ["rest_days", "thursday", "east_early_tz"]}


def build(ctx):
    keys = ctx.keys_for("QB")[["season", "week", "gsis_id", "team"]]
    seasons = sorted(int(s) for s in keys["season"].unique())
    tf = H.team_frame(ctx, seasons, with_weather=False)
    r = H.for_rows(keys, tf, ["rest_c", "thursday", "tz_shift", "early"])
    r["rest_days"] = r["rest_c"]
    r["east_early_tz"] = r["tz_shift"].clip(lower=0) * r["early"]
    return r[[*H.KEY, *COLUMNS["QB"]]]
