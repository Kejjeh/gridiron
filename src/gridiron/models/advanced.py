"""The advanced-stats projection: the baseline refined by NFL advanced stats.

Evidence and gate. Trained on 2023-2024 and scored on 2025 (out of sample),
this model lifted the start/sit success rate over the full baseline
(docs/research/ADVANCED_STATS_BACKTEST_2025.md); it is registered in
`gridiron.models.validated_signals` as `advanced_v1` (rule #5). The shipped
coefficients (`advanced_weights.json`) were refit on every season the
research covered, with the feature set the out-of-sample test selected.

ONE feature builder. `features_as_of(history, week, ...)` computes every
feature for week `w` from weeks `< w` only, and both the research backtest
(`scripts/research/advanced_model_backtest.py`, one call per week) and the
dashboard (one call for the week it renders) use it — so what was tested is
what runs.

Inputs (public nflverse data, rule #3 — every join on gsis id):
  * expected fantasy points (`ff_opportunity`), scored with the league's
    rules (rule #2) from the expected-stat components;
  * Next Gen Stats (receiving / rushing / passing);
  * snap share, target share, air-yards share, carries (player-weeks);
  * the injury report (teammates Out or Doubtful -> vacated opportunity;
    the player's own final practice participation);
  * the depth chart before kickoff;
  * the schedule (implied team total, opponent).

What it never does: invent a number. When an input is missing the feature is
filled with the training mean (as in training) and the record says which
inputs the model had; when the whole advanced frame is missing the page
keeps the baseline and says so.
"""
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import normalize_id
from gridiron.scoring import fantasy_points
from gridiron.weekly import schedule_index

POSITIONS = ("QB", "RB", "WR", "TE")
WEIGHTS_PATH = Path(__file__).with_name("advanced_weights.json")
NAME = "advanced_v1"

#: ff_opportunity expected-stat column -> nflverse column fantasy_points reads.
XFP_MAP = {
    "receptions_exp": "receptions", "rec_yards_gained_exp": "receiving_yards",
    "rec_touchdown_exp": "receiving_tds", "rush_yards_gained_exp": "rushing_yards",
    "rush_touchdown_exp": "rushing_tds", "pass_yards_gained_exp": "passing_yards",
    "pass_touchdown_exp": "passing_tds", "pass_interception_exp": "passing_interceptions",
    "rec_two_point_conv_exp": "receiving_2pt_conversions",
    "rush_two_point_conv_exp": "rushing_2pt_conversions",
    "pass_two_point_conv_exp": "passing_2pt_conversions",
}

NGS_COLUMNS = {"receiving": {"avg_separation": "ngs_sep",
                             "avg_yac_above_expectation": "ngs_yacoe"},
               "rushing": {"rush_yards_over_expected_per_att": "ngs_ryoe"},
               "passing": {"completion_percentage_above_expectation": "ngs_cpoe"}}

#: Every feature `features_as_of` produces, before the caller adds the two
#: projection-side columns (`baseline`, `ppg_to_date`) and, for the stack,
#: `sleeper`.
FEATURE_COLUMNS = ("xfp_l3", "xfp_season", "fpoe_season", "snap_l3", "tgt_share_l3",
                   "ay_share_l3", "carries_l3", "opp_l3", "ngs_sep", "ngs_yacoe",
                   "ngs_ryoe", "ngs_cpoe", "implied", "def_allowed", "vacated_pickup",
                   "practice_dnp", "practice_limited", "questionable", "depth_rank")


# ------------------------------------------------------------------ inputs

def xfp_from_ff_opportunity(ff: pd.DataFrame | None) -> pd.DataFrame:
    """(gsis_id, week, xfp): expected league points per player-week."""
    if ff is None or len(ff) == 0:
        return pd.DataFrame(columns=["gsis_id", "week", "xfp"])
    rows = []
    for r in ff.to_dict("records"):
        line = {col: float(r.get(k) or 0.0) for k, col in XFP_MAP.items()
                if r.get(k) == r.get(k)}
        rows.append({"gsis_id": normalize_id(r.get("player_id")),
                     "week": int(r["week"]), "xfp": fantasy_points(line)})
    out = pd.DataFrame(rows)
    return out.loc[out["gsis_id"] != ""].groupby(["gsis_id", "week"], as_index=False)["xfp"].sum()


def ngs_from_nextgen(frames: Mapping[str, pd.DataFrame | None]) -> pd.DataFrame:
    """(gsis_id, week, ngs_*) from the three Next Gen Stats frames."""
    out = None
    for kind, cols in NGS_COLUMNS.items():
        d = frames.get(kind)
        if d is None or len(d) == 0:
            continue
        d = d.loc[(d["week"] > 0) & (d.get("season_type", "REG") == "REG")]
        keep = {c: n for c, n in cols.items() if c in d.columns}
        d = d[["player_gsis_id", "week", *keep]].rename(
            columns={"player_gsis_id": "gsis_id", **keep})
        d["gsis_id"] = d["gsis_id"].map(normalize_id)
        out = d if out is None else out.merge(d, on=["gsis_id", "week"], how="outer")
    if out is None:
        return pd.DataFrame(columns=["gsis_id", "week"])
    return out.groupby(["gsis_id", "week"], as_index=False).mean(numeric_only=True)


def depth_from_charts(charts: pd.DataFrame | None, schedule: pd.DataFrame | None
                      ) -> pd.DataFrame:
    """(week, gsis_id, depth_rank): 1 = first on the depth chart at the
    player's offensive position, as published BEFORE that week's first
    kickoff. Handles both nflverse layouts (weekly `depth_team` through 2024,
    timestamped `dt` snapshots from 2025)."""
    cols = ["week", "gsis_id", "depth_rank"]
    if charts is None or len(charts) == 0:
        return pd.DataFrame(columns=cols)
    if "depth_team" in charts.columns:                       # weekly layout
        d = charts.loc[charts.get("formation", "Offense") == "Offense"]
        d = d.loc[d["depth_position"].isin(POSITIONS) & d["week"].notna()]
        d = d.assign(depth_rank=pd.to_numeric(d["depth_team"], errors="coerce"),
                     gsis_id=d["gsis_id"].map(normalize_id), week=d["week"].astype(int))
        return d.groupby(["week", "gsis_id"], as_index=False)["depth_rank"].min()
    if schedule is None or "dt" not in charts.columns:
        return pd.DataFrame(columns=cols)
    d = charts.loc[charts["pos_abb"].isin(POSITIONS)].copy()
    d["when"] = pd.to_datetime(d["dt"], utc=True)
    s = schedule.loc[schedule.get("game_type", "REG") == "REG"]
    first = pd.to_datetime(s["gameday"], utc=True).groupby(s["week"]).min()
    out = []
    for week, kickoff in first.items():
        snap = d.loc[d["when"] < kickoff]
        if len(snap) == 0:
            continue
        latest = snap.loc[snap["when"] == snap.groupby("team")["when"].transform("max")]
        g = latest.groupby("gsis_id", as_index=False)["pos_rank"].min()
        out.append(g.assign(week=int(week)).rename(columns={"pos_rank": "depth_rank"}))
    if not out:
        return pd.DataFrame(columns=cols)
    res = pd.concat(out)
    res["gsis_id"] = res["gsis_id"].map(normalize_id)
    return res[cols]


def practice_from_injuries(injuries: pd.DataFrame | None) -> pd.DataFrame:
    """(week, gsis_id, team, status flags) from the final weekly report."""
    cols = ["week", "gsis_id", "team", "practice_dnp", "practice_limited",
            "questionable", "out"]
    if injuries is None or len(injuries) == 0:
        return pd.DataFrame(columns=cols)
    d = injuries.loc[injuries["position"].isin(POSITIONS)].copy()
    prac = d["practice_status"].fillna("").str.lower()
    rep = d["report_status"].fillna("")
    return pd.DataFrame({
        "week": d["week"].astype(int), "gsis_id": d["gsis_id"].map(normalize_id),
        "team": d["team"].astype(str),
        "practice_dnp": prac.eq("did not participate in practice").astype(float),
        "practice_limited": prac.eq("limited participation in practice").astype(float),
        "questionable": rep.eq("Questionable").astype(float),
        "out": rep.isin(["Out", "Doubtful"]).astype(float)})


def history_frame(weeks: pd.DataFrame, xfp: pd.DataFrame, ngs: pd.DataFrame) -> pd.DataFrame:
    """Scored player-weeks (`gridiron.usage.player_weeks`) + expected points
    + NGS, one row per player-week that has a stat line."""
    cols = ["gsis_id", "week", "team", "opponent_team", "position", "league_points",
            "offense_pct", "target_share", "air_yards_share", "carries", "targets"]
    h = weeks[[c for c in cols if c in weeks.columns]].copy()
    for c in cols:
        if c not in h.columns:
            h[c] = np.nan
    h["gsis_id"] = h["gsis_id"].map(normalize_id)
    h["opps"] = h["carries"].fillna(0) + h["targets"].fillna(0)
    h = h.merge(xfp, on=["gsis_id", "week"], how="left")
    h = h.merge(ngs, on=["gsis_id", "week"], how="left")
    for c in ("ngs_sep", "ngs_yacoe", "ngs_ryoe", "ngs_cpoe"):
        if c not in h.columns:
            h[c] = np.nan
    h["fpoe"] = h["league_points"] - h["xfp"]
    return h.loc[h["position"].isin(POSITIONS)]


# ---------------------------------------------------------------- features

def features_as_of(history: pd.DataFrame, week: int, *,
                   schedule: pd.DataFrame | None = None,
                   practice: pd.DataFrame | None = None,
                   depth: pd.DataFrame | None = None,
                   teams: Mapping[str, str] | None = None) -> pd.DataFrame:
    """Every feature for `week`, from history before `week` only; one row
    per gsis id that has at least one earlier game. `teams` (gsis -> team)
    overrides the last team seen in history (a trade the box scores have not
    caught up with)."""
    past = history.loc[history["week"] < int(week)].sort_values("week")
    if len(past) == 0:
        return pd.DataFrame(columns=["gsis_id", "position", "team", *FEATURE_COLUMNS])
    g = past.groupby("gsis_id")
    last3 = past.groupby("gsis_id").tail(3).groupby("gsis_id")
    f = pd.DataFrame({
        "position": g["position"].last(), "team": g["team"].last(),
        "xfp_l3": last3["xfp"].mean(), "xfp_season": g["xfp"].mean(),
        "fpoe_season": g["fpoe"].mean(), "snap_l3": last3["offense_pct"].mean(),
        "tgt_share_l3": last3["target_share"].mean(),
        "ay_share_l3": last3["air_yards_share"].mean(),
        "carries_l3": last3["carries"].mean(), "opp_l3": last3["opps"].mean(),
        "ngs_sep": g["ngs_sep"].mean(), "ngs_yacoe": g["ngs_yacoe"].mean(),
        "ngs_ryoe": g["ngs_ryoe"].mean(), "ngs_cpoe": g["ngs_cpoe"].mean()})
    f.index.name = "gsis_id"
    f = f.reset_index()
    if teams:
        f["team"] = [teams.get(gid) or t for gid, t in zip(f["gsis_id"], f["team"])]

    games = schedule_index(schedule, int(week)) if schedule is not None else {}
    f["implied"] = [getattr(games.get(str(t)), "implied_total", None) for t in f["team"]]
    f["implied"] = pd.to_numeric(f["implied"], errors="coerce")
    opponent = _opponents(schedule, int(week))
    per = (past.groupby(["opponent_team", "position", "week"])["league_points"].sum()
           .reset_index())
    allowed = per.groupby(["opponent_team", "position"])["league_points"].mean()
    league = per.groupby("position")["league_points"].mean()
    f["def_allowed"] = [
        (allowed.get((opponent.get(str(t)), p), np.nan) - league.get(p, np.nan))
        if opponent.get(str(t)) else np.nan for t, p in zip(f["team"], f["position"])]

    prac = practice.loc[practice["week"] == int(week)] if practice is not None and len(practice) else None
    flags = (prac.groupby("gsis_id")[["practice_dnp", "practice_limited", "questionable"]]
             .max() if prac is not None and len(prac) else pd.DataFrame())
    for c in ("practice_dnp", "practice_limited", "questionable"):
        f[c] = [float(flags[c].get(gid, 0.0)) if len(flags) else 0.0 for gid in f["gsis_id"]]
    out_ids = set(prac.loc[prac["out"] > 0, "gsis_id"]) if prac is not None else set()
    freed: dict[str, float] = {}
    for gid, team, opp3 in zip(f["gsis_id"], f["team"], f["opp_l3"]):
        if gid in out_ids and opp3 == opp3:
            freed[str(team)] = freed.get(str(team), 0.0) + float(opp3)
    active = f.loc[~f["gsis_id"].isin(out_ids)]
    team_total = active.groupby("team")["opp_l3"].sum()
    f["vacated_pickup"] = [
        0.0 if gid in out_ids or not team_total.get(t) else
        freed.get(str(t), 0.0) * (float(o) if o == o else 0.0) / float(team_total.get(t))
        for gid, t, o in zip(f["gsis_id"], f["team"], f["opp_l3"])]

    dep = depth.loc[depth["week"] == int(week)].set_index("gsis_id")["depth_rank"] \
        if depth is not None and len(depth) else pd.Series(dtype=float)
    f["depth_rank"] = [dep.get(gid, np.nan) for gid in f["gsis_id"]]
    return f


def _opponents(schedule: pd.DataFrame | None, week: int) -> dict[str, str]:
    if schedule is None or len(schedule) == 0:
        return {}
    s = schedule.loc[(schedule["week"] == week)
                     & (schedule.get("game_type", "REG") == "REG")]
    out = {}
    for home, away in zip(s["home_team"], s["away_team"]):
        out[str(home)], out[str(away)] = str(away), str(home)
    return out


# ------------------------------------------------------------------- model

@dataclass(frozen=True)
class Ridge:
    """A standardised ridge regression, serialisable to JSON."""

    cols: tuple[str, ...]
    fill: tuple[float, ...]
    mu: tuple[float, ...]
    sd: tuple[float, ...]
    beta: tuple[float, ...]            # intercept first

    @classmethod
    def fit(cls, df: pd.DataFrame, y: pd.Series, cols: Sequence[str],
            lam: float = 5.0) -> "Ridge":
        x = df[list(cols)].astype("float64")
        fill = x.mean().fillna(0.0)
        x = x.fillna(fill)
        mu, sd = x.mean(), x.std().replace(0, 1.0).fillna(1.0)
        z = ((x - mu) / sd).to_numpy()
        z1 = np.hstack([np.ones((len(z), 1)), z])
        pen = lam * np.eye(z1.shape[1])
        pen[0, 0] = 0.0
        beta = np.linalg.solve(z1.T @ z1 + pen, z1.T @ y.to_numpy(dtype="float64"))
        return cls(tuple(cols), tuple(fill), tuple(mu), tuple(sd), tuple(float(b) for b in beta))

    def predict_row(self, row: Mapping[str, object]) -> float:
        acc = self.beta[0]
        for c, fill, mu, sd, b in zip(self.cols, self.fill, self.mu, self.sd, self.beta[1:]):
            v = row.get(c)
            try:
                v = float(v)
            except (TypeError, ValueError):
                v = fill
            if math.isnan(v):
                v = fill
            acc += b * (v - mu) / sd
        return float(acc)

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        return np.array([self.predict_row(r) for r in df.to_dict("records")])

    def to_json(self) -> dict:
        return {"cols": list(self.cols), "fill": list(self.fill), "mu": list(self.mu),
                "sd": list(self.sd), "beta": list(self.beta)}

    @classmethod
    def from_json(cls, blob: Mapping[str, Sequence]) -> "Ridge":
        return cls(*(tuple(blob[k]) for k in ("cols", "fill", "mu", "sd", "beta")))


@dataclass(frozen=True)
class AdvancedModel:
    """Per-position `adv` (no outside projection) and `stack` (+ Sleeper)."""

    adv: Mapping[str, Ridge]
    stack: Mapping[str, Ridge]
    meta: Mapping[str, object]

    @classmethod
    def load(cls, path: Path = WEIGHTS_PATH) -> "AdvancedModel | None":
        try:
            blob = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return cls({p: Ridge.from_json(v) for p, v in blob["adv"].items()},
                   {p: Ridge.from_json(v) for p, v in blob["stack"].items()},
                   blob.get("meta", {}))

    def save(self, path: Path = WEIGHTS_PATH) -> None:
        Path(path).write_text(json.dumps({
            "meta": dict(self.meta),
            "adv": {p: r.to_json() for p, r in self.adv.items()},
            "stack": {p: r.to_json() for p, r in self.stack.items()}}, indent=1),
            encoding="utf-8")

    def predict(self, position: str, row: Mapping[str, object], *,
                stacked: bool = False) -> float | None:
        model = (self.stack if stacked else self.adv).get(str(position))
        if model is None or (stacked and row.get("sleeper") is None):
            return None
        return max(0.0, model.predict_row(row))


# ------------------------------------------------------------ live inputs

#: Where the pull step keeps the model's inputs, beside the season cache.
#: Outside the manifest on purpose: they refine the projection and gate
#: nothing; when they are missing the page keeps the baseline and says so.
INPUT_DIR = "model_inputs"
INPUT_META = "meta.json"
INPUT_REFRESH_HOURS = 6.0


def fetch_inputs(season: int, *, loaders=None) -> dict[str, pd.DataFrame]:
    """Download and normalise the three advanced inputs for one season.
    `loaders` (for tests) maps name -> zero-arg callable returning pandas."""
    if loaders is None:
        import nflreadpy as nfl
        loaders = {
            "ff_opportunity": lambda: nfl.load_ff_opportunity(
                [season], stat_type="weekly").to_pandas(),
            **{f"ngs_{k}": (lambda k=k: nfl.load_nextgen_stats(
                [season], stat_type=k).to_pandas()) for k in NGS_COLUMNS},
            "depth_charts": lambda: nfl.load_depth_charts([season]).to_pandas(),
            "schedules": lambda: nfl.load_schedules([season]).to_pandas(),
        }
    raw = {name: fn() for name, fn in loaders.items()}
    return {"xfp": xfp_from_ff_opportunity(raw.get("ff_opportunity")),
            "ngs": ngs_from_nextgen({k: raw.get(f"ngs_{k}") for k in NGS_COLUMNS}),
            "depth": depth_from_charts(raw.get("depth_charts"), raw.get("schedules"))}


def write_inputs(directory: Path, season: int, frames: Mapping[str, pd.DataFrame],
                 now) -> Path:
    folder = Path(directory) / INPUT_DIR
    folder.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        tmp = folder / f"{name}.parquet.part"
        df.to_parquet(tmp, index=False)
        tmp.replace(folder / f"{name}.parquet")
    (folder / INPUT_META).write_text(json.dumps({
        "season": int(season), "fetched_at": now.isoformat(timespec="seconds"),
        "rows": {k: int(len(v)) for k, v in frames.items()},
        "weeks": {k: sorted({int(w) for w in v["week"].dropna()}) if "week" in v else []
                  for k, v in frames.items()}}), encoding="utf-8")
    return folder


def load_inputs(directory: Path, season: int) -> dict | None:
    """The saved inputs for `season`, or None when absent or for another
    season. Returns {"xfp", "ngs", "depth", "meta"}."""
    folder = Path(directory) / INPUT_DIR
    try:
        meta = json.loads((folder / INPUT_META).read_text(encoding="utf-8"))
        if int(meta.get("season") or 0) != int(season):
            return None
        frames = {k: pd.read_parquet(folder / f"{k}.parquet") for k in ("xfp", "ngs", "depth")}
    except (OSError, ValueError, KeyError):
        return None
    for df in frames.values():
        if "gsis_id" in df:
            df["gsis_id"] = df["gsis_id"].map(normalize_id)
    return {**frames, "meta": meta}


def inputs_fresh(directory: Path, season: int, now, hours: float = INPUT_REFRESH_HOURS) -> bool:
    from datetime import datetime
    blob = load_inputs(directory, season)
    if blob is None:
        return False
    try:
        at = datetime.fromisoformat(str(blob["meta"]["fetched_at"]))
    except (KeyError, ValueError):
        return False
    return (now - at).total_seconds() < hours * 3600


# --------------------------------------------------------- serving context

@dataclass(frozen=True)
class AdvancedContext:
    """One week's features, ready for the dashboard to refine projections.

    `status` is what the page and the record say about the model: which
    model ran, or why the baseline stands."""

    model: "AdvancedModel | None"
    features: Mapping[str, Mapping[str, object]]
    week: int
    status: str
    inputs_as_of: str = ""

    @property
    def active(self) -> bool:
        return self.model is not None and bool(self.features)

    def refine(self, gsis_id: str, position: str, baseline: float,
               ppg_to_date: float | None) -> tuple[float, dict] | None:
        """(advanced mean, the features used) or None to keep the baseline."""
        if not self.active or position not in POSITIONS:
            return None
        row = self.features.get(normalize_id(gsis_id))
        if row is None:
            return None
        full = {**row, "baseline": baseline,
                "ppg_to_date": baseline if ppg_to_date is None else ppg_to_date}
        mean = self.model.predict(position, full)
        return (None if mean is None else (round(mean, 3), full))

    def stacked(self, gsis_id: str, position: str, baseline: float,
                ppg_to_date: float | None, sleeper: float | None) -> float | None:
        if not self.active or sleeper is None or position not in POSITIONS:
            return None
        row = self.features.get(normalize_id(gsis_id))
        if row is None:
            return None
        full = {**row, "baseline": baseline, "sleeper": sleeper,
                "ppg_to_date": baseline if ppg_to_date is None else ppg_to_date}
        mean = self.model.predict(position, full, stacked=True)
        return None if mean is None else round(mean, 3)


def build_context(*, weeks: pd.DataFrame | None, inputs: Mapping | None,
                  injuries: pd.DataFrame | None, schedule: pd.DataFrame | None,
                  week: int, teams: Mapping[str, str] | None = None,
                  model: "AdvancedModel | None" = None) -> AdvancedContext:
    """Assemble the week's features, or an inactive context with a reason."""
    model = model if model is not None else AdvancedModel.load()
    if model is None:
        return AdvancedContext(None, {}, week, "baseline: the advanced model's "
                               "coefficients could not be read")
    min_week = int(model.meta.get("min_week") or 1)
    if week < min_week:
        return AdvancedContext(model, {}, week, f"baseline: the advanced model is "
                               f"trained on week {min_week} onward (thin history before)")
    if inputs is None:
        return AdvancedContext(model, {}, week, "baseline: advanced inputs (expected "
                               "points, Next Gen Stats, depth charts) not available "
                               "for this build")
    if weeks is None or len(weeks) == 0:
        return AdvancedContext(model, {}, week, "baseline: no box scores to build "
                               "advanced features from")
    hist = history_frame(weeks, inputs["xfp"], inputs["ngs"])
    feats = features_as_of(hist, int(week), schedule=schedule,
                           practice=practice_from_injuries(injuries),
                           depth=inputs["depth"], teams=teams)
    rows = {gid: {c: r.get(c) for c in FEATURE_COLUMNS}
            for gid, r in zip(feats["gsis_id"], feats.to_dict("records"))}
    as_of = str((inputs.get("meta") or {}).get("fetched_at") or "")
    ev = model.meta.get("evidence") or {}
    status = (f"{NAME}: baseline refined by advanced stats (out of sample "
              f"{ev.get('test')}: start/sit {float(ev.get('adv_pairwise', 0)):.1%} vs "
              f"baseline {float(ev.get('baseline_pairwise', 0)):.1%}); inputs as of {as_of}")
    return AdvancedContext(model, rows, week, status, as_of)
