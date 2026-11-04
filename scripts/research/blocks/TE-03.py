"""TE-03: the tight end's red-zone and end-zone target share.

His share of his team's targets inside the opponent's 20, and of its
end-zone targets (air yards reaching the goal line), season to date and over
his last 3 games. One definition with WR-04 (_p1a_pbp_roles.red_zone_shares),
its own block and test. Pre-registration: docs/research/blocks/TE-03.md.
"""
from __future__ import annotations

import _p1a_pbp_roles as R

ID = "TE-03"
TITLE = "Red-zone and end-zone target share (tight ends)"
COLUMNS = {"TE": list(R.RED_ZONE_COLUMNS)}


def build(ctx):
    return R.red_zone_shares(ctx, "TE")
