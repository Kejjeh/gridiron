"""Starter out -> backup inherits the job: does pricing the inherited role help? Out of sample.

    PYTHONPATH=src python scripts/research/inherited_role_backtest.py
        [--seasons 2019 ... 2025] [--rebuild] [--out FILE.json]

Research only: never imported by src/gridiron, writes nothing under src/,
scripts/weekly/ or the ledger. Season tables are cached to
data/research/cache/tables/season<YYYY>_inherit.parquet, written only after
`git check-ignore` confirms the path is ignored (rule #10).

The miss (2026 week 10): the Chiefs' lead back (65-82% of snaps) did not
practise on Friday and was ruled Out on Saturday; his backup played 88% of
snaps and scored 35.1. The weekly model moved the backup by -0.2 points:
`vacated_pickup` counts only teammates Out or Doubtful on the report (he was
Questionable on it) and, when it fires, shares the freed opportunities over
EVERY active teammate at every position by trailing volume, so a backup with
little volume inherits little (docs/research/INHERITED_ROLE_BACKTEST.md).

THE SUBSET (exactly the player-weeks an alert would name), week w:
  * a STARTER, from the box scores before w: an RB averaging >= 60% of
    offensive snaps over his last 3 games, or the team's top WR / top TE by
    targets per game over his last 3 games (at least `MIN_TGT` a game), whose
    last game is within the last 2 weeks (`RECENT_WEEKS`);
  * on week w's final injury report: Out or Doubtful, or Did Not Participate
    in the final practice (DNP; Questionable or no designation). Questionable
    alone is not a trigger (rule #11);
  * his BACKUP: the teammate at the same position with the highest snap
    share over his own last 3 games, among those who played in the last 3
    weeks and are not themselves Out or Doubtful; at WR, among teammates
    below a starter's 65% snap share (the other starters are already priced
    and rostered). Role from usage, never the position tag (rule #4).
  The row evaluated is the backup's week w, when he has a stat line and a
  Sleeper number (the harness universe; a backup who did not play has no
  row — survivorship, the same for every system).

CANDIDATES (2 — the bar's chance level is split between them):
  +inherit  the shipped feature set plus `inherit_pickup` = P(starter sits |
            his report class) x the starter's opportunities a game, a ridge
            feature that is 0 for every other player (rule #5: the baseline
            contains ALL existing features).
  resid     the shipped prediction plus k[pos] x P(sits) x starter opps,
            k fitted on the training seasons' subset residuals (no
            intercept): a direct inherited-volume correction.
  P(sits | class) is the share of training-season trigger rows whose starter
  had no stat line that week, per class ("out" = Out/Doubtful, "dnp" = DNP
  but not Out/Doubtful). Every parameter is fitted on the training seasons
  only.

JUDGE: season-fold CV, each season held out in turn. Metrics on the subset:
MAE, and start/sit pairwise over pairs (same week and position) with at
least one subset row, against the harness's relevance universe (Sleeper >=
5). The owner's bar (`recency_bar`, docs/DECISIONS.md 2026-11-18, mirrored
from `ros_backtest.recency_bar` on the weekly metrics): wins the latest
season on pairwise AND MAE; recency-weighted (half-life 2 seasons) mean gain
positive on both; sign-flip p on the weighted pairwise gain <= 0.05 / 2.
Sleeper's historical numbers are post-hoc records (they include the Saturday
news a Friday alert does not have): context, not a scoreboard. Output is
counts and rates only: no names, no ids.
"""
from __future__ import annotations

import argparse
import itertools
import json
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk, nflverse_team, normalize_id
from gridiron.models import advanced as A
from gridiron.paths import REPO_ROOT, RESEARCH_CACHE
from gridiron.usage import player_weeks

sys.path.insert(0, str(Path(__file__).resolve().parent))
from research_common import load_script  # noqa: E402

SEASONS = (2019, 2020, 2021, 2022, 2023, 2024, 2025)
TABLES = RESEARCH_CACHE / "tables"
MIN_WEEK = 4
#: Starter definitions (the task's: RB >= 60% snaps; top WR / TE by targets).
RB_SNAP = 0.60
MIN_TGT = {"WR": 5.0, "TE": 4.0}
WR_STARTER_SNAP = 0.65          # gridiron.openings.VOLUME_SNAP_PCT["WR"]
RECENT_WEEKS = 2                # gridiron.openings.VACANCY_RECENT_WEEKS
BACKUP_RECENT_WEEKS = 3
OUT_STATUSES = ("Out", "Doubtful")
DNP = "did not participate in practice"
#: The bar (mirrors ros_backtest: half-life 2 seasons, alpha 0.05 split).
RECENCY_HALFLIFE = 2.0
BAR_ALPHA = 0.05
CANDIDATES = ("+inherit", "resid")


# ------------------------------------------------------------------ inputs

def _ignored(path: Path) -> None:
    rel = path.resolve().relative_to(REPO_ROOT.resolve()) if path.resolve().is_relative_to(
        REPO_ROOT.resolve()) else path
    r = subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=REPO_ROOT)
    if r.returncode != 0:
        raise SystemExit(f"refusing to write {path}: not git-ignored (rule #10)")


def season_inputs(season: int, cw: Crosswalk, bt, ab, rebuild: bool) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """(table, hist, report): the harness table (features for week w from
    weeks < w, `advanced_model_backtest.season_table`), the scored
    player-weeks, and the final weekly injury report."""
    import nflreadpy as nfl
    TABLES.mkdir(parents=True, exist_ok=True)
    path = TABLES / f"season{season}_inherit.parquet"
    if path.exists() and not rebuild:
        table = pd.read_parquet(path)
    else:
        table = ab.season_table(season, cw, bt)
        _ignored(path)
        table.to_parquet(path, index=False)
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    weekly = weekly.loc[(weekly["season_type"] == "REG") & weekly["position"].isin(A.POSITIONS)]
    hist = player_weeks(weekly, nfl.load_snap_counts([season]).to_pandas(), cw)
    hist = hist.assign(gsis_id=hist["gsis_id"].map(normalize_id),
                       team=hist["team"].map(nflverse_team),
                       opps=pd.to_numeric(hist["carries"], errors="coerce").fillna(0)
                       + pd.to_numeric(hist["targets"], errors="coerce").fillna(0),
                       targets=pd.to_numeric(hist["targets"], errors="coerce"),
                       offense_pct=pd.to_numeric(hist["offense_pct"], errors="coerce"))
    hist = hist[["gsis_id", "week", "team", "position", "offense_pct", "targets", "opps",
                 "league_points"]]
    inj = nfl.load_injuries([season]).to_pandas()
    inj = inj.loc[(inj.get("game_type", "REG") == "REG") & inj["position"].isin(A.POSITIONS)]
    report = pd.DataFrame({"week": inj["week"].astype(int),
                           "gsis_id": inj["gsis_id"].map(normalize_id),
                           "team": inj["team"].map(nflverse_team),
                           "status": inj["report_status"].fillna(""),
                           "practice": inj["practice_status"].fillna("").str.lower()})
    return table, hist, report


# -------------------------------------------------------------- the subset

def vacancies(hist: pd.DataFrame, report: pd.DataFrame, week: int) -> pd.DataFrame:
    """One row per (starter on week `week`'s report, his backup), from the box
    scores before `week` and that week's final report only.

    Columns: team, position, starter, backup, klass ("out" | "dnp"),
    starter_opp, starter_snap, backup_snap, first_miss (the starter played
    the team's previous game)."""
    cols = ["team", "position", "starter", "backup", "klass", "starter_opp", "starter_snap",
            "backup_snap", "first_miss"]
    past = hist.loc[hist["week"] < int(week)].sort_values("week")
    rep = report.loc[report["week"] == int(week)]
    if len(past) == 0 or len(rep) == 0:
        return pd.DataFrame(columns=cols)
    last3 = past.groupby("gsis_id").tail(3).groupby("gsis_id")
    me = pd.DataFrame({"team": past.groupby("gsis_id")["team"].last(),
                       "position": past.groupby("gsis_id")["position"].last(),
                       "last_week": past.groupby("gsis_id")["week"].last(),
                       "snap3": last3["offense_pct"].mean(), "tgt3": last3["targets"].mean(),
                       "opp3": last3["opps"].mean()})
    team_last = past.groupby("team")["week"].max()
    out_ids = set(rep.loc[rep["status"].isin(OUT_STATUSES), "gsis_id"])
    dnp_ids = set(rep.loc[rep["practice"].eq(DNP), "gsis_id"])
    rep_team = rep.drop_duplicates("gsis_id").set_index("gsis_id")["team"]
    rows = []
    for (team, pos), g in me.loc[me["position"].isin(("RB", "WR", "TE"))].groupby(["team", "position"]):
        recent = g.loc[g["last_week"] >= int(week) - RECENT_WEEKS]
        if pos == "RB":
            starters = recent.loc[recent["snap3"] >= RB_SNAP]
        else:
            top = recent.loc[recent["tgt3"] >= MIN_TGT[pos]].sort_values("tgt3", ascending=False)
            starters = top.head(1)
        for sid, s in starters.iterrows():
            if sid not in out_ids and sid not in dnp_ids:
                continue
            if rep_team.get(sid) != team:            # traded / mislabelled: not this team's vacancy
                continue
            pool = g.loc[(g.index != sid) & ~g.index.isin(list(out_ids))
                         & (g["last_week"] >= int(week) - BACKUP_RECENT_WEEKS)
                         & g["snap3"].notna()]
            if pos == "WR":
                pool = pool.loc[pool["snap3"] < WR_STARTER_SNAP]
            if len(pool) == 0:
                continue
            b = pool["snap3"].idxmax()
            rows.append({"team": team, "position": pos, "starter": sid, "backup": b,
                         "klass": "out" if sid in out_ids else "dnp",
                         "starter_opp": float(s["opp3"]), "starter_snap": float(s["snap3"]),
                         "backup_snap": float(pool.loc[b, "snap3"]),
                         "first_miss": bool(s["last_week"] == team_last.get(team))})
    return pd.DataFrame(rows, columns=cols)


def season_subset(season: int, hist: pd.DataFrame, report: pd.DataFrame) -> pd.DataFrame:
    played = set(zip(hist.loc[(hist["offense_pct"].fillna(0) > 0) | (hist["opps"] > 0), "week"],
                     hist.loc[(hist["offense_pct"].fillna(0) > 0) | (hist["opps"] > 0), "gsis_id"]))
    out = []
    for w in sorted(report["week"].unique()):
        v = vacancies(hist, report, int(w))
        if len(v):
            v = v.assign(week=int(w), season=season,
                         starter_sat=[(int(w), s) not in played for s in v["starter"]])
            out.append(v)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


# ---------------------------------------------------------------- systems

def p_sits(train_sub: pd.DataFrame) -> dict[str, float]:
    """P(starter has no stat line | report class), from training rows."""
    return {k: float(g["starter_sat"].mean()) for k, g in train_sub.groupby("klass")}


def attach(table: pd.DataFrame, sub: pd.DataFrame, probs: Mapping[str, float]) -> pd.DataFrame:
    """The table with `inherit_pickup` (0 off the subset) and the subset flag.
    A backup named twice in a week (two starters out) sums the two."""
    t = table.copy()
    if len(sub):
        s = sub.assign(inherit=[probs.get(k, np.nan) * o for k, o in
                                zip(sub["klass"], sub["starter_opp"])])
        agg = (s.groupby(["week", "backup"])
               .agg(inherit=("inherit", "sum"), first_miss=("first_miss", "max"),
                    klass=("klass", "min"), starter_sat=("starter_sat", "max"))
               .reset_index().rename(columns={"backup": "gsis_id"}))
        t = t.merge(agg, on=["week", "gsis_id"], how="left")
    else:
        t = t.assign(inherit=np.nan, first_miss=np.nan, klass=np.nan, starter_sat=np.nan)
    t["in_subset"] = t["inherit"].notna()
    t["inherit_pickup"] = t["inherit"].fillna(0.0)
    return t


def fit(train: pd.DataFrame, cols: Mapping[str, list[str]], extra, *, stack: bool) -> dict:
    return {pos: A.Ridge.fit(train.loc[train["position"] == pos],
                             train.loc[train["position"] == pos, "actual"],
                             cols[pos] + list(extra) + (["sleeper"] if stack else []))
            for pos in A.POSITIONS}


def predict(models: Mapping, df: pd.DataFrame) -> pd.Series:
    out = pd.Series(index=df.index, dtype="float64")
    for pos, m in models.items():
        sel = df["position"] == pos
        if sel.any():
            out[sel] = m.predict(df[sel]).clip(min=0.0)
    return out


def resid_slopes(train: pd.DataFrame) -> dict[str, float]:
    """k[pos]: least-squares slope (no intercept) of (actual - shipped) on
    inherit_pickup over the training subset."""
    k = {}
    for pos, g in train.loc[train["in_subset"]].groupby("position"):
        x, y = g["inherit_pickup"].to_numpy(), (g["actual"] - g["advshipped"]).to_numpy()
        k[pos] = float((x * y).sum() / (x * x).sum()) if (x * x).sum() > 0 else 0.0
    return k


# ---------------------------------------------------------------- metrics

def subset_pairwise(test: pd.DataFrame, systems) -> dict:
    """Start/sit pairwise over pairs (same week and position) with at least
    one subset row, comparators from the relevance universe (Sleeper >= 5)
    or the subset; ties in actual points skipped (projection_backtest)."""
    hits = {s: 0 for s in systems}
    n = 0
    pool = test.loc[test["in_subset"] | (test["sleeper"] >= 5.0)]
    for _, g in pool.groupby(["week", "position"]):
        if not g["in_subset"].any():
            continue
        rows = g.to_dict("records")
        for a, b in itertools.combinations(rows, 2):
            if not (a["in_subset"] or b["in_subset"]) or a["actual"] == b["actual"]:
                continue
            n += 1
            truth = a["actual"] > b["actual"]
            for s in systems:
                if a[s] != b[s] and (a[s] > b[s]) == truth:
                    hits[s] += 1
    return {"n": n, **{s: hits[s] / n if n else None for s in systems}}


def recency_bar(gains: Mapping[int, Mapping[str, float]], n_candidates: int,
                *, halflife: float = RECENCY_HALFLIFE, alpha: float = BAR_ALPHA) -> dict:
    """`ros_backtest.recency_bar` on the weekly metrics: `gains[season]` has
    `pairwise` (candidate - shipped) and `mae` (shipped - candidate, so
    positive is better). Passes when it wins the latest season on BOTH, the
    recency-weighted mean gain is positive on BOTH, and the sign-flip p of
    the weighted pairwise gain is <= alpha / n_candidates."""
    seasons = sorted(gains, reverse=True)
    if len(seasons) < 2:
        raise ValueError("the bar needs at least two held-out seasons")
    w = np.array([0.5 ** (i / halflife) for i in range(len(seasons))])
    out: dict = {"seasons": seasons, "weights": [round(float(x), 3) for x in w]}
    signs = np.array(list(itertools.product((1.0, -1.0), repeat=len(seasons))))
    for m in ("pairwise", "mae"):
        x = np.array([float(gains[s][m]) for s in seasons])
        if np.isnan(x).any():
            raise ValueError(f"missing {m} gain in a season: {dict(zip(seasons, x))}")
        out[f"wmean_{m}"] = float((w * x).sum() / w.sum())
        obs = float((w * x).sum())
        out[f"p_{m}"] = float(((signs * x * w).sum(axis=1) >= obs - 1e-12).mean())
        out[f"latest_{m}"] = float(x[0])
    out["p"] = out["p_pairwise"]
    out["threshold"] = alpha / max(1, n_candidates)
    out["checks"] = {"wins_latest": out["latest_pairwise"] > 0 and out["latest_mae"] > 0,
                     "weighted_gain_both": out["wmean_pairwise"] > 0 and out["wmean_mae"] > 0,
                     "beats_chance": out["p"] <= out["threshold"]}
    out["passes"] = all(out["checks"].values())
    return out


def score(test: pd.DataFrame, systems) -> dict:
    sub = test.loc[test["in_subset"]]
    pw = subset_pairwise(test, systems)
    res = {"rows": int(len(sub)), "pairs": pw["n"], "systems": {}}
    for s in systems:
        res["systems"][s] = {"mae": round(float((sub[s] - sub["actual"]).abs().mean()), 3),
                             "bias": round(float((sub[s] - sub["actual"]).mean()), 3),
                             "pairwise": round(pw[s], 4) if pw[s] is not None else None}
    for name, part in (("first_miss", sub.loc[sub["first_miss"] == True]),  # noqa: E712
                       ("starter_sat", sub.loc[sub["starter_sat"] == True])):  # noqa: E712
        res[name] = {"rows": int(len(part)), **{
            s: round(float((part[s] - part["actual"]).abs().mean()), 3) for s in systems}}
    # context only (post hoc slices: not candidates, never judged by the bar)
    res["by_slice"] = {f"{p}/{k}": {"rows": int(len(g)), **{
        s: round(float((g[s] - g["actual"]).abs().mean()), 3) for s in systems}}
        for (p, k), g in sub.groupby(["position", "klass"])}
    return res


# ------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seasons", type=int, nargs="+", default=list(SEASONS))
    ap.add_argument("--crosswalk", type=Path, default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--build-only", action="store_true")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=365 * 86400, verbose=False)
    bt = load_script("projection_backtest", "scripts/research/projection_backtest.py")
    ab = load_script("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
    cw = Crosswalk.from_csv(args.crosswalk)
    shipped = A.AdvancedModel.load()
    cols = {p: [c for c in shipped.adv[p].cols] for p in A.POSITIONS}

    tables, subsets = {}, {}
    for s in args.seasons:
        table, hist, report = season_inputs(s, cw, bt, ab, args.rebuild)
        tables[s] = table.loc[(table["week"] >= MIN_WEEK) & table["sleeper"].notna()].copy()
        subsets[s] = season_subset(s, hist, report)
        sub = subsets[s]
        print(f"{s}: {len(tables[s])} player-weeks; {len(sub)} starter-out/DNP rows "
              f"(out {int((sub['klass'] == 'out').sum())}, dnp {int((sub['klass'] == 'dnp').sum())}); "
              f"starter sat: out {sub.loc[sub['klass'] == 'out', 'starter_sat'].mean():.0%}, "
              f"dnp {sub.loc[sub['klass'] == 'dnp', 'starter_sat'].mean():.0%}", flush=True)
    if args.build_only:
        return 0

    systems = ["baseline", "sleeper", "advshipped", "adv+inherit", "advresid",
               "stackshipped", "stack+inherit"]
    folds, gains = {}, {}
    for test_season in args.seasons:
        train_seasons = [s for s in args.seasons if s != test_season]
        tsub = pd.concat([subsets[s] for s in train_seasons], ignore_index=True)
        probs = p_sits(tsub)
        train = pd.concat([attach(tables[s], subsets[s], probs) for s in train_seasons],
                          ignore_index=True)
        test = attach(tables[test_season], subsets[test_season], probs)
        m_ship = fit(train, cols, [], stack=False)
        m_inh = fit(train, cols, ["inherit_pickup"], stack=False)
        train["advshipped"] = predict(m_ship, train)
        k = resid_slopes(train)
        test["advshipped"] = predict(m_ship, test)
        test["adv+inherit"] = predict(m_inh, test)
        test["advresid"] = (test["advshipped"] + test["position"].map(k).fillna(0.0)
                            * test["inherit_pickup"]).clip(lower=0.0)
        test["stackshipped"] = predict(fit(train, cols, [], stack=True), test)
        test["stack+inherit"] = predict(fit(train, cols, ["inherit_pickup"], stack=True), test)
        f = score(test, systems)
        f["p_sits"] = {k_: round(v, 3) for k_, v in probs.items()}
        f["resid_k"] = {p: round(v, 3) for p, v in k.items()}
        f["inherit_coef_per_unit"] = {p: round(m_inh[p].beta[1 + m_inh[p].cols.index("inherit_pickup")]
                                               / m_inh[p].sd[m_inh[p].cols.index("inherit_pickup")], 3)
                                      for p in A.POSITIONS}
        folds[test_season] = f
        gains[test_season] = {c: {"pairwise": f["systems"][f"adv{c}"]["pairwise"]
                                  - f["systems"]["advshipped"]["pairwise"],
                                  "mae": f["systems"]["advshipped"]["mae"]
                                  - f["systems"][f"adv{c}"]["mae"]} for c in CANDIDATES}
        _print(test_season, f, systems)

    bars = {c: recency_bar({s: gains[s][c] for s in gains}, len(CANDIDATES)) for c in CANDIDATES}
    print("\nthe bar (wins latest on pairwise AND MAE; weighted gain > 0 on both; "
          f"sign-flip p <= {BAR_ALPHA}/{len(CANDIDATES)}):")
    for c, b in bars.items():
        print(f"  {c:<9} pairwise latest {b['latest_pairwise']:+.4f} weighted {b['wmean_pairwise']:+.4f} "
              f"p={b['p_pairwise']:.3f} | MAE latest {b['latest_mae']:+.3f} weighted "
              f"{b['wmean_mae']:+.3f} p={b['p_mae']:.3f} -> {'PASSES' if b['passes'] else 'fails'} "
              f"{b['checks']}")
    pooled = pd.concat([subsets[s].assign(season=s) for s in args.seasons], ignore_index=True)
    summary = {"by_class": {k_: {"rows": int(len(g)), "starter_sat": round(float(g["starter_sat"].mean()), 3)}
                            for k_, g in pooled.groupby("klass")},
               "by_position": {p: int(n) for p, n in pooled["position"].value_counts().items()}}
    print("pooled subset:", json.dumps(summary))
    if args.out:
        args.out.write_text(json.dumps({"seasons": args.seasons, "folds": folds, "gains": gains,
                                        "bars": bars, "summary": summary}, indent=1),
                            encoding="utf-8")
    return 0


def _print(season: int, f: dict, systems) -> None:
    print(f"\nheld out {season}: subset {f['rows']} rows, {f['pairs']} pairs; P(sits) {f['p_sits']}; "
          f"resid k {f['resid_k']}; +inherit coef/opp {f['inherit_coef_per_unit']}")
    print(f"{'system':<14} {'MAE':>6} {'bias':>7} {'pairwise':>9} {'MAE 1st miss':>13} {'MAE sat':>8}")
    for s in systems:
        m = f["systems"][s]
        print(f"{s:<14} {m['mae']:6.3f} {m['bias']:+7.3f} {(m['pairwise'] or 0):9.2%} "
              f"{f['first_miss'].get(s, float('nan')):13.3f} {f['starter_sat'].get(s, float('nan')):8.3f}")


if __name__ == "__main__":
    sys.exit(main())
