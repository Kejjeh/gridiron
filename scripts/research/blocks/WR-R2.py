"""WR-R2: round 2, the union of WR-07 and WR-11 (declared post-hoc; docs/research/blocks/ROUND2.md).

The parts were WR-07 + WR-03 until the 2026-11-02 correction (ROUND2.md):
the selection rule, re-applied to the bug-fixed round-1 results before any
round-2 score, now ranks WR-11 second."""
from __future__ import annotations

import _round2 as R2

ID = "WR-R2"
TITLE = "Round 2: WR-07 + WR-11 on WR (declared post-hoc combination)"
PARTS = ("WR-07", "WR-11")
COLUMNS = {"WR": R2.columns(PARTS, "WR")}


def build(ctx):
    return R2.combine(ctx, PARTS, "WR")
