"""TE-05: tight-end-friendly play-caller history, and the tight end's part of it.

The share of his teams' targets that went to tight ends in the play-caller's
career before this season (any team, following him across teams), against
the league and shrunk toward it, and the target share it implies for this
tight end: the play-caller's expected tight-end share of team targets times
the tight end's share of his team's tight-end targets this season. The
shipped TE set has his own target share; nothing about the person's habit.
Pre-registration: docs/research/blocks/TE-05.md. Definitions: _tendencies.py.

The table starts in 2019, so no play-caller has a prior season there: the
block is scored on the 2020-2025 folds, and 2019 still trains (unknown).
"""
from __future__ import annotations

import _common as C
import _tendencies as T

ID = "TE-05"
TITLE = "Play-caller's tight-end target share before this season, and the TE's implied share"
COLUMNS = {"TE": ["pc_te_tgt_share", "te_implied_tgt_share"]}
FOLDS = (2020, 2021, 2022, 2023, 2024, 2025)


def build(ctx):
    keys = ctx.keys_for("TE")[[*T.KEY, "team"]]
    seasons = T.history_seasons("OPC", keys)
    pbp = ctx.pbp(seasons, T.TARGET_COLUMNS)
    tg = T.targets(pbp)
    pos = T.box_positions(ctx, seasons)
    obs = T.te_target_obs(tg, pos)
    t = T.tendency(ctx, keys, obs, "OPC")
    t["pc_te_tgt_share"] = T.career(t, T.K["te_targets"])
    te_tg = tg.merge(pos, on=T.KEY, how="inner")
    te_tg = te_tg.loc[te_tg["position"] == "TE", ["season", "week", "game_id", "team", "gsis_id"]]
    hist = T.share_history(T.games_played(ctx, seasons), te_tg, "te_tgt")
    r = C.as_of(keys[T.KEY], hist, ["gsis_id"], ["te_tgt", "te_tgt_team"])
    r["within_te"] = C.share(r["te_tgt_std"], r["te_tgt_team_std"])
    r = (r[[*T.KEY, "within_te"]].merge(keys, on=T.KEY, how="left")
         .merge(t[[*T.TEAM_WEEK, "pc_te_tgt_share"]], on=T.TEAM_WEEK, how="left"))
    league = r["season"].map(T.league_prior_season(obs))
    r["te_implied_tgt_share"] = (league + r["pc_te_tgt_share"]) * r["within_te"]
    return r[[*T.KEY, *COLUMNS["TE"]]]
