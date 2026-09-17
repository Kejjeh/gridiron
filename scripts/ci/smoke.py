"""smoke.py — fast offline sanity check. Run after ANY change.

    python scripts/ci/smoke.py

Two stages, both offline (no ESPN/Sleeper/nflverse calls):
  1. Import the load-bearing single-source modules (catches syntax errors,
     broken imports, and — once models exist — validated-signals registry
     drift at import time).
  2. A pytest subset selected by GLOB (discovery, not enumeration): repo
     hygiene meta-tests, contract pins, and pure-math tests. Target < 60s.
     The full suite is still
     `python scripts/ci/run_summary.py -- python -m pytest`.

Exit 0 = safe to proceed. Nonzero = a contract you touched broke; read the
pytest output above the summary line.

Ported from plv_clone scripts/ci/smoke.py; only IMPORTS/PATTERNS and the
anti-vacuity floor are repo-specific.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = REPO / "tests"
SRC = REPO / "src"

# Light, dependency-cheap modules whose import-time asserts are themselves
# guards. Add gridiron.models.<x>.validated_signals here the day the first
# model lands.
IMPORTS = [
    "gridiron.paths",
    "gridiron.league_config",
    "gridiron.scoring",
    "gridiron.config",
    "gridiron.winprob",
    "gridiron.shrinkage",
    "gridiron.vegas",
    "gridiron.season",
    "gridiron.draft",
    "gridiron.ledger",
    "gridiron.ids",
    "gridiron.freshness",
    "gridiron.sleeper",
    "gridiron.ingest",
    "gridiron.usage",
    "gridiron.weekly",
]

# Glob patterns, so newly added hygiene/contract tests join the smoke set on
# the day they are written.
PATTERNS = [
    "test_ids.py",
    "test_freshness.py",
    "test_sleeper.py",
    "test_ingest.py",
    "test_scoring_nflverse.py",
    "test_verify_league_settings.py",
    "test_ledger*.py",
    "test_draft*.py",
    "test_claude_md_budget.py",
    "test_hygiene_*.py",
    "test_paths.py",
    "test_scoring.py",
    "test_league_config.py",
    "test_winprob.py",
    "test_shrinkage.py",
    "test_vegas.py",
    "test_season.py",
]

# Anti-vacuity floor: a glob typo must not silently shrink the smoke set.
# Raise this as smoke tests are added (plv_clone ended at 8+).
MIN_FILES = 17


def main() -> int:
    env = dict(
        os.environ,
        PYTHONUTF8="1",
        PYTHONIOENCODING="utf-8",
        PYTHONPATH=str(SRC) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )

    print("[smoke 1/2] importing load-bearing modules...", flush=True)
    code = (
        "import importlib,sys\n"
        f"mods = {IMPORTS!r}\n"
        "for m in mods:\n"
        "    importlib.import_module(m)\n"
        "print('imports OK:', len(mods))\n"
    )
    r = subprocess.run([sys.executable, "-X", "utf8", "-c", code],
                       cwd=REPO, env=env)
    if r.returncode != 0:
        print("[smoke] FAIL at import stage", flush=True)
        return r.returncode

    files = sorted({p for pat in PATTERNS for p in TESTS.glob(pat)})
    if len(files) < MIN_FILES:
        print(f"[smoke] FAIL: only {len(files)} smoke test files matched — "
              f"expected >= {MIN_FILES}. Fix PATTERNS in scripts/ci/smoke.py.")
        return 2

    print(f"[smoke 2/2] pytest on {len(files)} contract/hygiene files...",
          flush=True)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         *map(str, files)],
        cwd=REPO, env=env)
    print(f"[smoke] {'PASS' if r.returncode == 0 else 'FAIL'}", flush=True)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
