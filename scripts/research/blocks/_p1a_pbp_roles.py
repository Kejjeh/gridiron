"""Helpers the P1a play-by-play role blocks share (QB-02, RB-05, WR-04,
TE-03, TE-08); not a block (no ID).

Play definitions, pinned once so every block counts the same plays
(QUANT_FOUNDATIONS §1.5: one play definition before any volume number):

  target        play_type "pass", pass_attempt 1, a receiver named, not a
                two-point try. This matches the nflverse box-score targets
                exactly (2024: 17,013 of 17,013).
  designed run  play_type "run", rush_attempt 1, not a scramble, not a
                two-point try, not an aborted snap. Kneels are play_type
                "qb_kneel" and never count (a quarterback's box-score
                carries include them; a kneel is never credited to a player).
  scramble      play_type "run", qb_scramble 1, not a two-point try.

A player's games are the regular-season games he was on the field for on
offense: offensive snaps > 0 in the snap counts (pfr id -> gsis id through
the one crosswalk), or a box score with a carry, a target or a pass
attempt. Box scores alone drop the games he played without a stat line
(2019-2025: 24% of the target tight ends' games, 10% of the receivers'),
which would leave those games' team totals out of his share's denominator;
a box score without offensive snaps is a special-teams game.

Team codes: under pandas 3 the runner's `normalize_teams` skips `str`
columns, so the schedule, snap counts and rosters still say OAK in 2019
where play-by-play and box scores say LV. `team_code` maps every team
column this module joins on.

Every count is summed over the player's games with week < the row's week
(`_common.as_of`), so the blocks pass the leak check by construction.
"""
from __future__ import annotations

import pandas as pd

import _common as C

TEAM_CODES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
KEY = ["season", "week", "gsis_id"]
TARGET_COLUMNS = ["play_type", "pass_attempt", "receiver_player_id", "two_point_attempt"]
RUN_COLUMNS = ["play_type", "rush_attempt", "qb_scramble", "two_point_attempt", "aborted_play",
               "rusher_player_id"]
RED_ZONE = 20              # yardline_100 at the snap: inside the opponent's 20
TWO_MINUTES = 120          # half_seconds_remaining: the two-minute drill
RED_ZONE_COLUMNS = ["rz_tgt_share_std", "rz_tgt_share_l3", "ez_tgt_share_std", "ez_tgt_share_l3"]


def team_code(s: pd.Series) -> pd.Series:
    return s.astype(object).replace(TEAM_CODES)


def _absent(p: pd.DataFrame, col: str) -> pd.Series:
    """True where a 0/1 play flag is 0 or missing."""
    return pd.to_numeric(p[col], errors="coerce").fillna(0) == 0


def is_target(p: pd.DataFrame) -> pd.Series:
    return ((p["play_type"] == "pass") & (p["pass_attempt"] == 1)
            & p["receiver_player_id"].notna() & _absent(p, "two_point_attempt"))


def is_designed_run(p: pd.DataFrame) -> pd.Series:
    return ((p["play_type"] == "run") & (p["rush_attempt"] == 1) & _absent(p, "qb_scramble")
            & _absent(p, "two_point_attempt") & _absent(p, "aborted_play"))


def is_scramble(p: pd.DataFrame) -> pd.Series:
    return (p["play_type"] == "run") & (p["qb_scramble"] == 1) & _absent(p, "two_point_attempt")


def games_played(ctx, seasons) -> pd.DataFrame:
    """(season, week, gsis_id, team): one row per regular-season game the
    player was on the field for on offense (module docstring). The snap
    counts' team wins where both sources have the game."""
    s = ctx.snaps(seasons)
    s = s.loc[pd.to_numeric(s["offense_snaps"], errors="coerce").fillna(0) > 0,
              ["season", "week", "gsis_id", "team"]].copy()
    s["gsis_id"] = C.ids(s["gsis_id"])
    w = ctx.weekly(seasons)
    opps = sum(pd.to_numeric(w[c], errors="coerce").fillna(0)
               for c in ("carries", "targets", "attempts"))
    w = w.loc[opps > 0, ["season", "week", "player_id", "team"]].rename(
        columns={"player_id": "gsis_id"})
    w["gsis_id"] = C.ids(w["gsis_id"])
    g = pd.concat([s, w], ignore_index=True)
    g = g.loc[g["gsis_id"] != ""].copy()
    g["team"] = team_code(g["team"])
    return g.drop_duplicates(KEY).reset_index(drop=True)


def event_history(ctx, seasons, plays: pd.DataFrame, events: dict) -> pd.DataFrame:
    """The player-game history `_common.as_of` sums: one row per game he
    played (`games_played`) with, for each event `name -> (mask over plays,
    player id column)`, his count `<name>` and his team's count in that game
    `<name>_team` (0 where there were none)."""
    hist = games_played(ctx, seasons)
    posteam = team_code(plays["posteam"])
    for name, (mask, who) in events.items():
        ev = plays.loc[mask, ["season", "week", who]].assign(team=posteam.loc[mask])
        team = (ev.groupby(["season", "week", "team"]).size().rename(f"{name}_team")
                .reset_index())
        mine = ev.assign(gsis_id=C.ids(ev[who]))
        mine = mine.loc[mine["gsis_id"] != ""].groupby(KEY).size().rename(name).reset_index()
        hist = (hist.merge(mine, on=KEY, how="left")
                .merge(team, on=["season", "week", "team"], how="left"))
    cols = [c for n in events for c in (n, f"{n}_team")]
    hist[cols] = hist[cols].fillna(0.0)
    return hist


def shares(r: pd.DataFrame, names) -> pd.DataFrame:
    """`<name>_share_std` and `<name>_share_l3`: his count over his teams'
    count in the same games, season to date and over his last 3 games
    (NaN with no earlier game, or when the team had none)."""
    for n in names:
        r[f"{n}_share_std"] = C.share(r[f"{n}_std"], r[f"{n}_team_std"])
        r[f"{n}_share_l3"] = C.share(r[f"{n}_l3"], r[f"{n}_team_l3"])
    return r


def red_zone_shares(ctx, position: str) -> pd.DataFrame:
    """WR-04 and TE-03 (one definition, two blocks): his share of his teams'
    targets inside the opponent's 20 (`rz_tgt`) and of their end-zone
    targets (`ez_tgt`: air yards reaching the goal line, from anywhere on the
    field), season to date and over his last 3 games."""
    keys = ctx.keys_for(position)[KEY]
    seasons = sorted(keys["season"].unique())
    pbp = ctx.pbp(seasons, [*TARGET_COLUMNS, "yardline_100", "air_yards"])
    tgt = is_target(pbp)
    yardline = pd.to_numeric(pbp["yardline_100"], errors="coerce")
    air = pd.to_numeric(pbp["air_yards"], errors="coerce")
    hist = event_history(ctx, seasons, pbp, {
        "rz_tgt": (tgt & (yardline <= RED_ZONE), "receiver_player_id"),
        "ez_tgt": (tgt & (air >= yardline), "receiver_player_id")})
    r = C.as_of(keys, hist, ["gsis_id"], ["rz_tgt", "rz_tgt_team", "ez_tgt", "ez_tgt_team"])
    r = shares(r, ["rz_tgt", "ez_tgt"])
    return r[[*KEY, *RED_ZONE_COLUMNS]]
