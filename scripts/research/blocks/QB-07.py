"""QB-07: benching or hook risk, as features (roadmap kind A).

Three season-to-date facts about the quarterback, from before the week:

  qb_start_db_share  his dropbacks over his team's dropbacks in the games he
                     started (a start: he had his team's first dropback);
                     below 1 when he did not finish starts or shared them
  qb_relieved_std    his starts in which another passer of his team took over
                     (RELIEF_DROPBACKS or more dropbacks) while his team was
                     not ahead by REST_LEAD or more at that passer's first
                     dropback (resting a starter in a blowout is not a hook)
  qb_benched_std     1 if he lost the QB1 spot on his team's pre-game depth
                     chart earlier this season while healthy (not designated
                     on that week's injury report, not on a reserve list),
                     after being its QB1 in an earlier week; else 0

Definitions: _p2a_events_offense.py. Pre-registration:
docs/research/blocks/QB-07.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

import _common as C
import _p1a_pbp_roles as R
import _p2a_events_offense as E

ID = "QB-07"
TITLE = "Hook risk: in-game reliefs and a lost QB1 spot this season"
COLUMNS = {"QB": ["qb_start_db_share", "qb_relieved_std", "qb_benched_std"]}
RELIEF_DROPBACKS = 5
REST_LEAD = 17


def starts(ctx, seasons) -> pd.DataFrame:
    """One row per start: (season, week, gsis_id, team, dropbacks,
    team_dropbacks, relieved)."""
    pbp = ctx.pbp(seasons, [*E.DROPBACK_COLUMNS, "score_differential"])
    pg = E.passer_games(pbp)
    st = E.starters(pg)
    # the team's score margin at each passer's first dropback of the game
    db = pbp.loc[E.flag(pbp, "qb_dropback")]
    margin = pd.DataFrame({"season": db["season"].astype(int), "week": db["week"].astype(int),
                           "team": R.team_code(db["posteam"]),
                           "first_play": pd.to_numeric(db["play_id"], errors="coerce"),
                           "margin": pd.to_numeric(db["score_differential"], errors="coerce")})
    others = pg.loc[pg["dropbacks"] >= RELIEF_DROPBACKS, [*E.MATE, "first_play"]].merge(
        margin.drop_duplicates([*E.TEAM_WEEK, "first_play"]), on=[*E.TEAM_WEEK, "first_play"],
        how="left")
    others = others.merge(st[[*E.TEAM_WEEK, "gsis_id"]].rename(columns={"gsis_id": "starter"}),
                          on=E.TEAM_WEEK, how="inner")
    hook = others.loc[(others["gsis_id"] != others["starter"])
                      & ~(others["margin"] >= REST_LEAD)]
    hooked = hook[E.TEAM_WEEK].drop_duplicates().assign(relieved=1.0)
    st = st.merge(hooked, on=E.TEAM_WEEK, how="left").fillna({"relieved": 0.0})
    return st.drop_duplicates(E.KEY)


def benched(ctx, seasons) -> pd.DataFrame:
    """One row per (season, week, gsis_id) a QB is on a pre-game depth chart:
    `lost` = 1 when he is not its QB1 that week, was QB1 on a chart in an
    earlier week this season, and is healthy that week."""
    q = E.chart_qbs(ctx, seasons)
    q["qb1"] = q["rank"] == 1
    q = q.sort_values(["season", "gsis_id", "week", "rank"]).reset_index(drop=True)
    seen = q.groupby(["season", "gsis_id", "week"], as_index=False)["qb1"].max()
    seen["was_qb1"] = (seen.groupby(["season", "gsis_id"])["qb1"]
                       .transform(lambda x: x.shift(1, fill_value=False).cummax()))
    q = q.merge(seen[["season", "gsis_id", "week", "was_qb1"]], on=E.KEY, how="left")
    rep = E.reports(ctx, seasons)
    q = q.merge(rep[[*E.KEY, "designated"]], on=E.KEY, how="left")
    healthy = ~q["designated"].fillna(False).astype(bool) & ~E.roster_away(ctx, q)
    q["lost"] = (q["was_qb1"].astype(bool) & ~q["qb1"] & healthy).astype(float)
    return q.groupby(E.KEY, as_index=False)["lost"].max()


def build(ctx):
    rows = E.target_rows(ctx, "QB")
    seasons = sorted(rows["season"].unique())
    st = starts(ctx, seasons)
    r = C.as_of(rows[E.KEY], st, ["gsis_id"], ["dropbacks", "team_dropbacks", "relieved"])
    out = rows[E.KEY].copy()
    out["qb_start_db_share"] = C.share(r["dropbacks_std"], r["team_dropbacks_std"]).to_numpy()
    out["qb_relieved_std"] = r["relieved_std"].fillna(0.0).to_numpy()
    b = C.as_of(rows[E.KEY], benched(ctx, seasons), ["gsis_id"], ["lost"])
    out["qb_benched_std"] = (b["lost_std"].fillna(0.0) > 0).astype(float).to_numpy()
    return out
