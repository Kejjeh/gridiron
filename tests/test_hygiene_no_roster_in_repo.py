"""The owner's roster stays on the owner's disk.

`scripts/weekly/report.py --write` renders a table of the players the owner
actually holds. That is exactly the kind of file that gets committed once by
accident and then lives in the history forever, so the boundary is a test
rather than a habit:

  - the rendered weekly report is gitignored, and
  - nothing tracked under data/outputs/ looks like one.

This is about newly committed artifacts. It deliberately does NOT scan
history, and it does not touch the draft-season files that were already
tracked before this rule existed — rewriting shared history to tidy up is a
bigger hazard than the exposure it would clean.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OUTPUTS = ROOT / "data" / "outputs"

#: Markers that only ONE MANAGER'S roster report has. The `lineup` column is
#: the tell: START/BENCH/IR is a statement about who the owner is holding.
#:
#: A player id is deliberately NOT a marker. `data/outputs/draft2026_board.csv`
#: is 983 rows of league-wide ADP keyed by `sleeper_id` and exposes nobody's
#: roster; treating an id column as private would ban the public files rule
#: #10 exists to keep committing.
ROSTER_MARKERS = ("lineup,player", "| lineup ", "## Roster")


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, check=True).stdout


def tracked_outputs() -> list[str]:
    return [l for l in _git("ls-files", "data/outputs/").splitlines() if l]


@pytest.mark.parametrize("pattern", [
    "data/outputs/week02_report.md",
    "data/outputs/week07_report.csv",
    "data/outputs/weekly_report_latest.csv",
])
def test_a_rendered_weekly_report_is_ignored(pattern):
    """`git check-ignore` is the real answer to 'would this get committed?' —
    reading .gitignore by hand is how a pattern silently stops matching."""
    r = subprocess.run(["git", "check-ignore", "-q", pattern], cwd=ROOT)
    assert r.returncode == 0, f"{pattern} is NOT ignored and would be committed"


def test_no_tracked_output_file_is_a_roster_report():
    offenders = []
    for rel in tracked_outputs():
        path = ROOT / rel
        if not path.exists() or path.suffix not in {".md", ".csv"}:
            continue
        head = path.read_text(encoding="utf-8", errors="replace")[:4000]
        if any(m in head for m in ROSTER_MARKERS):
            offenders.append(rel)
    assert not offenders, (
        f"roster-bearing report(s) tracked in the repo: {offenders}. "
        f"Render them locally; they are regenerable from the cache.")


def test_the_report_writer_only_writes_under_data_outputs():
    """A --write that could reach outside data/outputs/ would put the roster
    somewhere nothing is ignoring."""
    source = (ROOT / "scripts" / "weekly" / "report.py").read_text("utf-8")
    assert "OUTPUTS /" in source
    for escape in ("..", "os.path.expanduser", "Path.home()", "/tmp"):
        assert escape not in source, f"report writer references {escape}"
