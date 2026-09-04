"""Ensure the in-repo pytest basetemp parent exists (see pyproject: we keep
tmp dirs out of the machine-wide %TEMP% root, whose dead junctions crash
pytest's session teardown). pytest recreates the basetemp leaf itself but
not its parent."""
from pathlib import Path

(Path(__file__).resolve().parent.parent / ".cache").mkdir(exist_ok=True)
