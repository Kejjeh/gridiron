#!/usr/bin/env python
"""Token-saving test/build output summarizer.

Runs an arbitrary command, captures combined stdout+stderr, writes the FULL
log to a cache file, and prints only a compact summary to stdout. Always
exits with the underlying command's exit code so failures still register.

This is a native, Windows-compatible replacement for the WSL-only
"context-saver" MCP tool. Pure standard library, no dependencies.

Usage
-----
    python scripts/ci/run_summary.py -- python -m pytest -q
    python scripts/ci/run_summary.py -- python -m pytest tests/test_scoring.py
    python scripts/ci/run_summary.py pytest tests/          # convenience default

The first form (everything after a literal ``--``) runs that command verbatim.
The convenience form treats the leading token ``pytest`` as ``python -m pytest``.

When a summary isn't enough, read the full log at the printed path.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess
import sys
from pathlib import Path

# Repo root is two levels up from scripts/ci/run_summary.py
REPO_ROOT = Path(__file__).resolve().parents[2]
LOG_DIR = REPO_ROOT / ".cache" / "test-logs"

# Threshold below which we just print the raw output (no point summarizing).
SMALL_OUTPUT_LINES = 80

# Generic-fallback line matcher (non-pytest commands).
_GENERIC_PAT = re.compile(r"error|fail|exception|traceback|fatal|panic", re.IGNORECASE)
_TRAIL_LINES = 20


def _parse_argv(argv: list[str]) -> list[str]:
    """Resolve the command to run from CLI args.

    - ``run_summary.py -- foo bar``  -> ``["foo", "bar"]``
    - ``run_summary.py pytest ...``  -> ``[sys.executable, "-m", "pytest", ...]``
    - ``run_summary.py foo bar``     -> ``["foo", "bar"]``
    """
    if not argv:
        raise SystemExit(
            "usage: python scripts/ci/run_summary.py -- <command> [args...]\n"
            "       python scripts/ci/run_summary.py pytest [args...]"
        )

    if argv[0] == "--":
        cmd = argv[1:]
        if not cmd:
            raise SystemExit("error: no command given after '--'")
        return cmd

    # Convenience: bare 'pytest ...' -> 'python -m pytest ...'
    if argv[0] == "pytest":
        return [sys.executable, "-m", "pytest", *argv[1:]]

    return argv


def _run(cmd: list[str]) -> tuple[int, str]:
    """Run cmd, return (exit_code, combined_output). Handles launch failure."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as exc:
        return 127, f"run_summary: failed to launch command {cmd!r}: {exc}\n"
    except OSError as exc:
        return 126, f"run_summary: error launching command {cmd!r}: {exc}\n"
    return proc.returncode, proc.stdout or ""


def _write_log(output: str) -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_DIR / f"{ts}.log"
    # Avoid clobbering if two runs land in the same second.
    n = 1
    while path.exists():
        path = LOG_DIR / f"{ts}_{n}.log"
        n += 1
    path.write_text(output, encoding="utf-8", errors="replace")
    return path


def _looks_like_pytest(cmd: list[str], output: str) -> bool:
    joined = " ".join(cmd).lower()
    if "pytest" in joined:
        return True
    # Heuristic: pytest's signature header.
    return "test session starts" in output


def _summarize_pytest(lines: list[str]) -> list[str]:
    """Keep the 1-line platform header, FAILURES/ERRORS blocks, and result line.

    Drops progress dots, plugin/cache chatter, and PASSED noise.
    """
    out: list[str] = []

    # 1-line platform/rootdir header.
    for ln in lines:
        s = ln.strip()
        if s.startswith("platform "):
            out.append(ln.rstrip())
            break

    # rootdir line (single, useful context).
    for ln in lines:
        if ln.strip().startswith("rootdir:"):
            out.append(ln.rstrip())
            break

    # Verbatim FAILURES and ERRORS sections. A section starts at a banner like
    # "=== FAILURES ===" / "=== ERRORS ===" and runs until the next top-level
    # "===" banner (e.g. the short test summary or the final result line).
    section_re = re.compile(r"^=+\s+(FAILURES|ERRORS)\s+=+\s*$")
    banner_re = re.compile(r"^=+\s+\S.*\s+=+\s*$")
    short_summary_re = re.compile(r"^=+\s+short test summary info\s+=+\s*$", re.IGNORECASE)

    i = 0
    n = len(lines)
    captured_block = False
    while i < n:
        if section_re.match(lines[i].rstrip()):
            captured_block = True
            out.append("")
            out.append(lines[i].rstrip())
            i += 1
            while i < n and not banner_re.match(lines[i].rstrip()):
                out.append(lines[i].rstrip())
                i += 1
            continue
        i += 1

    # Short test summary info block (concise list of failures/errors) — keep it
    # if present; it's the most compact failure index pytest produces.
    i = 0
    while i < n:
        if short_summary_re.match(lines[i].rstrip()):
            out.append("")
            out.append(lines[i].rstrip())
            i += 1
            while i < n and not banner_re.match(lines[i].rstrip()):
                out.append(lines[i].rstrip())
                i += 1
            break
        i += 1

    # Final result line: the last "=== ... in <time>s ===" style banner.
    result_line = None
    for ln in reversed(lines):
        s = ln.rstrip()
        if banner_re.match(s) and re.search(r"\bin\s+[\d.]+s", s):
            result_line = s
            break
    if result_line is None:
        # Fallback: last non-empty line.
        for ln in reversed(lines):
            if ln.strip():
                result_line = ln.rstrip()
                break

    out.append("")
    if result_line:
        out.append(result_line)

    if not captured_block:
        # No FAILURES/ERRORS — make the "all good" case explicit & tiny.
        # (header + result line already present)
        pass

    return out


def _summarize_generic(lines: list[str]) -> list[str]:
    """Keep error-ish lines + the last ~20 lines, dedup consecutive dups."""
    keep: list[str] = []

    matched = [ln.rstrip() for ln in lines if _GENERIC_PAT.search(ln)]
    tail = [ln.rstrip() for ln in lines[-_TRAIL_LINES:]]

    if matched:
        keep.append("--- matched (error|fail|exception|traceback|fatal|panic) ---")
        keep.extend(matched)
        keep.append("")
    keep.append(f"--- last {min(_TRAIL_LINES, len(lines))} lines ---")
    keep.extend(tail)

    # Dedup consecutive identical lines.
    deduped: list[str] = []
    prev = object()
    for ln in keep:
        if ln != prev:
            deduped.append(ln)
        prev = ln
    return deduped


def main(argv: list[str]) -> int:
    cmd = _parse_argv(argv)
    code, output = _run(cmd)
    log_path = _write_log(output)

    lines = output.splitlines()

    # Header the model always sees.
    print(f"$ {' '.join(cmd)}")
    print(f"[exit {code}]  full log: {log_path}")

    if len(lines) < SMALL_OUTPUT_LINES:
        # Small enough — show it all.
        print("-" * 60)
        if output.strip():
            sys.stdout.write(output if output.endswith("\n") else output + "\n")
        else:
            print("(no output)")
        return code

    if _looks_like_pytest(cmd, output):
        summary = _summarize_pytest(lines)
    else:
        summary = _summarize_generic(lines)

    print("-" * 60)
    for ln in summary:
        print(ln)
    print("-" * 60)
    print(
        f"[summarized {len(lines)} lines -> {len(summary)} shown; "
        f"read {log_path} for full output]"
    )
    return code


if __name__ == "__main__":
    # Ensure stdout can emit utf-8 on Windows consoles.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main(sys.argv[1:]))
