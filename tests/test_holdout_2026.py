"""holdout_2026.py scores only block_runner.py blocks that passed. A pass
written by another harness (early-season, distribution) or with no block
module is skipped with one line naming it. Synthetic results in a temp dir:
no research cache, no tables, no network."""
from __future__ import annotations

import importlib.util
import json
import sys

from gridiron.paths import REPO_ROOT

RESEARCH = REPO_ROOT / "scripts" / "research"
if str(RESEARCH) not in sys.path:
    sys.path.insert(0, str(RESEARCH))          # holdout_2026 imports block_runner
SPEC = importlib.util.spec_from_file_location("holdout_2026_under_test", RESEARCH / "holdout_2026.py")
H = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(H)


def _dirs(tmp_path):
    results, blocks = tmp_path / "results", tmp_path / "blocks"
    results.mkdir()
    blocks.mkdir()
    for b in ("RB-91", "QB-91", "K-91"):
        (blocks / f"{b}.py").write_text(f"ID = '{b}'\n", encoding="utf-8")
    write = lambda name, res: (results / f"{name}.json").write_text(json.dumps(res), encoding="utf-8")
    write("RB-91", {"block": "RB-91", "passed": ["RB"], "verdict": {"positions": {}}})
    write("QB-91", {"block": "QB-91", "passed": ["QB"], "harness": "scripts/research/block_runner.py"})
    write("RB-92", {"block": "RB-92", "passed": ["RB"]})                      # no module
    write("WR-91", {"block": "WR-91", "passed": ["WR"],
                    "harness": "scripts/research/early_season_backtest.py"})
    write("K-91", {"block": "K-91", "passed": ["K"], "kind": "distribution",
                   "harness_blob": "0123456789ab"})
    write("TE-91", {"block": "TE-91", "passed": []})                          # a fail
    write("WR-91-2026", {"block": "WR-91-2026", "passed": [],
                         "harness": "scripts/research/early_season_backtest.py --holdout-2026"})
    return results, blocks


def test_only_passing_runner_blocks_with_a_module_are_scored(tmp_path):
    results, blocks = _dirs(tmp_path)
    passing, skipped = H.passing_results(results, blocks)
    assert passing == {"QB-91": (["QB"], results / "QB-91.json"),
                       "RB-91": (["RB"], results / "RB-91.json")}
    assert set(skipped) == {"K-91", "RB-92", "WR-91"}
    assert skipped["WR-91"] == "scored by scripts/research/early_season_backtest.py, not block_runner.py"
    assert "harness_blob 0123456789ab" in skipped["K-91"] and "kind distribution" in skipped["K-91"]
    assert skipped["RB-92"] == "no block module scripts/research/blocks/RB-92.py"


def test_no_runner_pass_exits_early_and_names_each_skip(tmp_path, monkeypatch, capsys):
    results, blocks = _dirs(tmp_path)
    for b in ("RB-91", "QB-91"):
        (results / f"{b}.json").unlink()
    monkeypatch.setattr(H.R, "RESULTS_DIR", results)
    monkeypatch.setattr(H.R, "BLOCKS_DIR", blocks)
    out = tmp_path / "holdout_2026.json"
    monkeypatch.setattr(H, "OUT", out)
    assert H.main(["--write"]) == 0                    # before any table is loaded
    lines = capsys.readouterr().out.splitlines()
    assert [x.split(":")[0] for x in lines[:3]] == ["skip K-91", "skip RB-92", "skip WR-91"]
    assert lines[3] == "no block_runner.py block has passed the historical bar: nothing to hold out"
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written["blocks"] == {} and set(written["skipped"]) == {"K-91", "RB-92", "WR-91"}
