"""CLAUDE.md is auto-loaded into every session; keep it from drifting.

Ported from plv_clone, where CLAUDE.md drifted from a stated ~200-line
budget to 635 lines before this ratchet was installed (their issue #46).
This repo installs it at commit #1, per the bootstrap doc.

The ratchet is two-sided: a hard ceiling (put detail in docs/memory/, leave
a one-line headline) AND a floor close beneath it, so the ceiling stays
binding — a ceiling far above the file is a ceiling that gets ignored.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLAUDE_MD = ROOT / "CLAUDE.md"
MEMORY = ROOT / "docs" / "memory"

#: Hard ceiling. Lower it when the file shrinks; raising it needs a reason
#: in the same commit, and "I added a rule inline" is not one. The bootstrap
#: doc caps day-one CLAUDE.md at 150; we start well under.
MAX_LINES = 75


def _text() -> str:
    return CLAUDE_MD.read_text(encoding="utf-8")


def test_claude_md_stays_under_the_ceiling():
    n = len(_text().splitlines())
    assert n <= MAX_LINES, (
        f"CLAUDE.md is {n} lines, over the {MAX_LINES}-line ceiling. It is "
        f"auto-loaded into every session. Put the detail in docs/memory/ and "
        f"leave a one-line headline here."
    )


def test_the_ceiling_is_actually_binding():
    """If this fails the file shrank a lot — lower MAX_LINES rather than
    leaving the slack."""
    n = len(_text().splitlines())
    assert n >= MAX_LINES - 60, (
        f"CLAUDE.md is {n} lines against a {MAX_LINES} ceiling — {MAX_LINES - n} "
        f"lines of slack. Lower MAX_LINES so the ratchet keeps working."
    )


def test_the_memory_dir_exists_and_is_reachable():
    assert MEMORY.is_dir(), "docs/memory/ is gone — the detail has nowhere to live"
    assert list(MEMORY.glob("*.md")), "docs/memory/ is empty"


def test_every_docs_memory_pointer_in_claude_md_resolves():
    """A dead pointer is worse than the inline text it replaced — the reader
    gets the headline and no evidence."""
    broken = [
        ref for ref in set(re.findall(r"docs/memory/([A-Za-z0-9_]+\.md)", _text()))
        if not (MEMORY / ref).exists()
    ]
    assert not broken, f"CLAUDE.md links to missing memory files: {sorted(broken)}"


def test_no_numbered_rule_was_lost():
    """Numbering is load-bearing — commits and docs cite 'rule #N' — so the
    list stays contiguous from 1 in CLAUDE.md AND every headlined rule has
    full text in docs/memory/rules.md. Retire in place, never renumber."""
    text = _text()

    def _numbers(block: str) -> list[int]:
        return sorted({int(m) for m in re.findall(r"^(\d+)\. ", block, flags=re.M)})

    heading = "## Rules"
    start = text.index(heading)
    next_h = text.find("\n## ", start + 1)
    end = next_h if next_h != -1 else len(text)
    headline_nums = _numbers(text[start:end])
    assert headline_nums == list(range(1, len(headline_nums) + 1)), (
        f"{heading} in CLAUDE.md lists {headline_nums}, not a contiguous 1..N — "
        f"a rule was dropped or renumbered."
    )
    assert headline_nums, "CLAUDE.md has no numbered rules under '## Rules'"

    full_nums = _numbers((MEMORY / "rules.md").read_text(encoding="utf-8"))
    missing = sorted(set(headline_nums) - set(full_nums))
    assert not missing, (
        f"these rules have a headline in CLAUDE.md but no full text in "
        f"docs/memory/rules.md: {missing}"
    )
