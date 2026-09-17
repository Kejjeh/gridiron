"""Rule #3, enforced mechanically: names are never a join key.

Two Josh Allens, a Jr. suffix, a source that writes "D.J. Moore" and one that
writes "DJ Moore" — a name join does not fail loudly, it silently attaches one
player's usage to another player's row. The in-season modules therefore never
merge, map or filter on a name column.

Scope: the in-season production package and the in-season drivers. The
research scripts under scripts/research/ predate this and are exempt; they
are one-shot analyses, not the weekly pipeline.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

IN_SEASON_DIRS = (ROOT / "src" / "gridiron",
                  ROOT / "scripts" / "ingest",
                  ROOT / "scripts" / "weekly")

#: Columns that identify a human by name rather than by id.
NAME_COLUMNS = {
    "name", "player", "player_name", "player_display_name", "full_name",
    "merge_name", "first_name", "last_name", "search_full_name",
    "display_name", "football_name", "short_name",
}

_CONTAINS = re.compile(r"\.str\.(contains|startswith|endswith|match)\s*\(")


def _files():
    for base in IN_SEASON_DIRS:
        if base.exists():
            yield from base.rglob("*.py")


def test_no_str_contains_anywhere_in_the_in_season_path():
    offenders = []
    for f in _files():
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if _CONTAINS.search(line):
                offenders.append(f"{f.relative_to(ROOT)}:{i}: {line.strip()}")
    assert not offenders, (
        "`.str.contains`-style matching is a banned join strategy (rule #3):\n"
        + "\n".join(offenders))


def _merge_on_args(tree: ast.AST):
    """Yield (lineno, on-argument node) for every DataFrame.merge/join call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "attr", None) not in {"merge", "join"}:
            continue
        for kw in node.keywords:
            if kw.arg in {"on", "left_on", "right_on"}:
                yield node.lineno, kw.value


def _literal_names(node: ast.AST) -> set[str]:
    out = set()
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
            out.add(sub.value)
    return out


def test_no_merge_keys_on_a_name_column():
    offenders = []
    for f in _files():
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for lineno, arg in _merge_on_args(tree):
            bad = _literal_names(arg) & NAME_COLUMNS
            if bad:
                offenders.append(f"{f.relative_to(ROOT)}:{lineno}: merges on {sorted(bad)}")
    assert not offenders, (
        "Joins must anchor on a stable player id (rule #3):\n" + "\n".join(offenders))


def test_the_check_is_not_vacuous():
    """A typo in IN_SEASON_DIRS must not silently disable this file."""
    files = list(_files())
    assert len(files) >= 8, f"only {len(files)} in-season modules scanned"
    assert any(f.name == "ids.py" for f in files)
    assert any(f.name == "usage.py" for f in files)


def test_the_check_would_actually_fire():
    """Pin the detectors against a synthetic offender, so a regex or AST
    change that neuters them fails here rather than in production."""
    bad = ast.parse("df.merge(other, on='player_display_name')")
    assert any(_literal_names(a) & NAME_COLUMNS for _, a in _merge_on_args(bad))
    assert _CONTAINS.search("frame[frame.player.str.contains('Allen')]")


def test_the_crosswalk_is_the_only_module_that_maps_platform_ids():
    """One cached crosswalk, not several ad-hoc dicts (rule #3)."""
    offenders = []
    for f in _files():
        if f.name in {"ids.py", "sleeper.py"}:
            continue
        text = f.read_text(encoding="utf-8")
        if "sleeper_id" in text and "gsis" in text and "Crosswalk" not in text \
                and "crosswalk" not in text:
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, (
        "these modules map platform ids without going through gridiron.ids: "
        + ", ".join(offenders))
