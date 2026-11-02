"""audit_blocks.py checks a scored block's integrity from git alone. Tested on a
synthetic repo in a temp dir: a block_runner.py block, a block scored by a
named harness (P9 style: no block module, the ledger's module_blob is the
harness) and one whose results JSON carries the harness blob (P7 style). No
research cache, no network, and never the checkout's own git state."""
from __future__ import annotations

import csv
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gridiron.paths import REPO_ROOT

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="needs git")

SPEC = importlib.util.spec_from_file_location(
    "audit_blocks_under_test", REPO_ROOT / "scripts" / "research" / "audit_blocks.py")
AUD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUD)

FIELDS = ["when", "block", "prereg_blob", "module_blob", "helpers_blob", "tables_digest", "head",
          "verdict", "positions", "folds", "folds_passed"]
PREREG = ("# {id}: a synthetic block (pre-registered 2026-11-02)\n\n"
          "**Status: PRE-REGISTERED.** Written before any score was seen.\n\n"
          "## Idea\nSynthetic.\n\n## Result\n")


class Repo:
    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.git("init", "-q")
        top = Path(self.git("rev-parse", "--show-toplevel")).resolve()
        assert top == root.resolve(), "the test repo must be its own repository"

    def git(self, *args: str) -> str:
        return subprocess.run(["git", "-C", str(self.root), "-c", "user.name=Test",
                               "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false",
                               *args], check=True, capture_output=True, text=True).stdout.strip()

    def write(self, rel: str, text: str) -> None:
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def append(self, rel: str, text: str) -> None:
        self.write(rel, (self.root / rel).read_text(encoding="utf-8") + text)

    def commit(self, msg: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", msg)
        return self.git("rev-parse", "HEAD")[:12]

    def blob(self, rel: str) -> str:
        return self.git("rev-parse", f"HEAD:{rel}")[:12]

    def score(self, block: str, module_blob: str, head: str, **result) -> None:
        """What a harness writes after one scored run (here: a fail), then the
        Result section and the commit."""
        b = "docs/research/blocks"
        row = {"when": "2026-11-02T00:00:00+00:00", "block": block,
               "prereg_blob": self.blob(f"{b}/{block}.md"), "module_blob": module_blob,
               "helpers_blob": "-", "tables_digest": "0", "head": head, "verdict": "FAIL",
               "positions": "WR", "folds": "2019", "folds_passed": "WR:0/1"}
        (self.root / b / "runs").mkdir(parents=True, exist_ok=True)
        with (self.root / b / "runs" / f"{block}.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            w.writeheader()
            w.writerow(row)
        self.write(f"{b}/results/{block}.json", json.dumps(
            {"block": block, "passed": [], "summary": "fail", "prereg_blob": row["prereg_blob"],
             "module_blob": module_blob, "head": head, **result}, indent=1) + "\n")
        self.append(f"{b}/{block}.md", "One scored run. It fails.\n")
        self.commit(f"{block} result")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for var in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
                "GIT_COMMON_DIR", "GIT_CEILING_DIRECTORIES"):
        monkeypatch.delenv(var, raising=False)
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    return Repo(tmp_path / "repo")


def _audit(repo: Repo, block: str) -> tuple[list[str], list[str]]:
    notes: list[str] = []
    return AUD.audit(repo.root, block, notes), notes


def test_a_runner_block_still_gets_the_module_check(repo):
    repo.write("docs/research/blocks/WR-91.md", PREREG.format(id="WR-91"))
    repo.write("scripts/research/blocks/WR-91.py", "ID = 'WR-91'\n")
    head = repo.commit("pre-register WR-91")
    repo.score("WR-91", repo.blob("scripts/research/blocks/WR-91.py"), head)
    assert _audit(repo, "WR-91") == ([], [])            # no harness named, nothing to note
    repo.write("scripts/research/blocks/WR-91.py", "ID = 'WR-91'\nX = 1\n")
    repo.commit("edit the module after the run")
    assert _audit(repo, "WR-91")[0] == ["the module changed after the last scored run"]


def test_a_named_harness_that_changed_is_a_problem_unless_the_result_names_blob_and_change(repo):
    repo.write("docs/research/blocks/WR-92.md", PREREG.format(id="WR-92"))
    repo.write("scripts/research/early.py", "print('v1')\n")
    head = repo.commit("pre-register WR-92 and its harness")
    scored = repo.blob("scripts/research/early.py")
    repo.score("WR-92", scored, head, harness="scripts/research/early.py --holdout-2026")
    problems, notes = _audit(repo, "WR-92")
    assert problems == [] and notes == [f"harness scripts/research/early.py: {scored}, unchanged"]

    repo.write("scripts/research/early.py", "print('v2')\n")
    change = repo.commit("change the harness after the run")[:7]
    problems, _ = _audit(repo, "WR-92")
    assert len(problems) == 1
    assert f"scripts/research/early.py changed after the run (in {change})" in problems[0]
    assert scored in problems[0] and "does not name the scoring blob" in problems[0]

    # naming the blob alone is not enough: each change since the run is named too
    repo.append("docs/research/blocks/WR-92.md", f"Scored by harness blob `{scored}`.\n")
    problems, _ = _audit(repo, "WR-92")
    assert problems and problems[0].endswith(f"names the scoring blob but not commit {change}")

    repo.append("docs/research/blocks/WR-92.md", f"Changed after the run in `{change}`, additively.\n")
    problems, notes = _audit(repo, "WR-92")
    assert problems == []
    assert notes and notes[0].endswith(f"the Result section names the scoring blob and {change}")


def test_a_later_harness_change_the_result_does_not_name_is_a_problem_again(repo):
    repo.write("docs/research/blocks/WR-95.md", PREREG.format(id="WR-95"))
    repo.write("scripts/research/early.py", "print('v1')\n")
    head = repo.commit("pre-register WR-95 and its harness")
    scored = repo.blob("scripts/research/early.py")
    repo.score("WR-95", scored, head, harness="scripts/research/early.py")
    repo.write("scripts/research/early.py", "print('v2')\n")
    first = repo.commit("an additive change")[:7]
    repo.append("docs/research/blocks/WR-95.md",
                f"Scored by harness blob `{scored}`; changed after the run in `{first}`, additively.\n")
    repo.commit("document the change")
    assert _audit(repo, "WR-95")[0] == []

    repo.write("scripts/research/early.py", "print('v3')\n")
    second = repo.commit("a second change, not documented")[:7]
    problems, _ = _audit(repo, "WR-95")
    assert len(problems) == 1
    assert f"(in {first}, {second})" in problems[0]
    assert problems[0].endswith(f"names the scoring blob but not commit {second}")


def test_a_harness_blob_finds_its_harness_in_the_runs_commit(repo):
    repo.write("docs/research/blocks/K-91.md", PREREG.format(id="K-91"))
    repo.write("scripts/research/blocks/K-91.py", "ID = 'K-91'\nKIND = 'distribution'\n")
    repo.write("scripts/research/dist.py", "print('harness v1')\n")
    head = repo.commit("pre-register K-91 and the distribution harness")
    scored = repo.blob("scripts/research/dist.py")
    repo.score("K-91", repo.blob("scripts/research/blocks/K-91.py"), head, harness_blob=scored)
    problems, notes = _audit(repo, "K-91")
    assert problems == [] and notes == [f"harness scripts/research/dist.py: {scored}, unchanged"]

    repo.write("scripts/research/dist.py", "print('harness v2')\n")
    repo.commit("change the harness after the run")
    problems, _ = _audit(repo, "K-91")
    assert len(problems) == 1 and "scripts/research/dist.py changed after the run" in problems[0]

    repo.write("scripts/research/blocks/K-91.py", "ID = 'K-91'\nKIND = 'distribution'\nX = 1\n")
    repo.commit("and the block module")
    assert "the module changed after the last scored run" in _audit(repo, "K-91")[0]


def test_a_holdout_checks_its_ideas_module_and_its_harness(repo):
    repo.write("docs/research/blocks/K-93-2026.md", PREREG.format(id="K-93-2026"))
    repo.write("scripts/research/blocks/K-93.py", "ID = 'K-93'\n")
    repo.write("scripts/research/dist.py", "print('harness v1')\n")
    head = repo.commit("pre-register the K-93 holdout")
    scored = repo.blob("scripts/research/dist.py")
    repo.score("K-93-2026", repo.blob("scripts/research/blocks/K-93.py"), head, idea="K-93",
               harness="scripts/research/dist.py --holdout-2026", harness_blob=scored)
    assert _audit(repo, "K-93-2026") == ([], [f"harness scripts/research/dist.py: {scored}, unchanged"])
    repo.write("scripts/research/blocks/K-93.py", "ID = 'K-93'\nX = 1\n")
    repo.commit("edit the idea's module after the holdout run")
    assert _audit(repo, "K-93-2026")[0] == ["the module changed after the last scored run"]


def test_a_scoring_harness_blob_that_is_not_in_git_is_a_problem(repo):
    repo.write("docs/research/blocks/K-92.md", PREREG.format(id="K-92"))
    repo.write("scripts/research/blocks/K-92.py", "ID = 'K-92'\n")
    head = repo.commit("pre-register K-92")
    repo.score("K-92", repo.blob("scripts/research/blocks/K-92.py"), head,
               harness_blob="0123456789ab")
    assert _audit(repo, "K-92")[0] == ["the scoring harness blob 0123456789ab is not in git"]

    # P9 style: the ledger's module_blob is the harness, reported once
    repo.write("docs/research/blocks/WR-93.md", PREREG.format(id="WR-93"))
    head = repo.commit("pre-register WR-93")
    repo.score("WR-93", "0123456789ab", head, harness="scripts/research/early.py")
    assert _audit(repo, "WR-93")[0] == ["the ledger's module blob is not in git"]


def test_main_counts_the_harness_blocks_and_exits_on_a_problem(repo, capsys):
    repo.write("docs/research/blocks/WR-94.md", PREREG.format(id="WR-94"))
    repo.write("scripts/research/early.py", "print('v1')\n")
    head = repo.commit("pre-register WR-94")
    repo.score("WR-94", repo.blob("scripts/research/early.py"), head,
               harness="scripts/research/early.py")
    assert AUD.main(["--repo", str(repo.root)]) == 0
    out = capsys.readouterr().out
    assert "1/1 blocks pass" in out and "1 of the 1 was scored by a separate harness" in out
    repo.write("scripts/research/early.py", "print('v2')\n")
    repo.commit("change the harness")
    assert AUD.main(["--repo", str(repo.root)]) == 1
    assert "PROBLEM: the harness scripts/research/early.py changed" in capsys.readouterr().out
