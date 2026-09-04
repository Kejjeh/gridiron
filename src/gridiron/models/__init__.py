"""Projection models, one file per model.

Gate (bootstrap doc §1, the rh3/rp3-v2 lesson): no feature enters a model's
FEATS list until it beats a baseline containing ALL existing features,
out-of-sample. The gate is an import-time assert in a validated_signals
module, installed alongside the FIRST model — not left to discipline.
"""
