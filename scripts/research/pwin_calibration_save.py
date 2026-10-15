"""Write the P(win) calibration the page reads, from the two studies, by a
rule fixed before the league-history numbers were seen.

    PYTHONPATH=src python scripts/research/pwin_calibration_save.py \
        [--synthetic data/research/cache/tables/pwin_calibration.json] \
        [--league data/research/cache/tables/pwin_league_history.json] [--dry-run]

Inputs: `pwin_calibration.py` (synthetic 12-team leagues, k fit on 2019-2022,
tested on 2023-2025) and `pwin_league_history.py` (this league's real
2023-2025 matchups). The factor k is ALWAYS the synthetic fit-season value:
165 real matchups are too few to fit on and are used only as a check.

bar_cleared (the page labels P(win) CALIBRATED only when it is true):
  1. on the held-out synthetic seasons, k beats k = 1 on log loss AND Brier;
  2. on this league's matchups, k is no worse than k = 1 on log loss AND
     Brier (the league-history study reports both at a grid of k; the
     nearest grid point to k is used).
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
    if args.league.is_file():
        lg = json.loads(args.league.read_text(encoding="utf-8"))
        hit = _at_k(lg.get("by_k") or {}, k)
        base = lg.get("raw") or (lg.get("by_k") or {}).get("1.0") or (lg.get("by_k") or {}).get("1.00")
        if hit and base:
            kk, at = hit
            lg_ok = at["logloss"] <= base["logloss"] and at["brier"] <= base["brier"]
            league = {"n": lg.get("n_matchups"), "seasons": lg.get("seasons"),
                      "k_grid_point": float(kk), "brier": at["brier"], "brier_raw": base["brier"],
                      "logloss": at["logloss"], "logloss_raw": base["logloss"],
                      "k_mle": lg.get("k_mle"), "k_ci": lg.get("k_ci"),
                      "outcome_label": lg.get("outcome_label"),
                      "n_excluded": lg.get("n_excluded")}
    bar = bool(syn_ok and (lg_ok is not False))
    blob = {
        "kind": "sd_scale", "k": k, "bar_cleared": bar,
        "rule": "k from the synthetic fit seasons; cleared when it beats k=1 on held-out synthetic "
                "seasons on log loss AND Brier, and is no worse on this league's matchups",
        "synthetic": {"n_fit": syn.get("n_fit_matchups"), "fit_seasons": syn.get("fit_seasons"),
                      "n_test": syn.get("n_test_matchups"), "test_seasons": syn.get("test_seasons"),
                      "k_ci": syn.get("k_ci"), "test_brier": cal["brier"], "test_brier_raw": raw["brier"],
                      "test_logloss": cal["logloss"], "test_logloss_raw": raw["logloss"],
                      "improvement_ci": syn.get("improvement_ci"),
                      "dnp_sensitivity_k": (syn.get("dnp_sensitivity_k") or {}).get("with"),
                      "def_mean_source": syn.get("def_mean_source")},
        "league_history": league or {},
        "doc": "docs/research/PWIN_CALIBRATION.md",
        "scripts": ["scripts/research/pwin_calibration.py", "scripts/research/pwin_league_history.py",
                    "scripts/research/pwin_calibration_save.py"],
        "written": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    print(json.dumps({k_: blob[k_] for k_ in ("k", "bar_cleared")}),
          f"| synthetic ok: {syn_ok} | league ok: {lg_ok}")
    if not args.dry_run:
        args.out.write_text(json.dumps(blob, indent=1), encoding="utf-8")
        print(f"wrote {args.out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
