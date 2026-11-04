"""Audit the pre-registration integrity of every scored roadmap block.

    PYTHONPATH=src python scripts/research/audit_blocks.py [--repo PATH] [--block ID ...]

Mechanical checks, from git and the committed files only (no scores are
computed). For every block with a run ledger (docs/research/blocks/runs/<ID>.csv):

  ledger      one scored run, or more only when the Result section says why
              (a bug fix): the README's rule
  committed   the ledger's pre-registration and module blobs exist in git
              (the runner records the committed blob of each, and refuses an
              uncommitted or modified file), and so does the scoring harness
              blob of a block scored by a separate harness
  unchanged   the pre-registration's text above "## Result" today is the text
              that was committed when the last run was scored, the module
              is the module that was scored, and a separate harness is the
              harness that was scored (below)
  registered  the doc says PRE-REGISTERED and that it was written before any
              score was seen
  verdict     results/<ID>.json and the ledger's last verdict agree, and the
              Result section states that verdict (pass or fail)

A block scored by a separate harness, not block_runner.py, says so in its
results JSON, in one of two ways:

  "harness"       a path (P9: early_season_backtest.py). There is no block
                  module; the ledger's module_blob is the harness's blob.
  "harness_blob"  the harness's blob (P7: distribution_backtest.py). The block
                  module is the ledger's module_blob; the harness is the
                  "harness" path when one is named too, else the file with
                  that blob in the run's commit ("head").

That harness's blob at HEAD must be the blob that scored the block. A harness
changed after the run passes only when the Result section names the scoring
blob (its 12-hex prefix) and every commit that changed the harness since the
run's commit (its 7-hex prefix): the documented exception, where the doc says
why each change does not touch the result. A later change that the Result
section does not name is a problem again. The audit checks the names, not
the reasons.

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

RUNNER = "block_runner.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout


def blob_text(repo: Path, blob: str) -> str | None:
    try:
        return git(repo, "cat-file", "-p", blob)
    except subprocess.CalledProcessError:
        return None


def is_blob(repo: Path, blob: str) -> bool:
    if not blob or blob == "-":
        return False
    try:
        return git(repo, "cat-file", "-t", blob).strip() == "blob"
    except subprocess.CalledProcessError:
        return False


def head_blob(repo: Path, path: str) -> str | None:
    """The full blob hash of `path` in HEAD, or None when HEAD has no such file."""
    try:
        return git(repo, "rev-parse", "--verify", "--quiet", f"HEAD:{path}").strip() or None
    except subprocess.CalledProcessError:
        return None


def files_with_blob(repo: Path, commit: str, blob: str) -> list[str] | None:
    """Paths in `commit`'s tree whose blob starts with `blob`; None when the
    commit is not in git."""
    try:
        tree = git(repo, "ls-tree", "-r", "-z", commit)
    except subprocess.CalledProcessError:
        return None
    out = []
    for entry in tree.split("\0"):
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) == 3 and parts[1] == "blob" and parts[2].startswith(blob):
            out.append(path)
    return out


def changes_since(repo: Path, commit: str, path: str) -> list[str] | None:
    """Full hashes of the commits after `commit` up to HEAD that changed `path`,
    oldest first; None when the commit is not in git."""
    try:
        git(repo, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
        out = git(repo, "log", "--reverse", "--format=%H", f"{commit}..HEAD", "--", path)
    except subprocess.CalledProcessError:
        return None
    return out.split()


def above_result(text: str) -> str:
    i = text.find("\n## Result")
    return (text if i < 0 else text[:i]).rstrip()


def scoring_harness(res: dict, last: dict) -> tuple[str | None, str | None]:
    """(harness path or None, scoring blob) of a block scored by a separate
    harness; (None, None) for a block_runner.py block, whose results name
    neither (the runner records no blob of itself)."""
    named = str(res.get("harness") or "").split()   # "path [--flags]"
    if res.get("harness_blob"):
        return (named[0] if named else None), str(res["harness_blob"])
    if named and Path(named[0]).name != RUNNER:
        return named[0], str(last.get("module_blob") or "")
    return None, None


def harness_problems(repo: Path, block: str, res: dict, last: dict, result_text: str,
                     notes: list[str]) -> list[str]:
    path, blob = scoring_harness(res, last)
    if blob is None:
        return []
    if not blob or blob == "-":
        return ["the results JSON names a harness but no scoring harness blob is recorded"]
    if not is_blob(repo, blob):
        if blob == (last.get("module_blob") or ""):
            return []           # reported once, as the ledger's module blob
        return [f"the scoring harness blob {blob} is not in git"]
    head = str(res.get("head") or last.get("head") or "")
    if path is None:
        found = files_with_blob(repo, head, blob) if head else None
        if found is None:
            return [(f"the run's commit {head or '(not recorded)'} is not in git, "
                     f"so the harness with blob {blob} cannot be found")]
        found = [p for p in found if p != f"scripts/research/blocks/{block}.py"]
        if len(found) != 1:
            return [(f"the scoring harness blob {blob} matches {len(found)} files at the "
                     f"run's commit {head}")]
        path = found[0]
    now = head_blob(repo, path)
    if now is None:
        return [f"the harness {path} is not in HEAD"]
    if now.startswith(blob):
        notes.append(f"harness {path}: {blob[:12]}, unchanged")
        return []
    # changed: the documented exception names the scoring blob AND every later
    # change, so a change made after the doc was written is caught again
    changes = changes_since(repo, head, path) if head else None
    if changes is None:
        return [(f"the harness {path} changed after the run ({blob[:12]} scored, {now[:12]} at "
                 f"HEAD), and the run's commit {head or '(not recorded)'} is not in git, so the "
                 "changes cannot be listed")]
    since = ", ".join(c[:7] for c in changes) or "no commit found"
    what = f"the harness {path} changed after the run (in {since}): {blob[:12]} scored, {now[:12]} at HEAD"
    if blob[:12] not in result_text:
        return [f"{what}, and the Result section does not name the scoring blob"]
    unnamed = [c[:7] for c in changes if c[:7] not in result_text]
    if unnamed:
        return [(f"{what}, and the Result section names the scoring blob but not "
                 f"commit {', '.join(unnamed)}")]
    notes.append(f"harness {path}: {blob[:12]} scored, {now[:12]} at HEAD; "
                 f"the Result section names the scoring blob and {since}")
    return []


def audit(repo: Path, block: str, notes: list[str] | None = None) -> list[str]:
    notes = [] if notes is None else notes
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
    res_path = blocks / "results" / f"{block}.json"
    res = json.loads(res_path.read_text(encoding="utf-8")) if res_path.exists() else None
    harness_path, harness_blob = scoring_harness(res or {}, last)
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
    # with a named harness and no harness_blob, module_blob is the harness:
    # it is checked against the harness below, never against a block module.
    # A holdout (<ID>-2026) names its idea; the idea's module is the one scored.
    if not (harness_path and harness_blob == (last.get("module_blob") or "")):
        idea = str((res or {}).get("idea") or block)
        for cand in dict.fromkeys(repo / "scripts" / "research" / "blocks" / f"{b}.py"
                                  for b in (block, idea)):
            if cand.exists():
                module = cand.read_text(encoding="utf-8")
                break
    if mod is None:
        if last.get("module_blob") not in ("", "-", None):
            problems.append("the ledger's module blob is not in git")
    elif module is not None and mod != module:
        problems.append("the module changed after the last scored run")
    if res is not None:
        problems += harness_problems(repo, block, res, last, result_text, notes)
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
    bad = harnessed = 0
    for b in blocks:
        notes: list[str] = []
        problems = audit(args.repo, b, notes)
        n = sum(1 for _ in open(runs / f"{b}.csv", encoding="utf-8")) - 1
        print(f"{b:<7} {n} run(s)  " + ("ok" if not problems else "PROBLEM: " + "; ".join(problems))
              + "".join(f" ({x})" for x in notes))
        bad += bool(problems)
        res = runs.parent / "results" / f"{b}.json"
        if res.exists() and scoring_harness(json.loads(res.read_text(encoding="utf-8")), {})[1] is not None:
            harnessed += 1
    print(f"{len(blocks) - bad}/{len(blocks)} blocks pass the integrity audit")
    if harnessed:
        print(f"{harnessed} of the {len(blocks)} {'was' if harnessed == 1 else 'were'} scored by a "
              "separate harness: its blob at HEAD checked against the scoring blob, and each later "
              "change against the Result section")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
