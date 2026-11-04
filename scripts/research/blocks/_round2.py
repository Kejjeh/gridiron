"""Round 2: the union of round-1 blocks' columns (a declared post-hoc test).

docs/research/blocks/ROUND2.md is the pre-registration. Each round-2 block
is built by its round-1 parts' own modules, unchanged, loaded by path; this
helper only joins their outputs on the target key. Not a block: no ID.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load(block_id: str):
    name = "r1_" + block_id.replace("-", "_")
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, HERE / f"{block_id}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def key(position: str) -> list[str]:
    return ["season", "week", "team"] if position == "DEF" else ["season", "week", "gsis_id"]


def columns(parts, position: str) -> list[str]:
    cols = [c for p in parts for c in load(p).COLUMNS[position]]
    if len(cols) != len(set(cols)):
        raise ValueError(f"round-1 parts {parts} share a column name: {cols}")
    return cols


def combine(ctx, parts, position: str):
    k, out = key(position), None
    for p in parts:
        mod = load(p)
        frame = mod.build(ctx)[k + list(mod.COLUMNS[position])]
        out = frame if out is None else out.merge(frame, on=k, how="outer")
    return out
