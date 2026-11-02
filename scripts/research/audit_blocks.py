"""Audit the pre-registration integrity of every scored roadmap block.

    PYTHONPATH=src python scripts/research/audit_blocks.py [--repo PATH] [--block ID ...]

Mechanical checks, from git and the committed files only (no scores are
computed). For every block with a run ledger (docs/research/blocks/runs/<ID>.csv):

  ledger      one scored run, or more only when the Result section says why
              (a bug fix): the README's rule
  committed   the ledger's pre-registration and module blobs exist in git
              (the runner records the committed blob of each, and refuses an
              uncommitted or modified file)
  unchanged   the pre-registration's text above "## Result" today is the text
              that was committed when the last run was scored, and the module
              is the module that was scored
  registered  the doc says PRE-REGISTERED and that it was written before any
              score was seen
  verdict     results/<ID>.json and the ledger's last verdict agree, and the
              Result section states that verdict (pass or fail)

It prints one line per block and a summary, and exits 1 when any check
fails. It cannot see what a person or agent looked at outside the runner:
that is what the reviews are for.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

from gridiron.paths import REPO_ROOT


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout


def blob_text(repo: Path, blob: str) -> str | None:
    try:
        return git(repo, "cat-file", "-p", blob)
    except subprocess.CalledProcessError:
        return None


def above_result(text: str) -> str:
    i = text.find("\n## Result")
    return (text if i < 0 else text[:i]).rstrip()


def audit(repo: Path, block: str) -> list[str]:
    problems = []
    blocks = repo / "docs" / "research" / "blocks"
    ledger = blocks / "runs" / f"{block}.csv"
    rows = list(csv.DictReader(ledger.open(encoding="utf-8")))
    doc_path = blocks / f"{block}.md"
    doc = doc_path.read_text(encoding="utf-8") if doc_path.exists() else ""
    result_text = doc[doc.find("\n## Result"):] if "\n## Result" in doc else ""
    if not rows:
        return ["ledger is empty"]
    if len(rows) > 1 and not re.search(r"\bbug\b|second (scored )?run|re-?run", result_text, re.I):
        problems.append(f"{len(rows)} scored runs and the Result section gives no reason")
    last = rows[-1]
    pre = blob_text(repo, last.get("prereg_blob") or "")
    mod = blob_text(repo, last.get("module_blob") or "")
    if pre is None:
        problems.append("the ledger's pre-registration blob is not in git")
    elif above_result(pre) != above_result(doc):
        problems.append("the pre-registration above '## Result' changed after the run")
    if not re.search(r"PRE-REGISTERED", above_result(doc)):
        problems.append("the doc does not say PRE-REGISTERED")
    if not re.search(r"before any score", above_result(doc), re.I):
        problems.append("the doc does not say it was written before any score")
    module = None
    for cand in (repo / "scripts" / "research" / "blocks" / f"{block}.py",):
        if cand.exists():
            module = cand.read_text(encoding="utf-8")
    if mod is None:
        if last.get("module_blob") not in ("", "-", None):
            problems.append("the ledger's module blob is not in git")
    elif module is not None and mod != module:
        problems.append("the module changed after the last scored run")
    res_path = blocks / "results" / f"{block}.json"
    if res_path.exists():
        res = json.loads(res_path.read_text(encoding="utf-8"))
        passed = res.get("passed")
        verdict = str(last.get("verdict") or "")
        if passed is not None:
            if bool(passed) != verdict.upper().startswith("PASS"):
                problems.append(f"results JSON passed={passed} but the ledger says {verdict!r}")
        says_pass = re.search(r"\*\*Pass(es)?\b", result_text) or re.search(r"\bPASS(ES)?\b", result_text)
        says_fail = re.search(r"\*\*Fails?\b", result_text) or re.search(r"\bfails?\b", result_text, re.I)
        if verdict.upper().startswith("PASS") and not says_pass:
            problems.append("the ledger passes but the Result section does not say so")
        if verdict.upper().startswith("FAIL") and not says_fail:
            problems.append("the ledger fails but the Result section does not say so")
    else:
        problems.append("no results JSON")
    if not result_text.strip():
        problems.append("no Result section")
    return problems


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", type=Path, default=REPO_ROOT)
    ap.add_argument("--block", action="append", default=[])
    args = ap.parse_args(argv)
    runs = args.repo / "docs" / "research" / "blocks" / "runs"
    blocks = args.block or sorted(p.stem for p in runs.glob("*.csv"))
    bad = 0
    for b in blocks:
        problems = audit(args.repo, b)
        n = sum(1 for _ in open(runs / f"{b}.csv", encoding="utf-8")) - 1
        print(f"{b:<7} {n} run(s)  " + ("ok" if not problems else "PROBLEM: " + "; ".join(problems)))
        bad += bool(problems)
    print(f"{len(blocks) - bad}/{len(blocks)} blocks pass the integrity audit")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
