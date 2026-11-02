"""WR-R2: round 2, the union of WR-07 and WR-03 (declared post-hoc; docs/research/blocks/ROUND2.md)."""
from __future__ import annotations

import _round2 as R2

ID = "WR-R2"
TITLE = "Round 2: WR-07 + WR-03 on WR (declared post-hoc combination)"
PARTS = ("WR-07", "WR-03")
COLUMNS = {"WR": R2.columns(PARTS, "WR")}


def build(ctx):
    return R2.combine(ctx, PARTS, "WR")
