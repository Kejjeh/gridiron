"""WR-08: team pass volume implied by the week's play-caller, and the receiver's part of it.

A receiver's targets are his share times his team's passes. The shipped set
has his share and his recent volume, never what the person calling the plays
implies for the team's pass volume. This block combines the play-caller's
neutral pass rate over expected and his pace (each career before this season
plus this season with this team so far, shrunk toward the league) into one
index of pass plays per unit of game clock against an average play-caller
(1.0), and multiplies the receiver's season-to-date target share by the
index's excess over 1.0. Pre-registration: docs/research/blocks/WR-08.md.
Definitions: _tendencies.py.
"""
from __future__ import annotations

import _common as C
import _tendencies as T

ID = "WR-08"
TITLE = "Play-caller's implied pass volume (pass rate over expected and pace) and the WR's part"
COLUMNS = {"WR": ["pc_pass_volume", "wr_volume_tilt"]}
#: The league constants come from the season before the target season, so
#: 2019 rows need 2018 play-by-play (no play-caller is known in 2018).
CONSTANTS_FIRST = 2018


def build(ctx):
    keys = ctx.keys_for("WR")[[*T.KEY, "team"]]
    seasons = T.history_seasons("OPC", keys)
    cols = sorted(set(T.PROE_COLUMNS) | set(T.PACE_COLUMNS) | set(T.PASS_RATE_COLUMNS)
                  | set(T.TARGET_COLUMNS))
    pbp = ctx.pbp([CONSTANTS_FIRST, *seasons], cols)
    proe_obs, pace_obs = T.proe_obs(pbp), T.pace_obs(pbp)
    proe = T.tendency(ctx, keys, proe_obs, "OPC")
    pace = T.tendency(ctx, keys, pace_obs, "OPC")
    tw = proe[T.TEAM_WEEK].copy()
    tw["proe"] = T.pooled(proe, T.K["proe"])
    tw = tw.merge(pace[T.TEAM_WEEK].assign(pace=T.pooled(pace, T.K["pace"])), on=T.TEAM_WEEK,
                  how="left")
    rate = T.league_prior_season(T.neutral_rate_obs(pbp, "pass"))
    secs = T.league_prior_season(pace_obs)
    r0, s0 = tw["season"].map(rate), tw["season"].map(secs)
    tw["pc_pass_volume"] = (1.0 + tw["proe"] / (100.0 * r0)) * (s0 / (s0 + tw["pace"]))
    hist = T.share_history(T.games_played(ctx, seasons),
                           T.targets(pbp.loc[pbp["season"].isin(seasons)]), "tgt")
    r = C.as_of(keys[T.KEY], hist, ["gsis_id"], ["tgt", "tgt_team"])
    r["tgt_share_std"] = C.share(r["tgt_std"], r["tgt_team_std"])
    r = (r[[*T.KEY, "tgt_share_std"]].merge(keys, on=T.KEY, how="left")
         .merge(tw[[*T.TEAM_WEEK, "pc_pass_volume"]], on=T.TEAM_WEEK, how="left"))
    r["wr_volume_tilt"] = r["tgt_share_std"] * (r["pc_pass_volume"] - 1.0)
    return r[[*T.KEY, *COLUMNS["WR"]]]
