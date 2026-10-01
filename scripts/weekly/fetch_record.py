"""fetch_record.py — download the latest cloud build's decision record. READ-ONLY.

    PYTHONPATH=src python scripts/weekly/fetch_record.py [--run RUN_ID]
        [--repo OWNER/NAME] [--dest data/outputs/cloud]

The public page shows the board; the `dashboard` run artifact of the
"Weekly dashboard artifact" workflow holds the RECORD behind it
(`outputs/dashboard/dashboard_latest.json`, with the usage block) and the
decision-time archives (`ledger/decisions/`). This finds the newest
successful run on main (or `--run`), downloads that artifact and unpacks it
under data/outputs/cloud/<run number>_<run id>/, which is gitignored: the
record names the owner's players.

It reads GitHub only. It never dispatches, re-runs or cancels a workflow,
never calls Sleeper, and adds no credential: listing runs is public; the
download uses the owner's own `gh` login when `gh` is installed, and
otherwise tries the plain API (which GitHub may refuse without a login — the
script then prints the `gh` command to run instead).

Then: `scripts/weekly/weekly_review.py --record <printed path>`.
"""
from __future__ import annotations

import argparse
import io
import json
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

from gridiron.paths import OUTPUTS

WORKFLOW = "dashboard-artifact.yml"
ARTIFACT = "dashboard"
DEFAULT_REPO = "Kejjeh/gridiron"
DEFAULT_DEST = OUTPUTS / "cloud"
API = "https://api.github.com"


def _get(url: str, *, accept: str = "application/vnd.github+json") -> bytes:
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": "gridiron-fetch-record"})
    with urllib.request.urlopen(req, timeout=60) as resp:     # noqa: S310
        return resp.read()


def latest_run(repo: str) -> dict:
    """The newest completed, successful run of the dashboard workflow on main."""
    url = (f"{API}/repos/{repo}/actions/workflows/{WORKFLOW}/runs"
           f"?branch=main&status=success&per_page=5")
    runs = json.loads(_get(url)).get("workflow_runs") or []
    if not runs:
        raise SystemExit(f"[fetch] no successful {WORKFLOW} run on main in {repo}")
    return runs[0]


def run_info(repo: str, run_id: int) -> dict:
    return json.loads(_get(f"{API}/repos/{repo}/actions/runs/{run_id}"))


def artifact_id(repo: str, run_id: int) -> int:
    arts = json.loads(_get(f"{API}/repos/{repo}/actions/runs/{run_id}/artifacts"))
    for a in arts.get("artifacts") or []:
        if a.get("name") == ARTIFACT and not a.get("expired"):
            return int(a["id"])
    raise SystemExit(f"[fetch] run {run_id} has no unexpired '{ARTIFACT}' artifact")


def download(repo: str, run_id: int, target: Path) -> str:
    """Unpack the artifact into `target`. Returns how it was fetched."""
    target.mkdir(parents=True, exist_ok=True)
    if shutil.which("gh"):
        r = subprocess.run(["gh", "run", "download", str(run_id), "-R", repo,
                            "-n", ARTIFACT, "-D", str(target)],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "gh"
        print(f"[fetch] gh run download failed: {r.stderr.strip()[:200]}",
              file=sys.stderr)
    aid = artifact_id(repo, run_id)
    try:
        blob = _get(f"{API}/repos/{repo}/actions/artifacts/{aid}/zip",
                    accept="application/vnd.github+json")
    except Exception as exc:                                  # noqa: BLE001
        raise SystemExit(
            f"[fetch] GitHub refused the artifact download ({exc}). Artifact "
            f"downloads need a GitHub login; run:\n  gh run download {run_id} "
            f"-R {repo} -n {ARTIFACT} -D {target}") from None
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        zf.extractall(target)
    return "api"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", default=DEFAULT_REPO)
    ap.add_argument("--run", type=int, default=None, help="a specific run id")
    ap.add_argument("--dest", type=Path, default=DEFAULT_DEST)
    args = ap.parse_args(argv)

    run = run_info(args.repo, args.run) if args.run else latest_run(args.repo)
    if run.get("conclusion") != "success":
        print(f"[fetch] run {run['id']} concluded {run.get('conclusion')!r}; its "
              f"artifact may be missing or partial", file=sys.stderr)
    target = args.dest / f"{run['run_number']:04d}_{run['id']}"
    how = download(args.repo, int(run["id"]), target)
    record = next(target.rglob("dashboard_latest.json"), None)
    archives = next((p for p in target.rglob("decisions") if p.is_dir()), None)
    print(f"[fetch] run #{run['run_number']} ({run['id']}, {run.get('event')}, "
          f"{run.get('head_sha', '')[:8]}, created {run.get('created_at')}) via {how}")
    print(f"record:   {record or 'NOT FOUND in the artifact'}")
    print(f"archives: {archives or 'none in the artifact'}")
    return 0 if record else 1


if __name__ == "__main__":
    sys.exit(main())
