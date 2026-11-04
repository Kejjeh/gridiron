"""Does the offline harness reproduce the live 2026 shoot-out, and does the
live board compute what the harness computes?

    PYTHONPATH=src python scripts/research/live_reproduce_2026.py [--skip-2025] [--out x.json]

Research only: never imported by src/gridiron, writes nothing under src/,
scripts/weekly/ or the ledger. Season tables are built once with
`advanced_model_backtest.season_table` (the SAME feature builder the
dashboard runs) and cached to data/research/cache/tables/ (gitignored).
Sleeper's CURRENT numbers for past weeks are fetched only so the table
builds; they are post-hoc and score nothing here. The ONLY Sleeper numbers
scored are the pre-kickoff shadow projections each live board recorded.

Steps (one section of output each):
  1. tables   2026 (and 2025) player-week tables, features from earlier weeks.
  2. models   baseline (table column), adv_new (shipped weights),
              adv_old (pre-PR#12 weights, `git show 601abed:...`).
  3. live     for each board, the shoot-out pool rebuilt with
              grade_week.shootout's rules, joined to the table by gsis id and
              week; pairwise (projection_backtest.pairwise semantics, pairs
              within week x position among Sleeper >= 5, ties skipped) and MAE
              for the live systems and the harness systems on the SAME rows,
              beside the grades the ledger recorded.
  4. match    page `projected` vs harness adv, contenders.baseline_v1 vs
              harness baseline, per gsis id/week; plus an ablation that asks
              which missing input block would make the harness look like the
              page.
  5. inputs   feature availability (share non-null) for 2026 weeks 4-5 vs
              2025 weeks 4-5, and on the live universe.
No player names anywhere in the output (the repo is public).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

from gridiron.ids import Crosswalk, normalize_id
from gridiron.models import advanced as A
from gridiron.paths import LEDGER, REPO_ROOT, RESEARCH_CACHE

# nflreadpy reads its settings from the environment at import time; cache the
# bulk pulls to disk beside the other research caches (gitignored).
os.environ.setdefault("NFLREADPY_CACHE", "filesystem")
os.environ.setdefault("NFLREADPY_CACHE_DIR", str(RESEARCH_CACHE / "nflreadpy"))

from research_common import load_script  # noqa: E402

TABLES = RESEARCH_CACHE / "tables"
SKILL = ("QB", "RB", "WR", "TE")
SHOOTOUT_MIN = 5.0
OLD_COMMIT = "601abed"
CLOUD = REPO_ROOT / "data" / "outputs" / "cloud"
BOARDS = (
    ("w4_old", 4, "old", CLOUD / "0098_37853128349/ledger/decisions/season2026/"
     "week04_20261004T124837Z_c11d026e.json"),
    ("w5_old", 5, "old", CLOUD / "0125_38636106757/ledger/decisions/season2026/"
     "week05_20261011T130514Z_88534aa5.json"),
    ("w5_new", 5, "new", LEDGER / "decisions/season2026/week05_20261011T163751Z_a073fe54.json"),
)
#: The grade the task hands over for the week-5 new-model board (not in the
#: ledger: the ledger grades one archive per week).
REPORTED_W5_NEW = {"page_pairwise": 0.615, "baseline_v1_pairwise": 0.669,
                   "sleeper_pairwise": 0.650, "stack_pairwise": 0.653, "shootout_pairs": 317}
AVAIL = ["xfp_l3", "xfp_season", "fpoe_season", "snap_l3", "tgt_share_l3", "carries_l3",
         "depth_rank", "ngs_sep", "ngs_yacoe", "ngs_ryoe", "ngs_cpoe", "xfp_last",
         "opp_last", "snap_last", "implied", "def_allowed", "vacated_pickup",
         "practice_dnp", "questionable"]
#: Which NGS column a position's model reads (coverage is partial by design:
#: Next Gen Stats publish players above a volume threshold).
NGS_FOR = {"QB": "ngs_cpoe", "RB": "ngs_ryoe", "WR": "ngs_sep", "TE": "ngs_sep"}
#: Input blocks a live build could have lacked; the ablation sets each to NaN
#: (the ridge then fills the training mean, exactly what the page would do).
ABLATE = {
    "none": [],
    "depth": ["depth_rank"],
    "xfp": ["xfp_l3", "xfp_season", "fpoe_season", "xfp_last"],
    "ngs": ["ngs_sep", "ngs_yacoe", "ngs_ryoe", "ngs_cpoe"],
    "role_last": ["xfp_last", "opp_last", "snap_last"],
    "practice": ["practice_dnp", "practice_limited", "questionable", "vacated_pickup"],
    "schedule": ["implied", "def_allowed"],
    "snaps": ["snap_l3", "snap_last", "tgt_share_l3", "ay_share_l3", "carries_l3"],
}


# ------------------------------------------------------------------ tables

def table(season: int, cw: Crosswalk, bt, amb) -> pd.DataFrame:
    TABLES.mkdir(parents=True, exist_ok=True)
    path = TABLES / f"season{season}_table.parquet"
    if path.exists():
        t = pd.read_parquet(path)
        print(f"  {season}: {len(t)} player-weeks from cache {path.relative_to(REPO_ROOT)}")
        return t
    t = amb.season_table(season, cw, bt)
    t.to_parquet(path, index=False)
    return t


def old_weights_path() -> Path:
    TABLES.mkdir(parents=True, exist_ok=True)
    path = TABLES / f"advanced_weights_{OLD_COMMIT}.json"
    if not path.exists():
        blob = subprocess.run(["git", "show", f"{OLD_COMMIT}:src/gridiron/models/advanced_weights.json"],
                              cwd=REPO_ROOT, check=True, capture_output=True, text=True).stdout
        path.write_text(blob, encoding="utf-8")
    return path


# ------------------------------------------------------------------ models

def predict_adv(model: A.AdvancedModel, df: pd.DataFrame, *, nan_cols=()) -> pd.Series:
    """The model's `adv` mean per row, clipped at 0 like `AdvancedModel.predict`."""
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    work = df.copy()
    for c in nan_cols:
        work[c] = np.nan
    for pos in SKILL:
        m = model.adv.get(pos)
        sel = work["position"] == pos
        if m is None or not sel.any():
            continue
        out[sel] = np.maximum(0.0, m.predict(work.loc[sel]))
    return out


def predict_stack(model: A.AdvancedModel, df: pd.DataFrame, sleeper: pd.Series,
                  adv: pd.Series) -> pd.Series:
    """The `stack` mean with the pre-kickoff Sleeper number (and, for the
    two-stage form, the adv mean) as inputs — `AdvancedContext.stacked`."""
    work = df.copy()
    work["sleeper"] = sleeper.to_numpy(dtype="float64")
    work["adv"] = adv.to_numpy(dtype="float64")
    out = pd.Series(np.nan, index=df.index, dtype="float64")
    for pos in SKILL:
        m = model.stack.get(pos)
        sel = work["position"] == pos
        if m is None or not sel.any():
            continue
        out[sel] = np.maximum(0.0, m.predict(work.loc[sel]))
    return out


# -------------------------------------------------------------------- live

_EXPLAIN = re.compile(r"= ([\d.]+) baseline -> ([\d.]+) with advanced stats")
_EXPLAIN_BASE = re.compile(r"= ([\d.]+) baseline")


def live_pool(board: dict) -> pd.DataFrame:
    """grade_week.shootout's pool, row level: roster + radar candidates at a
    skill position, shadow entry pre-kickoff, not withheld, page and shadow
    numbers present, every contender present. Actuals join later."""
    sp = (board.get("shadow") or {}).get("players") or {}
    people = [(p.get("sleeper_id"), p) for p in board.get("roster") or []]
    people += [(c.get("id"), c) for c in (board.get("radar") or {}).get("candidates") or []]
    people = [(sid, p) for sid, p in people if str(p.get("position")) in SKILL]
    group = {str(sid or "") for sid, _ in people}
    cont = board.get("contenders") or {}
    extra = {k: cont.get(k) or {} for k in ("baseline_v1", "stack")
             if group & set(cont.get(k) or {})}
    rows, seen = [], set()
    for sid, p in people:
        sid = str(sid or "")
        entry = sp.get(sid)
        if not sid or sid in seen or not isinstance(entry, dict):
            continue
        seen.add(sid)
        ours, theirs = p.get("projected"), entry.get("points")
        if (not entry.get("pre_kickoff") or p.get("withheld") or ours is None
                or theirs is None):
            continue
        explain = str(p.get("explain") or "")
        hit = _EXPLAIN.search(explain)
        base = _EXPLAIN_BASE.search(explain)
        row = {"sleeper_id": sid, "gsis_id": normalize_id(p.get("gsis_id") or ""),
               "position": str(p.get("position")), "page": float(ours),
               "sleeper_pk": float(theirs), "has_explain": bool(explain),
               "explain_says_adv": hit is not None,
               "explain_baseline": float(base.group(1)) if base else np.nan,
               "explain_adv": float(hit.group(2)) if hit else np.nan}
        for k, t in extra.items():
            row[k] = t.get(sid)
        if any(row.get(k) is None for k in extra):
            continue
        rows.append(row)
    out = pd.DataFrame(rows)
    # the advanced layer ran on a row when the page's number left the baseline
    # (radar candidates carry no `explain`, so the string is not the test)
    out["adv_ran"] = ((out["page"] - out["baseline_v1"].astype(float)).abs() > 1e-9
                      if "baseline_v1" in out else False)
    return out


def disagreements(frame: pd.DataFrame, a: str, b: str, *, min_col: str = "sleeper_pk",
                  min_proj: float = SHOOTOUT_MIN) -> dict:
    """Over the shoot-out pairs (same week and position, both >= min_proj,
    actuals not tied): the pairs `a` and `b` ORDER differently — the only
    pairs where the two systems can differ — and the share `a` got right."""
    rel = frame.loc[frame[min_col] >= min_proj]
    n = wins = total = 0
    for _, g in rel.groupby(["week", "position"]):
        rows = g.to_dict("records")
        for i, x in enumerate(rows):
            for y in rows[i + 1:]:
                if x["actual"] == y["actual"]:
                    continue
                total += 1
                sa, sb = np.sign(x[a] - y[a]), np.sign(x[b] - y[b])
                if sa == sb:
                    continue
                n += 1
                truth = np.sign(x["actual"] - y["actual"])
                wins += int(sa == truth)
    # two-sided binomial-ish z against a coin flip on the disagreed pairs
    z = (wins - n / 2) / np.sqrt(n / 4) if n else None
    return {"pairs": total, "disagree": n, f"{a}_wins": wins,
            f"{a}_win_share": round(wins / n, 3) if n else None,
            "z_vs_coin": round(float(z), 2) if z is not None else None}


def mae(frame: pd.DataFrame, systems) -> dict:
    return {s: round(float((frame[s] - frame["actual"]).abs().mean()), 3) for s in systems}


def ledger_grades() -> dict[int, dict]:
    path = LEDGER / "grades" / "season2026.csv"
    if not path.exists():
        return {}
    g = pd.read_csv(path)
    return {int(r["week"]): r.to_dict() for _, r in g.iterrows()}


def diff_stats(a: pd.Series, b: pd.Series, pos: pd.Series) -> dict:
    d = (a - b).abs()
    ok = d.notna()
    big = pos[ok & (d > 3.0)].value_counts().to_dict()
    return {"n": int(ok.sum()), "median": round(float(d[ok].median()), 3) if ok.any() else None,
            "p90": round(float(d[ok].quantile(0.9)), 3) if ok.any() else None,
            "within_0.5": round(float((d[ok] <= 0.5).mean()), 3) if ok.any() else None,
            "over_3_by_pos": {k: int(v) for k, v in sorted(big.items())},
            "mean_signed": round(float((a - b)[ok].mean()), 3) if ok.any() else None}


def availability(t: pd.DataFrame, weeks, label: str) -> dict:
    sub = t.loc[t["week"].isin(list(weeks))]
    out = {"label": label, "rows": int(len(sub)),
           "share": {c: round(float(sub[c].notna().mean()), 3) for c in AVAIL if c in sub}}
    by_pos = {}
    for pos, g in sub.groupby("position"):
        by_pos[pos] = {"rows": int(len(g)),
                       "depth_rank": round(float(g["depth_rank"].notna().mean()), 3),
                       "xfp_l3": round(float(g["xfp_l3"].notna().mean()), 3),
                       "xfp_last": round(float(g["xfp_last"].notna().mean()), 3),
                       NGS_FOR[pos]: round(float(g[NGS_FOR[pos]].notna().mean()), 3)}
    out["by_position"] = by_pos
    return out


def fmt_pct(v) -> str:
    return "   —  " if v is None or (isinstance(v, float) and np.isnan(v)) else f"{v:6.1%}"


# -------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--skip-2025", action="store_true", help="skip the 2025 availability table")
    ap.add_argument("--crosswalk", type=Path, default=RESEARCH_CACHE / "season2026" / "crosswalk.csv")
    ap.add_argument("--out", type=Path, default=TABLES / "live_reproduce_2026.json")
    args = ap.parse_args(argv)
    assert TABLES.is_relative_to(RESEARCH_CACHE)      # gitignored by data/research/cache/

    bt = load_script("projection_backtest", "scripts/research/projection_backtest.py")
    amb = load_script("advanced_model_backtest", "scripts/research/advanced_model_backtest.py")
    cw = Crosswalk.from_csv(args.crosswalk)
    summary: dict = {}

    print("1. tables")
    t26 = table(2026, cw, bt, amb)
    t26["gsis_id"] = t26["gsis_id"].map(normalize_id)
    t25 = None if args.skip_2025 else table(2025, cw, bt, amb)

    print("\n2. models")
    new = A.AdvancedModel.load()
    old = A.AdvancedModel.load(old_weights_path())
    assert new is not None and old is not None
    print(f"  new: {new.meta.get('feature_set')} fit_on {new.meta.get('fit_on')}; "
          f"old: {old.meta.get('feature_set')} fit_on {old.meta.get('fit_on')}")
    t26["adv_new"] = predict_adv(new, t26)
    t26["adv_old"] = predict_adv(old, t26)
    models = {"old": old, "new": new}

    # the harness on its own universe, 2026 weeks 4-5 (no Sleeper number used)
    hv = {}
    for weeks in ([4, 5], [4], [5]):
        for label, mask in (("all", t26["week"].isin(weeks)),
                            ("ppg_to_date>=5", t26["week"].isin(weeks) & (t26["ppg_to_date"] >= 5))):
            sub = t26.loc[mask]
            pw = bt.pairwise(sub, ["baseline", "adv_old", "adv_new"])
            key = f"wk{'-'.join(map(str, weeks))} [{label}]"
            hv[key] = {"rows": int(len(sub)), "pairs": pw["n"],
                       **{s: round(pw[s], 4) for s in ("baseline", "adv_old", "adv_new")},
                       "mae": mae(sub, ["baseline", "adv_old", "adv_new"]),
                       "adv_new_vs_baseline": disagreements(sub, "adv_new", "baseline",
                                                            min_col="ppg_to_date",
                                                            min_proj=5.0 if label != "all" else -1.0)}
            d = hv[key]["adv_new_vs_baseline"]
            print(f"  harness universe {key}: {len(sub)} rows, {pw['n']} pairs: "
                  f"baseline {pw['baseline']:.1%}  adv_old {pw['adv_old']:.1%}  "
                  f"adv_new {pw['adv_new']:.1%}; MAE {hv[key]['mae']}; adv_new vs baseline "
                  f"disagree on {d['disagree']} pairs, adv_new right {d['adv_new_win_share']} "
                  f"(z {d['z_vs_coin']})")
    summary["harness_universe_2026_w4_5"] = hv

    print("\n3. live universe")
    grades = ledger_grades()
    live = {}
    pools = {}
    for name, week, which, path in BOARDS:
        board = json.loads(path.read_text(encoding="utf-8"))
        pool = live_pool(board)
        pools[name] = pool
        # join on the stable id only (rule #3); the board's position (Sleeper's)
        # drives the pairing, as in the live grader, and the table's is kept
        # beside it so a disagreement is counted, not hidden
        tw = t26.loc[t26["week"] == week].drop_duplicates("gsis_id")
        m = pool.loc[pool["gsis_id"] != ""].merge(tw, on="gsis_id", how="inner",
                                                  suffixes=("", "_t"))
        unmatched = len(pool) - len(m)
        pos_disagree = int((m["position"] != m["position_t"]).sum())
        # harness adv with the LIVE baseline as the baseline input (isolates
        # a baseline mismatch from a feature mismatch)
        lb = m.assign(baseline=m["baseline_v1"].astype(float))
        m["adv_old_lb"] = predict_adv(old, lb)
        m["adv_new_lb"] = predict_adv(new, lb)
        m["stack_old_h"] = predict_stack(old, m, m["sleeper_pk"], m["adv_old"])
        m["stack_new_h"] = predict_stack(new, m, m["sleeper_pk"], m["adv_new"])
        systems = ["page", "baseline_v1", "stack", "sleeper_pk", "baseline", "adv_old",
                   "adv_new", "adv_old_lb", "adv_new_lb", "stack_old_h", "stack_new_h"]
        systems = [s for s in systems if s in m and m[s].notna().all()]
        rel = m.loc[m["sleeper_pk"] >= SHOOTOUT_MIN]
        pw = bt.pairwise(rel, systems)
        by_pos = {}
        for pos, g in rel.groupby("position"):
            p = bt.pairwise(g, systems)
            by_pos[pos] = {"players": int(len(g)), "pairs": p["n"],
                           **{s: (round(p[s], 4) if p[s] is not None else None) for s in systems}}
        rec = grades.get(week, {}) if which == "old" else REPORTED_W5_NEW
        recorded = {k: rec.get(k) for k in ("shootout_n", "shootout_pairs", "page_pairwise",
                                              "baseline_v1_pairwise", "sleeper_pairwise",
                                              "stack_pairwise", "page_mae", "baseline_v1_mae",
                                              "sleeper_mae", "stack_mae") if k in rec}
        live[name] = {"week": week, "model_on_board": which, "pool_rows": int(len(pool)),
                      "rows_with_actual": int(len(m)), "unmatched": int(unmatched),
                      "position_disagreements": pos_disagree,
                      "adv_ran_share": round(float(m["adv_ran"].mean()), 3),
                      "pairs": pw["n"], "pairwise": {s: round(pw[s], 4) for s in systems},
                      "mae_all_rows": mae(m, systems), "by_position": by_pos,
                      "recorded_grade": recorded}
        print(f"  {name} (week {week}, {which} model on the board): pool {len(pool)}, "
              f"with actual {len(m)}, unmatched {unmatched}, position disagreements "
              f"{pos_disagree}, adv ran on {m['adv_ran'].mean():.0%}; pairs {pw['n']} "
              f"(recorded {recorded.get('shootout_pairs')}, n {recorded.get('shootout_n')})")
        print(f"    {'system':<12} {'pairwise':>8} {'MAE':>6}   recorded")
        for s in systems:
            key = {"sleeper_pk": "sleeper"}.get(s, s)
            rp, rm = recorded.get(f"{key}_pairwise"), recorded.get(f"{key}_mae")
            tail = (f"{rp:.1%}" if isinstance(rp, float) and not np.isnan(rp) else "") + \
                   (f" / {rm:.3f}" if isinstance(rm, float) and not np.isnan(rm) else "")
            print(f"    {s:<12} {pw[s]:8.1%} {live[name]['mae_all_rows'][s]:6.3f}   {tail}")
        print("    by position (pairs: baseline / adv_old / adv_new / page / sleeper): " + "; ".join(
            f"{pos} {v['pairs']}: {fmt_pct(v['baseline'])}/{fmt_pct(v['adv_old'])}/"
            f"{fmt_pct(v['adv_new'])}/{fmt_pct(v['page'])}/{fmt_pct(v['sleeper_pk'])}"
            for pos, v in by_pos.items()))
        dis = {f"{a}_vs_{b}": disagreements(m, a, b) for a, b in (
            ("page", "baseline_v1"), ("adv_old", "baseline"), ("adv_new", "baseline"),
            ("adv_new", "adv_old"), ("stack", "sleeper_pk"), ("baseline", "sleeper_pk"))}
        live[name]["disagreements"] = dis
        print("    disagreed pairs (n, first system's win share, z): " + "; ".join(
            f"{k} {v['disagree']}, {v[k.split('_vs_')[0] + '_wins']}/{v['disagree']} "
            f"= {v[k.split('_vs_')[0] + '_win_share']}, z {v['z_vs_coin']}" for k, v in dis.items()))
        pools[name] = m
    # the two old-model boards are distinct weeks: pool them (the week-5 boards
    # overlap, so the new-model board is not added)
    pooled = pd.concat([pools["w4_old"], pools["w5_old"]])
    live["pooled_w4_w5_old"] = {k: disagreements(pooled, a, b) for k, (a, b) in {
        "page_vs_baseline_v1": ("page", "baseline_v1"), "adv_old_vs_baseline": ("adv_old", "baseline"),
        "adv_new_vs_baseline": ("adv_new", "baseline"), "adv_new_vs_adv_old": ("adv_new", "adv_old"),
        "stack_vs_sleeper_pk": ("stack", "sleeper_pk"),
        "baseline_vs_sleeper_pk": ("baseline", "sleeper_pk")}.items()}
    pr = pooled.loc[pooled["sleeper_pk"] >= SHOOTOUT_MIN]
    pw = bt.pairwise(pr, ["page", "baseline_v1", "stack", "sleeper_pk", "baseline", "adv_old", "adv_new"])
    live["pooled_w4_w5_old"]["pairwise"] = {k: round(v, 4) for k, v in pw.items() if k != "n"}
    live["pooled_w4_w5_old"]["pairs"] = pw["n"]
    print(f"  pooled w4_old + w5_old ({pw['n']} pairs): " + ", ".join(
        f"{k} {v:.1%}" for k, v in pw.items() if k != "n"))
    print("    disagreed pairs: " + "; ".join(
        f"{k} {v['disagree']}, win {v[k.split('_vs_')[0] + '_win_share']}, z {v['z_vs_coin']}"
        for k, v in live["pooled_w4_w5_old"].items() if k not in ("pairwise", "pairs")))
    summary["live_universe"] = live

    print("\n4. match check (live board vs harness, same gsis id and week)")
    match = {}
    for name, week, which, _ in BOARDS:
        m = pools[name]
        adv_col = f"adv_{which}"
        res = {
            "n": int(len(m)),
            "page_vs_adv": diff_stats(m["page"], m[adv_col], m["position"]),
            "page_vs_adv_live_baseline": diff_stats(m["page"], m[f"{adv_col}_lb"], m["position"]),
            "baseline_v1_vs_baseline": diff_stats(m["baseline_v1"].astype(float), m["baseline"],
                                                  m["position"]),
            "explain_baseline_vs_baseline_v1": diff_stats(m["explain_baseline"],
                                                          m["baseline_v1"].astype(float),
                                                          m["position"]),
            "adv_ran_share": round(float(m["adv_ran"].mean()), 3),
        }
        if "stack" in m and m["stack"].notna().all():
            res["stack_vs_stack_h"] = diff_stats(m["stack"].astype(float), m[f"stack_{which}_h"],
                                                 m["position"])
        # which missing block would make the harness look like the page?
        lb = m.assign(baseline=m["baseline_v1"].astype(float))
        abl = {}
        ran = m["adv_ran"].to_numpy()
        for tag, cols in ABLATE.items():
            v = predict_adv(models[which], lb, nan_cols=cols)
            d = (m["page"] - v).abs()[ran]
            abl[tag] = {"median": round(float(d.median()), 3), "p90": round(float(d.quantile(0.9)), 3),
                        "within_0.5": round(float((d <= 0.5).mean()), 3)}
        res["ablation_page_vs_adv_live_baseline"] = abl
        match[name] = res
        print(f"  {name}: n={len(m)}, adv ran on {res['adv_ran_share']:.0%} of rows")
        for k in ("baseline_v1_vs_baseline", "page_vs_adv", "page_vs_adv_live_baseline",
                  "stack_vs_stack_h", "explain_baseline_vs_baseline_v1"):
            if k in res:
                d = res[k]
                print(f"    {k:<34} median {d['median']}, p90 {d['p90']}, within 0.5: "
                      f"{d['within_0.5']}, >3 by pos {d['over_3_by_pos']}, signed mean {d['mean_signed']}")
        print("    ablation (median |page - adv| with live baseline, block NaN'd): " + ", ".join(
            f"{k} {v['median']}" for k, v in abl.items()))
    summary["match"] = match

    print("\n5. feature availability (share non-null)")
    av = {"2026_w4_5": availability(t26, [4, 5], "2026 weeks 4-5")}
    live_rows = pd.concat([pools[n] for n in pools]).drop_duplicates(["gsis_id", "week"])
    av["2026_live_universe"] = availability(live_rows, [4, 5], "2026 live universe")
    if t25 is not None:
        av["2025_w4_5"] = availability(t25, [4, 5], "2025 weeks 4-5")
    for k, v in av.items():
        print(f"  {v['label']} ({v['rows']} rows): " + ", ".join(
            f"{c} {s:.2f}" for c, s in v["share"].items()))
        print("    by position: " + "; ".join(
            f"{p} n={d['rows']} depth {d['depth_rank']:.2f} xfp_l3 {d['xfp_l3']:.2f} "
            f"xfp_last {d['xfp_last']:.2f} {NGS_FOR[p]} {d[NGS_FOR[p]]:.2f}"
            for p, d in v["by_position"].items()))
    # the live model inputs on disk now, for the record
    meta_path = RESEARCH_CACHE / "season2026" / A.INPUT_DIR / A.INPUT_META
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        av["model_inputs_meta_now"] = {"fetched_at": meta.get("fetched_at"), "rows": meta.get("rows"),
                                       "weeks": {k: (v[:1] + ["..", v[-1]] if len(v) > 3 else v)
                                                 for k, v in (meta.get("weeks") or {}).items()}}
        print(f"  model inputs on disk now: fetched {meta.get('fetched_at')}, rows {meta.get('rows')}")
    summary["availability"] = av

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(summary, indent=1, default=str), encoding="utf-8")
    print(f"\nsummary -> {args.out.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
