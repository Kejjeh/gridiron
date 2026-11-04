"""RB-R2: round 2, the union of RB-06 and RB-02 (declared post-hoc; docs/research/blocks/ROUND2.md)."""
from __future__ import annotations

import _round2 as R2

ID = "RB-R2"
TITLE = "Round 2: RB-06 + RB-02 on RB (declared post-hoc combination)"
PARTS = ("RB-06", "RB-02")
COLUMNS = {"RB": R2.columns(PARTS, "RB")}


def build(ctx):
    return R2.combine(ctx, PARTS, "RB")
