"""RB-02: the first game back after missing games, and the fill-in's give-back (running backs).

Pre-registration: docs/research/blocks/RB-02.md. RB-02, WR-13 and TE-12 share
one definition, `_p2b_events_ramps_defense.ramp`; each block touches its own
position only.
"""
from __future__ import annotations

import _p2b_events_ramps_defense as H

ID = "RB-02"
TITLE = "Return-from-injury ramp and the fill-in's give-back, running backs"
COLUMNS = {"RB": ["ret_back", "ret_missed", "ret_soft", "mate_back_opp"]}


def build(ctx):
    return H.ramp(ctx, "RB")
