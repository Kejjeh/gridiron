"""Event builders package P2b's blocks share (RB-02, WR-13, TE-12, RB-10, K-08,
DEF-02, DEF-06, DEF-11); not a block (no ID).

What a block may treat as known before week w's games, as the runner
classifies the frames:

  * week w's final injury report (`injuries`, pre-game). Out and Doubtful
    mean "not expected to play"; Questionable does not (rule #11).
  * week w's weekly roster (`rosters_weekly`, pre-game: one snapshot per team
    game, none in a bye week). A player is on the 53 when his status is ACT
    or INA. INA is the game-day inactive list, known 90 minutes before
    kickoff, so it is never read as a fact here: INA counts exactly like ACT.
    In 2019-2025 a player who played in week w is ACT on week w's roster in
    all but a handful of rows, so the snapshot does not carry what happened
    in the game.
  * the depth chart published for week w (`depth_charts`, pre-game).

Everything that happened in a game (box scores, snap counts, the schedule's
starting quarterbacks) is read from the weeks before w only.

One loader bug is worked around here, not fixed (the runner is not ours to
change): `Context._get` filters a multi-season frame on `season_type` when the
column exists, else `game_type`. nflverse's 2025 injury file adds
`season_type`, so `ctx.injuries` over 2019-2025 returns 2025 alone; the
2025 depth chart has no `game_type`, so `ctx.depth_charts` over 2019-2025
loses 2025. `injuries_by_season` and `depth_by_season` load one season at a
time (each cut by the Context as usual).

Ids only (rule #3). Box scores, reports, rosters and depth charts carry gsis
ids. Snap counts carry PFR ids: `pfr_gsis` maps them through the crosswalk
first, then through nflverse's own player table (`load_players`: gsis_id,
pfr_id) for the ids the crosswalk lacks. The crosswalk has almost no
offensive linemen (under 2% of their snap rows map), the player table maps
over 99% of linemen's and defenders' snap rows. An id the two map to
different players is dropped.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

import _common as C
from gridiron.ids import normalize_id

#: Weekly-roster statuses that put a player on the 53 (see the docstring: INA
#: is read as ACT, never as a game-day fact).
ON_53 = ("ACT", "INA")
#: Final-report designations that mean "not expected to play".
NOT_EXPECTED = ("Out", "Doubtful")
SEVERITY = {"Out": 3, "Doubtful": 2, "Questionable": 1}
#: Snap-count positions (PFR's labels; 2025 adds the group labels OL, DL, S, DB).
OL = ("T", "G", "C", "OL", "OT", "OG", "LT", "LG", "RG", "RT")
FRONT = ("DE", "DT", "NT", "DL", "LB", "ILB", "OLB", "MLB", "EDGE")
BACK = ("CB", "S", "FS", "SS", "DB", "SAF")
#: Muscle-strain sites, matched as whole words of the report's injury text
#: (lower case), plus the phrases below.
SOFT_TISSUE = frozenset({"hamstring", "hamstrings", "groin", "calf", "calves", "quad", "quads",
                         "quadricep", "quadriceps", "thigh", "pectoral", "oblique", "abdomen",
                         "abdominal", "adductor"})
SOFT_PHRASES = ("hip flexor",)
#: Report texts that are not an injury (rest days, personal matters, coach's
#: decisions, suspensions, travel, illness, COVID protocols), as lower-case
#: phrases; and whole texts that say nothing.
NON_INJURY = ("not injury related", "personal", "coach", "suspension", "decision", "travel",
              "inactive", "load management", "covid", "discipline", "illness")
NON_INJURY_WORDS = frozenset({"rest", "resting"})
NON_INJURY_TEXTS = frozenset({"other", "nir"})
#: Weekly-roster entries that are an injury list: injured reserve (with or
#: without a return designation), physically unable to perform, non-football
#: injury. From 2021 every reserve list is status RES with a description
#: code, so the code decides (`INJURY_CODES`); 2019 and most of 2020 carry no
#: code and separate statuses, so the status decides (`HURT_STATUSES`: RES,
#: PUP, and RSN, the old non-football-injury list). PUP and RSN count with any
#: code. Not injury lists, whatever the era: COVID-19 (R59), the 2020 RSR
#: status (it maps to both R59 and R48, so it stays uncounted as before),
#: the 2020 opt-outs (R62, NWT), suspensions (R30, R33, R40, SUS),
#: non-football illness (R27), retired (R02), did not report (R03), left
#: squad (R06) and every other code. Mapped on the 2020 rosters, which carry
#: both forms: a player's old status two weeks either side of his coded one
#: (RES -> R01/R48, PUP -> R04, RSN -> R05/R27, NWT -> R62, SUS -> R30).
#: Corrected 2026-11-02 (the near-miss bug hunt): every RES row used to count,
#: so from 2020 the COVID list and suspensions counted as injuries, against the
#: pre-registrations of RB-02, WR-13 and TE-12; and RSN never counted.
HURT_STATUSES = ("RES", "PUP", "RSN", "NFI")
INJURY_CODES = ("R01", "R04", "R05", "R47", "R48")
#: ret_missed is capped here (a long reserve stint counts as this many games).
MAX_MISSED = 8
BOX_COLUMNS = ("player_id", "team", "position", "carries", "targets", "fg_att", "pat_att")
_WORD = re.compile(r"[a-z]+")


def _nfl():
    """nflreadpy on the research cache, configured as block_runner._nfl does."""
    import nflreadpy as nfl
    from nflreadpy.config import update_config

    from gridiron.paths import RESEARCH_CACHE
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    return nfl


# ------------------------------------------------------------------- loaders

def _memo(ctx, name: str, seasons, fn):
    """Cache a processed frame in the Context's store, keyed by the cutoff too:
    the leak check's truncated contexts share the store, and each must build
    its own copy from its own cut frames."""
    key = ("_p2b", name, tuple(sorted(int(s) for s in seasons)), ctx.cutoff)
    if key not in ctx._store:
        ctx._store[key] = fn()
    return ctx._store[key]


def box(ctx, seasons) -> pd.DataFrame:
    """Lean player box scores, one row per player-game, regular season
    (post-game: stored and cut as `weekly`): season, week, gsis_id, team,
    position, carries, targets, kicks (field-goal plus extra-point attempts)."""
    def load(ss, _):
        nfl = _nfl()
        parts = []
        for s in ss:
            w = nfl.load_player_stats([s], summary_level="week").to_pandas()
            for c in BOX_COLUMNS:
                if c not in w.columns:
                    w[c] = np.nan
            parts.append(w[["season", "week", "season_type", *BOX_COLUMNS]])
        return pd.concat(parts, ignore_index=True)

    def build():
        b = ctx._get("weekly", seasons, load, ["p2b_box"])
        num = lambda c: pd.to_numeric(b[c], errors="coerce").fillna(0.0)  # noqa: E731
        out = pd.DataFrame({"season": b["season"].astype(int), "week": b["week"].astype(int),
                            "gsis_id": C.ids(b["player_id"]).astype(str),
                            "team": b["team"].astype(str),
                            "position": b["position"].fillna("").astype(str),
                            "carries": num("carries"), "targets": num("targets"),
                            "kicks": num("fg_att") + num("pat_att")})
        out = out.loc[~out["gsis_id"].isin(["", "nan", "None"])]
        return out.drop_duplicates(["season", "week", "gsis_id"]).reset_index(drop=True)
    return _memo(ctx, "box", seasons, build)


def pfr_gsis(ctx) -> dict:
    """{pfr id: gsis id}: the crosswalk first, nflverse's player table for the
    ids the crosswalk lacks; an id the two map differently is dropped."""
    key = "_p2b_pfr_gsis"
    if key not in ctx._store:
        cw = {str(p): normalize_id(g) for p, g in ctx.pfr_to_gsis.items()}
        pl = _nfl().load_players().to_pandas()[["gsis_id", "pfr_id"]].dropna()
        nv = {str(p): normalize_id(g) for p, g in zip(pl["pfr_id"], pl["gsis_id"])}
        out = {**nv, **cw}
        for p in set(cw) & set(nv):
            if cw[p] != nv[p]:
                out.pop(p, None)
        ctx._store[key] = {p: g for p, g in out.items() if g}
    return ctx._store[key]


def snaps(ctx, seasons) -> pd.DataFrame:
    """Snap counts (post-game) with `gsis_id` from `pfr_gsis` (rows that do not
    map are dropped): season, week, team, gsis_id, position, offense_snaps,
    defense_snaps, st_snaps."""
    def build():
        sn = ctx.snaps(seasons)
        out = pd.DataFrame({"season": sn["season"].astype(int), "week": sn["week"].astype(int),
                            "team": sn["team"].astype(str),
                            "gsis_id": sn["pfr_player_id"].astype(str).map(pfr_gsis(ctx)),
                            "position": sn["position"].fillna("").astype(str)})
        for c in ("offense_snaps", "defense_snaps", "st_snaps"):
            out[c] = pd.to_numeric(sn[c], errors="coerce").fillna(0.0).to_numpy()
        out = out.loc[out["gsis_id"].notna()]
        out["gsis_id"] = out["gsis_id"].astype(str)
        return out.drop_duplicates(["season", "week", "gsis_id"]).reset_index(drop=True)
    return _memo(ctx, "snaps", seasons, build)


def played(ctx, seasons) -> pd.DataFrame:
    """Every regular-season game a player took part in (post-game): a box-score
    line, or any snap in the snap counts. (season, week, gsis_id, team)."""
    def build():
        b = box(ctx, seasons)[["season", "week", "gsis_id", "team"]]
        sn = snaps(ctx, seasons)
        took = (sn["offense_snaps"] + sn["defense_snaps"] + sn["st_snaps"]) > 0
        s = sn.loc[took, ["season", "week", "gsis_id", "team"]]
        return (pd.concat([b, s], ignore_index=True)
                .drop_duplicates(["season", "week", "gsis_id"]).reset_index(drop=True))
    return _memo(ctx, "played", seasons, build)


def injuries_by_season(ctx, seasons) -> pd.DataFrame:
    """ctx.injuries one season at a time (see the docstring: the multi-season
    call drops 2019-2024 when 2025 is in it)."""
    return pd.concat([ctx.injuries([int(s)]) for s in sorted(set(seasons))], ignore_index=True)


def depth_by_season(ctx, seasons) -> list[pd.DataFrame]:
    """ctx.depth_charts one season at a time (the multi-season call drops 2025)."""
    return [ctx.depth_charts([int(s)]) for s in sorted(set(seasons))]


def report(ctx, seasons) -> pd.DataFrame:
    """Week w's final injury report, one row per player-week (pre-game):
    season, week, gsis_id, team, status ("Out", "Doubtful", "Questionable",
    or "" for a practice-only listing), injury (the report's primary injury,
    else the practice report's). Two rows for one player-week keep the most
    severe status."""
    def build():
        r = injuries_by_season(ctx, seasons)
        r = r.loc[r["gsis_id"].notna()]
        injury = r["report_primary_injury"].where(r["report_primary_injury"].notna(),
                                                   r["practice_primary_injury"])
        out = pd.DataFrame({"season": r["season"].astype(int), "week": r["week"].astype(int),
                            "gsis_id": C.ids(r["gsis_id"]).astype(str),
                            "team": r["team"].astype(str),
                            "status": r["report_status"].fillna("").astype(str),
                            "injury": injury.fillna("").astype(str)})
        out["_sev"] = out["status"].map(SEVERITY).fillna(0.0)
        out = (out.sort_values(["season", "week", "gsis_id", "_sev"], kind="stable")
               .drop_duplicates(["season", "week", "gsis_id"], keep="last"))
        return out.drop(columns="_sev").reset_index(drop=True)
    return _memo(ctx, "report", seasons, build)


def roster(ctx, seasons) -> pd.DataFrame:
    """Week w's rosters, one row per team-week-player (pre-game): season, week,
    team, gsis_id, status, code (the status description code, "" when the
    row has none), position (the roster's position group)."""
    def build():
        r = ctx.rosters_weekly(seasons)
        r = r.loc[r["gsis_id"].notna()]
        code = (r["status_description_abbr"] if "status_description_abbr" in r.columns
                else pd.Series("", index=r.index))
        out = pd.DataFrame({"season": r["season"].astype(int), "week": r["week"].astype(int),
                            "team": r["team"].astype(str),
                            "gsis_id": C.ids(r["gsis_id"]).astype(str),
                            "status": r["status"].fillna("").astype(str),
                            "code": code.fillna("").astype(str),
                            "position": r["position"].fillna("").astype(str)})
        return out.drop_duplicates(["season", "week", "team", "gsis_id"]).reset_index(drop=True)
    return _memo(ctx, "roster", seasons, build)


def injury_list(status: pd.Series, code: pd.Series) -> pd.Series:
    """True where a weekly-roster row is an injury list (`HURT_STATUSES`,
    `INJURY_CODES`): RES with an injury code, or with no code (2019-2020);
    PUP, RSN or NFI with any code."""
    status, code = status.astype(str), code.astype(str)
    res = status.eq("RES") & (code.isin(INJURY_CODES) | code.eq(""))
    return res | status.isin([s for s in HURT_STATUSES if s != "RES"])


# ------------------------------------------------------------------ builders

def availability(ctx, seasons, cand: pd.DataFrame) -> pd.DataFrame:
    """For candidate rows (season, week, team, gsis_id), unique on those keys:
    `out` = 1 when he is not expected to play that week, as known before its
    games: Out or Doubtful on the week's final report, or not on the team's
    53 that week (a reserve list, the practice squad, released, traded) when
    the team's roster for the week exists. `questionable` = 1 when listed
    Questionable and not out. A team-week with no roster is judged by the
    report alone."""
    cand = cand.assign(season=cand["season"].astype(int), week=cand["week"].astype(int),
                       team=cand["team"].astype(str), gsis_id=cand["gsis_id"].astype(str))
    rep = report(ctx, seasons)[["season", "week", "gsis_id", "status"]]
    ros = roster(ctx, seasons)
    on53 = ros.loc[ros["status"].isin(ON_53), ["season", "week", "team", "gsis_id"]].assign(_on=1.0)
    have = ros[["season", "week", "team"]].drop_duplicates().assign(_have=1.0)
    c = (cand.merge(rep, on=["season", "week", "gsis_id"], how="left")
         .merge(on53, on=["season", "week", "team", "gsis_id"], how="left")
         .merge(have, on=["season", "week", "team"], how="left"))
    st = c["status"].fillna("").astype(str)
    off53 = c["_have"].eq(1.0) & c["_on"].isna()
    c["out"] = (st.isin(NOT_EXPECTED) | off53).astype(float)
    c["questionable"] = (st.eq("Questionable") & c["out"].eq(0.0)).astype(float)
    return c.drop(columns=["status", "_on", "_have"])


def soft_tissue(text) -> bool:
    t = str(text or "").lower()
    return bool(SOFT_TISSUE & set(_WORD.findall(t))) or any(p in t for p in SOFT_PHRASES)


def real_injury(text) -> bool:
    """A report text that names an injury (not a rest day, personal matter,
    coach's decision, suspension, travel, illness or COVID protocol)."""
    t = str(text or "").strip().lower()
    if not t or t in NON_INJURY_TEXTS or any(p in t for p in NON_INJURY):
        return False
    return not (NON_INJURY_WORDS & set(_WORD.findall(t)))


def hurt(ctx, seasons) -> pd.DataFrame:
    """Player-weeks known to be injured from that week's own pre-game sources:
    on the week's report with an injury (`real_injury`, any designation), or
    on an injury list (`injury_list`) on the week's roster. (season, week,
    gsis_id, injury: the report's text, "" when only the roster says so)."""
    def build():
        rep = report(ctx, seasons)
        rep = rep.loc[[real_injury(x) for x in rep["injury"]], ["season", "week", "gsis_id", "injury"]]
        ros = roster(ctx, seasons)
        ros = ros.loc[injury_list(ros["status"], ros["code"]),
                      ["season", "week", "gsis_id"]].assign(injury="")
        out = pd.concat([rep, ros], ignore_index=True)
        out["_has"] = (out["injury"] != "").astype(int)
        out = (out.sort_values(["season", "week", "gsis_id", "_has"], kind="stable")
               .drop_duplicates(["season", "week", "gsis_id"], keep="last"))
        return out.drop(columns="_has").reset_index(drop=True)
    return _memo(ctx, "hurt", seasons, build)


def regulars(ctx, seasons, targets: pd.DataFrame, side: str, groups, *, last: int = 3,
             min_share: float = 0.5) -> pd.DataFrame:
    """Usage regulars (rule #4) of each target team-week (season, week, team):
    players at the snap-count positions `groups` who took at least
    `min_share` of the team's `side` snaps ("offense" or "defense") over the
    team's last `last` games before the week (snap counts, post-game; a
    team's snaps in a game = the most any of its players took on that side).
    Returns season, week, team, gsis_id, share, position (his snap-count
    position in the latest of those games)."""
    col = f"{side}_snaps"
    sn = snaps(ctx, seasons)
    sn = sn.assign(snaps=sn[col])
    team = (sn.groupby(["season", "team", "week"], as_index=False)["snaps"].max()
            .rename(columns={"snaps": "team_snaps"}))
    team = team.loc[team["team_snaps"] > 0].sort_values(["season", "team", "week"])
    team["i"] = team.groupby(["season", "team"]).cumcount()
    # k = how many of the team's charted games came before the target week
    t = targets[["season", "week", "team"]].drop_duplicates().sort_values("week")
    t = pd.merge_asof(t, team[["season", "team", "week", "i"]].sort_values("week"), on="week",
                      by=["season", "team"], allow_exact_matches=False, direction="backward")
    t = t.loc[t["i"].notna()].copy()
    t["k"] = t["i"].astype(int) + 1
    p = sn.loc[sn["position"].isin(list(groups)) & (sn["snaps"] > 0)]
    p = p.merge(team[["season", "team", "week", "i"]], on=["season", "team", "week"], how="inner")
    # a game counts toward the windows of the next `last` target weeks
    ex = pd.concat([p.assign(k=p["i"] + j) for j in range(1, last + 1)], ignore_index=True)
    tx = pd.concat([team.assign(k=team["i"] + j) for j in range(1, last + 1)], ignore_index=True)
    den = tx.groupby(["season", "team", "k"], as_index=False)["team_snaps"].sum()
    num = (ex.sort_values("week", kind="stable")
           .groupby(["season", "team", "k", "gsis_id"], as_index=False)
           .agg(snaps=("snaps", "sum"), position=("position", "last")))
    r = num.merge(den, on=["season", "team", "k"], how="inner")
    r["share"] = r["snaps"] / r["team_snaps"]
    r = r.loc[r["share"] >= min_share]
    out = t[["season", "week", "team", "k"]].merge(r, on=["season", "team", "k"], how="inner")
    return out[["season", "week", "team", "gsis_id", "share", "position"]].reset_index(drop=True)


def _team_weeks(tg: pd.DataFrame) -> dict:
    return {k: np.sort(g["week"].to_numpy()) for k, g in tg.groupby(["season", "team"])}


def ramp(ctx, position: str) -> pd.DataFrame:
    """The return-ramp columns for one position's target rows (RB-02, WR-13 and
    TE-12 share these definitions), as of the weeks before the row's week:

      ret_back       1 when this is his first game back from an injury
                     absence: he played earlier this season, the team has
                     played at least one game since his last one, and he was
                     injured in between (`hurt` in a week after his last
                     game, through this week's report). 0 otherwise: a
                     healthy scratch, a suspension or a personal absence is
                     not a return here, and neither is a player with no
                     earlier game this season (a season debut is the
                     returner study's population).
      ret_missed     how many of the team's games he missed since his last
                     game when ret_back (else 0), capped at MAX_MISSED.
      ret_soft       ret_back, and the injury on his latest report entry
                     that names one since his last game (through this week's
                     report) is a muscle strain (SOFT_TISSUE); else 0.
      mate_back_opp  his share of the position's opportunities (carries +
                     targets) in the team's last game x the opportunities per
                     game this season of the same-position teammates expected
                     back this week from injury: each has a box score for the
                     team earlier this season, took no part in its last game
                     and was injured that week (`hurt`), and is on its 53
                     this week and not Out or Doubtful. The volume a fill-in
                     is about to give back (the reverse of the shipped
                     `vacated_pickup`).

    "Played" is a box-score line or any snap (`played`); positions are the
    box score's. Never missing: every target row gets all four."""
    keys = ctx.keys_for(position)[["season", "week", "gsis_id", "team"]].reset_index(drop=True)
    keys = keys.assign(season=keys["season"].astype(int), week=keys["week"].astype(int),
                       gsis_id=keys["gsis_id"].astype(str), team=keys["team"].astype(str))
    seasons = sorted(int(s) for s in keys["season"].unique())
    pl = played(ctx, seasons)
    bx = box(ctx, seasons)
    tg = C.team_games(ctx, seasons)[["season", "week", "team"]].drop_duplicates()
    tg = tg.assign(team=tg["team"].astype(str))
    tweeks = _team_weeks(tg)

    # his last game before the week, this season
    k = keys.assign(_o=np.arange(len(keys))).sort_values("week", kind="stable")
    h = pl[["season", "week", "gsis_id"]].assign(last_week=pl["week"].astype(float))
    h = h.assign(gsis_id=h["gsis_id"].astype(str)).sort_values("week", kind="stable")
    k = pd.merge_asof(k, h, on="week", by=["season", "gsis_id"], allow_exact_matches=False,
                      direction="backward")
    missed = []
    for s, t, w, lw in zip(k["season"], k["team"], k["week"], k["last_week"]):
        arr = tweeks.get((int(s), str(t)))
        if arr is None or lw != lw:
            missed.append(0.0)
            continue
        missed.append(float(np.searchsorted(arr, w, "left") - np.searchsorted(arr, lw, "right")))
    k["missed"] = missed

    # injured since his last game (through this week's report and roster)?
    hu = hurt(ctx, seasons)
    hw = hu[["season", "week", "gsis_id"]].assign(hurt_week=hu["week"].astype(float))
    k = pd.merge_asof(k.sort_values("week", kind="stable"), hw.sort_values("week", kind="stable"),
                      on="week", by=["season", "gsis_id"], allow_exact_matches=True,
                      direction="backward")
    injured = k["hurt_week"].notna() & (k["hurt_week"] > k["last_week"].fillna(-1))
    k["ret_back"] = ((k["missed"] >= 1) & injured).astype(float)
    k["ret_missed"] = k["missed"].clip(upper=MAX_MISSED) * k["ret_back"]

    # the injury on his latest report entry that names one, since his last game
    named = hu.loc[hu["injury"] != "", ["season", "week", "gsis_id", "injury"]]
    named = named.assign(rep_week=named["week"].astype(float))
    k = pd.merge_asof(k.sort_values("week", kind="stable"), named.sort_values("week", kind="stable"),
                      on="week", by=["season", "gsis_id"], allow_exact_matches=True,
                      direction="backward")
    since = k["rep_week"].notna() & (k["rep_week"] > k["last_week"].fillna(-1))
    soft = np.array([soft_tissue(x) for x in k["injury"].fillna("")])
    k["ret_soft"] = (k["ret_back"].eq(1.0) & since & soft).astype(float)

    # teammates expected back this week, and his share of the last game
    bxp = bx.loc[bx["position"] == position].assign(opps=lambda d: d["carries"] + d["targets"])
    plh = pl.assign(gsis_id=pl["gsis_id"].astype(str))
    hurt_keys = set(zip(hu["season"], hu["week"], hu["gsis_id"]))
    groups = k.groupby(["season", "week"]).groups
    cands, shares = [], {}
    for (s, w), idx in groups.items():
        s, w = int(s), int(w)
        past = bxp.loc[(bxp["season"] == s) & (bxp["week"] < w)]
        rows = k.loc[idx]
        teams = sorted(set(rows["team"]))
        last_game = {}
        for t in teams:
            arr = tweeks.get((s, t))
            n = 0 if arr is None else int(np.searchsorted(arr, w, "left"))
            last_game[t] = int(arr[n - 1]) if n > 0 else None
        seen = past.loc[past["team"].isin(teams)]
        if seen.empty:
            continue
        opp_pg = past.groupby("gsis_id")["opps"].mean()
        last_any = plh.loc[(plh["season"] == s) & (plh["week"] < w)].groupby("gsis_id")["week"].max()
        cand = seen[["team", "gsis_id"]].drop_duplicates()
        cand = cand.assign(tl=pd.to_numeric(cand["team"].map(last_game), errors="coerce"))
        cand = cand.loc[cand["tl"].notna()]
        cand = cand.loc[cand["gsis_id"].map(last_any).fillna(-1).to_numpy(dtype="float64")
                        < cand["tl"].to_numpy(dtype="float64")]
        cand = cand.loc[[(s, int(tl), g) in hurt_keys for tl, g in zip(cand["tl"], cand["gsis_id"])]]
        if len(cand):
            cands.append(cand[["team", "gsis_id"]].assign(season=s, week=w,
                                                          opp_pg=cand["gsis_id"].map(opp_pg)))
        tl = pd.to_numeric(seen["team"].map(last_game), errors="coerce").to_numpy(dtype="float64")
        game = seen.loc[seen["week"].to_numpy(dtype="float64") == tl]
        tot = game.groupby("team")["opps"].sum()
        mine = game.groupby(["team", "gsis_id"])["opps"].sum()
        for i, g, t in zip(idx, rows["gsis_id"], rows["team"]):
            den = float(tot.get(t, 0.0))
            shares[i] = float(mine.get((t, g), 0.0)) / den if den > 0 else 0.0
    k["mate_back_opp"] = 0.0
    if cands:
        cand = pd.concat(cands, ignore_index=True)
        av = availability(ctx, seasons, cand)
        back = av.loc[av["out"] == 0.0].groupby(["season", "week", "team"])["opp_pg"].sum()
        pool = pd.Series([back.get((int(s), int(w), str(t)), 0.0)
                          for s, w, t in zip(k["season"], k["week"], k["team"])], index=k.index)
        share = pd.Series(shares, dtype="float64").reindex(k.index).fillna(0.0)
        k["mate_back_opp"] = (share * pool).astype("float64")
    k = k.sort_values("_o").reset_index(drop=True)
    return k[["season", "week", "gsis_id", "ret_back", "ret_missed", "ret_soft", "mate_back_opp"]]
