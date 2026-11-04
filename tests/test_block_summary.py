"""block_summary.py turns the committed results into RESULTS.md. Tested on a
synthetic roadmap, results, holdout record and round-2 modules in a temp dir:
no research cache. It must count a harness's "fail (DEF): ..." as a fail,
show a 2026 holdout beside a historical pass, list every round-2 block and
leave no results/*.json out."""
from __future__ import annotations

import importlib.util
import json

import pytest

from gridiron.paths import REPO_ROOT

SPEC = importlib.util.spec_from_file_location(
    "block_summary_under_test", REPO_ROOT / "scripts" / "research" / "block_summary.py")
S = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(S)

ROADMAP = """# Roadmap
| ID | idea | data | kind | phase | note |
|---|---|---|---|---|---|
| QB-91 | Runner pass, holdout pass | pbp | F | 1 | - |
| RB-91 | Runner fail | pbp | F | 1 | - |
| WR-91 | Early-season pass, holdout fail | draft | A | 9 | - |
| TE-91 | Not run | pbp | F | 1 | - |
| K-91 | Distribution pass | pbp | D | 7 | - |
| DEF-91 | Distribution fail | pbp | D | 7 | - |
| QB-92 | Market projection | props | M | 1 | - |
"""
R2_MODULE = '''import _round2 as R2

ID = "{id}"
TITLE = "Round 2: {a} + {b} on {pos} (declared post-hoc combination)"
PARTS = ("{a}", "{b}")
COLUMNS = {{"{pos}": R2.columns(PARTS, "{pos}")}}


def build(ctx):
    return R2.combine(ctx, PARTS, "{pos}")
'''


def _runner(pos: str, folds_better: int, passed: bool) -> dict:
    return {"verdict": {"positions": {pos: {"folds_better_on_both": folds_better, "folds": 7,
                                            "passes": passed, "mean_pairwise_delta": 0.0042,
                                            "mean_mae_delta": -0.0357}}},
            "passed": [pos] if passed else [], "folds_tested": [2019, 2020, 2021]}


@pytest.fixture
def tree(tmp_path, monkeypatch):
    results, blocks = tmp_path / "blocks" / "results", tmp_path / "code"
    results.mkdir(parents=True)
    blocks.mkdir()
    (tmp_path / "roadmap.md").write_text(ROADMAP, encoding="utf-8")
    res = {
        "QB-91": _runner("QB", 7, True),
        "RB-91": _runner("RB", 2, False),
        "WR-91": {"summary": "PASS WR", "passed": ["WR"], "folds": "WR 7/7",
                  "verdict": {"cand_beats_page_by_fold": {}}, "harness": "early.py",
                  "folds_tested": [2019, 2020]},
        "WR-91-2026": {"summary": "fail", "passed": [], "idea": "WR-91", "folds": "WR 2026 0/1"},
        "K-91": {"summary": "PASS (K): lower CRPS in 7/7 folds", "passed": ["K"], "verdict": "PASS K",
                 "folds": "K CRPS 7/7", "harness_blob": "0123456789ab"},
        "DEF-91": {"summary": "fail (DEF): lower CRPS in 7/7 folds", "passed": [], "verdict": "FAIL",
                   "folds": "DEF CRPS 7/7", "harness_blob": "0123456789ab"},
        "RB-R2": _runner("RB", 7, True),
        "QB-R2": _runner("QB", 6, False),
        "TE-99": _runner("TE", 1, False),               # in no roadmap table
    }
    for name, r in res.items():
        (results / f"{name}.json").write_text(json.dumps(r), encoding="utf-8")
    for bid, a, b, pos in (("RB-R2", "RB-06", "RB-02", "RB"), ("QB-R2", "QB-02", "QB-11", "QB"),
                           ("WR-R2", "WR-07", "WR-11", "WR")):
        (blocks / f"{bid}.py").write_text(R2_MODULE.format(id=bid, a=a, b=b, pos=pos), encoding="utf-8")
    holdout = tmp_path / "blocks" / "holdout_2026.json"
    holdout.write_text(json.dumps({"season": 2026, "through_week": 7, "blocks": {
        "QB-91": {"verdict": {"QB": {"better_on_both": True, "eligible": False}}},
        "RB-R2": {"verdict": {"RB": {"better_on_both": False, "eligible": False}}}}}),
        encoding="utf-8")
    for name, value in (("ROADMAP", tmp_path / "roadmap.md"), ("RESULTS", results),
                        ("HOLDOUT", holdout), ("BLOCKS", blocks),
                        ("OUT", tmp_path / "blocks" / "RESULTS.md")):
        monkeypatch.setattr(S, name, value)
    return tmp_path, sorted(res)


def _line(text: str, block: str) -> str:
    return next(x for x in text.splitlines() if x.startswith(f"| {block} |"))


def test_pass_and_fail_read_the_start_of_a_summary_or_verdict():
    assert S.is_fail("fail (DEF): lower CRPS") and S.is_fail("FAIL") and S.is_fail("fail")
    assert S.is_pass("PASS (K): lower CRPS") and S.is_pass("PASS WR")
    assert not S.is_pass("pre-registered, not scored") and not S.is_fail("not run yet")


def test_the_header_counts_a_harness_fail_and_a_pass_that_fails_its_holdout(tree, capsys):
    assert S.main([]) == 0
    text = capsys.readouterr().out
    assert ("7 ideas: 2 pass the historical bar (1 of them also passes a 2026 holdout: a "
            "record-only contender), 1 passes history and fails its 2026 holdout, "
            "2 fail, 2 have no result (see the last column).") in text
    assert _line(text, "WR-91").endswith("| PASS WR; 2026 holdout (WR-91-2026): fail |")
    assert _line(text, "QB-91").endswith(
        "| PASS QB; 2026 holdout (holdout_2026.json, weeks 4-7): PASS QB |")
    assert _line(text, "DEF-91").endswith("| fail (DEF): lower CRPS in 7/7 folds |")
    assert "| 2019, 2020 |" in _line(text, "WR-91")      # a season list, not its repr


def test_round_two_lists_every_module_with_its_parts_and_result(tree, capsys):
    S.main([])
    text = capsys.readouterr().out
    assert "## Round 2 (declared post-hoc combinations, docs/research/blocks/ROUND2.md)" in text
    assert ("3 blocks: 1 passes part 1 and fails part 2 (the 2026 holdout), 1 fails, "
            "1 pre-registered, not scored.") in text      # no "0 pass part 1" beside it
    r2 = [x for x in text.splitlines() if x.startswith("| ") and "-R2 |" in x.split("|")[1] + "|"]
    assert [x.split(" | ")[0] for x in r2] == ["| QB-R2", "| RB-R2", "| WR-R2"]
    assert _line(text, "RB-R2").startswith("| RB-R2 | RB-06 + RB-02 | RB | 2019, 2020, 2021 | RB 7/7 |")
    assert _line(text, "RB-R2").endswith("| PASS RB; 2026 holdout (holdout_2026.json, weeks 4-7): fail |")
    assert _line(text, "WR-R2") == "| WR-R2 | WR-07 + WR-11 | WR |  |  |  |  | pre-registered, not scored |"


def test_every_result_appears_and_write_goes_to_the_out_path(tree, capsys):
    root, stems = tree
    S.main(["--write"])
    assert capsys.readouterr().out.startswith("wrote ")
    text = (root / "blocks" / "RESULTS.md").read_text(encoding="utf-8")
    assert "## Other results" in text and _line(text, "TE-99").startswith("| TE-99 | 2019, 2020, 2021 |")
    assert [s for s in stems if f"| {s} |" not in text and f"({s})" not in text] == []
