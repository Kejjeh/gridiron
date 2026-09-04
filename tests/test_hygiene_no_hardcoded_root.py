"""No script or module may hardcode the repo root (the 91-file plv scar).

Everything derives from gridiron.paths.REPO_ROOT (env-overridable), or from
its own __file__ in the case of the standalone CI scripts.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Absolute Windows paths (C:\Users\...) or POSIX home paths in code.
_BAD = re.compile(r"""["'](?:[A-Za-z]:[\\/]Users|/home/|/Users/)""")


def _py_files():
    for base in ("src", "scripts"):
        yield from (ROOT / base).rglob("*.py")


def test_no_absolute_user_paths_in_code():
    offenders = []
    for f in _py_files():
        text = f.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            if _BAD.search(line) and "test_hygiene" not in f.name:
                offenders.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "Hardcoded user-absolute paths found — derive from gridiron.paths "
        "instead:\n" + "\n".join(offenders)
    )
