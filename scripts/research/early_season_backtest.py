"""Early-season priors: a number for weeks 1-3 from last season, the draft and the combine.

    PYTHONPATH=src python scripts/research/early_season_backtest.py --idea QB-12 --coverage-only
    PYTHONPATH=src python scripts/research/early_season_backtest.py --idea QB-12
    PYTHONPATH=src python scripts/research/early_season_backtest.py --list

Roadmap ideas QB-12, WR-10 and TE-13 (docs/research/MODEL_ROADMAP.md, kind A,
phase 4): the week 1-3 priors for 2027, tested now on 2019-2025 so that the
offseason only re-tests them on 2026. block_runner.py scores weeks 4-18 (the
advanced model's `min_week`), so these are not runner blocks; this script is
their harness, in the style of returner_backtest.py. Each idea's own
pre-registration is docs/research/blocks/<ID>.md.

WHAT THE PAGE DOES TODAY in weeks 1-3. The advanced model and the stack start
in week 4, so the page serves `gridiron.projection.project` on THIS season's
box scores before the week, with the week's implied team total. In week 1 it
abstains for everyone ("no admissible box scores"). In weeks 2-3 it projects
the players with an earlier game from one or two games of usage shrunk to
role priors, and abstains on the rest. It never reads last season, the draft
or the combine for a projection.

PRE-REGISTRATION (written before any result was seen)

Rows. Every regular-season player-week of weeks 1-3, 2019-2025, at the idea's
position (the box score's position), with a box score (he played) and a
Sleeper projection (the block runner's universe). Actual = league points
(gridiron.scoring through gridiron.usage.player_weeks).

Columns, each as of before the row's week. Ground rule 4: a column with any
missing value at the position enters as 0 plus `<col>__known`, never the
training mean.
  page       the page's number today (above), computed by the current
             gridiron.projection; missing where it abstains
  ppg_prev   his league points per game over last season's regular-season
             games (any team), known when he played >= 4 of them (the
             returner backtest's threshold)
  the idea's own columns (QB-12: new_coach, tgt_left; WR-10:
             rookie_capital, rookie_forty; TE-13: te_capital, te_age,
             te_age_sq), defined in its doc and in `IDEAS` below

Systems, at the idea's position, each fitted separately for week 1, week 2
and week 3 on the training seasons (A.Ridge, lam 5: the shipped model's
standardised least squares on the mean scale; fitted predictions floored at 0):
  null       the training mean of that week's rows: knows nothing
  today      the page as served: `page` where it has a number, else `null`
  today_fit  the page refit: [page, page__known] (in week 1 this is the null)
  prior      today_fit's columns + [ppg_prev, ppg_prev__known]: last season,
             discounted (the fitted coefficient is the discount)
  cand       prior's columns + the idea's columns
  sleeper    Sleeper's published projection for the player-week, scored with
             the league's rules: CONTEXT ONLY, never part of the bar

Folds: the seven seasons 2019-2025, each held out in turn, every other season
training (block_runner.py's folds).

Metrics per fold (block_runner.py's): start/sit pairwise within a week among
the rows Sleeper projected 5+ (pairs with equal actual points skipped; a tie
in a system is a miss, so a constant orders nobody), and MAE and bias
(actual - prediction) on every row.

BAR, all three:
  1. cand beats BOTH today and today_fit, on MAE (lower) AND pairwise (higher),
     in every fold, over weeks 1-3 and again over weeks 2-3 alone (where the
     page has a number of its own; in week 1 it is a constant).
  2. cand beats prior on MAE AND pairwise in every fold, over weeks 1-3: the
     idea's own columns add something to last season, discounted.
  3. Out-of-fold cand, the seven test seasons pooled: |bias| <= 1.5 points in
     every band with n >= 10, in both band families: last season's points
     per game (none, 0-5, 5-10, 10-15, 15-20, 20+) and the idea's own
     (`IDEAS`, its doc).
PASS = 1 and 2 and 3. The plain prior is judged on 1 and 3 too and reported
as `prior_clears`. It is the shared core (QB-12's first clause, WR-10's
veterans), never a pass for an idea.

Leak check (a scored run refuses without it): for (2021, week 1), (2023,
week 2) and (2025, week 3) every column is rebuilt with that season's box
scores cut to the weeks before and its schedule and weekly rosters cut to the
week itself, and must come out identical.

Output: docs/research/blocks/results/<ID>.json (top-level `summary` and
`folds_tested`; the fold tables, the bands, Sleeper as context, and the fit
on all seven seasons that the 2026 re-test applies unchanged) and one line in
docs/research/blocks/runs/<ID>.csv with the blob hashes, as block_runner.py
writes them. A scored run refuses unless this script and
docs/research/blocks/<ID>.md are committed and unchanged. `--coverage-only`
prints coverage, feature counts and the leak check, never a score.

Inputs are public: nflverse box scores, schedules (lines, head coaches),
weekly rosters, draft picks, the combine and the players table (birth date,
rookie season), and Sleeper's projections (research cache, gitignored). Joins
are by gsis id; Sleeper and PFR ids go through the one cached crosswalk
(rule #3). Nothing written names a player.

2026 HOLDOUT (added 2026-11-02, after QB-12, WR-10 and TE-13 were scored;
nothing above changed)

    PYTHONPATH=src python scripts/research/early_season_backtest.py --idea WR-10 --holdout-2026 --coverage-only
    PYTHONPATH=src python scripts/research/early_season_backtest.py --idea WR-10 --holdout-2026

For an idea whose committed result passes (results/<ID>.json), the fit on all
seven seasons stored there (`frozen_fit`: prior and cand per week, and the
null) is applied UNCHANGED to the 2026 week 1-3 rows, built exactly as the
historical rows were (same population, columns and encoding: a column with
`<col>__known` in the frozen fit enters as 0 where missing plus the
indicator). `today` is the page or the stored null; `today_fit`, which the
frozen fit does not store, is [page, page__known] fitted on all seven
seasons the same way. All are floored at 0; Sleeper is context. Each system
is scored pooled over the 2026 rows (weeks 1-3, weeks 2-3, and each week) on
the same metrics. The bar is the holdout's own pre-registration,
docs/research/blocks/<ID>-2026.md. A scored holdout refuses unless that doc,
this script and results/<ID>.json are committed and unchanged; the
2019-2025 rows rebuilt today have the digest the idea was scored on; the
refit on them reproduces the stored fit; and the leak check passes for 2026
weeks 1, 2 and 3. It writes results/<ID>-2026.json and one line in
runs/<ID>-2026.csv. `--coverage-only` prints the 2026 coverage, the counts,
the leak check and the two reproduction checks, never a score.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk, normalize_id
from gridiron.models import advanced as A
from gridiron.paths import REPO_ROOT
from gridiron.projection import build_evidence, project
from gridiron.usage import player_weeks
from gridiron.weekly import schedule_index

from research_common import load_script

br = load_script("block_runner", "scripts/research/block_runner.py")

SEASONS = tuple(range(2019, 2026))
FOLDS = SEASONS
WEEKS = (1, 2, 3)
#: Box-score positions the page's evidence is built from (the live board's).
FRAME_POSITIONS = ("QB", "RB", "WR", "TE", "K")
MIN_PRIOR_GAMES = 4
#: An undrafted player's pick, just past the last compensatory pick.
UNDRAFTED_PICK = 260
#: The age the development curve is centred on (te_age_sq).
AGE_CENTRE = 27.0
#: Weekly-roster statuses that mean "on the 53 for week 1" (INA is the
#: game-day inactive list: still on the team).
ON_ROSTER = ("ACT", "INA")
LAM = 5.0
RELEVANT = br.RELEVANT
BIAS_LIMIT = 1.5
BAND_MIN_N = 10
#: Bands of last season's points per game (the bias families' first).
PPG_EDGES = (-math.inf, 5.0, 10.0, 15.0, 20.0, math.inf)
PPG_LABELS = ("<5", "5-10", "10-15", "15-20", "20+")
LEAK_CHECKS = ((2021, 1), (2023, 2), (2025, 3))
SYSTEMS = ("null", "today", "today_fit", "prior", "cand", "sleeper")
SCRIPT = REPO_ROOT / "scripts" / "research" / "early_season_backtest.py"
RUN_FIELDS = br.RUN_FIELDS
#: The holdout season (`--holdout-2026`) and its leak check: every week it scores.
HOLDOUT = 2026
HOLDOUT_LEAK_CHECKS = tuple((HOLDOUT, w) for w in WEEKS)


# --------------------------------------------------------------------------- data

class Data:
    """The frames the columns read. `cut(s, w)` keeps what was known before
    week w of season s: that season's box scores before the week, its
    schedule and weekly rosters through the week, every earlier season whole,
    no later season. The static tables (draft, combine, players) are facts
    fixed before any season they are used in."""

    POST_GAME = ("weekly",)
    PRE_GAME = ("schedule", "rosters")

    def __init__(self, frames: dict[str, pd.DataFrame], crosswalk: Crosswalk,
                 cutoff: tuple[int, int] | None = None):
        self.f = frames
        self.crosswalk = crosswalk
        self.cutoff = cutoff

    def cut(self, season: int, week: int) -> "Data":
        out = {}
        for name, df in self.f.items():
            if name in self.POST_GAME:
                keep = (df["season"] < season) | ((df["season"] == season) & (df["week"] < week))
            elif name in self.PRE_GAME:
                keep = (df["season"] < season) | ((df["season"] == season) & (df["week"] <= week))
            else:
                out[name] = df
                continue
            out[name] = df.loc[keep]
        return Data(out, self.crosswalk, (season, week))


def load_data(crosswalk: Crosswalk, last: int = SEASONS[-1]) -> Data:
    """Box scores and schedules from the season before the first through
    `last`, weekly rosters from the first through `last` (the holdout passes
    HOLDOUT; the historical test, the default)."""
    nfl = br._nfl()
    seasons = list(range(SEASONS[0] - 1, last + 1))
    empty = Crosswalk({}, {})
    parts = []
    for s in seasons:
        w = nfl.load_player_stats([s], summary_level="week").to_pandas()
        w = w.loc[(w["season_type"] == "REG") & w["position"].isin(FRAME_POSITIONS)]
        parts.append(player_weeks(w, None, empty))
        print(f"  box scores {s}: {len(parts[-1])} player-weeks", flush=True)
    weekly = br.normalize_teams(pd.concat(parts, ignore_index=True))
    weekly["season"] = weekly["season"].astype(int)
    weekly["week"] = weekly["week"].astype(int)
    weekly = weekly.drop_duplicates(["season", "week", "gsis_id"])

    sched = nfl.load_schedules(seasons).to_pandas()
    keep = ["season", "week", "game_id", "game_type", "gameday", "gametime", "home_team",
            "away_team", "home_coach", "away_coach", "total_line", "spread_line"]
    sched = br.normalize_teams(sched.loc[sched["game_type"] == "REG", keep].copy())
    sched["season"], sched["week"] = sched["season"].astype(int), sched["week"].astype(int)

    ros = nfl.load_rosters_weekly(list(range(SEASONS[0], last + 1))).to_pandas()
    ros = ros.loc[(ros["game_type"] == "REG") & ros["week"].isin(WEEKS),
                  ["season", "week", "team", "gsis_id", "status"]].copy()
    ros["gsis_id"] = ros["gsis_id"].map(normalize_id)
    ros = br.normalize_teams(ros.loc[ros["gsis_id"] != ""])
    ros["season"], ros["week"] = ros["season"].astype(int), ros["week"].astype(int)

    # Static tables: only the columns that were known before the season. The
    # draft table also carries career outcomes (games, AV, Pro Bowls): never read.
    draft = nfl.load_draft_picks().to_pandas()[["season", "round", "pick", "gsis_id"]]
    draft["gsis_id"] = draft["gsis_id"].map(normalize_id)
    draft = draft.loc[draft["gsis_id"] != ""].drop_duplicates("gsis_id")
    combine = nfl.load_combine().to_pandas()[["season", "pfr_id", "forty"]]
    combine["pfr_id"] = combine["pfr_id"].map(normalize_id)
    combine = (combine.loc[(combine["pfr_id"] != "") & combine["forty"].notna()]
               .sort_values("season").drop_duplicates("pfr_id", keep="last"))
    players = nfl.load_players().to_pandas()[["gsis_id", "birth_date", "rookie_season"]]
    players["gsis_id"] = players["gsis_id"].map(normalize_id)
    players = players.loc[players["gsis_id"] != ""].drop_duplicates("gsis_id")
    return Data({"weekly": weekly, "schedule": sched, "rosters": ros, "draft": draft,
                 "combine": combine, "players": players}, crosswalk)


def sleeper_frame(crosswalk: Crosswalk, seasons=SEASONS) -> pd.DataFrame:
    pb = load_script("projection_backtest", "scripts/research/projection_backtest.py")
    parts = []
    for s in seasons:
        sl = pb.sleeper_projections(s, list(WEEKS), crosswalk)
        parts.append(sl[["week", "gsis_id", "sleeper"]].assign(season=s))
    out = pd.concat(parts, ignore_index=True)
    out["week"] = out["week"].astype(int)
    return out


def population(data: Data, position: str, sleeper: pd.DataFrame,
               seasons=SEASONS) -> pd.DataFrame:
    wk = data.f["weekly"]
    rows = wk.loc[wk["season"].isin(seasons) & wk["week"].isin(WEEKS)
                  & (wk["position"] == position),
                  ["season", "week", "gsis_id", "position", "team", "league_points"]]
    rows = rows.rename(columns={"league_points": "actual"})
    rows = rows.merge(sleeper, on=["season", "week", "gsis_id"], how="inner")
    return rows.sort_values(["season", "week", "gsis_id"]).reset_index(drop=True)


# ------------------------------------------------------------------- shared columns

def page_numbers(data: Data, rows: pd.DataFrame) -> pd.Series:
    """What the page serves today: `project` on this season's box scores
    before the week, with the week's implied total. NaN where it abstains."""
    out = pd.Series(np.nan, index=rows.index, dtype="float64")
    wk, sc = data.f["weekly"], data.f["schedule"]
    for (s, w), g in rows.groupby(["season", "week"]):
        frame = wk.loc[(wk["season"] == s) & (wk["week"] < w)]
        if len(frame) == 0:
            continue                    # week 1: no box score this season yet
        ev = build_evidence(frame, through_week=int(w) - 1)
        games = schedule_index(sc.loc[sc["season"] == s], int(w))
        for i, r in zip(g.index, g.itertuples()):
            pe = ev.players.get(str(r.gsis_id))
            if pe is None:
                continue
            game = games.get(str(r.team))
            p = project(pe, position=str(r.position), week=int(w),
                        implied_total=game.implied_total if game else None, evidence=ev)
            if p.usable:
                out.at[i] = float(p.mean)
    return out


def prev_ppg(data: Data, rows: pd.DataFrame) -> pd.Series:
    """League points per game over last season's regular-season games (any
    team), known with >= MIN_PRIOR_GAMES games."""
    wk = data.f["weekly"]
    out = pd.Series(np.nan, index=rows.index, dtype="float64")
    for s, g in rows.groupby("season"):
        prev = wk.loc[wk["season"] == s - 1]
        agg = prev.groupby("gsis_id").agg(games=("week", "nunique"), pts=("league_points", "sum"))
        agg = agg.loc[agg["games"] >= MIN_PRIOR_GAMES]
        out.loc[g.index] = g["gsis_id"].map(agg["pts"] / agg["games"]).to_numpy(dtype="float64")
    return out


def draft_pick(data: Data, gids: pd.Series) -> pd.Series:
    """Overall pick from the nflverse draft table; undrafted -> UNDRAFTED_PICK."""
    return gids.map(data.f["draft"].set_index("gsis_id")["pick"]).astype("float64") \
        .fillna(float(UNDRAFTED_PICK))


def draft_round(data: Data, gids: pd.Series) -> pd.Series:
    return gids.map(data.f["draft"].set_index("gsis_id")["round"]).astype("float64")


def is_rookie(data: Data, rows: pd.DataFrame) -> pd.Series:
    first = rows["gsis_id"].map(data.f["players"].set_index("gsis_id")["rookie_season"])
    return pd.to_numeric(first, errors="coerce") == rows["season"]


def bands_of(x: pd.Series, edges, labels) -> pd.Series:
    """Band labels, "none" where the value is missing."""
    out = pd.cut(x, list(edges), right=False, labels=list(labels)).astype(object)
    return out.where(x.notna(), "none").astype(str)


def ppg_band(x: pd.Series) -> pd.Series:
    return bands_of(x, PPG_EDGES, PPG_LABELS)


# ------------------------------------------------------------------ idea columns

def head_coaches(sched: pd.DataFrame) -> pd.DataFrame:
    home = sched[["season", "week", "home_team", "home_coach"]].rename(
        columns={"home_team": "team", "home_coach": "coach"})
    away = sched[["season", "week", "away_team", "away_coach"]].rename(
        columns={"away_team": "team", "away_coach": "coach"})
    return pd.concat([home, away], ignore_index=True)


def build_qb12(data: Data, rows: pd.DataFrame) -> pd.DataFrame:
    """new_coach: his team's head coach in its first game this season is not
    the head coach of most of his games last season (a team change counts);
    tgt_left: the share of his team's targets last season that went to
    players not on its week-1 roster (ACT or INA) this season."""
    sc, wk, ros = data.f["schedule"], data.f["weekly"], data.f["rosters"]
    hc = head_coaches(sc)
    first = hc.sort_values("week").drop_duplicates(["season", "team"])
    now = rows[["season", "team"]].merge(first[["season", "team", "coach"]],
                                         on=["season", "team"], how="left")["coach"]
    games = wk[["season", "week", "gsis_id", "team"]].merge(hc, on=["season", "week", "team"],
                                                            how="left")
    games = games.dropna(subset=["coach"])
    cnt = (games.groupby(["season", "gsis_id", "coach"])
           .agg(n=("week", "size"), last=("week", "max")).reset_index()
           .sort_values(["season", "gsis_id", "n", "last"], ascending=[True, True, False, False])
           .drop_duplicates(["season", "gsis_id"]))
    cnt["season"] = cnt["season"] + 1                 # last season's coach, for this season
    prev = rows[["season", "gsis_id"]].merge(cnt[["season", "gsis_id", "coach"]],
                                             on=["season", "gsis_id"], how="left")["coach"]
    new_coach = pd.Series(np.where(now.to_numpy() != prev.to_numpy(), 1.0, 0.0), index=rows.index)
    new_coach[prev.isna().to_numpy() | now.isna().to_numpy()] = np.nan

    tg = wk.groupby(["season", "team", "gsis_id"])["targets"].sum(min_count=1).fillna(0.0) \
        .reset_index()
    tg["season"] = tg["season"] + 1                   # last season's targets, for this season
    on53 = ros.loc[(ros["week"] == 1) & ros["status"].isin(ON_ROSTER),
                   ["season", "team", "gsis_id"]].drop_duplicates().assign(stayed=1.0)
    tg = tg.merge(on53, on=["season", "team", "gsis_id"], how="left")
    tg["kept"] = tg["targets"] * tg["stayed"].fillna(0.0)
    tot = tg.groupby(["season", "team"])[["targets", "kept"]].sum()
    listed = pd.MultiIndex.from_frame(on53[["season", "team"]].drop_duplicates())
    tot = tot.loc[tot.index.isin(listed) & (tot["targets"] > 0)]
    left = 1.0 - tot["kept"] / tot["targets"]
    key = pd.MultiIndex.from_frame(rows[["season", "team"]])
    tgt_left = pd.Series(left.reindex(key).to_numpy(dtype="float64"), index=rows.index)

    out = pd.DataFrame({"new_coach": new_coach, "tgt_left": tgt_left}, index=rows.index)
    out["band_new_coach"] = np.select([out["new_coach"] == 1.0, out["new_coach"] == 0.0],
                                      ["new coach", "same coach"], "none")
    out["band_tgt_left"] = bands_of(out["tgt_left"], (-math.inf, 0.25, 0.5, math.inf),
                                    ("<25%", "25-50%", "50%+"))
    return out


def build_wr10(data: Data, rows: pd.DataFrame) -> pd.DataFrame:
    """For a rookie (the players table's rookie season is this season):
    rookie_capital = -ln(overall pick), undrafted at pick 260, and
    rookie_forty = his combine 40-yard time; missing for everyone else."""
    rookie = is_rookie(data, rows)
    pick = draft_pick(data, rows["gsis_id"])
    pfr = rows["gsis_id"].map(lambda g: data.crosswalk.pfr(g) or "")
    forty = pfr.map(data.f["combine"].set_index("pfr_id")["forty"]).astype("float64")
    out = pd.DataFrame({"rookie_capital": (-np.log(pick)).where(rookie),
                        "rookie_forty": forty.where(rookie)}, index=rows.index)
    rnd = draft_round(data, rows["gsis_id"])
    band = np.select([~rookie, rnd == 1, rnd == 2, rnd.isin([3, 4]), rnd.isin([5, 6, 7])],
                     ["veteran", "rookie R1", "rookie R2", "rookie R3-4", "rookie R5-7"],
                     "rookie undrafted")
    out["band_rookie"] = band
    return out


def build_te13(data: Data, rows: pd.DataFrame) -> pd.DataFrame:
    """te_capital = -ln(overall pick), undrafted at pick 260, for every tight
    end; te_age = age on 1 September of the season; te_age_sq = (age - 27)^2."""
    pick = draft_pick(data, rows["gsis_id"])
    birth = pd.to_datetime(rows["gsis_id"].map(data.f["players"].set_index("gsis_id")["birth_date"]),
                           errors="coerce")
    ref = pd.to_datetime(rows["season"].astype(int).astype(str) + "-09-01")
    age = (ref - birth).dt.days.astype("float64") / 365.25
    out = pd.DataFrame({"te_capital": -np.log(pick), "te_age": age,
                        "te_age_sq": (age - AGE_CENTRE) ** 2}, index=rows.index)
    out["band_age"] = bands_of(age, (-math.inf, 24.0, 27.0, 30.0, math.inf),
                               ("<24", "24-27", "27-30", "30+"))
    return out


@dataclass(frozen=True)
class Idea:
    id: str
    position: str
    title: str
    columns: tuple[str, ...]
    build: Callable[[Data, pd.DataFrame], pd.DataFrame]
    bands: tuple[str, ...]                # the idea's own band families


IDEAS = {
    "QB-12": Idea("QB-12", "QB", "Week 1-3 prior: last season, discounted, with a head-coach "
                  "change and receiver turnover", ("new_coach", "tgt_left"), build_qb12,
                  ("band_new_coach", "band_tgt_left")),
    "WR-10": Idea("WR-10", "WR", "Rookie and early-season priors: draft capital and the 40 for "
                  "rookies, last season for veterans", ("rookie_capital", "rookie_forty"),
                  build_wr10, ("band_rookie",)),
    "TE-13": Idea("TE-13", "TE", "Tight-end development curve: draft capital and age",
                  ("te_capital", "te_age", "te_age_sq"), build_te13, ("band_age",)),
}


def features(idea: Idea, data: Data, rows: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=rows.index)
    f["page"] = page_numbers(data, rows)
    f["ppg_prev"] = prev_ppg(data, rows)
    f["band_ppg"] = ppg_band(f["ppg_prev"])
    return pd.concat([f, idea.build(data, rows)], axis=1)


def numeric_columns(idea: Idea) -> list[str]:
    return ["page", "ppg_prev", *idea.columns]


def band_columns(idea: Idea) -> list[str]:
    return ["band_ppg", *idea.bands]


# --------------------------------------------------------------------- checks

def leak_check(idea: Idea, data: Data, rows: pd.DataFrame, full: pd.DataFrame,
               checks=LEAK_CHECKS) -> list[str]:
    problems = []
    num, cat = numeric_columns(idea), band_columns(idea)
    for s, w in checks:
        sel = rows.loc[(rows["season"] == s) & (rows["week"] == w)]
        if len(sel) == 0:
            problems.append(f"{s} week {w}: no rows to check")
            continue
        cut = features(idea, data.cut(s, w), sel)
        a = full.loc[sel.index, num].to_numpy(dtype="float64")
        b = cut.loc[sel.index, num].to_numpy(dtype="float64")
        diff = ~np.isclose(a, b, equal_nan=True, rtol=0, atol=1e-9)
        bad = [num[j] for j in sorted(set(np.where(diff)[1]))]
        bad += [c for c in cat if not full.loc[sel.index, c].equals(cut.loc[sel.index, c])]
        if bad:
            problems.append(f"{s} week {w}: {int(diff.any(axis=1).sum())} row(s) differ in {bad}"
                            " when that season's data from the week on is removed")
    return problems


def digest(frame: pd.DataFrame, cols: list[str]) -> str:
    f = frame.sort_values(["season", "week", "gsis_id"])[cols]
    return hashlib.sha256(f.to_csv(index=False, float_format="%.6f").encode()).hexdigest()[:16]


def coverage(frame: pd.DataFrame, idea: Idea) -> dict:
    cov = {}
    for c in numeric_columns(idea):
        cov[c] = {str(s): round(float(g[c].notna().mean()), 3) for s, g in frame.groupby("season")}
    cov["page_by_week"] = {str(w): round(float(g["page"].notna().mean()), 3)
                           for w, g in frame.groupby("week")}
    return cov


def counts(frame: pd.DataFrame, idea: Idea) -> dict:
    """Feature-side counts only (no actual points): rows, and band sizes."""
    out = {"rows": {str(s): {str(w): int(len(gw)) for w, gw in g.groupby("week")}
                    for s, g in frame.groupby("season")},
           "relevant_rows": int((frame["sleeper"] >= RELEVANT).sum())}
    for b in band_columns(idea):
        out[b] = {str(k): int(v) for k, v in frame[b].value_counts().sort_index().items()}
    return out


# -------------------------------------------------------------------- scoring

def prepare(frame: pd.DataFrame, cols: list[str]) -> dict[str, list[str]]:
    """Ground rule 4, as block_runner.prepare: a column with any missing value
    at the position becomes 0 there plus `<col>__known`."""
    out = {}
    for c in cols:
        if frame[c].isna().any():
            frame[f"{c}__known"] = frame[c].notna().astype(float)
            frame[c] = frame[c].fillna(0.0)
            out[c] = [c, f"{c}__known"]
        else:
            out[c] = [c]
    return out


def system_columns(mc: dict[str, list[str]], idea: Idea) -> dict[str, list[str]]:
    page = mc["page"]
    prior = page + mc["ppg_prev"]
    return {"today_fit": page, "prior": prior,
            "cand": prior + [x for c in idea.columns for x in mc[c]]}


def crossval(frame: pd.DataFrame, sys_cols: dict[str, list[str]]) -> pd.DataFrame:
    oof = []
    for test_s in FOLDS:
        train = frame.loc[frame["season"] != test_s]
        test = frame.loc[frame["season"] == test_s].copy()
        for name in ("null", "today", *sys_cols):
            test[name] = np.nan
        for w in WEEKS:
            tr = train.loc[train["week"] == w]
            idx = test.index[test["week"] == w]
            null = float(tr["actual"].mean())
            test.loc[idx, "null"] = null
            test.loc[idx, "today"] = test.loc[idx, "page_raw"].fillna(null)
            for name, cols in sys_cols.items():
                m = A.Ridge.fit(tr, tr["actual"], cols, lam=LAM)
                test.loc[idx, name] = np.maximum(br.predict(m, test.loc[idx]), 0.0)
        oof.append(test)
    return pd.concat(oof)


def score(df: pd.DataFrame) -> dict:
    rel = df.loc[df["sleeper"] >= RELEVANT]
    pw = br.pairwise(rel, SYSTEMS, by=("week", "position"))
    out = {"rows": int(len(df)), "pairs": int(pw["n"])}
    for s in SYSTEMS:
        e = df["actual"] - df[s]
        out[s] = {"pairwise": None if pw[s] is None else round(float(pw[s]), 6),
                  "mae": round(float(e.abs().mean()), 4), "bias": round(float(e.mean()), 4)}
    return out


def beats(m: dict, a: str, b: str) -> bool:
    pa, pb = m[a]["pairwise"], m[b]["pairwise"]
    return pa is not None and pb is not None and pa > pb and m[a]["mae"] < m[b]["mae"]


def fold_tables(oof: pd.DataFrame) -> dict:
    out = {}
    for s, g in oof.groupby("season"):
        out[str(s)] = {"weeks_1_3": score(g), "weeks_2_3": score(g.loc[g["week"] >= 2]),
                       **{f"week_{w}": score(g.loc[g["week"] == w]) for w in WEEKS}}
    return out


def beats_page(f: dict, sys: str) -> bool:
    return all(beats(f[win], sys, ref) for win in ("weeks_1_3", "weeks_2_3")
               for ref in ("today", "today_fit"))


def band_tables(oof: pd.DataFrame, idea: Idea) -> dict:
    out = {}
    for b in band_columns(idea):
        rows = []
        for band, g in oof.groupby(b):
            row = {"band": str(band), "n": int(len(g)),
                   "actual_mean": round(float(g["actual"].mean()), 3)}
            for s in SYSTEMS:
                e = g["actual"] - g[s]
                row[s] = {"mean": round(float(g[s].mean()), 3), "bias": round(float(e.mean()), 3),
                          "mae": round(float(e.abs().mean()), 3)}
            rows.append(row)
        out[b] = rows
    return out


def bias_ok(bands: dict, sys: str) -> tuple[bool, list[str]]:
    bad = [f"{fam}:{r['band']} ({r[sys]['bias']:+.2f}, n={r['n']})" for fam, rows in bands.items()
           for r in rows if r["n"] >= BAND_MIN_N and abs(r[sys]["bias"]) > BIAS_LIMIT]
    return not bad, bad


def frozen_fit(frame: pd.DataFrame, sys_cols: dict[str, list[str]]) -> dict:
    """The fit on all seven seasons, per week: what the 2026 re-test applies
    unchanged. `per_unit` is each column's coefficient in its own units."""
    out = {}
    for name in ("prior", "cand"):
        out[name] = {}
        for w in WEEKS:
            tr = frame.loc[frame["week"] == w]
            m = A.Ridge.fit(tr, tr["actual"], sys_cols[name], lam=LAM)
            out[name][str(w)] = {**m.to_json(), "per_unit": {
                c: round(float(b) / float(sd), 4) for c, b, sd in zip(m.cols, m.beta[1:], m.sd)}}
    out["null"] = {str(w): round(float(frame.loc[frame["week"] == w, "actual"].mean()), 4)
                   for w in WEEKS}
    return out


# ----------------------------------------------------------------------- main

def assemble(idea: Idea, data: Data, sleeper: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = population(data, idea.position, sleeper)
    feats = features(idea, data, rows)
    return rows, pd.concat([rows, feats], axis=1)


def integrity(idea: Idea) -> dict:
    """The blob hashes of the pre-registration and this script, both
    committed and unchanged (block_runner.committed_blob raises otherwise)."""
    return {"prereg_blob": br.committed_blob(br.PREREG_DIR / f"{idea.id}.md"),
            "module_blob": br.committed_blob(SCRIPT), "helpers_blob": "-"}


def run(idea: Idea, data: Data, sleeper: pd.DataFrame, *, coverage_only: bool) -> dict:
    blobs = None if coverage_only else integrity(idea)
    rows, frame = assemble(idea, data, sleeper)
    num = numeric_columns(idea)
    dig = digest(frame, ["season", "week", "gsis_id", "actual", "sleeper", *num])
    cov, cnt = coverage(frame, idea), counts(frame, idea)
    print(f"\n{idea.id} - {idea.title}")
    print(f"  {len(frame)} {idea.position} rows, weeks 1-3 of {SEASONS[0]}-{SEASONS[-1]}; "
          f"{cnt['relevant_rows']} with Sleeper 5+; digest {dig}")
    for s, by in cnt["rows"].items():
        print(f"    {s}: " + ", ".join(f"week {w} {n}" for w, n in by.items()))
    for c in num:
        print(f"  coverage {c}: " + ", ".join(f"{s} {v:.0%}" for s, v in cov[c].items()))
    print("  page by week: " + ", ".join(f"week {w} {v:.0%}" for w, v in cov["page_by_week"].items()))
    for b in band_columns(idea):
        print(f"  band sizes {b}: {cnt[b]}")
    leaks = leak_check(idea, data, rows, frame)
    for line in leaks:
        print(f"  LEAK {line}")
    print(f"  leak check: {'FAILED' if leaks else 'passed'} "
          f"({', '.join(f'{s} week {w}' for s, w in LEAK_CHECKS)})")
    if coverage_only:
        return {"idea": idea.id, "coverage": cov, "counts": cnt, "leaks": leaks, "digest": dig}
    if leaks:
        raise SystemExit(f"refused: {idea.id} fails the leak check")

    frame["page_raw"] = frame["page"]
    mc = prepare(frame, num)
    sys_cols = system_columns(mc, idea)
    oof = crossval(frame, sys_cols)
    folds = fold_tables(oof)
    bands = band_tables(oof, idea)
    c1 = {s: beats_page(f, "cand") for s, f in folds.items()}
    c2 = {s: beats(f["weeks_1_3"], "cand", "prior") for s, f in folds.items()}
    p1 = {s: beats_page(f, "prior") for s, f in folds.items()}
    cand_bias, cand_bad = bias_ok(bands, "cand")
    prior_bias, prior_bad = bias_ok(bands, "prior")
    passes = all(c1.values()) and all(c2.values()) and cand_bias
    prior_clears = all(p1.values()) and prior_bias
    k = sum(c1[s] and c2[s] for s in c1)
    pos = idea.position

    print(f"\n  {'fold':<6}{'today':>16}{'today_fit':>16}{'prior':>16}{'cand':>16}{'sleeper':>16}"
          "   (weeks 1-3: pairwise / MAE)")
    for s, f in folds.items():
        m = f["weeks_1_3"]
        print(f"  {s:<6}" + "".join(f"{m[x]['pairwise']:>8.2%}/{m[x]['mae']:<7.3f}"
                                     for x in ("today", "today_fit", "prior", "cand", "sleeper")))
    print(f"  cand beats the page (both, weeks 1-3 and 2-3): "
          f"{sum(c1.values())}/{len(c1)} folds; cand beats prior: {sum(c2.values())}/{len(c2)}; "
          f"cand bias ok: {cand_bias} {cand_bad}")
    print(f"  prior beats the page: {sum(p1.values())}/{len(p1)} folds; prior bias ok: "
          f"{prior_bias} {prior_bad}")
    print(f"  -> {idea.id} {'PASSES' if passes else 'fails'}; plain prior "
          f"{'clears' if prior_clears else 'does not clear'}")

    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = br._git("rev-parse", "HEAD")[:12]
    summary = f"PASS {pos}" if passes else "fail"
    result = {
        "block": idea.id, "title": idea.title, "kind": "A: early-season prior (weeks 1-3)",
        "harness": "scripts/research/early_season_backtest.py",
        "summary": summary,
        "folds": f"{pos} {k}/{len(c1)} (vs the page {sum(c1.values())}/{len(c1)}, "
                 f"vs the plain prior {sum(c2.values())}/{len(c2)})",
        "note": (f"plain prior (last season, discounted) {'clears' if prior_clears else 'does not clear'}"
                 f" the bar against the page: {sum(p1.values())}/{len(p1)} folds, bias "
                 f"{'ok' if prior_bias else 'off in ' + '; '.join(prior_bad)}"),
        "columns": {pos: list(idea.columns)}, "model_columns": sys_cols,
        "seasons": list(SEASONS), "folds_tested": list(FOLDS), "weeks": list(WEEKS),
        "rows": int(len(frame)), "coverage": cov, "counts": cnt,
        "fold_results": folds,
        "verdict": {"cand_beats_page_by_fold": c1, "cand_beats_prior_by_fold": c2,
                    "cand_bias_ok": cand_bias, "cand_biased_bands": cand_bad,
                    "passes": passes,
                    "prior_beats_page_by_fold": p1, "prior_bias_ok": prior_bias,
                    "prior_biased_bands": prior_bad, "prior_clears": prior_clears},
        "passed": [pos] if passes else [],
        "bands": bands, "frozen_fit": frozen_fit(frame, sys_cols),
        "tables_digest": dig, **blobs, "head": head, "run_at": run_at}
    br.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = br.RESULTS_DIR / f"{idea.id}.json"
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    br.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ledger = br.RUNS_DIR / f"{idea.id}.csv"
    new = not ledger.exists()
    with ledger.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RUN_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"when": run_at, "block": idea.id, **blobs, "tables_digest": dig, "head": head,
                    "verdict": f"PASS {pos}" if passes else "FAIL", "positions": pos,
                    "folds": " ".join(str(f) for f in FOLDS), "folds_passed": f"{pos}:{k}/{len(c1)}"})
    print(f"  wrote {_rel(out)} and {_rel(ledger)}")
    return result


def _rel(path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return str(path)


# ----------------------------------------------------------------- 2026 holdout

def holdout_id(idea: Idea) -> str:
    return f"{idea.id}-{HOLDOUT}"


def stored_result(idea: Idea) -> dict:
    """The idea's historical result; the holdout refuses one that did not pass."""
    path = br.RESULTS_DIR / f"{idea.id}.json"
    if not path.exists():
        raise SystemExit(f"refused: {idea.id} has no result to hold out")
    res = json.loads(path.read_text(encoding="utf-8"))
    if not res.get("passed"):
        raise SystemExit(f"refused: {idea.id} did not pass its historical bar; the {HOLDOUT} "
                         "holdout scores only ideas that did")
    return res


def holdout_integrity(idea: Idea) -> tuple[dict, str]:
    """The ledger's blobs (the holdout's pre-registration and this script) and
    the blob of the stored result whose frozen fit is applied, each committed
    and unchanged (block_runner.committed_blob raises otherwise)."""
    blobs = {"prereg_blob": br.committed_blob(br.PREREG_DIR / f"{holdout_id(idea)}.md"),
             "module_blob": br.committed_blob(SCRIPT), "helpers_blob": "-"}
    return blobs, br.committed_blob(br.RESULTS_DIR / f"{idea.id}.json")


def frozen_problems(idea: Idea, hist: pd.DataFrame, stored: dict) -> list[str]:
    """Rebuild the training seasons' encoding and refit them as `frozen_fit`
    did: where that differs from the stored fit (empty when it reproduces)."""
    h = hist.copy()
    sys_cols = system_columns(prepare(h, numeric_columns(idea)), idea)
    if sys_cols != stored["model_columns"]:
        return [f"columns {sys_cols} are not the stored {stored['model_columns']}"]
    refit, ff = frozen_fit(h, sys_cols), stored["frozen_fit"]
    bad = []
    for name in ("prior", "cand"):
        for w in map(str, WEEKS):
            a, b = refit[name][w], ff[name][w]
            if a["cols"] != b["cols"]:
                bad.append(f"{name} week {w}: columns")
                continue
            bad += [f"{name} week {w}: {k}" for k in ("fill", "mu", "sd", "beta")
                    if not np.allclose(a[k], b[k], rtol=1e-9, atol=1e-9)]
    bad += [f"null week {w}" for w in map(str, WEEKS)
            if not math.isclose(refit["null"][w], ff["null"][w], abs_tol=1e-9)]
    return bad


def encode_like(frame: pd.DataFrame, cols: list[str], model_cols: set[str]) -> None:
    """Ground rule 4 with the training seasons' encoding: a column the frozen
    fit carries `<col>__known` for enters as 0 where missing plus that
    indicator. A column it carries none for was complete in training and must
    be complete here (the fit would otherwise fill it with the training mean)."""
    for c in cols:
        if f"{c}__known" in model_cols:
            frame[f"{c}__known"] = frame[c].notna().astype(float)
            frame[c] = frame[c].fillna(0.0)
        elif frame[c].isna().any():
            raise SystemExit(f"refused: {c} is missing in {HOLDOUT} rows and the frozen fit has "
                             "no indicator for it")


def holdout_score(idea: Idea, hist: pd.DataFrame, test: pd.DataFrame, stored: dict) -> dict:
    """The stored frozen fit (prior, cand and the null), unchanged, on `test`
    (the holdout rows), beside the page as served and refit and Sleeper.
    `hist`, the seven training seasons, only fits `today_fit` (the frozen
    fit does not store it), the same way `frozen_fit` fitted the others."""
    num = numeric_columns(idea)
    hist, test = hist.copy(), test.copy()
    sys_cols = system_columns(prepare(hist, num), idea)
    ff = stored["frozen_fit"]
    test["page_raw"] = test["page"]
    encode_like(test, num, {c for cols in sys_cols.values() for c in cols})
    for name in SYSTEMS[:-1]:
        test[name] = np.nan
    page_fit = {}
    for w in WEEKS:
        idx = test.index[test["week"] == w]
        tr = hist.loc[hist["week"] == w]
        null = float(ff["null"][str(w)])
        m = A.Ridge.fit(tr, tr["actual"], sys_cols["today_fit"], lam=LAM)
        page_fit[str(w)] = {**m.to_json(), "per_unit": {
            c: round(float(b) / float(sd), 4) for c, b, sd in zip(m.cols, m.beta[1:], m.sd)}}
        test.loc[idx, "null"] = null
        test.loc[idx, "today"] = test.loc[idx, "page_raw"].fillna(null)
        test.loc[idx, "today_fit"] = np.maximum(br.predict(m, test.loc[idx]), 0.0)
        for name in ("prior", "cand"):
            frozen = A.Ridge.from_json(ff[name][str(w)])
            test.loc[idx, name] = np.maximum(br.predict(frozen, test.loc[idx]), 0.0)
    tables = {"weeks_1_3": score(test), "weeks_2_3": score(test.loc[test["week"] >= 2]),
              **{f"week_{w}": score(test.loc[test["week"] == w]) for w in WEEKS}}
    return {"tables": tables, "bands": band_tables(test, idea), "today_fit_fit": page_fit}


def holdout(idea: Idea, data: Data, sleeper: pd.DataFrame, stored: dict, *,
            coverage_only: bool) -> dict:
    blobs, fit_blob = (None, None) if coverage_only else holdout_integrity(idea)
    hid, pos, num = holdout_id(idea), idea.position, numeric_columns(idea)
    cols = ["season", "week", "gsis_id", "actual", "sleeper", *num]
    _, hist = assemble(idea, data, sleeper)
    hist_dig = digest(hist, cols)
    rows = population(data, pos, sleeper, seasons=(HOLDOUT,))
    test = pd.concat([rows, features(idea, data, rows)], axis=1)
    dig = digest(test, cols)
    cov, cnt = coverage(test, idea), counts(test, idea)
    same = hist_dig == stored["tables_digest"]
    print(f"\n{hid} - {idea.id}'s frozen fit on {HOLDOUT} weeks 1-3")
    print(f"  training rows {SEASONS[0]}-{SEASONS[-1]}: {len(hist)}, digest {hist_dig} "
          + ("(the one scored)" if same else f"(NOT the one scored, {stored['tables_digest']})"))
    print(f"  {len(test)} {pos} rows, weeks 1-3 of {HOLDOUT}; {cnt['relevant_rows']} with "
          f"Sleeper 5+; digest {dig}")
    for s, by in cnt["rows"].items():
        print(f"    {s}: " + ", ".join(f"week {w} {n}" for w, n in by.items()))
    for c in num:
        print(f"  coverage {c}: " + ", ".join(f"{s} {v:.0%}" for s, v in cov[c].items()))
    print("  page by week: " + ", ".join(f"week {w} {v:.0%}" for w, v in cov["page_by_week"].items()))
    for b in band_columns(idea):
        print(f"  band sizes {b}: {cnt[b]}")
    leaks = leak_check(idea, data, rows, test, checks=HOLDOUT_LEAK_CHECKS)
    for line in leaks:
        print(f"  LEAK {line}")
    print(f"  leak check: {'FAILED' if leaks else 'passed'} "
          f"({', '.join(f'{s} week {w}' for s, w in HOLDOUT_LEAK_CHECKS)})")
    bad = frozen_problems(idea, hist, stored)
    print(f"  the refit on {SEASONS[0]}-{SEASONS[-1]} reproduces the stored fit: "
          + ("yes" if not bad else "NO: " + "; ".join(bad)))
    if coverage_only:
        return {"idea": hid, "coverage": cov, "counts": cnt, "leaks": leaks, "digest": dig,
                "training_digest_matches": same, "fit_problems": bad}
    if leaks:
        raise SystemExit(f"refused: {hid} fails the leak check")
    if not same:
        raise SystemExit(f"refused: the {SEASONS[0]}-{SEASONS[-1]} rows are not the ones "
                         f"{idea.id} was scored on")
    if bad:
        raise SystemExit("refused: the refit does not reproduce the stored fit")

    res = holdout_score(idea, hist, test, stored)
    t = res["tables"]
    c1, c2 = beats_page(t, "cand"), beats(t["weeks_1_3"], "cand", "prior")
    p1 = beats_page(t, "prior")
    passes = c1 and c2

    def cell(m: dict, x: str) -> str:
        pw = m[x]["pairwise"]
        return f"{'-' if pw is None else f'{pw:.2%}':>8}/{m[x]['mae']:<7.3f}"

    print(f"\n  {'window':<10}" + "".join(f"{x:>16}" for x in SYSTEMS) + "   (pairwise / MAE)")
    for win, m in t.items():
        print(f"  {win:<10}" + "".join(cell(m, x) for x in SYSTEMS) + f"   {m['pairs']} pairs")
    print(f"  cand beats the page (both, weeks 1-3 and 2-3): {c1}; cand beats prior "
          f"(weeks 1-3): {c2}; prior beats the page: {p1}")
    print(f"  -> {hid} {'PASSES' if passes else 'fails'}")

    run_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    head = br._git("rev-parse", "HEAD")[:12]
    result = {
        "block": hid, "idea": idea.id,
        "title": f"{idea.id}'s frozen fit on {HOLDOUT} weeks 1-3: {idea.title}",
        "kind": f"holdout: {HOLDOUT} weeks 1-3, the frozen fit unchanged",
        "harness": "scripts/research/early_season_backtest.py --holdout-2026",
        "summary": f"PASS {pos}" if passes else "fail",
        "folds": f"{pos} {HOLDOUT} {int(passes)}/1 (vs the page {'yes' if c1 else 'no'}, "
                 f"vs the plain prior {'yes' if c2 else 'no'})",
        "note": f"plain prior (frozen) {'beats' if p1 else 'does not beat'} the page on both "
                "metrics over weeks 1-3 and 2-3",
        "frozen_fit_from": {"path": _rel(br.RESULTS_DIR / f"{idea.id}.json"), "blob": fit_blob,
                            "head": stored.get("head"), "run_at": stored.get("run_at")},
        "columns": {pos: list(idea.columns)}, "model_columns": stored["model_columns"],
        "seasons": [HOLDOUT], "folds_tested": [HOLDOUT], "weeks": list(WEEKS),
        "training_seasons": list(SEASONS), "training_digest": hist_dig,
        "rows": int(len(test)), "coverage": cov, "counts": cnt,
        "results": t,
        "verdict": {"cand_beats_page": c1, "cand_beats_prior": c2, "passes": passes,
                    "prior_beats_page": p1},
        "passed": [pos] if passes else [],
        "bands": res["bands"], "today_fit_fit": res["today_fit_fit"],
        "tables_digest": dig, **blobs, "head": head, "run_at": run_at}
    br.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out = br.RESULTS_DIR / f"{hid}.json"
    out.write_text(json.dumps(result, indent=1) + "\n", encoding="utf-8")
    br.RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ledger = br.RUNS_DIR / f"{hid}.csv"
    new = not ledger.exists()
    with ledger.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RUN_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"when": run_at, "block": hid, **blobs, "tables_digest": dig, "head": head,
                    "verdict": f"PASS {pos}" if passes else "FAIL", "positions": pos,
                    "folds": str(HOLDOUT), "folds_passed": f"{pos}:{int(passes)}/1"})
    print(f"  wrote {_rel(out)} and {_rel(ledger)}")
    return result


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--idea", action="append", default=[], choices=sorted(IDEAS))
    ap.add_argument("--coverage-only", action="store_true")
    ap.add_argument("--holdout-2026", action="store_true",
                    help="score a passing idea's frozen fit on 2026 weeks 1-3 (its own "
                         "pre-registration, docs/research/blocks/<ID>-2026.md)")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)
    if args.list:
        for i in IDEAS.values():
            res = br.RESULTS_DIR / f"{i.id}.json"
            state = json.loads(res.read_text(encoding="utf-8"))["summary"] if res.exists() else "not run"
            print(f"{i.id:<7} {i.position:<3} {state:<10} {i.title}")
        return 0
    if not args.idea:
        ap.error("name at least one --idea")
    if args.holdout_2026:
        if len(args.idea) != 1:
            ap.error("the holdout takes one --idea")
        idea = IDEAS[args.idea[0]]
        stored = stored_result(idea)            # refuse an idea that did not pass
        if not args.coverage_only:
            holdout_integrity(idea)             # refuse before any data is read
        crosswalk = Crosswalk.from_csv(br.CROSSWALK)
        data = load_data(crosswalk, last=HOLDOUT)
        holdout(idea, data, sleeper_frame(crosswalk, (*SEASONS, HOLDOUT)), stored,
                coverage_only=args.coverage_only)
        return 0
    if len(args.idea) > 1 and not args.coverage_only:
        ap.error("a scored run takes one --idea at a time (one ledger line each)")
    if not args.coverage_only:
        integrity(IDEAS[args.idea[0]])          # refuse before any data is read
    crosswalk = Crosswalk.from_csv(br.CROSSWALK)
    data = load_data(crosswalk)
    sleeper = sleeper_frame(crosswalk)
    for i in args.idea:
        run(IDEAS[i], data, sleeper, coverage_only=args.coverage_only)
    return 0


if __name__ == "__main__":
    sys.exit(main())
