"""Every roadmap idea and where it stands, from the committed block results.

    PYTHONPATH=src python scripts/research/block_summary.py [--write]

Reads the idea tables of docs/research/MODEL_ROADMAP.md (78 ideas) and every
docs/research/blocks/results/<ID>.json, and prints (or, with --write, writes
docs/research/blocks/RESULTS.md) one table: the idea, its kind, the
positions and folds it was scored on, how many folds it beat the shipped set
on both metrics, the mean changes, and the verdict. An idea with no result
says why (waiting on data, deferred, not run yet). A second table lists the
round-2 blocks (scripts/research/blocks/*-R2.py, docs/research/blocks/ROUND2.md)
with their parts.

A 2026 holdout shows in its block's verdict cell: results/<ID>-2026.json (a
harness's own holdout) or the block's entry in holdout_2026.json (written by
holdout_2026.py). Every results/*.json appears somewhere: a result that is
neither an idea, a round-2 block nor a shown holdout gets a row of its own
under "Other results". Nothing here scores anything; it only reads what the
runner and the harnesses wrote.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

from gridiron.paths import REPO_ROOT

ROADMAP = REPO_ROOT / "docs" / "research" / "MODEL_ROADMAP.md"
RESULTS = REPO_ROOT / "docs" / "research" / "blocks" / "results"
HOLDOUT = REPO_ROOT / "docs" / "research" / "blocks" / "holdout_2026.json"
BLOCKS = REPO_ROOT / "scripts" / "research" / "blocks"
OUT = REPO_ROOT / "docs" / "research" / "blocks" / "RESULTS.md"
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
EMPTY = {"scored": "", "folds": "", "d_pw": "", "d_mae": ""}
NOT_SCORED = "pre-registered, not scored"


def ideas() -> list[dict]:
    out = []
    for line in ROADMAP.read_text(encoding="utf-8").splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) == 6 and cells[0].split("-")[0] in POSITIONS and cells[0][-2:].isdigit():
            out.append(dict(zip(("id", "idea", "data", "kind", "phase", "note"), cells)))
    return out


def result(idea_id: str) -> dict | None:
    p = RESULTS / f"{idea_id}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def is_pass(verdict: str) -> bool:
    return verdict.startswith("PASS")


def is_fail(verdict: str) -> bool:
    return verdict.lower().startswith("fail")


def _seasons(x) -> str:
    return ", ".join(str(s) for s in x) if isinstance(x, (list, tuple)) else str(x)


def result_cells(r: dict) -> dict:
    """The folds-scored, better-on-both, mean-change and verdict cells of one result."""
    if "verdict" in r and isinstance(r["verdict"], dict) and "positions" in r["verdict"]:
        pos = r["verdict"]["positions"]
        folds = "; ".join(f"{p} {v['folds_better_on_both']}/{v['folds']}" for p, v in pos.items())
        d_pw = "; ".join(f"{p} {v['mean_pairwise_delta'] * 100:+.2f}" for p, v in pos.items())
        d_mae = "; ".join(f"{p} {v['mean_mae_delta']:+.3f}" for p, v in pos.items())
        passed = r.get("passed") or []
        verdict = ("PASS " + "+".join(passed)) if passed else "fail"
        tested = _seasons(r.get("folds_tested", r.get("seasons", [])))
        return {"scored": tested, "folds": folds, "d_pw": d_pw, "d_mae": d_mae, "verdict": verdict}
    # a result from another harness (distributions, early-season priors): its own summary
    summary = r.get("summary") or r.get("verdict") or "see its doc"
    return {"scored": _seasons(r.get("folds_tested", "")), "folds": str(r.get("folds", "")),
            "d_pw": "", "d_mae": "", "verdict": str(summary)}


def short_verdict(r: dict) -> str:
    """PASS <positions> or fail, for a verdict cell's holdout note."""
    passed = r.get("passed")
    if isinstance(passed, list):
        return ("PASS " + "+".join(passed)) if passed else "fail"
    v = result_cells(r)["verdict"]
    return "PASS" if is_pass(v) else "fail" if is_fail(v) else v


def runner_holdouts() -> dict:
    """holdout_2026.py's record: {block: its entry, with the last week scored}."""
    if not HOLDOUT.exists():
        return {}
    h = json.loads(HOLDOUT.read_text(encoding="utf-8"))
    return {b: {**v, "through_week": h.get("through_week")} for b, v in (h.get("blocks") or {}).items()}


def holdout(block_id: str, holdouts: dict) -> dict | None:
    """A block's 2026 holdout: {label, verdict, shows (the result it shows)}, or None."""
    r = result(f"{block_id}-2026")
    if r is not None:
        return {"label": f"{block_id}-2026", "verdict": short_verdict(r), "shows": f"{block_id}-2026"}
    h = holdouts.get(block_id)
    if not h:
        return None
    v = h.get("verdict") or {}
    better = [p for p, x in v.items() if x.get("better_on_both")]
    worse = [p for p in v if p not in better]
    text = ("PASS " + "+".join(better)) if better else "fail"
    if better and worse:
        text += ", fail " + "+".join(worse)
    if any(x.get("eligible") for x in v.values()):
        text += ", eligible for a serving decision"
    weeks = f", weeks 4-{h['through_week']}" if h.get("through_week") else ""
    return {"label": f"holdout_2026.json{weeks}", "verdict": text, "shows": None}


def scored(block_id: str, base: dict, holdouts: dict) -> dict | None:
    """A row's result cells, its holdout and the results it shows; None when unscored."""
    r = result(block_id)
    if r is None:
        return None
    h = holdout(block_id, holdouts)
    return {**base, **result_cells(r), "holdout": h,
            "shows": [block_id] + ([h["shows"]] if h and h["shows"] else [])}


def row(i: dict, holdouts: dict | None = None) -> dict:
    base = {"id": i["id"], "idea": i["idea"], "kind": i["kind"], "holdout": None, "shows": []}
    done = scored(i["id"], base, holdouts or {})
    if done is not None:
        return done
    doc = RESULTS.parent / f"{i['id']}.md"
    if doc.exists() and "NOT TESTABLE" in doc.read_text(encoding="utf-8")[:600]:
        return {**base, **EMPTY, "verdict": "not testable with free data (see its doc)"}
    if i["kind"] == "—":
        why = "deferred: no free data"
    elif i["kind"] == "M":
        why = "waiting on collected props (workstream A)"
    elif i["kind"] == "P":
        why = "product list, shipped (`gridiron.streaming`)"
    else:
        why = "not run yet"
    return {**base, **EMPTY, "verdict": why}


def module_constants(path: Path) -> dict:
    """ID, PARTS and the COLUMNS positions of a block module, read without importing it."""
    out = {}
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)):
            continue
        name, value = node.targets[0].id, node.value
        if name in ("ID", "PARTS"):
            try:
                out[name] = ast.literal_eval(value)
            except (ValueError, TypeError):
                pass
        elif name == "COLUMNS" and isinstance(value, ast.Dict):
            out["positions"] = [k.value for k in value.keys if isinstance(k, ast.Constant)]
    return out


def _position_order(block_id: str) -> tuple:
    p = block_id.split("-")[0]
    return (POSITIONS.index(p) if p in POSITIONS else len(POSITIONS), block_id)


def round2_rows(holdouts: dict | None = None) -> list[dict]:
    """Every scripts/research/blocks/*-R2.py block, scored or not."""
    out = []
    for p in BLOCKS.glob("*-R2.py"):
        m = module_constants(p)
        bid = str(m.get("ID") or p.stem)
        base = {"id": bid, "parts": " + ".join(m.get("PARTS") or ()),
                "position": ", ".join(m.get("positions") or [bid.split("-")[0]]),
                "holdout": None, "shows": []}
        out.append(scored(bid, base, holdouts or {}) or {**base, **EMPTY, "verdict": NOT_SCORED})
    return sorted(out, key=lambda r: _position_order(r["id"]))


def other_rows(shown: set, holdouts: dict | None = None) -> list[dict]:
    """Rows for every results/*.json that no other row shows (a holdout last,
    so a result's own row shows its holdout first)."""
    stems = sorted(p.stem for p in RESULTS.glob("*.json"))
    out, seen = [], set(shown)
    for stem in [s for s in stems if not s.endswith("-2026")] + [s for s in stems if s.endswith("-2026")]:
        if stem in seen:
            continue
        r = scored(stem, {"id": stem, "holdout": None, "shows": []}, holdouts or {})
        seen.update(r["shows"])
        out.append(r)
    return out


def _v(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _holdout_failed(r: dict) -> bool:
    return bool(r.get("holdout")) and is_fail(r["holdout"]["verdict"])


def verdict_cell(r: dict) -> str:
    h = r.get("holdout")
    return r["verdict"] + (f"; 2026 holdout ({h['label']}): {h['verdict']}" if h else "")


def _cells(r: dict) -> str:
    return f"{r['scored']} | {r['folds']} | {r['d_pw']} | {r['d_mae']} | {verdict_cell(r)} |"


def render(rows: list[dict], round2: list[dict] = (), others: list[dict] = ()) -> str:
    n = len(rows)
    held_out = [r for r in rows if is_pass(r["verdict"]) and _holdout_failed(r)]
    passed = [r for r in rows if is_pass(r["verdict"]) and not _holdout_failed(r)]
    failed = [r for r in rows if is_fail(r["verdict"])]
    unscored = [r for r in rows if not r.get("shows")]
    neither = n - len(passed) - len(held_out) - len(failed) - len(unscored)
    # a zero pass count beside "passes history and fails its holdout" reads as a contradiction
    held = [r for r in passed if r.get("holdout") and is_pass(r["holdout"]["verdict"])]
    pass_text = _v(len(passed), "passes", "pass") + " the historical bar"
    if held:
        pass_text += (f" ({_v(len(held), 'of them also passes', 'of them also pass')} a 2026 holdout: "
                      + ("a record-only contender)" if len(held) == 1 else "record-only contenders)"))
    counts = [] if held_out and not passed else [pass_text]
    if held_out:
        counts.append(_v(len(held_out), "passes history and fails its 2026 holdout",
                         "pass history and fail their 2026 holdout"))
    counts.append(_v(len(failed), "fails", "fail"))
    if neither:
        counts.append(_v(neither, "has a result that neither passes nor fails",
                         "have results that neither pass nor fail") + " (see the doc)")
    counts.append(_v(len(unscored), "has", "have") + " no result (see the last column)")
    lines = [
        "# Roadmap results: every idea and where it stands",
        "",
        "Generated by `scripts/research/block_summary.py --write` from",
        "`docs/research/blocks/results/*.json`. Do not edit by hand. Each block's own",
        "doc (`docs/research/blocks/<ID>.md`) holds its pre-registration and result.",
        "",
        f"{n} ideas: " + ", ".join(counts) + ".",
        "A pass is necessary, not sufficient: it then runs record-only on live weeks",
        "(ground rule 3) before it may serve. A 2026 holdout shows in the verdict cell:",
        "`results/<ID>-2026.json` (a harness's own holdout) or `holdout_2026.json`",
        "(`holdout_2026.py`).",
        "",
        "Folds column: the folds a block beat the shipped set on BOTH start/sit pairwise and",
        "MAE, per position. Mean changes are block minus shipped (pairwise in points,",
        "positive is better; MAE, negative is better). A block scored by another harness",
        "shows that harness's own fold count and summary instead.",
        "",
        "| ID | idea | kind | folds scored | better on both | mean Δ pairwise | mean Δ MAE | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(f"| {r['id']} | {r['idea']} | {r['kind']} | " + _cells(r))
    if round2:
        k = len(round2)
        r2_held = [r for r in round2 if is_pass(r["verdict"]) and _holdout_failed(r)]
        r2_pass = sum(is_pass(r["verdict"]) for r in round2) - len(r2_held)
        r2_counts = ([] if r2_held and not r2_pass
                     else [_v(r2_pass, "passes", "pass") + " part 1 (the historical bar)"])
        if r2_held:
            r2_counts.append(_v(len(r2_held), "passes part 1 and fails", "pass part 1 and fail")
                             + " part 2 (the 2026 holdout)")
        r2_counts.append(_v(sum(is_fail(r["verdict"]) for r in round2), "fails", "fail"))
        unscored_r2 = sum(r["verdict"] == NOT_SCORED for r in round2)
        if unscored_r2:
            r2_counts.append(f"{unscored_r2} {NOT_SCORED}")
        lines += [
            "",
            "## Round 2 (declared post-hoc combinations, docs/research/blocks/ROUND2.md)",
            "",
            f"{_v(k, 'block', 'blocks')}: " + ", ".join(r2_counts) + ".",
            "Each is the union of its round-1 parts' columns. Part 1 is the bar above;",
            "part 2, only for a block that clears part 1, is the 2026 holdout.",
            "",
            "| ID | parts | position | folds scored | better on both | mean Δ pairwise | mean Δ MAE | verdict |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in round2:
            lines.append(f"| {r['id']} | {r['parts']} | {r['position']} | " + _cells(r))
    if others:
        lines += [
            "",
            "## Other results (not a roadmap idea, a round-2 block or a shown holdout)",
            "",
            "| ID | folds scored | better on both | mean Δ pairwise | mean Δ MAE | verdict |",
            "|---|---|---|---|---|---|",
        ]
        for r in others:
            lines.append(f"| {r['id']} | " + _cells(r))
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    holdouts = runner_holdouts()
    rows = [row(i, holdouts) for i in ideas()]
    if len(rows) != 78:
        print(f"warning: parsed {len(rows)} ideas from the roadmap, expected 78", file=sys.stderr)
    round2 = round2_rows(holdouts)
    others = other_rows({s for r in rows + round2 for s in r["shows"]}, holdouts)
    text = render(rows, round2, others)
    if args.write:
        OUT.write_text(text, encoding="utf-8")
        try:
            print(f"wrote {OUT.relative_to(REPO_ROOT)}")
        except ValueError:
            print(f"wrote {OUT}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
