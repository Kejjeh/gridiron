"""Write the P(win) calibration the page reads, from the two studies, by a
rule fixed before the league-history numbers were seen.

    PYTHONPATH=src python scripts/research/pwin_calibration_save.py \
        [--synthetic data/research/cache/tables/pwin_calibration.json] \
        [--league data/research/cache/tables/pwin_league_history.json] [--dry-run]

Inputs: `pwin_calibration.py` (synthetic 12-team leagues, k fit on 2019-2022,
tested on 2023-2025) and `pwin_league_history.py` (this league's real
2023-2025 matchups). The factor k is ALWAYS the synthetic fit-season value:
165 real matchups are too few to fit on and are used only as a check.

Both studies are read with the page's own skill mean (advanced_v1 refit
leave-one-season-out): the synthetic study's primary result, and the league
study's `served_mean_advanced_out_of_sample` sensitivity (its primary when
that is missing).

bar_cleared (the page applies k and labels P(win) CALIBRATED only then):
  1. on the held-out synthetic seasons, k beats k = 1 on log loss AND Brier;
  2. on this league's matchups, k is no worse than k = 1 on log loss AND
     Brier (the league-history study reports both at a grid of k; the
     nearest grid point to k is used).

raw_tested (added after the first results, and it changes no number: the
page keeps k = 1, only the label changes from UNCALIBRATED to TESTED). When
the factor does not clear the bar, the raw formula is labelled tested only if
the same held-out evidence shows its spread is right on its own:
  3. the held-out synthetic margin z-SD is within [0.9, 1.1];
  4. on this league, a k fit on the other seasons does not beat k = 1 on the
     held-out season (pooled leave-one-season-out log loss AND Brier), and
     the league margin z-SD is within [0.9, 1.1].
Neither -> the file records the test and the page stays UNCALIBRATED.
Writes src/gridiron/models/pwin_calibration.json (`gridiron.winprob`).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TABLES = ROOT / "data/research/cache/tables"
OUT = ROOT / "src/gridiron/models/pwin_calibration.json"


def _at_k(by_k: dict, k: float) -> tuple[str, dict] | None:
    if not by_k:
        return None
    key = min(by_k, key=lambda x: abs(float(x) - k))
    return key, by_k[key]


ZSD_OK = (0.9, 1.1)


def _pooled(by_season: dict, key: str) -> float | None:
    rows = [(v["n"], v["raw"][key]) for v in (by_season or {}).values()
            if v.get("n") and (v.get("raw") or {}).get(key) is not None]
    n = sum(r[0] for r in rows)
    return round(sum(a * b for a, b in rows) / n, 4) if n else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--synthetic", type=Path, default=TABLES / "pwin_calibration.json")
    ap.add_argument("--league", type=Path, default=TABLES / "pwin_league_history.json")
    ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    syn = json.loads(args.synthetic.read_text(encoding="utf-8"))
    k = float(syn["k_fit"])
    raw, cal = syn["test"]["raw"], syn["test"]["calibrated"]
    syn_ok = cal["logloss"] < raw["logloss"] and cal["brier"] < raw["brier"]
    league = None
    lg_ok = None
    syn_zsd = ((syn.get("margin_z_sd") or {}).get("test_raw"))
    lg_raw_ok = None
    if args.league.is_file():
        top = json.loads(args.league.read_text(encoding="utf-8"))
        served = (top.get("sensitivity_detail") or {}).get("served_mean_advanced_out_of_sample")
        lg = served or top
        hit = _at_k(lg.get("by_k") or {}, k)
        base = lg.get("raw") or (lg.get("by_k") or {}).get("1.0") or (lg.get("by_k") or {}).get("1.00")
        if hit and base:
            kk, at = hit
            lg_ok = at["logloss"] <= base["logloss"] and at["brier"] <= base["brier"]
            pooled = (lg.get("loso") or {}).get("pooled") or {}
            zsd = base.get("zsd")
            lg_raw_ok = bool(pooled and pooled["logloss_raw"] <= pooled["logloss_k"]
                             and pooled["brier_raw"] <= pooled["brier_k"]
                             and zsd is not None and ZSD_OK[0] <= zsd <= ZSD_OK[1])
            league = {"n": lg.get("n") or top.get("n_matchups"), "seasons": top.get("seasons"),
                      "skill_mean": ("advanced_v1 leave-one-season-out" if served
                                     else "gridiron.projection baseline"),
                      "k_grid_point": float(kk), "brier": at["brier"], "brier_raw": base["brier"],
                      "logloss": at["logloss"], "logloss_raw": base["logloss"],
                      "margin_z_sd": zsd, "loso_pooled": pooled,
                      "fav_stated": _pooled(lg.get("by_season"), "fav_stated"),
                      "fav_won": _pooled(lg.get("by_season"), "fav_observed"),
                      "k_mle": lg.get("k_mle"), "k_ci": lg.get("k_ci"),
                      "outcome_label": top.get("outcome_label"),
                      "n_excluded": top.get("n_excluded"),
                      "baseline_mean_check": {"k_mle": top.get("k_mle"), "raw": top.get("raw"),
                                              "loso_pooled": (top.get("loso") or {}).get("pooled")}}
    bar = bool(syn_ok and (lg_ok is not False))
    raw_tested = bool(not bar and syn_zsd is not None and ZSD_OK[0] <= syn_zsd <= ZSD_OK[1]
                      and lg_raw_ok)
    blob = {
        "kind": "sd_scale", "k": 1.0 if raw_tested else k, "bar_cleared": bar,
        "raw_tested": raw_tested, "k_tested": k,
        "rule": "k from the synthetic fit seasons; cleared when it beats k=1 on held-out synthetic "
                "seasons on log loss AND Brier, and is no worse on this league's matchups. "
                "raw_tested (k=1, label TESTED) when it does not clear, the held-out synthetic "
                "margin z-SD is in [0.9, 1.1], and on this league a k fit on the other seasons "
                "does not beat k=1 on the held-out season and the margin z-SD is in [0.9, 1.1]",
        "synthetic": {"n_fit": syn.get("n_fit_matchups"), "fit_seasons": syn.get("fit_seasons"),
                      "n_test": syn.get("n_test_matchups"), "test_seasons": syn.get("test_seasons"),
                      "k_ci": syn.get("k_ci"), "test_brier": cal["brier"], "test_brier_raw": raw["brier"],
                      "test_logloss": cal["logloss"], "test_logloss_raw": raw["logloss"],
                      "test_margin_z_sd": syn_zsd, "skill_mean": syn.get("skill_mean_source"),
                      "improvement_ci": syn.get("improvement_ci"),
                      "baseline_mean_k": syn.get("baseline_mean_k"),
                      "dnp_sensitivity_k": (syn.get("dnp_sensitivity_k") or {}).get("with"),
                      "def_mean_source": syn.get("def_mean_source")},
        "league_history": league or {},
        "doc": "docs/research/PWIN_CALIBRATION.md",
        "scripts": ["scripts/research/pwin_calibration.py", "scripts/research/pwin_league_history.py",
                    "scripts/research/pwin_calibration_save.py"],
        "written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(json.dumps({k_: blob[k_] for k_ in ("k", "k_tested", "bar_cleared", "raw_tested")}),
          f"| factor beats raw held out: {syn_ok} | league no worse at k: {lg_ok} | "
          f"synthetic z-SD {syn_zsd} | league raw holds: {lg_raw_ok}")
    if not args.dry_run:
        args.out.write_text(json.dumps(blob, indent=1), encoding="utf-8")
        print(f"wrote {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
