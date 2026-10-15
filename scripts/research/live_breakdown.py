"""live_breakdown.py — break the live shoot-out weeks down: position, tier, disagreement.

    PYTHONPATH=src python scripts/research/live_breakdown.py \
        --board data/outputs/cloud/0098_.../week04_<stamp>.json --label w4 \
        --board data/outputs/cloud/0125_.../week05_<stamp>.json --label w5-old \
        --board data/ledger/decisions/season2026/week05_<stamp>.json --label w5-new \
        [--season 2026] [--week 4 --week 5 --week 5] [--pool 0 1] [--out summary.json]

Research only (never imported by src/gridiron). The weekly grader
(`scripts/weekly/grade_week.py`) reports one number per system per week; this
script rebuilds the SAME shoot-out pool (its selection rules, kept row-level,
and cross-checked against `grade_week.shootout` on every board) and asks
where the page's deficit to its own baseline comes from:

  (a) per position and overall: players, pairs, pairwise hit rate and MAE
      for page / baseline_v1 / sleeper / blend / stack;
  (b) the pairs where page and baseline_v1 ORDER a pair differently — the
      only pairs the advanced layer changed — and who was right;
  (c) bias per position (mean projected - actual) and the size of the
      adjustment |page - baseline_v1|, plus how much of the adjustment the
      actuals confirm (slope of actual-baseline on page-baseline; < 1 means
      the layer over-adjusts);
  (d) pairwise by Sleeper tier, both players in [5,10), [10,15) or 15+;
  (e) a shrink sweep, baseline + k * (page - baseline) for several k, which
      says whether a smaller (or larger) adjustment would have graded better;
  (f) `--bootstrap N`: players resampled within board x position, the pairs
      rebuilt, and a 90% interval for the page - baseline_v1 difference;
  (g) when two boards cover the same week, both re-graded on the players
      they have in COMMON, so a model change is not confused with a pool
      change (a later build loses the players whose games began).

Actuals come from nflverse by gsis id (`grade_week.actuals_from_nflverse`,
public, cached); every join is on a stable id (rule #3). Output is counts and
rates only — no names, no ids — because the repository is public.
"""
from __future__ import annotations

import argparse
import itertools
import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import numpy as np
import pandas as pd

from research_common import load_script

POS = ("QB", "RB", "WR", "TE")
TIERS = (("5-10", 5.0, 10.0), ("10-15", 10.0, 15.0), ("15+", 15.0, float("inf")))
SHRINK = (0.0, 0.25, 0.5, 0.75, 1.0, 1.5)
#: Two extra systems scored beside the grader's five, named so they cannot
#: be mistaken for anything the page shipped:
#:   blend_b   (baseline_v1 + sleeper) / 2 — the blend without the advanced layer
#:   page_k<k> baseline_v1 + k * (page - baseline_v1) — the shrink sweep
EXTRA = ["blend_b"] + [f"page_k{k:g}" for k in SHRINK]


def _gw():
    return load_script("grade_week", "scripts/weekly/grade_week.py")


# ----------------------------------------------------------------- the pool
def pool_rows(archive: Mapping[str, object], actuals: Mapping[str, float], gw,
              *, positions: Sequence[str] = POS) -> tuple[pd.DataFrame, list[str]]:
    """One row per scored player, selected exactly as `grade_week.shootout`
    selects them (roster + radar candidates, skill positions, shadow entry
    pre_kickoff, not withheld, page projection, actual, every system
    present). Kept row-level so the pairs can be broken down."""
    shadow = archive.get("shadow") or {}
    sp = shadow.get("players") if isinstance(shadow, Mapping) else None
    if not sp:
        return pd.DataFrame(), []
    people = [(p.get("sleeper_id"), p) for p in archive.get("roster") or []]
    radar = archive.get("radar") or {}
    people += [(c.get("id"), c) for c in radar.get("candidates") or []]
    people = [(sid, p) for sid, p in people if str(p.get("position")) in positions]
    group = {str(sid or "") for sid, _ in people}
    cont = archive.get("contenders") or {}
    extra = {k: cont.get(k) or {} for k in ("baseline_v1", "stack")
             if isinstance(cont, Mapping) and group & set(cont.get(k) or {})}
    systems = ["page"] + [k for k in ("baseline_v1",) if k in extra] + ["sleeper", "blend"] \
        + [k for k in ("stack",) if k in extra]
    rows, seen = [], set()
    for sid, p in people:
        sid = str(sid or "")
        entry = sp.get(sid) if isinstance(sp, Mapping) else None
        if not sid or sid in seen or not isinstance(entry, Mapping):
            continue
        seen.add(sid)
        ours, theirs = p.get("projected"), entry.get("points")
        actual = actuals.get(gw.actual_key(p))
        if (not entry.get("pre_kickoff") or p.get("withheld") or ours is None
                or theirs is None or actual is None):
            continue
        row = {"sleeper_id": sid, "gsis_id": str(p.get("gsis_id") or ""),
               "position": str(p.get("position")), "actual": float(actual),
               "page": float(ours), "sleeper": float(theirs),
               "blend": (float(ours) + float(theirs)) / 2.0}
        for k, table in extra.items():
            row[k] = table.get(sid)
        if any(row.get(k) is None for k in systems):
            continue
        for k in extra:
            row[k] = float(row[k])
        rows.append(row)
    return pd.DataFrame(rows), systems


def add_extras(rows: pd.DataFrame) -> pd.DataFrame:
    """The shrink sweep and the baseline blend (needs baseline_v1)."""
    out = rows.copy()
    out["tier"] = [tier_of(x) for x in out["sleeper"]]
    if "baseline_v1" in out:
        adj = out["page"] - out["baseline_v1"]
        out["adj"] = adj
        out["blend_b"] = (out["baseline_v1"] + out["sleeper"]) / 2.0
        for k in SHRINK:
            out[f"page_k{k:g}"] = (out["baseline_v1"] + k * adj).clip(lower=0.0)
    return out


def tier_of(x: float) -> str | None:
    for name, lo, hi in TIERS:
        if lo <= x < hi:
            return name
    return None


# ---------------------------------------------------------------- the pairs
def make_pairs(rows: pd.DataFrame, systems: Sequence[str], *, min_proj: float) -> pd.DataFrame:
    """Every pair the grader forms: same board, same position, both Sleeper
    >= min_proj, actuals not tied. A system that ties a pair misses it, as in
    the grader. Adds the page-vs-baseline disagreement flag."""
    rel = rows.loc[rows["sleeper"] >= min_proj]
    have_base = "baseline_v1" in rows
    out = []
    for (board, pos), g in rel.groupby(["board", "position"], sort=False):
        for a, b in itertools.combinations(g.to_dict("records"), 2):
            if a["actual"] == b["actual"]:
                continue
            truth = a["actual"] > b["actual"]
            r = {"board": board, "position": pos,
                 "tier": a["tier"] if a["tier"] == b["tier"] else "mixed",
                 "gap_sleeper": abs(a["sleeper"] - b["sleeper"])}
            for s in systems:
                r[f"hit_{s}"] = bool(a[s] != b[s] and (a[s] > b[s]) == truth)
            if have_base:
                strict = a["page"] != b["page"] and a["baseline_v1"] != b["baseline_v1"]
                r["one_tied"] = not strict
                r["disagree"] = bool(strict and (a["page"] > b["page"])
                                     != (a["baseline_v1"] > b["baseline_v1"]))
                # the page's change to the pair's gap, signed toward the
                # player who actually scored more (> 0: the layer moved the
                # pair the right way)
                sign = 1.0 if truth else -1.0
                r["adj_gap_toward_truth"] = sign * ((a["page"] - b["page"])
                                                    - (a["baseline_v1"] - b["baseline_v1"]))
            out.append(r)
    return pd.DataFrame(out)


def rates(pairs: pd.DataFrame, systems: Sequence[str]) -> dict:
    n = int(len(pairs))
    return {"pairs": n, **{s: (round(float(pairs[f"hit_{s}"].mean()), 4) if n else None)
                           for s in systems}}


def by_position(rows: pd.DataFrame, pairs: pd.DataFrame, systems: Sequence[str]) -> dict:
    out = {}
    for pos in (*POS, "ALL"):
        r = rows if pos == "ALL" else rows.loc[rows["position"] == pos]
        p = pairs if pos == "ALL" else pairs.loc[pairs["position"] == pos]
        if r.empty:
            continue
        out[pos] = {"n": int(len(r)), **rates(p, systems),
                    **{f"mae_{s}": round(float((r[s] - r["actual"]).abs().mean()), 3)
                       for s in systems}}
    return out


def disagreement(pairs: pd.DataFrame) -> dict:
    out = {}
    for pos in (*POS, "ALL"):
        p = pairs if pos == "ALL" else pairs.loc[pairs["position"] == pos]
        if p.empty:
            continue
        d = p.loc[p["disagree"]]
        n = int(len(d))
        page_right = int(d["hit_page"].sum())
        out[pos] = {"pairs": int(len(p)), "disagree": n, "page_right": page_right,
                    "baseline_right": n - page_right,
                    "page_rate": round(page_right / n, 4) if n else None,
                    "one_tied": int(p["one_tied"].sum()),
                    # over every pair: did the layer move the gap toward the truth?
                    "gap_moved_right_share": round(float((p["adj_gap_toward_truth"] > 0).mean()), 4),
                    "mean_gap_move_toward_truth": round(float(p["adj_gap_toward_truth"].mean()), 3)}
    return out


def bias(rows: pd.DataFrame, systems: Sequence[str]) -> dict:
    out = {}
    for pos in (*POS, "ALL"):
        r = rows if pos == "ALL" else rows.loc[rows["position"] == pos]
        if r.empty:
            continue
        rec = {"n": int(len(r)),
               **{f"bias_{s}": round(float((r[s] - r["actual"]).mean()), 3) for s in systems}}
        if "adj" in r:
            x = r["adj"].to_numpy(dtype=float)
            y = (r["actual"] - r["baseline_v1"]).to_numpy(dtype=float)
            moved = np.abs(x) > 0.25
            rec.update({
                "mean_abs_adj": round(float(np.abs(x).mean()), 3),
                "mean_adj": round(float(x.mean()), 3),
                # slope through the origin of (actual - baseline) on (page - baseline):
                # 1 = the adjustment was the right size, < 1 too big, < 0 wrong way
                "adj_slope": round(float((x * y).sum() / (x * x).sum()), 3) if (x * x).sum() else None,
                "adj_slope_se": round(float(np.sqrt(((y - x * (x * y).sum() / (x * x).sum()) ** 2).sum()
                                                    / max(len(x) - 1, 1) / (x * x).sum())), 3)
                if (x * x).sum() and len(x) > 2 else None,
                "adj_sign_agree": round(float(((x[moved] > 0) == (y[moved] > 0)).mean()), 4)
                if moved.any() else None,
                "adj_moved_n": int(moved.sum()),
                "mae_page_on_adjusted": round(float(np.abs(y[moved] - x[moved]).mean()), 3)
                if moved.any() else None,
                "mae_baseline_on_adjusted": round(float(np.abs(y[moved]).mean()), 3)
                if moved.any() else None,
            })
        out[pos] = rec
    return out


def by_tier(pairs: pd.DataFrame, systems: Sequence[str]) -> dict:
    out = {}
    for name in (*(t[0] for t in TIERS), "mixed"):
        p = pairs.loc[pairs["tier"] == name]
        d = p.loc[p["disagree"]] if "disagree" in p else p.iloc[0:0]
        out[name] = {**rates(p, systems),
                     "disagree": int(len(d)), "page_right": int(d["hit_page"].sum()) if len(d) else 0,
                     "by_position": {pos: rates(p.loc[p["position"] == pos], systems)
                                     for pos in POS if (p["position"] == pos).any()}}
    return out


def shrink(pairs: pd.DataFrame) -> dict:
    cols = [f"page_k{k:g}" for k in SHRINK]
    return {pos: rates(pairs if pos == "ALL" else pairs.loc[pairs["position"] == pos], cols)
            for pos in (*POS, "ALL")}


def bootstrap(rows: pd.DataFrame, reps: int, *, min_proj: float, seed: int = 7) -> dict:
    """Resample PLAYERS with replacement within each board x position, rebuild
    the pairs, and read the page - baseline_v1 pairwise difference: a 90%
    interval for the deficit, per position and overall. Pairs share players,
    so a per-pair binomial error would be far too small; this is the honest
    one (duplicated players tie on actual and drop out, which shrinks the
    pair count a little each draw)."""
    rng = np.random.default_rng(seed)
    groups = [g for _, g in rows.groupby(["board", "position"], sort=False)]
    diffs = {pos: [] for pos in (*POS, "ALL")}
    for _ in range(reps):
        draw = pd.concat([g.iloc[rng.integers(0, len(g), len(g))] for g in groups],
                         ignore_index=True)
        pairs = make_pairs(draw, ["page", "baseline_v1"], min_proj=min_proj)
        for pos in diffs:
            p = pairs if pos == "ALL" else pairs.loc[pairs["position"] == pos]
            if len(p):
                diffs[pos].append(float(p["hit_page"].mean() - p["hit_baseline_v1"].mean()))
    out = {}
    for pos, d in diffs.items():
        if d:
            lo, hi = np.percentile(d, [5, 95])
            out[pos] = {"reps": len(d), "p05": round(float(lo), 4), "p95": round(float(hi), 4),
                        "share_below_zero": round(float(np.mean(np.array(d) < 0)), 3)}
    return out


# -------------------------------------------------------------- the report
def fmt_pct(v) -> str:
    return "   -  " if v is None else f"{v:6.1%}"


def print_block(label: str, meta: dict, summary: dict, systems: Sequence[str]) -> None:
    print(f"\n== {label}: week {meta['week']}, built {meta['built']}, pool {meta['n']} players / "
          f"{meta['pairs']} pairs; cross-check vs grade_week.shootout: {meta['crosscheck']}")
    short = {"page": "page", "baseline_v1": "base", "sleeper": "slpr", "blend": "blend",
             "stack": "stack", "blend_b": "blnd_b"}
    sy = [s for s in systems if s in short]
    if "blend_b" in summary["by_position"]["ALL"]:
        sy.append("blend_b")
    print("(a) pos    n  pairs  " + " ".join(f"{short[s]:>6}" for s in sy)
          + "  | MAE " + " ".join(f"{short[s]:>6}" for s in sy))
    for pos, v in summary["by_position"].items():
        print(f"    {pos:<3} {v['n']:4d} {v['pairs']:6d}  "
              + " ".join(fmt_pct(v[s]) for s in sy) + "  |     "
              + " ".join(f"{v[f'mae_{s}']:6.2f}" for s in sy))
    print("(b) page-vs-baseline disagreements   pos  pairs  disagree  page_right  base_right  "
          "page%   one_tied  gap->truth%  mean_gap_move")
    for pos, v in summary["disagreement"].items():
        print(f"    {'':<32} {pos:<3} {v['pairs']:6d} {v['disagree']:9d} {v['page_right']:11d} "
              f"{v['baseline_right']:11d} {fmt_pct(v['page_rate'])} {v['one_tied']:9d} "
              f"{fmt_pct(v['gap_moved_right_share'])}    {v['mean_gap_move_toward_truth']:+6.2f}")
    print("(c) bias (proj - actual)   pos    n   page   base   slpr  stack  |adj|  mean_adj  "
          "slope  sign%  moved  MAE_pg  MAE_bs (on moved)")
    for pos, v in summary["bias"].items():
        print(f"    {'':<22} {pos:<3} {v['n']:4d} {v['bias_page']:+6.2f} {v['bias_baseline_v1']:+6.2f} "
              f"{v['bias_sleeper']:+6.2f} {v.get('bias_stack', float('nan')):+6.2f} "
              f"{v['mean_abs_adj']:5.2f} {v['mean_adj']:+8.2f}  "
              f"{v['adj_slope'] if v['adj_slope'] is not None else float('nan'):+5.2f} "
              f"{fmt_pct(v['adj_sign_agree'])} {v['adj_moved_n']:5d}  "
              f"{v['mae_page_on_adjusted'] or float('nan'):6.2f}  "
              f"{v['mae_baseline_on_adjusted'] or float('nan'):6.2f}")
    print("(d) tier (both players)  pairs  " + " ".join(f"{short[s]:>6}" for s in sy)
          + "  | disagree page_right")
    for name, v in summary["by_tier"].items():
        print(f"    {name:<20} {v['pairs']:6d}  " + " ".join(fmt_pct(v[s]) for s in sy)
              + f"  | {v['disagree']:8d} {v['page_right']:10d}")
    print("(e) shrink k (base + k*(page-base))  pos  pairs  "
          + " ".join(f"k={k:<4g}" for k in SHRINK))
    for pos, v in summary["shrink"].items():
        print(f"    {'':<33} {pos:<3} {v['pairs']:6d}  "
              + " ".join(fmt_pct(v[f'page_k{k:g}']) for k in SHRINK))


def print_bootstrap(bs: dict) -> None:
    print("(f) bootstrap of page - baseline pairwise (players resampled): "
          + "  ".join(f"{pos} [{v['p05']:+.1%}, {v['p95']:+.1%}] P(<0)={v['share_below_zero']:.2f}"
                      for pos, v in bs.items()))


def summarize(rows: pd.DataFrame, systems: Sequence[str], *, min_proj: float) -> tuple[dict, pd.DataFrame]:
    pairs = make_pairs(rows, [*systems, *EXTRA], min_proj=min_proj)
    sy = [*systems, "blend_b"]
    return {"by_position": by_position(rows, pairs, sy),
            "disagreement": disagreement(pairs),
            "bias": bias(rows, systems),
            "by_tier": by_tier(pairs, sy),
            "shrink": shrink(pairs)}, pairs


def crosscheck(archive, actuals, gw, summary: dict, systems: Sequence[str]) -> str:
    """The grader's own numbers on this board must equal our ALL row."""
    ref = gw.shootout(archive, actuals)
    ours = summary["by_position"]["ALL"]
    bad = []
    if ref["n"] != ours["n"] or ref["pairs"] != ours["pairs"]:
        bad.append(f"n/pairs {ref['n']}/{ref['pairs']} vs {ours['n']}/{ours['pairs']}")
    for s in systems:
        if ref.get(f"{s}_pairwise") != ours[s]:
            bad.append(f"{s} {ref.get(f'{s}_pairwise')} vs {ours[s]}")
        if ref.get(f"{s}_mae") != ours[f"mae_{s}"]:
            bad.append(f"{s} mae {ref.get(f'{s}_mae')} vs {ours[f'mae_{s}']}")
    return "OK" if not bad else "MISMATCH " + "; ".join(bad)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--board", type=Path, action="append", required=True,
                    help="a decision record (repeat); order fixes the indices --pool uses")
    ap.add_argument("--label", action="append", default=[],
                    help="one per board, in order (default: the file stem)")
    ap.add_argument("--season", type=int, default=None,
                    help="default: the record's season, else 2026")
    ap.add_argument("--week", type=int, action="append", default=[],
                    help="one per board; must agree with the record when it names its week")
    ap.add_argument("--pool", type=int, nargs="*", default=None,
                    help="board indices to pool (default: the first board of each week)")
    ap.add_argument("--min-proj", type=float, default=None,
                    help="pairs only among Sleeper >= this (default grade_week.SHOOTOUT_MIN)")
    ap.add_argument("--bootstrap", type=int, default=0,
                    help="player-cluster bootstrap reps for the page - baseline difference")
    ap.add_argument("--out", type=Path, default=None, help="write the summary JSON here")
    args = ap.parse_args(argv)

    gw = _gw()
    from gridiron.decisions import archive_stamp, read_archive
    min_proj = gw.SHOOTOUT_MIN if args.min_proj is None else args.min_proj
    if args.label and len(args.label) != len(args.board):
        ap.error("--label must be given once per --board")
    if args.week and len(args.week) != len(args.board):
        ap.error("--week must be given once per --board")

    boards, actuals_by_week, results = [], {}, {"min_proj": min_proj, "boards": {}}
    for i, path in enumerate(args.board):
        archive = read_archive(path)
        label = args.label[i] if args.label else path.stem
        week = archive.get("week")
        if args.week:
            if week is not None and int(week) != args.week[i]:
                raise SystemExit(f"[live] {path.name} says week {week}, --week says {args.week[i]}")
            week = args.week[i]
        if week is None:
            raise SystemExit(f"[live] {path.name} names no week; pass --week")
        week = int(week)
        season = int(args.season or archive.get("season") or 2026)
        if (season, week) not in actuals_by_week:
            actuals_by_week[(season, week)] = gw.actuals_from_nflverse(season, week)
        actuals = actuals_by_week[(season, week)]
        rows, systems = pool_rows(archive, actuals, gw)
        if rows.empty:
            print(f"[live] {label}: no shoot-out pool (no shadow)", file=sys.stderr)
            continue
        if "baseline_v1" not in systems:
            raise SystemExit(f"[live] {label}: the record carries no baseline_v1 contender")
        rows = add_extras(rows).assign(board=label, week=week)
        summary, pairs = summarize(rows, systems, min_proj=min_proj)
        stamp = archive_stamp(path)
        meta = {"week": week, "season": season, "path": str(path),
                "built": stamp.isoformat(timespec="minutes") if stamp else "?",
                "n": summary["by_position"]["ALL"]["n"],
                "pairs": summary["by_position"]["ALL"]["pairs"],
                "crosscheck": crosscheck(archive, actuals, gw, summary, systems),
                "model": str(archive.get("projection_model") or "")[:60]}
        boards.append((label, week, rows, systems))
        results["boards"][label] = {"meta": meta, **summary}
        print_block(label, meta, summary, systems)
        if args.bootstrap:
            bs = bootstrap(rows, args.bootstrap, min_proj=min_proj)
            results["boards"][label]["bootstrap_page_minus_baseline"] = bs
            print_bootstrap(bs)

    # ---- pooled: the first board of each week unless --pool says otherwise
    if boards:
        if args.pool is None:
            seen, idx = set(), []
            for j, (_, week, _, _) in enumerate(boards):
                if week not in seen:
                    seen.add(week)
                    idx.append(j)
        else:
            idx = [j for j in args.pool if 0 <= j < len(boards)]
        if len(idx) > 1:
            parts = [boards[j] for j in idx]
            systems = [s for s in parts[0][3] if all(s in p[3] for p in parts)]
            rows = pd.concat([p[2] for p in parts], ignore_index=True)
            summary, _ = summarize(rows, systems, min_proj=min_proj)
            label = "POOLED[" + "+".join(p[0] for p in parts) + "]"
            meta = {"week": "+".join(str(p[1]) for p in parts), "built": "-",
                    "n": summary["by_position"]["ALL"]["n"],
                    "pairs": summary["by_position"]["ALL"]["pairs"],
                    "crosscheck": "n/a (pairs within board x position)"}
            results["pooled"] = {"meta": meta, "boards": [p[0] for p in parts], **summary}
            print_block(label, meta, summary, systems)
            if args.bootstrap:
                bs = bootstrap(rows, args.bootstrap, min_proj=min_proj)
                results["pooled"]["bootstrap_page_minus_baseline"] = bs
                print_bootstrap(bs)

    # ---- like-for-like: boards of the same week on their common players
    by_week: dict[int, list] = {}
    for b in boards:
        by_week.setdefault(b[1], []).append(b)
    for week, group in by_week.items():
        if len(group) < 2:
            continue
        common = set.intersection(*(set(b[2]["sleeper_id"]) for b in group))
        results.setdefault("common", {})[str(week)] = {"n_common": len(common)}
        print(f"\n== week {week} like-for-like on the {len(common)} players every board scored:")
        for label, _, rows, systems in group:
            sub = rows.loc[rows["sleeper_id"].isin(common)]
            summary, _ = summarize(sub, systems, min_proj=min_proj)
            results["common"][str(week)][label] = summary["by_position"]
            v = summary["by_position"]["ALL"]
            print(f"   {label:<8} ALL n={v['n']:3d} pairs={v['pairs']:3d}  "
                  + "  ".join(f"{s} {fmt_pct(v[s]).strip()}" for s in (*systems, "blend_b"))
                  + "  | disagree " + str(summary["disagreement"]["ALL"]["disagree"])
                  + f" page_right {summary['disagreement']['ALL']['page_right']}")
            for pos in POS:
                if pos in summary["by_position"]:
                    w = summary["by_position"][pos]
                    print(f"            {pos:<3} n={w['n']:3d} pairs={w['pairs']:3d}  "
                          + "  ".join(f"{s} {fmt_pct(w[s]).strip()}" for s in (*systems, "blend_b")))

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(results, indent=1), encoding="utf-8")
        print(f"\n[live] summary written to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
