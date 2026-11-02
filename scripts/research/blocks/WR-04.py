"""WR-04: the wide receiver's red-zone and end-zone target share.

His share of his team's targets inside the opponent's 20, and of its
end-zone targets (air yards reaching the goal line), season to date and over
his last 3 games. One definition with TE-03 (_p1a_pbp_roles.red_zone_shares).
Pre-registration: docs/research/blocks/WR-04.md.
"""
from __future__ import annotations

import _p1a_pbp_roles as R

ID = "WR-04"
TITLE = "Red-zone and end-zone target share (wide receivers)"
COLUMNS = {"WR": list(R.RED_ZONE_COLUMNS)}


def build(ctx):
    return R.red_zone_shares(ctx, "WR")
