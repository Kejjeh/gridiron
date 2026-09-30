"""Rule #12: at most the five named skills, each one honest about itself.

plv_clone grew 94 skills with a registry test keeping them honest; this repo
starts with the test and a hard allowlist. A skill that points at a script
that does not exist is worse than no skill: the session follows it and fails
halfway through a weekly decision.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / ".claude" / "skills"
RULES = ROOT / "docs" / "memory" / "rules.md"

#: Rule #12's five, verbatim. Adding a sixth needs the rule changed first.
ALLOWED = ("roster-audit", "waiver-board", "start-sit", "matchup", "decision-log")

_FRONT = re.compile(r"\A---\n(.*?)\n---\n", re.S)


def _skills() -> list[Path]:
    return sorted(p for p in SKILLS.iterdir() if p.is_dir()) if SKILLS.is_dir() else []


def _front(path: Path) -> dict[str, str]:
    m = _FRONT.match(path.read_text(encoding="utf-8"))
    assert m, f"{path} has no YAML front matter"
    out = {}
    for line in m.group(1).splitlines():
        key, _, value = line.partition(":")
        out[key.strip()] = value.strip()
    return out


def test_the_allowlist_is_rule_12():
    text = RULES.read_text(encoding="utf-8")
    for name in ALLOWED:
        assert name in text, f"rule #12 no longer names {name}; update ALLOWED with it"


def test_only_allowed_skills_exist_and_there_are_some():
    names = [p.name for p in _skills()]
    assert names, "no skills under .claude/skills — the registry has nothing to check"
    assert len(names) <= len(ALLOWED)
    assert set(names) <= set(ALLOWED), f"not in rule #12: {set(names) - set(ALLOWED)}"


def test_every_skill_is_well_formed():
    for d in _skills():
        md = d / "SKILL.md"
        assert md.is_file(), f"{d.name} has no SKILL.md"
        front = _front(md)
        assert front.get("name") == d.name, f"{md}: name must equal the folder"
        assert len(front.get("description", "")) >= 80, f"{md}: description too thin"
        body = md.read_text(encoding="utf-8")
        assert "## Never" in body, f"{md}: every skill states what it must never do"


def test_every_script_a_skill_names_exists():
    missing = []
    for d in _skills():
        text = (d / "SKILL.md").read_text(encoding="utf-8")
        for rel in set(re.findall(r"(scripts/[\w/.-]+\.py)", text)):
            if not (ROOT / rel).is_file():
                missing.append(f"{d.name}: {rel}")
    assert not missing, "skills point at scripts that do not exist:\n" + "\n".join(missing)


def test_no_skill_submits_anything():
    """The repo never writes to the league; a skill must not suggest it can."""
    for d in _skills():
        text = (d / "SKILL.md").read_text(encoding="utf-8").lower()
        assert "submit" in text, f"{d.name}: say explicitly that nothing is submitted"
        for bad in ("gh workflow run", "rerun", "re-run the workflow", "players/nfl"):
            assert bad not in text, f"{d.name} mentions {bad!r}"
