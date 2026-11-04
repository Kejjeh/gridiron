"""TE-12: the first game back after missing games, and the fill-in's give-back (tight ends).

Pre-registration: docs/research/blocks/TE-12.md. RB-02, WR-13 and TE-12 share
one definition, `_p2b_events_ramps_defense.ramp`; each block touches its own
position only.
"""
from __future__ import annotations

import _p2b_events_ramps_defense as H

ID = "TE-12"
TITLE = "Return ramp and the fill-in's give-back, tight ends"
COLUMNS = {"TE": ["ret_back", "ret_missed", "ret_soft", "mate_back_opp"]}


def build(ctx):
    return H.ramp(ctx, "TE")
