"""Position blocks against the shipped set, out of sample (MODEL_ROADMAP.md, ground rules 1-4).

    PYTHONPATH=src python scripts/research/block_runner.py --reproduce
    PYTHONPATH=src python scripts/research/block_runner.py --block RB-03 [--block QB-02 ...]
    PYTHONPATH=src python scripts/research/block_runner.py --block RB-03 --coverage-only
    PYTHONPATH=src python scripts/research/block_runner.py --list

The one harness every feature block in docs/research/MODEL_ROADMAP.md is
judged by. Same rows, universe and metrics as FEATURE_EXPANSION.md: the
regular-season weeks 4-18 of 2019-2025, players Sleeper projected, start/sit
pairwise order (among players Sleeper projected 5+) and MAE.

A BLOCK is a module scripts/research/blocks/<ID>.py (ID like "RB-03", file
name with the dash, loaded by path) that declares

    ID       = "RB-03"
    TITLE    = "Share of carries inside the 5"
    COLUMNS  = {"RB": ["inside5_share_l3", ...]}     # positions it touches
    def build(ctx) -> pandas.DataFrame               # see Context below

`build` returns one row per (season, week, gsis_id) for QB/RB/WR/TE and K
(per (season, week, team) for DEF) with the declared columns, computed from
what was known BEFORE that week's games only. Missing is allowed and honest:
the runner turns every column with any missing value into the value (0 where
missing) plus an explicit `<col>__known` indicator (ground rule 4), never
the training mean.

The runner refuses to score a block unless
  * its pre-registration docs/research/blocks/<ID>.md and its module are
    committed and unchanged in the working tree (ground rule 1); each run
    records both blob hashes, and appends one line to
    docs/research/blocks/runs/<ID>.csv, so a quiet re-run is visible;
  * the leak check passes: the block is rebuilt with every post-game frame
    cut at week w (and pre-game frames after week w) for two (season, week)
    pairs, and week w's features must come out identical.

`--coverage-only` builds the block and prints coverage and the leak check,
never a score, so a block can be developed without seeing its result.

The bar (ground rule 2), per position the block touches: better than the
shipped set on that position's rows on start/sit pairwise AND MAE in EVERY
one of the seven season folds; the pooled numbers (all positions, the block
applied only where it touches) must not be worse in any fold. K and DEF use
the same folds on the kicker and defense tables of k_def_backtest.py
(pairwise among all kickers or defenses in a week, as that harness does).
Passing here is necessary, not sufficient: a block that passes runs
record-only for two live weeks before it may serve (ground rule 3).
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk, normalize_id
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT

from research_common import load_script

SEASONS = tuple(range(2019, 2026))
MIN_WEEK = 4
RELEVANT = 5.0
SKILL = A.POSITIONS
SPECIAL = ("K", "DEF")
TABLES = RESEARCH_CACHE / "tables" / "seasons"
KDEF_TABLES = RESEARCH_CACHE / "tables" / "kdef"
CROSSWALK = RESEARCH_CACHE / "crosswalk_db_playerids.csv"
BLOCKS_DIR = REPO_ROOT / "scripts" / "research" / "blocks"
PREREG_DIR = REPO_ROOT / "docs" / "research" / "blocks"
RESULTS_DIR = PREREG_DIR / "results"
RUNS_DIR = PREREG_DIR / "runs"
LEAK_CHECKS = ((2021, 9), (2024, 14))
K_ADV = ["baseline", "k_ppg_season", "k_ppg_l3", "team_fga_pg", "team_xpa_pg",
         "implied", "spread", "dome"]
DEF_ADV = list(A.DEFENSE_FEATURES)
RUN_FIELDS = ("when", "block", "prereg_blob", "module_blob", "tables_digest", "head",
              "verdict", "positions", "folds_passed")

# Frames whose rows describe what happened in a game (cut at week < w for the
# leak check) and frames published before the week's games (cut at week <= w).
POST_GAME = {"pbp", "participation", "ftn", "weekly", "snaps", "team_stats",
             "ff_opportunity", "schedule_results"}
PRE_GAME = {"injuries", "depth_charts", "rosters_weekly", "schedule"}
RESULT_COLUMNS = ("away_score", "home_score", "result", "total", "overtime",
                  "away_qb_id", "home_qb_id", "away_qb_name", "home_qb_name")
# nflverse writes the franchise's code of the time in some frames (pbp,
# schedules: OAK in 2019) and today's in others (player stats: LV). Blocks
# see one code per franchise.
TEAM_CODES = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
TEAM_COLUMNS = ("team", "recent_team", "posteam", "defteam", "home_team", "away_team",
                "possession_team", "opponent_team", "opponent", "club_code", "team_abbr",
                "side_of_field", "return_team", "td_team", "timeout_team", "penalty_team",
                "forced_fumble_player_1_team", "fumble_recovery_1_team")


def normalize_teams(df: pd.DataFrame) -> pd.DataFrame:
    for c in TEAM_COLUMNS:
        if c in df.columns and df[c].dtype == object:
            df[c] = df[c].replace(TEAM_CODES)
    return df


def _nfl():
    import nflreadpy as nfl
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    return nfl


# --------------------------------------------------------------------------- data

class Context:
    """What a block may read. Every frame comes back with integer `season`
    and `week` columns, regular season only, filtered by the cutoff when the
    runner is doing a leak check. `keys` is the target rows' pre-game facts
    (who plays for whom, against whom, home or away) and is never cut.

    Loaders (seasons are any years nflverse has; data before 2019 is fine for
    priors):
      pbp(seasons, columns)          play-by-play, only the columns asked for
      participation(seasons)         2016-2025 only (nflverse has no 2026)
      ftn(seasons)                   FTN charting, 2022 on
      weekly(seasons)                player box scores (player_id = gsis id)
      snaps(seasons)                 snap counts (pfr_player_id; see pfr_to_gsis)
      team_stats(seasons)            team box scores
      ff_opportunity(seasons)        expected fantasy points, weekly
      schedule(seasons)              PRE-GAME schedule: lines, roof, surface,
                                     rest, coaches, starting QBs; no scores
      schedule_results(seasons)      the schedule with scores (post-game)
      injuries(seasons)              final weekly injury reports (pre-game)
      depth_charts(seasons)          as published (pre-game; 2025 on is
                                     timestamped, see A.depth_from_charts)
      rosters_weekly(seasons)        weekly rosters, reserve lists (pre-game)
      pfr_to_gsis                    {pfr id: gsis id} from the crosswalk
      keys_for(position)             the target rows to return features for

    Build features FOR the target rows (keys_for), as of the weeks before
    each row's week, not for the rows the data happens to contain: a row the
    data lacks must still come back (missing), or the leak check fails.
    Schedule `temp` and `wind` are observed at kickoff (a stand-in for the
    forecast, as FEATURE_EXPANSION's +line block used them); the starting
    quarterbacks (`*_qb_id`) are post-game facts and sit in schedule_results
    only. Team codes are today's (OAK -> LV) in every frame.
    """

    def __init__(self, keys: pd.DataFrame, crosswalk: Crosswalk,
                 cutoff: tuple[int, int] | None = None, store: dict | None = None,
                 special_keys: dict | None = None):
        self.keys = keys
        self.special_keys = special_keys or {}
        self.crosswalk = crosswalk
        self.cutoff = cutoff
        self._store = {} if store is None else store

    def keys_for(self, position: str) -> pd.DataFrame:
        """Target rows for one position: (season, week, gsis_id, position, team,
        opponent, game_id, home) for QB/RB/WR/TE and K; DEF rows carry no gsis_id."""
        if position in SPECIAL:
            return self.special_keys[position]
        return self.keys.loc[self.keys["position"] == position]

    def truncated(self, season: int, week: int) -> "Context":
        return Context(self.keys, self.crosswalk, (season, week), self._store, self.special_keys)

    # -- plumbing
    def _cut(self, name: str, df: pd.DataFrame) -> pd.DataFrame:
        if self.cutoff is None:
            return df
        s, w = self.cutoff
        if "season" not in df.columns or "week" not in df.columns:
            raise RuntimeError(f"{name}: no season/week, cannot be cut for the leak check")
        season, week = df["season"], df["week"]
        if name in POST_GAME:
            keep = (season < s) | ((season == s) & (week < w))
        elif name in PRE_GAME:
            keep = (season < s) | ((season == s) & (week <= w))
        else:
            raise RuntimeError(f"{name}: frame not classified as pre- or post-game")
        return df.loc[keep]

    def _get(self, name: str, seasons, loader, columns=None) -> pd.DataFrame:
        seasons = tuple(sorted({int(s) for s in seasons}))
        key = (name, seasons, tuple(columns) if columns else None)
        if key not in self._store:
            df = loader(list(seasons), columns)
            df = df.loc[df["season"].isin(seasons)].copy()
            if "season_type" in df.columns:
                df = df.loc[df["season_type"].isin(["REG"])]
            elif "game_type" in df.columns:
                df = df.loc[df["game_type"].isin(["REG"])]
            df["season"] = df["season"].astype(int)
            df = df.loc[df["week"].notna()].copy()
            df["week"] = df["week"].astype(int)
            self._store[key] = normalize_teams(df)
        return self._cut(name, self._store[key])

    # -- loaders
    def pbp(self, seasons, columns) -> pd.DataFrame:
        need = sorted(set(columns) | {"season", "week", "season_type", "game_id", "play_id",
                                      "posteam", "defteam"})

        def load(ss, cols):
            nfl = _nfl()
            frame = nfl.load_pbp(ss)
            return frame.select([c for c in cols if c in frame.columns]).to_pandas()
        return self._get("pbp", seasons, load, need)

    def participation(self, seasons) -> pd.DataFrame:
        def load(ss, _):
            nfl = _nfl()
            df = nfl.load_participation([s for s in ss if s <= 2025]).to_pandas()
            gid = df["nflverse_game_id"].astype(str).str.split("_", expand=True)
            df["season"] = pd.to_numeric(gid[0], errors="coerce")
            df["week"] = pd.to_numeric(gid[1], errors="coerce")
            return df.loc[df["season"].notna()]
        return self._get("participation", seasons, load)

    def ftn(self, seasons) -> pd.DataFrame:
        def load(ss, _):
            nfl = _nfl()
            df = nfl.load_ftn_charting([s for s in ss if s >= 2022]).to_pandas()
            return df
        return self._get("ftn", seasons, load)

    def weekly(self, seasons) -> pd.DataFrame:
        return self._get("weekly", seasons, lambda ss, _: _nfl().load_player_stats(
            ss, summary_level="week").to_pandas())

    def snaps(self, seasons) -> pd.DataFrame:
        def load(ss, _):
            df = _nfl().load_snap_counts(ss).to_pandas()
            df["gsis_id"] = df["pfr_player_id"].map(lambda p: self.pfr_to_gsis.get(str(p)))
            return df
        return self._get("snaps", seasons, load)

    def team_stats(self, seasons) -> pd.DataFrame:
        return self._get("team_stats", seasons, lambda ss, _: _nfl().load_team_stats(
            ss, summary_level="week").to_pandas())

    def ff_opportunity(self, seasons) -> pd.DataFrame:
        def load(ss, _):
            df = _nfl().load_ff_opportunity(ss, stat_type="weekly").to_pandas()
            df["season"] = pd.to_numeric(df["season"], errors="coerce")
            df["week"] = pd.to_numeric(df["week"], errors="coerce")
            return df.loc[df["season"].notna()]
        return self._get("ff_opportunity", seasons, load)

    def schedule(self, seasons) -> pd.DataFrame:
        return self._get("schedule", seasons, lambda ss, _: _nfl().load_schedules(ss)
                         .to_pandas().drop(columns=list(RESULT_COLUMNS), errors="ignore"))

    def schedule_results(self, seasons) -> pd.DataFrame:
        return self._get("schedule_results", seasons,
                         lambda ss, _: _nfl().load_schedules(ss).to_pandas())

    def injuries(self, seasons) -> pd.DataFrame:
        return self._get("injuries", seasons, lambda ss, _: _nfl().load_injuries(ss).to_pandas())

    def depth_charts(self, seasons) -> pd.DataFrame:
        def load(ss, _):
            nfl = _nfl()
            parts = []
            for s in ss:
                df = nfl.load_depth_charts([s]).to_pandas()
                if "week" not in df.columns and "dt" in df.columns:
                    # 2025 on: timestamped snapshots. Week = the first week whose
                    # first kickoff is after the snapshot (what it was published for).
                    sched = nfl.load_schedules([s]).to_pandas()
                    sched = sched.loc[sched["game_type"] == "REG"]
                    first = pd.to_datetime(sched["gameday"], utc=True).groupby(sched["week"]).min()
                    when = pd.to_datetime(df["dt"], utc=True)
                    edges = first.sort_index()
                    df["week"] = np.searchsorted(edges.to_numpy(), when.to_numpy(), side="left") + 1
                    df.loc[df["week"] > int(edges.index.max()), "week"] = np.nan
                    df["season"] = s
                parts.append(df)
            return pd.concat(parts, ignore_index=True)
        return self._get("depth_charts", seasons, load)

    def rosters_weekly(self, seasons) -> pd.DataFrame:
        return self._get("rosters_weekly", seasons,
                         lambda ss, _: _nfl().load_rosters_weekly(ss).to_pandas())

    @property
    def pfr_to_gsis(self) -> dict:
        if "_pfr" not in self._store:
            g2p = getattr(self.crosswalk, "_g2p", {})
            self._store["_pfr"] = {p: g for g, p in g2p.items()}
        return self._store["_pfr"]


def _keep(d: pd.DataFrame) -> pd.DataFrame:
    return d.loc[(d["week"] >= MIN_WEEK) & d["sleeper"].notna()].copy()


def load_skill_tables(crosswalk: Crosswalk) -> dict[int, pd.DataFrame]:
    """The frozen season tables (feature_expansion_backtest.py --tables),
    built once if missing, restricted to the backtest universe, with the
    pre-game keys every block may join on."""
    out = {}
    missing = [s for s in SEASONS if not (TABLES / f"season_{s}.parquet").exists()]
    if missing:
        bt = load_script("projection_backtest", "scripts/research/projection_backtest.py")
        ab = load_script("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
        _nfl()
        TABLES.mkdir(parents=True, exist_ok=True)
        for s in missing:
            ab.season_table(s, crosswalk, bt).to_parquet(TABLES / f"season_{s}.parquet", index=False)
    nfl = _nfl()
    for s in SEASONS:
        t = _keep(pd.read_parquet(TABLES / f"season_{s}.parquet"))
        wk = nfl.load_player_stats([s], summary_level="week").to_pandas()
        wk = wk.loc[wk["season_type"] == "REG", ["player_id", "week", "team", "opponent_team",
                                                 "game_id"]]
        wk = wk.rename(columns={"player_id": "gsis_id", "opponent_team": "opponent"})
        wk["gsis_id"] = wk["gsis_id"].map(normalize_id)
        wk = wk.drop_duplicates(["gsis_id", "week"])
        t = t.merge(wk, on=["gsis_id", "week"], how="left")
        sched = normalize_teams(nfl.load_schedules([s]).to_pandas())
        home = sched.loc[sched["game_type"] == "REG", ["game_id", "home_team"]]
        t = t.merge(home, on="game_id", how="left")
        t["home"] = (t["team"] == t["home_team"]).astype(float)
        out[s] = t.drop(columns=["home_team"])
    return out


def load_kdef_tables(crosswalk: Crosswalk) -> dict[str, dict[int, pd.DataFrame]]:
    KDEF_TABLES.mkdir(parents=True, exist_ok=True)
    kd = load_script("k_def_backtest", "scripts/research/k_def_backtest.py")
    bt = load_script("projection_backtest", "scripts/research/projection_backtest.py")
    out = {"K": {}, "DEF": {}}
    for s in SEASONS:
        kp, dp = KDEF_TABLES / f"K_{s}.parquet", KDEF_TABLES / f"DEF_{s}.parquet"
        if not (kp.exists() and dp.exists()):
            _nfl()
            K, D = kd.season_tables(s, crosswalk, bt)
            K.to_parquet(kp, index=False)
            D.to_parquet(dp, index=False)
        nfl = _nfl()
        sched = normalize_teams(nfl.load_schedules([s]).to_pandas())
        sched = sched.loc[sched["game_type"] == "REG"]
        sides = pd.concat([
            sched[["game_id", "week", "home_team", "away_team"]].rename(
                columns={"home_team": "team", "away_team": "opponent"}).assign(home=1.0),
            sched[["game_id", "week", "away_team", "home_team"]].rename(
                columns={"away_team": "team", "home_team": "opponent"}).assign(home=0.0)])
        wk = nfl.load_player_stats([s], summary_level="week").to_pandas()
        wk = wk.loc[wk["season_type"] == "REG", ["player_id", "week", "team"]].rename(
            columns={"player_id": "gsis_id"})
        wk["gsis_id"] = wk["gsis_id"].map(normalize_id)
        wk = normalize_teams(wk.drop_duplicates(["gsis_id", "week"]))
        for pos, path in (("K", kp), ("DEF", dp)):
            t = pd.read_parquet(path)
            t = t.loc[(t["week"] >= MIN_WEEK) & t["sleeper"].notna() & t["actual"].notna()].copy()
            t["season"] = s
            if pos == "K":
                t = t.merge(wk, on=["gsis_id", "week"], how="left")
                t = t.merge(sides.drop(columns=["home"]).rename(columns={"opponent": "opponent"}),
                            on=["week", "team"], how="left")
                t = t.merge(sides[["week", "team", "home"]], on=["week", "team"], how="left",
                            suffixes=("", "_sched"))
            else:
                t = t.merge(sides.drop(columns=["home"]), on=["week", "team"], how="left")
            out[pos][s] = t
    return out


def tables_digest(paths) -> str:
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(Path(p).name.encode())
        h.update(Path(p).read_bytes())
    return h.hexdigest()[:16]


def base_columns() -> dict[str, list[str]]:
    shipped = A.AdvancedModel.load()
    return {p: [c for c in shipped.adv[p].cols if c not in A.EXPANSION_FEATURES] for p in SKILL}


# ------------------------------------------------------------------------ scoring

def predict(model: A.Ridge, df: pd.DataFrame) -> np.ndarray:
    """A.Ridge.predict, vectorised (same fill, same standardisation)."""
    cols = list(model.cols)
    x = df[cols].apply(pd.to_numeric, errors="coerce").astype("float64")
    x = x.fillna(pd.Series(model.fill, index=cols))
    z = (x.to_numpy() - np.asarray(model.mu)) / np.asarray(model.sd)
    return model.beta[0] + z @ np.asarray(model.beta[1:])


def pairwise(df: pd.DataFrame, systems, by=("week", "position")) -> dict:
    """projection_backtest.pairwise, vectorised: pairs in the same group with
    different actual points; a system scores a pair it orders as the actual
    points were (ties in the system count as misses)."""
    n, hits = 0, dict.fromkeys(systems, 0)
    for _, g in df.groupby(list(by), sort=False):
        a = g["actual"].to_numpy(dtype="float64")
        if len(a) < 2:
            continue
        i, j = np.triu_indices(len(a), 1)
        da = a[i] - a[j]
        valid = da != 0
        n += int(valid.sum())
        for s in systems:
            v = g[s].to_numpy(dtype="float64")
            ds = v[i] - v[j]
            hits[s] += int((valid & (ds != 0) & ((ds > 0) == (da > 0))).sum())
    return {"n": n, **{s: (hits[s] / n if n else None) for s in systems}}


def _metrics(test: pd.DataFrame, systems, *, by, relevant: bool) -> dict:
    rel = test.loc[test["sleeper"] >= RELEVANT] if relevant else test
    pw = pairwise(rel, systems, by=by)
    return {"pairs": pw["n"], "rows": int(len(test)), **{
        s: {"pairwise": None if pw[s] is None else round(pw[s], 6),
            "mae": round(float((test[s] - test["actual"]).abs().mean()), 4)} for s in systems}}


def prepare(block_frame: pd.DataFrame | None, tables: dict[int, pd.DataFrame],
            columns: dict[str, list[str]], key: list[str]) -> tuple[dict, dict]:
    """Merge the block's columns into every season table; every column with
    any missing value becomes 0 there plus `<col>__known` (ground rule 4).
    Returns the merged tables and the model columns per position."""
    wanted = sorted({c for cs in columns.values() for c in cs})
    merged = {}
    for s, t in tables.items():
        if block_frame is None:
            merged[s] = t
            continue
        part = block_frame.loc[block_frame["season"] == s, key + wanted]
        clash = [c for c in wanted if c in t.columns]
        if clash:
            raise ValueError(f"block columns already in the tables: {clash}")
        merged[s] = t.merge(part.drop(columns=["season"]), on=[k for k in key if k != "season"],
                            how="left")
    # decide every column's indicator first (a column two positions share is
    # judged on both positions' rows), then fill once
    flagged = set()
    for pos, cs in columns.items():
        for c in cs:
            rows = pd.concat([m.loc[m["position"] == pos, c] if "position" in m else m[c]
                              for m in merged.values()])
            if rows.isna().any():
                flagged.add(c)
    for m in merged.values():
        for c in sorted(flagged):
            m[f"{c}__known"] = m[c].notna().astype(float)
            m[c] = m[c].fillna(0.0)
    model_cols = {pos: [x for c in cs for x in ([c, f"{c}__known"] if c in flagged else [c])]
                  for pos, cs in columns.items()}
    return merged, model_cols


def cv_skill(tables: dict[int, pd.DataFrame], base: dict[str, list[str]],
             extra: dict[str, list[str]]) -> dict:
    """Seven season folds; `shipped` and `block` on identical rows. Per fold:
    pooled metrics and per-position metrics for every skill position."""
    folds = {}
    for test_s in SEASONS:
        train = pd.concat([tables[s] for s in SEASONS if s != test_s])
        test = tables[test_s].copy()
        test["shipped"], test["block"] = np.nan, np.nan
        for p in SKILL:
            tr, te = train["position"] == p, test["position"] == p
            m0 = A.Ridge.fit(train.loc[tr], train.loc[tr, "actual"], base[p])
            test.loc[te, "shipped"] = predict(m0, test.loc[te])
            if extra.get(p):
                m1 = A.Ridge.fit(train.loc[tr], train.loc[tr, "actual"], base[p] + extra[p])
                test.loc[te, "block"] = predict(m1, test.loc[te])
            else:
                test.loc[te, "block"] = test.loc[te, "shipped"]
        test[["shipped", "block"]] = test[["shipped", "block"]].clip(lower=0)
        res = {"pooled": _metrics(test, ["sleeper", "shipped", "block"], by=("week", "position"),
                                  relevant=True)}
        for p in SKILL:
            res[p] = _metrics(test.loc[test["position"] == p], ["sleeper", "shipped", "block"],
                              by=("week", "position"), relevant=True)
        folds[test_s] = res
    return folds


def cv_special(tables: dict[str, dict[int, pd.DataFrame]], extra: dict[str, list[str]]) -> dict:
    folds = {}
    for test_s in SEASONS:
        res = {}
        for pos, cols in (("K", K_ADV), ("DEF", DEF_ADV)):
            if pos not in extra:
                continue
            tab = tables[pos]
            train = pd.concat([tab[s] for s in SEASONS if s != test_s])
            test = tab[test_s].copy()
            m0 = A.Ridge.fit(train, train["actual"], cols)
            m1 = A.Ridge.fit(train, train["actual"], cols + extra[pos])
            test["shipped"], test["block"] = predict(m0, test), predict(m1, test)
            if pos == "K":
                test[["shipped", "block"]] = test[["shipped", "block"]].clip(lower=0)
            res[pos] = _metrics(test, ["sleeper", "shipped", "block"], by=("week",),
                                relevant=False)
        folds[test_s] = res
    return folds


def verdict(folds: dict, positions) -> dict:
    out = {}
    for p in positions:
        wins = []
        for s, f in folds.items():
            m = f[p]
            wins.append(m["block"]["pairwise"] > m["shipped"]["pairwise"]
                        and m["block"]["mae"] < m["shipped"]["mae"])
        out[p] = {"folds_better_on_both": int(sum(wins)), "folds": len(wins),
                  "passes": bool(all(wins)),
                  "mean_pairwise_delta": round(float(np.mean(
                      [f[p]["block"]["pairwise"] - f[p]["shipped"]["pairwise"]
                       for f in folds.values()])), 6),
                  "mean_mae_delta": round(float(np.mean(
                      [f[p]["block"]["mae"] - f[p]["shipped"]["mae"] for f in folds.values()])), 4)}
    pooled_ok = all(f["pooled"]["block"]["pairwise"] >= f["pooled"]["shipped"]["pairwise"]
                    and f["pooled"]["block"]["mae"] <= f["pooled"]["shipped"]["mae"]
                    for f in folds.values() if "pooled" in f)
    return {"positions": out, "pooled_not_worse_every_fold": pooled_ok}


# --------------------------------------------------------------------- integrity

def _git(*args) -> str:
    return subprocess.run(["git", "-C", str(REPO_ROOT), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def committed_blob(path: Path) -> str:
    """The blob hash of a committed, unmodified file; raises otherwise."""
    rel = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    try:
        _git("ls-files", "--error-unmatch", rel)
    except subprocess.CalledProcessError:
        raise SystemExit(f"refused: {rel} is not committed (pre-register and commit first)")
    if _git("status", "--porcelain", "--", rel):
        raise SystemExit(f"refused: {rel} has uncommitted changes")
    return _git("rev-parse", f"HEAD:{rel}")[:12]


def load_block(block_id: str):
    if str(BLOCKS_DIR) not in sys.path:
        sys.path.insert(0, str(BLOCKS_DIR))           # blocks import _common
    path = BLOCKS_DIR / f"{block_id}.py"
    if not path.exists():
        raise SystemExit(f"no block module {path.relative_to(REPO_ROOT)}")
    mod = load_script(f"block_{block_id.replace('-', '_')}", path.relative_to(REPO_ROOT).as_posix())
    for attr in ("ID", "TITLE", "COLUMNS", "build"):
        if not hasattr(mod, attr):
            raise SystemExit(f"{block_id}: missing {attr}")
    if mod.ID != block_id:
        raise SystemExit(f"{path.name} declares ID {mod.ID!r}")
    bad = set(mod.COLUMNS) - set(SKILL) - set(SPECIAL)
    if bad:
        raise SystemExit(f"{block_id}: unknown positions {sorted(bad)}")
    if set(mod.COLUMNS) & set(SPECIAL) and len(mod.COLUMNS) != 1:
        raise SystemExit(f"{block_id}: a K or DEF block touches that one position only")
    return mod, path


def build_block(mod, ctx: Context) -> pd.DataFrame:
    df = mod.build(ctx)
    key = ["season", "week", "team"] if set(mod.COLUMNS) == {"DEF"} else ["season", "week", "gsis_id"]
    if "DEF" in mod.COLUMNS and set(mod.COLUMNS) != {"DEF"}:
        raise SystemExit(f"{mod.ID}: a DEF block may not touch other positions (keys differ)")
    wanted = sorted({c for cs in mod.COLUMNS.values() for c in cs})
    missing = [c for c in key + wanted if c not in df.columns]
    if missing:
        raise SystemExit(f"{mod.ID}: build() did not return {missing}")
    df = df[key + wanted].copy()
    if df.duplicated(key).any():
        raise SystemExit(f"{mod.ID}: build() returned duplicate {key} rows")
    df["season"], df["week"] = df["season"].astype(int), df["week"].astype(int)
    for c in wanted:
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
        if np.isinf(df[c]).any():
            raise SystemExit(f"{mod.ID}: {c} has infinite values")
    return df, key


def leak_check(mod, ctx: Context, full: pd.DataFrame, key: list[str]) -> list[str]:
    problems = []
    wanted = sorted({c for cs in mod.COLUMNS.values() for c in cs})
    for s, w in LEAK_CHECKS:
        cut, _ = build_block(mod, ctx.truncated(s, w))
        a = full.loc[(full["season"] == s) & (full["week"] == w)].set_index(key)[wanted]
        b = cut.loc[(cut["season"] == s) & (cut["week"] == w)].set_index(key)[wanted]
        a, b = a.sort_index(), b.reindex(a.index)
        diff = ~np.isclose(a.to_numpy(), b.to_numpy(), equal_nan=True, rtol=0, atol=1e-9)
        if diff.any():
            cols = [wanted[i] for i in sorted(set(np.where(diff)[1]))]
            problems.append(f"{s} week {w}: {int(diff.any(axis=1).sum())} row(s) differ in {cols}"
                            " when data from week >= w is removed")
        if len(a) == 0:
            problems.append(f"{s} week {w}: block returned no rows to check")
    return problems


def coverage(df: pd.DataFrame, tables: dict, mod, key) -> dict:
    out = {}
    for pos, cols in mod.COLUMNS.items():
        if pos in SKILL:
            rows = pd.concat([t.loc[t["position"] == pos, ["season", "week", "gsis_id"]]
                              for t in tables.values()])
        else:
            rows = pd.concat([t[key] for t in tables[pos].values()])
        m = rows.merge(df, on=key, how="left")
        out[pos] = {c: {str(s): round(float(g[c].notna().mean()), 3)
                        for s, g in m.groupby("season")} for c in cols}
    return out


# --------------------------------------------------------------------------- main

def run_block(block_id: str, args, crosswalk, skill, special, base) -> dict:
    mod, path = load_block(block_id)
    prereg = PREREG_DIR / f"{block_id}.md"
    blobs = None
    if not args.coverage_only:
        blobs = {"prereg_blob": committed_blob(prereg), "module_blob": committed_blob(path)}
    cols = ["season", "week", "gsis_id", "position", "team", "opponent", "game_id", "home"]
    keys = pd.concat([t[cols] for t in skill.values()], ignore_index=True)
    special_keys = {
        "K": pd.concat([t[cols] for t in special["K"].values()], ignore_index=True),
        "DEF": pd.concat([t[[c for c in cols if c != "gsis_id"]] for t in special["DEF"].values()],
                         ignore_index=True)}
    ctx = Context(keys, crosswalk, special_keys=special_keys)
    frame, key = build_block(mod, ctx)
    tabs = special if set(mod.COLUMNS) & set(SPECIAL) else skill
    cov = coverage(frame, tabs, mod, key)
    print(f"\n{block_id} — {mod.TITLE}")
    for pos, cs in cov.items():
        for c, by in cs.items():
            print(f"  coverage {pos} {c}: " + ", ".join(f"{s} {v:.0%}" for s, v in by.items()))
    leaks = [] if args.no_leak_check else leak_check(mod, ctx, frame, key)
    for line in leaks:
        print(f"  LEAK {line}")
    print(f"  leak check: {'skipped' if args.no_leak_check else ('FAILED' if leaks else 'passed')}")
    if args.coverage_only:
        return {"block": block_id, "coverage": cov, "leaks": leaks}
    if leaks:
        raise SystemExit(f"refused: {block_id} fails the leak check")
    if args.no_leak_check:
        raise SystemExit("refused: a scored run needs the leak check")
    if set(mod.COLUMNS) & set(SPECIAL):
        merged = {}
        extra = {}
        for pos in mod.COLUMNS:
            m, cols = prepare(frame, special[pos], {pos: mod.COLUMNS[pos]}, key)
            merged[pos], extra[pos] = m, cols[pos]
        folds = cv_special({**special, **merged}, extra)
    else:
        merged, extra = prepare(frame, skill, mod.COLUMNS, key)
        folds = cv_skill(merged, base, extra)
    v = verdict(folds, list(mod.COLUMNS))
    passed = [p for p, r in v["positions"].items() if r["passes"]]
    print(f"\n  {'fold':<6}" + "".join(f"{p + ' shipped':>18}{p + ' block':>18}" for p in mod.COLUMNS))
    for s, f in folds.items():
        print(f"  {s:<6}" + "".join(
            f"{f[p]['shipped']['pairwise']:>10.2%}/{f[p]['shipped']['mae']:<7.3f}"
            f"{f[p]['block']['pairwise']:>10.2%}/{f[p]['block']['mae']:<7.3f}" for p in mod.COLUMNS))
    for p, r in v["positions"].items():
        print(f"  {p}: better on both in {r['folds_better_on_both']}/{r['folds']} folds "
              f"(mean Δpairwise {r['mean_pairwise_delta']:+.4f}, ΔMAE {r['mean_mae_delta']:+.4f})"
              f" -> {'PASSES' if r['passes'] else 'fails'}")
    result = {"block": block_id, "title": mod.TITLE, "columns": mod.COLUMNS, "model_columns": extra,
              "seasons": list(SEASONS), "min_week": MIN_WEEK, "coverage": cov,
              "folds": {str(s): f for s, f in folds.items()}, "verdict": v, "passed": passed,
              "tables_digest": args.digest, **blobs, "head": _git("rev-parse", "HEAD")[:12],
              "run_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"{block_id}.json").write_text(json.dumps(result, indent=1) + "\n",
                                                  encoding="utf-8")
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    ledger = RUNS_DIR / f"{block_id}.csv"
    new = not ledger.exists()
    with ledger.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=RUN_FIELDS)
        if new:
            w.writeheader()
        w.writerow({"when": result["run_at"], "block": block_id, **blobs,
                    "tables_digest": args.digest, "head": result["head"],
                    "verdict": "PASS " + "+".join(passed) if passed else "FAIL",
                    "positions": "+".join(mod.COLUMNS),
                    "folds_passed": " ".join(f"{p}:{r['folds_better_on_both']}/{r['folds']}"
                                             for p, r in v["positions"].items())})
    print(f"  wrote {(RESULTS_DIR / f'{block_id}.json').relative_to(REPO_ROOT)} and "
          f"{ledger.relative_to(REPO_ROOT)}")
    return result


def reproduce(skill, special, base) -> dict:
    """The shipped set through this harness, against the stored evidence."""
    meta = A.AdvancedModel.load().meta
    folds = cv_skill(skill, base, {})
    mean = {m: round(float(np.mean([f["pooled"]["shipped"][m] for f in folds.values()])), 4)
            for m in ("pairwise", "mae")}
    pairs = sum(f["pooled"]["pairs"] for f in folds.values())
    ev = meta["evidence"]
    print("shipped set, seven season folds (this harness | stored evidence 2026-10-01):")
    for s, f in folds.items():
        print(f"  {s}: {f['pooled']['shipped']['pairwise']:.4%} / {f['pooled']['shipped']['mae']:.4f}"
              f"  (Sleeper {f['pooled']['sleeper']['pairwise']:.4%} / "
              f"{f['pooled']['sleeper']['mae']:.4f}, {f['pooled']['pairs']} pairs)")
    print(f"  mean: {mean['pairwise']:.4f} / {mean['mae']:.4f} over {pairs} pairs | "
          f"{ev['adv_pairwise']} / {ev['adv_mae']} over {ev['pairs']} pairs")
    kd = meta["k_def_evidence"]
    out = {"skill": {"folds": {str(s): f["pooled"] for s, f in folds.items()}, "mean": mean,
                     "pairs": pairs, "stored": {"pairwise": ev["adv_pairwise"],
                                                "mae": ev["adv_mae"], "pairs": ev["pairs"]}}}
    for pos, cols in (("K", K_ADV), ("DEF", DEF_ADV)):
        tab = special[pos]
        train = pd.concat([tab[s] for s in kd["train"]])
        test = tab[kd["test"]].copy()
        m = A.Ridge.fit(train, train["actual"], cols)
        test["adv"] = predict(m, test)
        if pos == "K":
            test["adv"] = test["adv"].clip(lower=0)
        got = _metrics(test, ["adv"], by=("week",), relevant=False)
        stored = kd[pos]["systems"]["adv"]
        print(f"  {pos} train {kd['train'][0]}-{kd['train'][-1]}, test {kd['test']}: "
              f"{got['adv']['pairwise']:.4f} / {got['adv']['mae']:.3f} over {got['pairs']} pairs | "
              f"{stored['pairwise']} / {stored['mae']} over {kd[pos]['pairs']} pairs")
        out[pos] = {"got": got, "stored": {**stored, "pairs": kd[pos]["pairs"]}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--block", action="append", default=[])
    ap.add_argument("--reproduce", action="store_true")
    ap.add_argument("--coverage-only", action="store_true")
    ap.add_argument("--no-leak-check", action="store_true", help="coverage-only runs only")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--crosswalk", type=Path, default=CROSSWALK)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    if args.list:
        for p in sorted(BLOCKS_DIR.glob("*.py")):
            if p.name.startswith("_"):
                continue
            res = RESULTS_DIR / f"{p.stem}.json"
            state = json.loads(res.read_text(encoding="utf-8"))["passed"] if res.exists() else None
            print(f"{p.stem:<8} {'not run' if state is None else ('PASS ' + '+'.join(state) if state else 'fail')}")
        return 0
    crosswalk = Crosswalk.from_csv(args.crosswalk)
    skill = load_skill_tables(crosswalk)
    special = load_kdef_tables(crosswalk)
    args.digest = tables_digest([*TABLES.glob("season_*.parquet"), *KDEF_TABLES.glob("*.parquet")])
    print(f"tables digest {args.digest}")
    base = base_columns()
    out = {}
    if args.reproduce:
        out["reproduce"] = reproduce(skill, special, base)
    for b in args.block:
        out[b] = run_block(b, args, crosswalk, skill, special, base)
    if args.out:
        args.out.write_text(json.dumps(out, indent=1, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
