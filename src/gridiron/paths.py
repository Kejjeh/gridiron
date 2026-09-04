"""ONE root/paths module. Nothing else in the repo may compute the repo root.

The plv_clone scar: 91 files hardcoded the root and all needed migration.
Every path in this repo derives from REPO_ROOT below; CI or a container can
relocate everything with the GRIDIRON_REPO_ROOT env var.
"""
from __future__ import annotations

import os
from pathlib import Path


def _repo_root() -> Path:
    env = os.environ.get("GRIDIRON_REPO_ROOT")
    if env:
        return Path(env).resolve()
    # src/gridiron/paths.py -> src/gridiron -> src -> repo root
    return Path(__file__).resolve().parents[2]


REPO_ROOT: Path = _repo_root()

DATA = REPO_ROOT / "data"
RESEARCH_CACHE = DATA / "research" / "cache"   # bulk pulls, gitignored
OUTPUTS = DATA / "outputs"                     # small weekly CSVs, committed
LEDGER = DATA / "ledger"                       # decision ledger, committed
DOCS = REPO_ROOT / "docs"
TEST_LOG_DIR = REPO_ROOT / ".cache" / "test-logs"


def ensure_dirs() -> None:
    """Create the writable data directories. Call from ingest entry points,
    never at import time (imports must stay side-effect free)."""
    for d in (RESEARCH_CACHE, OUTPUTS, LEDGER, TEST_LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)
