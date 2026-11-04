"""DEF-R2: round 2, the union of DEF-10 and DEF-11 (declared post-hoc; docs/research/blocks/ROUND2.md)."""
from __future__ import annotations

import _round2 as R2

ID = "DEF-R2"
TITLE = "Round 2: DEF-10 + DEF-11 on DEF (declared post-hoc combination)"
PARTS = ("DEF-10", "DEF-11")
COLUMNS = {"DEF": R2.columns(PARTS, "DEF")}


def build(ctx):
    return R2.combine(ctx, PARTS, "DEF")
