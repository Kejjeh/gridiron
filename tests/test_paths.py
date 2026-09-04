"""Contract pins on the single paths module."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from gridiron import paths


def test_repo_root_points_at_this_repo():
    assert (paths.REPO_ROOT / "CLAUDE.md").exists()
    assert (paths.REPO_ROOT / "src" / "gridiron" / "paths.py").exists()


def test_all_paths_derive_from_repo_root():
    for p in (paths.DATA, paths.RESEARCH_CACHE, paths.OUTPUTS,
              paths.LEDGER, paths.DOCS, paths.TEST_LOG_DIR):
        assert paths.REPO_ROOT in p.parents, f"{p} does not derive from REPO_ROOT"


def test_env_var_override_wins(tmp_path):
    """CI/containers relocate the root with GRIDIRON_REPO_ROOT. Check in a
    subprocess so the override is evaluated at import time as designed."""
    code = (
        "from gridiron import paths\n"
        "print(paths.REPO_ROOT)\n"
    )
    src = str(Path(__file__).resolve().parent.parent / "src")
    env = dict(os.environ, GRIDIRON_REPO_ROOT=str(tmp_path), PYTHONPATH=src)
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env,
    )
    assert out.returncode == 0, out.stderr
    assert Path(out.stdout.strip()) == tmp_path.resolve()
