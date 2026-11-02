"""QB-R2: round 2, the union of QB-02 and QB-11 (declared post-hoc; docs/research/blocks/ROUND2.md)."""
from __future__ import annotations

import _round2 as R2

ID = "QB-R2"
TITLE = "Round 2: QB-02 + QB-11 on QB (declared post-hoc combination)"
PARTS = ("QB-02", "QB-11")
COLUMNS = {"QB": R2.columns(PARTS, "QB")}


def build(ctx):
    return R2.combine(ctx, PARTS, "QB")
