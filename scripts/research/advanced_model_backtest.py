"""Can NFL advanced stats beat Sleeper's weekly projections? Out of sample.

    PYTHONPATH=src python scripts/research/advanced_model_backtest.py \
        --train 2023 2024 --test 2025

Trains per-position ridge regressions on the TRAIN seasons and scores them on
the TEST season, beside this repo's baseline and Sleeper's published
projections, on identical player-weeks. Every feature for week w is built
from weeks < w of the same season only (shifted rolling windows), so nothing
a model sees could not have been known before kickoff — except Sleeper's own
number, whose historical records were modified after the games (see
docs/research/PROJECTION_BACKTEST_2025.md). Hence two candidates:

  adv    our baseline + advanced stats, NO Sleeper input — the clean test:
         if it matches Sleeper, it matches a number that may carry hindsight;
  stack  adv + Sleeper's projection — only a forward test can grade it fairly.

Advanced inputs (all public, nflverse via nflreadpy):
  * expected fantasy points (ff_opportunity): what each player's targets,
    carries and field position were worth on average — volume and red-zone
    role with touchdown luck removed; scored with the league's own rules
    (rule #2) from the *_exp components;
  * points over expectation (actual - expected), season to date — the
    efficiency signal, left to the regression to shrink (rule #6);
  * snap share, target share, air-yards share, carries (last 3 games);
  * Next Gen Stats: receiver separation and YAC over expectation, rushing
    yards over expectation per attempt, QB completion % over expectation;
  * the team's implied total from the posted line.

Rule #5 gate: nothing here ships. A model enters the product only after it
beats the full baseline out of sample AND survives the live shadow test.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.evaluate import chronological_evaluation
from gridiron.ids import Crosswalk
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT
from gridiron.scoring import fantasy_points
from gridiron.usage import player_weeks
from gridiron.weekly import schedule_index

POSITIONS = ("QB", "RB", "WR", "TE")

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

FEATURES = {
    "QB": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "carries_l3", "ngs_cpoe", "implied", "def_allowed", "vacated_pickup"],
    "RB": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "carries_l3", "tgt_share_l3", "ngs_ryoe", "implied", "def_allowed",
           "vacated_pickup"],
    "WR": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "tgt_share_l3", "ay_share_l3", "ngs_sep", "ngs_yacoe", "implied",
           "def_allowed", "vacated_pickup"],
    "TE": ["baseline", "ppg_to_date", "xfp_l3", "xfp_season", "fpoe_season",
           "snap_l3", "tgt_share_l3", "ay_share_l3", "ngs_sep", "ngs_yacoe", "implied",
           "def_allowed", "vacated_pickup"],
}


def _bt():
    spec = importlib.util.spec_from_file_location(
        "projection_backtest", REPO_ROOT / "scripts" / "research" / "projection_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["projection_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


def xfp_frame(season: int) -> pd.DataFrame:
    import nflreadpy as nfl
    ff = nfl.load_ff_opportunity([season], stat_type="weekly").to_pandas()
    rows = []
    for r in ff.to_dict("records"):
        line = {col: float(r.get(k) or 0.0) for k, col in XFP_MAP.items()}
        rows.append({"gsis_id": str(r["player_id"]), "week": int(r["week"]),
                     "xfp": fantasy_points(line)})
    return pd.DataFrame(rows).groupby(["gsis_id", "week"], as_index=False)["xfp"].sum()


def ngs_frame(season: int) -> pd.DataFrame:
    import nflreadpy as nfl
    parts = []
    spec = {"receiving": {"avg_separation": "ngs_sep",
                          "avg_yac_above_expectation": "ngs_yacoe"},
            "rushing": {"rush_yards_over_expected_per_att": "ngs_ryoe"},
            "passing": {"completion_percentage_above_expectation": "ngs_cpoe"}}
    for kind, cols in spec.items():
        d = nfl.load_nextgen_stats([season], stat_type=kind).to_pandas()
        d = d.loc[(d["week"] > 0) & (d["season_type"] == "REG")]
        keep = {c: n for c, n in cols.items() if c in d.columns}
        d = d[["player_gsis_id", "week", *keep]].rename(
            columns={"player_gsis_id": "gsis_id", **keep})
        parts.append(d)
    out = parts[0]
    for p in parts[1:]:
        out = out.merge(p, on=["gsis_id", "week"], how="outer")
    out["gsis_id"] = out["gsis_id"].astype(str)
    return out


def season_table(season: int, cw: Crosswalk, bt) -> pd.DataFrame:
    """One row per player-week with every system and every prior-week feature."""
    import nflreadpy as nfl
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    weekly = weekly.loc[(weekly["season_type"] == "REG") & weekly["position"].isin(POSITIONS)]
    snaps = nfl.load_snap_counts([season]).to_pandas()
    sched = nfl.load_schedules([season]).to_pandas()
    frame = player_weeks(weekly, snaps, cw)
    rows = chronological_evaluation(frame, sched).rows          # baseline etc.

    hist = frame[["gsis_id", "week", "team", "opponent_team", "position",
                  "league_points", "offense_pct", "target_share", "air_yards_share",
                  "carries", "targets"]].copy()
    hist["opps"] = hist["carries"].fillna(0) + hist["targets"].fillna(0)
    hist = hist.merge(xfp_frame(season), on=["gsis_id", "week"], how="left")
    hist = hist.merge(ngs_frame(season), on=["gsis_id", "week"], how="left")
    hist["fpoe"] = hist["league_points"] - hist["xfp"]
    hist = hist.sort_values(["gsis_id", "week"])
    g = hist.groupby("gsis_id")

    def prior(col, n=None):
        s = g[col].shift(1)
        grp = s.groupby(hist["gsis_id"])
        return (grp.transform(lambda x: x.rolling(n, min_periods=1).mean()) if n
                else grp.transform(lambda x: x.expanding().mean()))
    feats = pd.DataFrame({"gsis_id": hist["gsis_id"], "week": hist["week"],
                          "team": hist["team"],
                          "xfp_l3": prior("xfp", 3), "xfp_season": prior("xfp"),
                          "fpoe_season": prior("fpoe"), "snap_l3": prior("offense_pct", 3),
                          "tgt_share_l3": prior("target_share", 3),
                          "ay_share_l3": prior("air_yards_share", 3),
                          "carries_l3": prior("carries", 3),
                          "ngs_sep": prior("ngs_sep"), "ngs_yacoe": prior("ngs_yacoe"),
                          "ngs_ryoe": prior("ngs_ryoe"), "ngs_cpoe": prior("ngs_cpoe")})
    feats["opp_l3"] = prior("opps", 3)
    feats = feats.merge(defense_allowed(hist), on=["gsis_id", "week"], how="left")
    feats = feats.merge(vacated(season, hist, feats), on=["gsis_id", "week"], how="left")
    out = rows.merge(feats, on=["gsis_id", "week"], how="left")
    implied = []
    for w, grp in out.groupby("week"):
        games = schedule_index(sched, int(w))
        for i, team in zip(grp.index, grp["team"]):
            g_ = games.get(str(team))
            implied.append((i, getattr(g_, "implied_total", None) if g_ else None))
    out["implied"] = pd.Series(dict(implied), dtype="float64")
    sl = bt.sleeper_projections(season, sorted(out["week"].unique().tolist()), cw)
    out = out.merge(sl[["week", "gsis_id", "sleeper"]], on=["week", "gsis_id"], how="left")
    out["season"] = season
    return out


def defense_allowed(hist: pd.DataFrame) -> pd.DataFrame:
    """For each player-week: how many points this week's opponent allowed to
    the player's position per game in EARLIER weeks, minus the league's
    per-defense average over the same earlier weeks."""
    per = (hist.groupby(["opponent_team", "position", "week"])["league_points"]
           .sum().reset_index().rename(columns={"opponent_team": "defense"}))
    per = per.sort_values("week")
    per["prior"] = per.groupby(["defense", "position"])["league_points"].transform(
        lambda x: x.shift(1).expanding().mean())
    league = per.groupby(["position", "week"])["league_points"].mean().reset_index()
    league = league.sort_values("week")
    league["league_prior"] = league.groupby("position")["league_points"].transform(
        lambda x: x.shift(1).expanding().mean())
    per = per.merge(league[["position", "week", "league_prior"]], on=["position", "week"])
    per["def_allowed"] = per["prior"] - per["league_prior"]
    out = hist[["gsis_id", "week", "opponent_team", "position"]].merge(
        per[["defense", "position", "week", "def_allowed"]],
        left_on=["opponent_team", "position", "week"],
        right_on=["defense", "position", "week"], how="left")
    return out[["gsis_id", "week", "def_allowed"]]


def vacated(season: int, hist: pd.DataFrame, feats: pd.DataFrame) -> pd.DataFrame:
    """Opportunity freed by teammates on the FINAL injury report as Out or
    Doubtful (known before kickoff): the sum of their last-3-game targets +
    carries, times this player's share of the active teammates' recent
    opportunities."""
    import nflreadpy as nfl
    inj = nfl.load_injuries([season]).to_pandas()
    inj = inj.loc[inj["report_status"].isin(["Out", "Doubtful"])
                  & inj["position"].isin(POSITIONS)]
    games = hist.sort_values("week")
    freed: dict[tuple[str, int], float] = {}
    for r in inj.itertuples():
        past = games.loc[(games["gsis_id"] == r.gsis_id) & (games["week"] < r.week)].tail(3)
        if len(past):
            key = (str(r.team), int(r.week))
            freed[key] = freed.get(key, 0.0) + float(past["opps"].mean())
    f = feats[["gsis_id", "week", "team", "opp_l3"]].copy()
    team_total = f.groupby(["team", "week"])["opp_l3"].transform("sum")
    share = f["opp_l3"] / team_total.where(team_total > 0)
    f["vacated_pickup"] = [freed.get((str(t), int(w)), 0.0) for t, w in zip(f["team"], f["week"])]
    f["vacated_pickup"] = f["vacated_pickup"] * share.fillna(0.0)
    return f[["gsis_id", "week", "vacated_pickup"]]


class Ridge:
    """Standardised ridge regression with training-set imputation (numpy only)."""

    def __init__(self, cols, lam=5.0):
        self.cols, self.lam = cols, lam

    def _x(self, df):
        x = df[self.cols].astype("float64").copy()
        for c in self.cols:
            x[c] = x[c].fillna(self.fill[c])
        return ((x - self.mu) / self.sd).to_numpy()

    def fit(self, df, y):
        x = df[self.cols].astype("float64")
        self.fill = x.mean()
        x = x.fillna(self.fill)
        self.mu, self.sd = x.mean(), x.std().replace(0, 1.0)
        z = ((x - self.mu) / self.sd).to_numpy()
        z1 = np.hstack([np.ones((len(z), 1)), z])
        pen = self.lam * np.eye(z1.shape[1])
        pen[0, 0] = 0.0
        self.beta = np.linalg.solve(z1.T @ z1 + pen, z1.T @ y.to_numpy())
        return self

    def predict(self, df):
        z = self._x(df)
        return np.hstack([np.ones((len(z), 1)), z]) @ self.beta

    def weights(self):
        return {"intercept": round(float(self.beta[0]), 3),
                **{c: round(float(b), 3) for c, b in zip(self.cols, self.beta[1:])}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--train", type=int, nargs="+", default=[2023, 2024])
    ap.add_argument("--test", type=int, default=2025)
    ap.add_argument("--min-week", type=int, default=4,
                    help="score weeks >= this (early weeks have thin history)")
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)

    bt = _bt()
    cw = Crosswalk.from_csv(args.crosswalk)
    tables = {s: season_table(s, cw, bt) for s in (*args.train, args.test)}
    train = pd.concat([tables[s] for s in args.train])
    test = tables[args.test]
    train = train.loc[(train["week"] >= args.min_week) & train["sleeper"].notna()]
    test = test.loc[(test["week"] >= args.min_week) & test["sleeper"].notna()].copy()

    weights = {}
    for pos in POSITIONS:
        tr, te = train[train["position"] == pos], test["position"] == pos
        adv = Ridge(FEATURES[pos]).fit(tr, tr["actual"])
        stack = Ridge(FEATURES[pos] + ["sleeper"]).fit(tr, tr["actual"])
        test.loc[te, "adv"] = adv.predict(test[te])
        test.loc[te, "stack"] = stack.predict(test[te])
        weights[pos] = {"adv": adv.weights(), "stack": stack.weights(), "train_n": len(tr)}
    test["blend"] = (test["baseline"] + test["sleeper"]) / 2.0

    systems = ["baseline", "sleeper", "blend", "adv", "stack"]
    rel = test.loc[test["sleeper"] >= 5.0]
    pw, cc = bt.pairwise(rel, systems), bt.pairwise(rel, systems, close=3.0)
    sp = bt.spearman(rel, systems)
    res = {"train": args.train, "test": args.test, "train_rows": int(len(train)),
           "test_rows": int(len(test)), "pairs": pw["n"], "close_pairs": cc["n"],
           "systems": {s: {"mae": round(float((test[s] - test["actual"]).abs().mean()), 3),
                           "pairwise": round(pw[s], 4), "close_calls": round(cc[s], 4),
                           "spearman": round(sp[s], 4)} for s in systems},
           "by_position": {}, "weekly_edge_adv_vs_sleeper": [], "weights": weights}
    for pos, g in rel.groupby("position"):
        p = bt.pairwise(g, systems)
        res["by_position"][pos] = {"pairs": p["n"], **{s: round(p[s], 4) for s in systems}}
    for w, g in rel.groupby("week"):
        p = bt.pairwise(g, ["adv", "sleeper", "stack"])
        res["weekly_edge_adv_vs_sleeper"].append(
            [int(w), round(p["adv"] - p["sleeper"], 4), round(p["stack"] - p["sleeper"], 4)])

    print(f"train {args.train} ({len(train)} rows) -> test {args.test} "
          f"({len(test)} rows, weeks >= {args.min_week}); {pw['n']} pairs, "
          f"{cc['n']} close calls")
    print(f"{'system':<10} {'MAE':>6} {'pairwise':>9} {'close':>7} {'spearman':>9}")
    for s in systems:
        m = res["systems"][s]
        print(f"{s:<10} {m['mae']:6.2f} {m['pairwise']:9.1%} {m['close_calls']:7.1%} "
              f"{m['spearman']:9.3f}")
    print("by position:", json.dumps(res["by_position"]))
    wk = res["weekly_edge_adv_vs_sleeper"]
    print(f"weeks adv > sleeper: {sum(e[1] > 0 for e in wk)}/{len(wk)}; "
          f"stack > sleeper: {sum(e[2] > 0 for e in wk)}/{len(wk)}")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
