"""WR-13: the first game back after missing games, and the fill-in's give-back (wide receivers).

Pre-registration: docs/research/blocks/WR-13.md. RB-02, WR-13 and TE-12 share
one definition, `_p2b_events_ramps_defense.ramp`; each block touches its own
position only.
"""
from __future__ import annotations

import _p2b_events_ramps_defense as H

ID = "WR-13"
TITLE = "Soft-tissue return ramp and the fill-in's give-back, wide receivers"
COLUMNS = {"WR": ["ret_back", "ret_missed", "ret_soft", "mate_back_opp"]}


def build(ctx):
    return H.ramp(ctx, "WR")
