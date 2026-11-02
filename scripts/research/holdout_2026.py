"""The 2026 holdout: blocks that passed the historical bar, scored on 2026 weeks.

    PYTHONPATH=src python scripts/research/holdout_2026.py [--through-week N] [--write]
    PYTHONPATH=src python scripts/research/holdout_2026.py --block RB-03 --harness-test

Ground rule 3 of docs/research/MODEL_ROADMAP.md: with 78 hypotheses some will
pass the historical bar by luck, so a block that passes is scored on the
untouched 2026 season, record-only, before it may serve. This script is that
record. For every block whose committed result passes (results/<ID>.json),
it fits the shipped set and the block on all of 2019-2025, predicts 2026 weeks
4..N from the same frozen pipeline (season_table for 2026, Sleeper's 2026
projections, the block's own build), and scores both on identical rows, week
by week and pooled, on the positions the block passed.

Only block_runner.py blocks are scored here. A pass written by another
harness (its results JSON names a "harness" other than block_runner.py, or
carries a "harness_blob": the early-season and distribution harnesses), or
a pass with no module in scripts/research/blocks/, is skipped with one line
naming it. Its 2026 holdout, if any, is its own harness's
(results/<ID>-2026.json, as for WR-10).

The live forward check is the weeks played AFTER the block's result was
committed (`live` in the output): a block is eligible for a serving
decision only with at least two such weeks and better pooled pairwise AND MAE
on the holdout. Eligible is not served: serving is a DECISIONS.md row, and
nothing changes after the week-14 board (ground rule 8). Weeks 4..8 of 2026
were played before any block existed, so they are a clean holdout too, but
they are not "live".

`--write` saves docs/research/blocks/holdout_2026.json (small, committed).
`--harness-test` runs one named block regardless of its verdict and writes
nothing, to test this script; never use it to look at a failed block's 2026
numbers with a view to redesigning it.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import block_runner as R
from gridiron.ids import Crosswalk
from gridiron.models import advanced as A
from gridiron.paths import REPO_ROOT, RESEARCH_CACHE

SEASON = 2026
HOLDOUT_TABLES = RESEARCH_CACHE / "tables" / "holdout"
OUT = R.PREREG_DIR / "holdout_2026.json"
RUNNER = "block_runner.py"


def skip_reason(res: dict, blocks_dir) -> str | None:
    """Why a passing result is not a block_runner.py block, or None when it is.
    Runner results name no harness; the other harnesses say who scored them."""
    block = res.get("block")
    named = str(res.get("harness") or "").split()
    if named and Path(named[0]).name != RUNNER:
        return f"scored by {named[0]}, not {RUNNER}"
    if not named and res.get("harness_blob"):
        kind = f", kind {res['kind']}" if res.get("kind") else ""
        return f"scored by another harness (harness_blob {res['harness_blob']}{kind}), not {RUNNER}"
    if not (Path(blocks_dir) / f"{block}.py").exists():
        return f"no block module scripts/research/blocks/{block}.py"
    return None


def passing_results(results_dir=None, blocks_dir=None) -> tuple[dict, dict]:
    """The committed results that pass, split in two: {block: (positions, path)}
    for the block_runner.py blocks this script scores, and {block: reason} for
    the rest."""
    results_dir = Path(results_dir or R.RESULTS_DIR)
    blocks_dir = Path(blocks_dir or R.BLOCKS_DIR)
    passing, skipped = {}, {}
    for p in sorted(results_dir.glob("*.json")):
        res = json.loads(p.read_text(encoding="utf-8"))
        if not res.get("passed"):
            continue
        res.setdefault("block", p.stem)
        why = skip_reason(res, blocks_dir)
        if why:
            skipped[res["block"]] = why
        else:
            passing[res["block"]] = (res["passed"], p)
    return passing, skipped


def table_2026(crosswalk: Crosswalk, refresh: bool) -> tuple[pd.DataFrame, dict]:
    """The 2026 skill and K/DEF tables, built like every historical season."""
    HOLDOUT_TABLES.mkdir(parents=True, exist_ok=True)
    sp, kp, dp = (HOLDOUT_TABLES / f"{n}_{SEASON}.parquet" for n in ("season", "K", "DEF"))
    bt = R.load_script("projection_backtest", "scripts/research/projection_backtest.py")
    if refresh or not sp.exists():
        R._nfl()
        ab = R.load_script("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
        ab.season_table(SEASON, crosswalk, bt).to_parquet(sp, index=False)
    if refresh or not (kp.exists() and dp.exists()):
        R._nfl()
        kd = R.load_script("k_def_backtest", "scripts/research/k_def_backtest.py")
        K, D = kd.season_tables(SEASON, crosswalk, bt)
        K.to_parquet(kp, index=False)
        D.to_parquet(dp, index=False)
    skill = R.attach_keys(R._keep(pd.read_parquet(sp)), SEASON)
    special = {}
    for pos, path in (("K", kp), ("DEF", dp)):
        t = pd.read_parquet(path)
        t = t.loc[(t["week"] >= R.MIN_WEEK) & t["sleeper"].notna() & t["actual"].notna()]
        special[pos] = R.attach_special_keys(t, pos, SEASON)
    return skill, special


def committed_at(path) -> datetime | None:
    rel = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    out = subprocess.run(["git", "-C", str(REPO_ROOT), "log", "-1", "--format=%cI", "--", rel],
                         capture_output=True, text=True).stdout.strip()
    return datetime.fromisoformat(out) if out else None


def first_kickoffs() -> dict[int, datetime]:
    sched = R._nfl().load_schedules([SEASON]).to_pandas()
    sched = sched.loc[sched["game_type"] == "REG"]
    when = pd.to_datetime(sched["gameday"] + " " + sched["gametime"]).dt.tz_localize(
        "America/New_York").dt.tz_convert("UTC")
    return {int(w): t.to_pydatetime() for w, t in when.groupby(sched["week"]).min().items()}


def score_block(block_id: str, positions, crosswalk, hist_skill, hist_special, skill26, special26,
                base, through: int) -> dict:
    mod, _ = R.load_block(block_id)
    cols = ["season", "week", "gsis_id", "position", "team", "opponent", "game_id", "home"]
    keys = pd.concat([*(t[cols] for t in hist_skill.values()), skill26[cols]], ignore_index=True)
    sk = {p: pd.concat([*(t[cols] for t in hist_special[p].values()), special26[p][cols]],
                       ignore_index=True) for p in ("K",)}
    sk["DEF"] = pd.concat([*(t[[c for c in cols if c != "gsis_id"]] for t in hist_special["DEF"].values()),
                           special26["DEF"][[c for c in cols if c != "gsis_id"]]], ignore_index=True)
    ctx = R.Context(keys, crosswalk, special_keys=sk)
    frame, key = R.build_block(mod, ctx)
    out = {"block": block_id, "positions": list(positions), "weeks": {}, "pooled": {}}
    special = bool(set(mod.COLUMNS) & set(R.SPECIAL))
    for pos in positions:
        cols_pos = {pos: mod.COLUMNS[pos]}
        if special:
            tabs = {**hist_special[pos], SEASON: special26[pos]}
            merged, extra = R.prepare(frame, tabs, cols_pos, key)
            base_cols = R.K_ADV if pos == "K" else R.DEF_ADV
            train = pd.concat([merged[s] for s in R.SEASONS])
            test = merged[SEASON].copy()
            tr, te = slice(None), slice(None)
        else:
            tabs = {**hist_skill, SEASON: skill26}
            merged, extra = R.prepare(frame, tabs, cols_pos, key)
            base_cols = base[pos]
            train = pd.concat([merged[s] for s in R.SEASONS])
            train = train.loc[train["position"] == pos]
            test = merged[SEASON]
            test = test.loc[test["position"] == pos].copy()
        test = test.loc[test["week"] <= through]
        m0 = A.Ridge.fit(train, train["actual"], base_cols)
        m1 = A.Ridge.fit(train, train["actual"], base_cols + extra[pos])
        test["shipped"], test["block"] = R.predict(m0, test), R.predict(m1, test)
        if pos != "DEF":
            test[["shipped", "block"]] = test[["shipped", "block"]].clip(lower=0)
        by, rel = (("week",), False) if special else (("week", "position"), True)
        out["pooled"][pos] = R._metrics(test, ["sleeper", "shipped", "block"], by=by, relevant=rel)
        out["weeks"][pos] = {str(int(w)): R._metrics(g, ["shipped", "block"], by=by, relevant=rel)
                             for w, g in test.groupby("week")}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--through-week", type=int, default=18)
    ap.add_argument("--refresh", action="store_true", help="rebuild the 2026 tables")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--block", action="append", default=[])
    ap.add_argument("--harness-test", action="store_true")
    ap.add_argument("--crosswalk", default=str(R.CROSSWALK))
    args = ap.parse_args(argv)
    if args.block and not args.harness_test:
        raise SystemExit("--block is for --harness-test only; the holdout scores passing blocks")
    found, skipped = passing_results()
    passing = {b: (positions, committed_at(p)) for b, (positions, p) in found.items()}
    if args.harness_test:
        passing, skipped = {b: (list(R.load_block(b)[0].COLUMNS), None) for b in args.block}, {}
    for b, why in skipped.items():
        print(f"skip {b}: {why}")
    if not passing:
        print("no block_runner.py block has passed the historical bar: nothing to hold out")
        if args.write:
            OUT.write_text(json.dumps({"season": SEASON, "blocks": {},
                                       "note": "no passing block_runner.py block",
                                       "skipped": skipped}, indent=1) + "\n", encoding="utf-8")
        return 0
    crosswalk = Crosswalk.from_csv(args.crosswalk)
    hist_skill = R.load_skill_tables(crosswalk)
    hist_special = R.load_kdef_tables(crosswalk)
    skill26, special26 = table_2026(crosswalk, args.refresh)
    played = sorted(int(w) for w in skill26["week"].unique())
    through = min(args.through_week, max(played))
    kick = first_kickoffs()
    base = R.base_columns()
    report = {"season": SEASON, "through_week": through,
              "built": datetime.now(timezone.utc).isoformat(timespec="seconds"), "blocks": {},
              "skipped": skipped}
    for block_id, (positions, passed_at) in passing.items():
        r = score_block(block_id, positions, crosswalk, hist_skill, hist_special, skill26,
                        special26, base, through)
        live = [w for w in played if w <= through and passed_at is not None
                and kick.get(w) and kick[w] > passed_at]
        r["result_committed"] = passed_at.isoformat() if passed_at else None
        r["live_weeks"] = live
        verdicts = {}
        for pos in positions:
            m = r["pooled"][pos]
            better = (m["block"]["pairwise"] > m["shipped"]["pairwise"]
                      and m["block"]["mae"] < m["shipped"]["mae"])
            verdicts[pos] = {"better_on_both": bool(better), "eligible": bool(better and len(live) >= 2)}
            print(f"{block_id} {pos} 2026 weeks 4-{through}: shipped "
                  f"{m['shipped']['pairwise']:.2%}/{m['shipped']['mae']:.3f}  block "
                  f"{m['block']['pairwise']:.2%}/{m['block']['mae']:.3f}  ({m['pairs']} pairs); "
                  f"live weeks {live or 'none yet'} -> "
                  f"{'ELIGIBLE for a serving decision' if verdicts[pos]['eligible'] else 'not eligible'}")
        r["verdict"] = verdicts
        report["blocks"][block_id] = r
    if args.write and not args.harness_test:
        OUT.write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
