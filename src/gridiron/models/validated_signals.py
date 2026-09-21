"""Rule #5, as an import-time assert rather than a habit.

No model feature ships without beating a baseline that contains ALL existing
features, out-of-sample. This module is the registry that gate reads:

  * `BASELINE` names the one projection that everything is measured against.
    It is the ARCHITECTURE step-3 shape — usage prior × positional efficiency
    × line multiplier (docs/research/QUANT_FOUNDATIONS.md §1.4) — and it is
    labelled a BASELINE, never a validated model, everywhere it is rendered.
  * `FEATS` is the list of signals a projection may use ON TOP of the
    baseline. It is empty. Adding a name here without a matching entry in
    `VALIDATED` fails every import of `gridiron.projection`, which is what
    smoke.py runs first.
  * `VALIDATED` maps a feature name to the out-of-sample evidence that it
    beat the full baseline: the chronological evaluation's file, the weeks
    it covered and the metric delta. `gridiron.evaluate` is the tool that
    produces that evidence; a feature is not validated because someone
    believes in it.

The plv_clone scar (rh3/rp3-v2): a candidate signal evaluated against a
stripped-down baseline over-claimed its lift four-fold. The gate exists so
that the comparison is always against everything already in.
"""
from __future__ import annotations

BASELINE = "usage_prior x positional_efficiency x line_multiplier"

#: Signals layered on the baseline. Empty until one clears the gate.
FEATS: tuple[str, ...] = ()

#: feature name -> {"evidence": path, "weeks": (lo, hi), "mae_delta": float}
VALIDATED: dict[str, dict] = {}

_unvalidated = [f for f in FEATS if f not in VALIDATED]
assert not _unvalidated, (
    f"rule #5: feature(s) {_unvalidated} are in FEATS without out-of-sample "
    f"evidence in VALIDATED. Run the chronological evaluation "
    f"(gridiron.evaluate) against the full baseline first."
)
