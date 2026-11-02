"""K-R2: round 2, the union of K-05 and K-06 (declared post-hoc; docs/research/blocks/ROUND2.md)."""
from __future__ import annotations

import _round2 as R2

ID = "K-R2"
TITLE = "Round 2: K-05 + K-06 on K (declared post-hoc combination)"
PARTS = ("K-05", "K-06")
COLUMNS = {"K": R2.columns(PARTS, "K")}


def build(ctx):
    return R2.combine(ctx, PARTS, "K")
