"""Kickers and team defenses: can the advanced model beat what we have? Out of sample.

    PYTHONPATH=src python scripts/research/k_def_backtest.py --train 2023 2024 --test 2025 [--save]

Same discipline as the skill-position model (advanced_model_backtest.py):
features come from `gridiron.models.advanced` (kicker_features_as_of,
defense_features_as_of — the builders the dashboard runs), each week built
from earlier weeks only; train on TRAIN seasons, score on TEST; identical
rows for every system.

  K    baseline = this repo's kicker projection (gridiron.projection, via
       gridiron.evaluate); naive = the kicker's points per game so far.
  DEF  there is NO existing defense projection (the board abstains), so the
       bar is the naive one: the defense's points per game so far.
  sleeper — Sleeper's published projection, scored with league weights
       (gridiron.shadow), with the same hindsight caveat as before.
  adv  — ridge on the advanced features, no Sleeper; stack — adv + Sleeper.

Metrics: MAE and the start/sit success rate — pairs of kickers (or of
defenses) in the same week, ordered as the actual points were.

`--save` refits adv and stack for K and DEF on all seasons and ADDS them to
src/gridiron/models/advanced_weights.json (the skill-position models are left
untouched), with the out-of-sample evidence and each position's residual SD.
"""
from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.evaluate import chronological_evaluation
from gridiron.ids import Crosswalk, nflverse_team, normalize_id
from gridiron.models import advanced as A
from gridiron.paths import RESEARCH_CACHE, REPO_ROOT
from gridiron.shadow import score_defense_projection, score_kicker_projection
from gridiron.usage import player_weeks

K_ADV = ["baseline", "k_ppg_season", "k_ppg_l3", "team_fga_pg", "team_xpa_pg",
         "implied", "spread", "dome"]
DEF_ADV = list(A.DEFENSE_FEATURES)


def _bt():
    spec = importlib.util.spec_from_file_location(
        "projection_backtest", REPO_ROOT / "scripts" / "research" / "projection_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["projection_backtest"] = mod
    spec.loader.exec_module(mod)
    return mod


def sleeper(bt, season: int, weeks, pos: str, cw: Crosswalk) -> pd.DataFrame:
    cache = RESEARCH_CACHE / f"sleeper_proj_{season}"
    rows = []
    for w in weeks:
        for p in bt.sleeper_week(season, int(w), pos, cache):
            st = p.get("stats") or {}
            pts = (score_kicker_projection if pos == "K" else score_defense_projection)(st)
            if pts is None or st.get("gp", 1) == 0:
                continue
            sid = normalize_id(p.get("player_id"))
            key = nflverse_team(sid) if pos == "DEF" else cw.gsis(sid)
            if key:
                rows.append({"week": int(w), "key": key, "sleeper": pts})
    return pd.DataFrame(rows).drop_duplicates(subset=["week", "key"])


def season_tables(season: int, cw: Crosswalk, bt) -> tuple[pd.DataFrame, pd.DataFrame]:
    import nflreadpy as nfl
    weekly = nfl.load_player_stats([season], summary_level="week").to_pandas()
    weekly = weekly.loc[weekly["season_type"] == "REG"]
    sched = nfl.load_schedules([season]).to_pandas()
    team = nfl.load_team_stats([season], summary_level="week").to_pandas()
    dh = A.defense_history(team, sched)
    kframe = player_weeks(weekly.loc[weekly["position"] == "K"], None, cw)
    krows = chronological_evaluation(kframe, sched).rows
    kweeks = kframe[["gsis_id", "week", "team", "league_points"]]
    weeks = sorted(set(krows["week"]) | set(dh["week"]))
    kf, df = [], []
    for w in weeks:
        if w < 2:
            continue
        kf.append(A.kicker_features_as_of(kweeks, dh, int(w), sched).assign(week=int(w)))
        d = A.defense_features_as_of(dh, int(w), sched)
        df.append(d.assign(week=int(w)))
    K = krows.merge(pd.concat(kf).drop(columns=["team"]), on=["gsis_id", "week"], how="left")
    K = K.merge(sleeper(bt, season, weeks, "K", cw).rename(columns={"key": "gsis_id"}),
                on=["week", "gsis_id"], how="left")
    D = pd.concat(df).merge(dh[["team", "week", "dst_points"]], on=["team", "week"], how="inner")
    D = D.rename(columns={"dst_points": "actual"})
    D["naive"] = D["dst_ppg_season"]
    D = D.merge(sleeper(bt, season, weeks, "DEF", cw).rename(columns={"key": "team"}),
                on=["week", "team"], how="left")
    K["naive"], K["season"], D["season"], D["position"] = K["ppg_to_date"], season, season, "DEF"
    return K, D


def pairwise(df: pd.DataFrame, systems, close: float | None = None) -> dict:
    hits, n = dict.fromkeys(systems, 0), 0
    for _, g in df.groupby("week"):
        for a, b in itertools.combinations(g.to_dict("records"), 2):
            if a["actual"] == b["actual"]:
                continue
            if close is not None and abs(a["sleeper"] - b["sleeper"]) > close:
                continue
            n += 1
            for s in systems:
                if a[s] != b[s] and (a[s] > b[s]) == (a["actual"] > b["actual"]):
                    hits[s] += 1
    return {"n": n, **{s: hits[s] / n if n else None for s in systems}}


def evaluate(name, train, test, adv_cols, systems_extra):
    adv = A.Ridge.fit(train, train["actual"], adv_cols)
    stack = A.Ridge.fit(train, train["actual"], adv_cols + ["sleeper"])
    test = test.copy()
    test["adv"] = adv.predict(test).clip(min=0) if name == "K" else adv.predict(test)
    test["stack"] = stack.predict(test).clip(min=0) if name == "K" else stack.predict(test)
    systems = systems_extra + ["sleeper", "adv", "stack"]
    pw, cc = pairwise(test, systems), pairwise(test, systems, close=2.0)
    res = {"rows": int(len(test)), "pairs": pw["n"], "close_pairs": cc["n"], "systems": {
        s: {"mae": round(float((test[s] - test["actual"]).abs().mean()), 3),
            "pairwise": round(pw[s], 4), "close": round(cc[s], 4)} for s in systems}}
    wk = []
    for w, g in test.groupby("week"):
        p = pairwise(g, [systems_extra[-1], "adv"])
        wk.append(p["adv"] - p[systems_extra[-1]])
    res["weeks_adv_beats_reference"] = f"{sum(x > 0 for x in wk)}/{len(wk)}"
    print(f"\n{name}: {len(test)} rows, {pw['n']} pairs ({cc['n']} close)")
    print(f"{'system':<10} {'MAE':>6} {'pairwise':>9} {'close':>7}")
    for s in systems:
        m = res["systems"][s]
        print(f"{s:<10} {m['mae']:6.2f} {m['pairwise']:9.1%} {m['close']:7.1%}")
    print(f"adv beat {systems_extra[-1]} in {res['weeks_adv_beats_reference']} weeks")
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--train", type=int, nargs="+", default=[2023, 2024])
    ap.add_argument("--test", type=int, default=2025)
    ap.add_argument("--min-week", type=int, default=4)
    ap.add_argument("--crosswalk", type=Path,
                    default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--save", action="store_true")
    args = ap.parse_args(argv)
    bt = _bt()
    cw = Crosswalk.from_csv(args.crosswalk)
    tabs = {s: season_tables(s, cw, bt) for s in (*args.train, args.test)}
    keep = lambda d: d.loc[(d["week"] >= args.min_week) & d["sleeper"].notna()  # noqa: E731
                           & d["actual"].notna()]
    Ktr = keep(pd.concat([tabs[s][0] for s in args.train]))
    Kte = keep(tabs[args.test][0])
    Dtr = keep(pd.concat([tabs[s][1] for s in args.train]))
    Dte = keep(tabs[args.test][1])
    kres = evaluate("K", Ktr, Kte, K_ADV, ["naive", "baseline"])
    dres = evaluate("DEF", Dtr, Dte, DEF_ADV, ["naive"])
    if args.save:
        model = A.AdvancedModel.load()
        allK, allD = pd.concat([Ktr, Kte]), pd.concat([Dtr, Dte])
        adv, stack = dict(model.adv), dict(model.stack)
        meta = dict(model.meta)
        sds = dict(meta.get("resid_sd") or {})
        for pos, data, cols in (("K", allK, K_ADV), ("DEF", allD, DEF_ADV)):
            adv[pos] = A.Ridge.fit(data, data["actual"], cols)
            stack[pos] = A.Ridge.fit(data, data["actual"], cols + ["sleeper"])
            sds[pos] = round(float(np.std(data["actual"] - adv[pos].predict(data))), 3)
        meta["resid_sd"] = sds
        meta["k_def_evidence"] = {"train": args.train, "test": args.test,
                                  "K": kres, "DEF": dres,
                                  "doc": "docs/research/K_DEF_BACKTEST_2025.md"}
        A.AdvancedModel(adv, stack, meta).save()
        print(f"\nsaved K and DEF into {A.WEIGHTS_PATH.relative_to(REPO_ROOT)}; "
              f"residual SD {sds}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
