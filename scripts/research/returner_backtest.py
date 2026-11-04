"""Returning players: can last season project a player with no game this season?

    PYTHONPATH=src python scripts/research/returner_backtest.py [--boot 2000]

The page projects from THIS season's box scores only (`gridiron.projection`
abstains with "no admissible box scores"), so a player who has not played
yet this season — back from a suspension, the exempt list, IR, a holdout —
has no number, is never ranked on the Free Agent Radar, and leaves P(win)
abstaining when he starts. 2026 week 7: a former starter sat in the free-agent
pool for five days unranked and another manager claimed him.

POPULATION (pre-registered, written before any result was seen): every
player-season 2019-2025 whose FIRST game of the regular season (first box-
score row) is in week >= 2, at QB/RB/WR/TE, with >= 4 games the previous
regular season. That first game is the only week a returner number would be
served: from his second game on, this season's evidence exists and the
baseline takes over. Rows are players who PLAYED (the usual caveat: a
returner listed but inactive is the availability layer's job, not this one's).

CANDIDATES, each scaled by a per-position discount k (grid 0.30-1.30) fit by
minimum MAE on the FIT seasons 2019-2022:
  ppg_all    k x last season's league points per game (all games)
  ppg_last8  k x last season's points per game over his last 8 games
  base_prior k x `gridiron.projection.project` run on LAST season's evidence
             (usage shares and efficiency, `build_evidence` over the whole
             prior season) with THIS week's implied team total — the
             page's own baseline, fed last season instead of this one
The candidate with the lowest pooled fit MAE is the one tested.

NULL (what the page can say today): per-position constant = the mean actual
of returners at that position in the fit seasons. The page has no number for
these players at all; a constant is the honest "knows nothing about him".

BAR, pre-registered: on the TEST seasons 2023-2025 the chosen candidate
  1. has lower MAE than the null (bootstrap CI of the difference excludes 0),
  2. ranks players: Spearman within position, pooled, CI excludes 0,
  3. |bias| <= 1.5 points.
Sleeper's published projection for the same player-weeks (scored with the
league's rules) is reported for context, not as a bar.

Output: printed tables and data/research/cache/tables/returner_backtest.json
(gitignored). No league data, no player-map call; joins by gsis id (Sleeper
ids through the crosswalk).

RESULT (2026-10-21, docs/research/RETURNER_BACKTEST.md): the bar CLEARED on
the pooled test (ppg_all; MAE 2.45 vs null 3.32, Spearman 0.48, bias +1.37)
and the candidate is NOT shipped. The post-hoc table this script now also
prints — calibration by last season's points per game, descriptive, never
part of the bar — shows k (0.30-0.37, at the grid floor for QB and RB)
under-projecting last season's best scorers by about 5 points (n=14), the
very players a returner number exists for; Sleeper's number beat it on the
same rows. The `--save` path that would have written the discounts is gone.
"""
from __future__ import annotations

import argparse
import json
import sys

import numpy as np
import pandas as pd

from gridiron.ids import CROSSWALK_URL, Crosswalk
from gridiron.paths import REPO_ROOT, RESEARCH_CACHE
from gridiron.projection import build_evidence, project
from gridiron.usage import player_weeks
from gridiron.weekly import schedule_index

from research_common import load_script

POSITIONS = ("QB", "RB", "WR", "TE")
SEASONS = tuple(range(2019, 2026))
FIT, TEST = (2019, 2020, 2021, 2022), (2023, 2024, 2025)
MIN_PRIOR_GAMES = 4
K_GRID = np.round(np.arange(0.30, 1.30 + 1e-9, 0.01), 2)
OUT = RESEARCH_CACHE / "tables" / "returner_backtest.json"
#: Post hoc, descriptive only: last season's points per game bands.
PRIOR_BANDS = (0, 5, 10, 15, 100)
SEED = 20261021


def crosswalk() -> Crosswalk:
    path = RESEARCH_CACHE / "crosswalk_db_playerids.csv"
    if not path.exists():
        import urllib.request
        path.parent.mkdir(parents=True, exist_ok=True)
        req = urllib.request.Request(CROSSWALK_URL, headers={"User-Agent": "gridiron-research"})
        with urllib.request.urlopen(req, timeout=120) as r:      # noqa: S310
            path.write_bytes(r.read())
    return Crosswalk.from_csv(path)


def load(seasons) -> tuple[dict[int, pd.DataFrame], dict[int, pd.DataFrame]]:
    import nflreadpy as nfl
    from nflreadpy.config import update_config
    update_config(cache_mode="filesystem", cache_dir=RESEARCH_CACHE / "nflreadpy",
                  cache_duration=30 * 86400, verbose=False)
    empty = Crosswalk({}, {})
    weeks, scheds = {}, {}
    for s in seasons:
        w = nfl.load_player_stats([s], summary_level="week").to_pandas()
        w = w.loc[w["season_type"] == "REG"].copy()
        weeks[s] = player_weeks(w, None, empty)
        scheds[s] = nfl.load_schedules([s]).to_pandas()
        print(f"  {s}: {len(weeks[s])} player-weeks", flush=True)
    return weeks, scheds


def population(weeks, scheds) -> pd.DataFrame:
    rows = []
    for s in SEASONS:
        prior, cur = weeks[s - 1], weeks[s]
        prior = prior.loc[prior["position"].isin(POSITIONS)]
        pev = build_evidence(prior, through_week=None)
        g = prior.sort_values("week").groupby("gsis_id")
        summ = pd.DataFrame({"prior_games": g["week"].nunique(),
                             "ppg_all": g["league_points"].mean(),
                             "ppg_last8": g["league_points"].apply(lambda x: x.tail(8).mean())})
        first = cur.sort_values("week").groupby("gsis_id").head(1)
        first = first.loc[(first["week"] >= 2) & first["position"].isin(POSITIONS)]
        for r in first.itertuples():
            gid = str(r.gsis_id)
            if gid not in summ.index or summ.at[gid, "prior_games"] < MIN_PRIOR_GAMES:
                continue
            games = schedule_index(scheds[s], int(r.week))
            game = games.get(str(r.team))
            p = project(pev.players.get(gid), position=str(r.position), week=int(r.week),
                        implied_total=game.implied_total if game else None, evidence=pev)
            rows.append({"season": s, "week": int(r.week), "gsis_id": gid,
                         "position": str(r.position), "team": str(r.team),
                         "actual": float(r.league_points),
                         "prior_games": int(summ.at[gid, "prior_games"]),
                         "ppg_all": float(summ.at[gid, "ppg_all"]),
                         "ppg_last8": float(summ.at[gid, "ppg_last8"]),
                         "base_prior": float(p.mean) if p.usable else np.nan})
    return pd.DataFrame(rows)


def fit_k(frame: pd.DataFrame, col: str) -> dict[str, float]:
    out = {}
    for pos, g in frame.groupby("position"):
        g = g.dropna(subset=[col])
        maes = [np.mean(np.abs(g["actual"] - k * g[col])) for k in K_GRID]
        out[pos] = float(K_GRID[int(np.argmin(maes))])
    return out


def apply_k(frame: pd.DataFrame, col: str, k: dict[str, float]) -> pd.Series:
    return frame[col] * frame["position"].map(k)


def spearman_pooled(frame: pd.DataFrame, pred: str) -> float:
    vals = []
    for _, g in frame.dropna(subset=[pred]).groupby("position"):
        if len(g) > 3 and g[pred].nunique() > 1:
            vals.append((g[pred].rank().corr(g["actual"].rank()), len(g)))
    if not vals:
        return float("nan")
    return float(sum(v * n for v, n in vals) / sum(n for _, n in vals))


def boot(frame: pd.DataFrame, fn, n: int, seed: int) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    idx = np.arange(len(frame))
    vals = []
    for _ in range(n):
        vals.append(fn(frame.iloc[rng.choice(idx, size=len(idx), replace=True)]))
    vals = np.array([v for v in vals if v == v])
    return (round(float(np.quantile(vals, 0.025)), 4), round(float(np.quantile(vals, 0.975)), 4))


def sleeper_context(test: pd.DataFrame, cw: Crosswalk) -> pd.Series:
    pb = load_script("projection_backtest", "scripts/research/projection_backtest.py")
    out = pd.Series(np.nan, index=test.index)
    for s, g in test.groupby("season"):
        sl = pb.sleeper_projections(int(s), sorted(g["week"].unique()), cw)
        if len(sl) == 0:
            continue
        m = g.reset_index().merge(sl, on=["week", "gsis_id"], how="left").set_index("index")
        out.loc[m.index] = m["sleeper"]
    return out


def calibration_by_prior(test: pd.DataFrame) -> list[dict]:
    """POST HOC, descriptive (not part of the pre-registered bar): mean actual
    vs mean prediction by last season's points per game, with Sleeper's
    number on the rows it covers."""
    rows = []
    bands = pd.cut(test["ppg_all"], list(PRIOR_BANDS), right=False)
    for band, g in test.groupby(bands, observed=True):
        row = {"prior_ppg": str(band), "n": len(g),
               "actual_mean": round(float(g["actual"].mean()), 2),
               "pred_mean": round(float(g["pred"].mean()), 2),
               "bias": round(float((g["actual"] - g["pred"]).mean()), 2),
               "mae": round(float((g["actual"] - g["pred"]).abs().mean()), 2)}
        if "sleeper" in g:
            sl = g.dropna(subset=["sleeper"])
            row.update({"sleeper_n": len(sl),
                        "sleeper_mean": round(float(sl["sleeper"].mean()), 2),
                        "sleeper_bias": round(float((sl["actual"] - sl["sleeper"]).mean()), 2),
                        "sleeper_mae": round(float((sl["actual"] - sl["sleeper"]).abs().mean()),
                                             2)})
        rows.append(row)
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--boot", type=int, default=2000)
    ap.add_argument("--no-sleeper", action="store_true")
    args = ap.parse_args(argv)

    weeks, scheds = load(range(SEASONS[0] - 1, SEASONS[-1] + 1))
    pop = population(weeks, scheds)
    fit, test = pop.loc[pop["season"].isin(FIT)].copy(), pop.loc[pop["season"].isin(TEST)].copy()
    print(f"\npopulation: {len(pop)} returner first games ({len(fit)} fit, {len(test)} test); "
          f"by position {pop['position'].value_counts().to_dict()}")

    null = fit.groupby("position")["actual"].mean().to_dict()
    cands = {}
    for col in ("ppg_all", "ppg_last8", "base_prior"):
        k = fit_k(fit, col)
        f_mae = float(np.mean(np.abs(fit["actual"] - apply_k(fit, col, k))))
        cands[col] = {"k": k, "fit_mae": round(f_mae, 4)}
        print(f"  {col:10} k {k}  fit MAE {f_mae:.3f}")
    chosen = min(cands, key=lambda c: cands[c]["fit_mae"])
    k = cands[chosen]["k"]
    print(f"chosen on the fit seasons: {chosen}")

    test["null"] = test["position"].map(null)
    for col in ("ppg_all", "ppg_last8", "base_prior"):
        test[f"p_{col}"] = apply_k(test, col, cands[col]["k"])
    test["pred"] = test[f"p_{chosen}"]
    if not args.no_sleeper:
        test["sleeper"] = sleeper_context(test, crosswalk())

    def mae(f, c):
        return float(np.mean(np.abs(f["actual"] - f[c])))

    res = {"population": len(pop), "fit_n": len(fit), "test_n": len(test),
           "by_position_test": test["position"].value_counts().to_dict(),
           "candidates": cands, "chosen": chosen, "null_by_position": null, "test": {}}
    systems = ["null", "p_ppg_all", "p_ppg_last8", "p_base_prior", "pred"] + \
        (["sleeper"] if "sleeper" in test and test["sleeper"].notna().any() else [])
    print(f"\n{'system':14} {'n':>5} {'MAE':>7} {'bias':>7} {'Spearman':>9}")
    for c in systems:
        f = test.dropna(subset=[c])
        res["test"][c] = {"n": len(f), "mae": round(mae(f, c), 4),
                          "bias": round(float((f["actual"] - f[c]).mean()), 4),
                          "spearman": round(spearman_pooled(f, c), 4)}
        r = res["test"][c]
        print(f"{c:14} {r['n']:5d} {r['mae']:7.3f} {r['bias']:+7.3f} {r['spearman']:9.3f}")
    both = test.dropna(subset=["pred", "null"])
    d_ci = boot(both, lambda f: mae(f, "null") - mae(f, "pred"), args.boot, SEED)
    s_ci = boot(both, lambda f: spearman_pooled(f, "pred"), args.boot, SEED + 1)
    bias = res["test"]["pred"]["bias"]
    cleared = bool(d_ci[0] > 0 and s_ci[0] > 0 and abs(bias) <= 1.5)
    res.update({"mae_gain_vs_null_ci": d_ci, "spearman_ci": s_ci, "bar_cleared": cleared})
    if "sleeper" in test:
        sb = test.dropna(subset=["sleeper", "pred"])
        res["vs_sleeper_same_rows"] = {"n": len(sb), "pred_mae": round(mae(sb, "pred"), 4),
                                       "sleeper_mae": round(mae(sb, "sleeper"), 4),
                                       "pred_spearman": round(spearman_pooled(sb, "pred"), 4),
                                       "sleeper_spearman": round(spearman_pooled(sb, "sleeper"), 4)}
        print(f"same rows as Sleeper: {res['vs_sleeper_same_rows']}")
    print(f"\nMAE gain vs null CI {d_ci}; Spearman CI {s_ci}; bias {bias:+.2f} -> "
          f"bar {'CLEARED' if cleared else 'NOT cleared'}")

    res["post_hoc_by_prior_ppg"] = calibration_by_prior(test)
    print("\nPOST HOC, descriptive (not the bar): by last season's points per game")
    print(pd.DataFrame(res["post_hoc_by_prior_ppg"]).to_string(index=False))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
