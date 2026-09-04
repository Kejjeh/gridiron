# ADR-0001: Bootstrap shape — what was ported verbatim vs adapted

Date: 2026-09-03 · Status: accepted

## Context

docs/BOOTSTRAP_FROM_PLV.md says to port `run_summary.py`, `smoke.py`, and
`golden_run.py` "VERBATIM". Two of the three contain plv_clone-specific
content (module lists, pipeline targets, baseball model pkl paths) that
cannot run here.

## Decision

- `run_summary.py`: ported byte-for-byte — it is fully generic.
- `smoke.py`: engine ported unchanged; only `IMPORTS`, `PATTERNS`, and the
  anti-vacuity floor (`MIN_FILES = 5`, was 8) are repo-specific. Also added
  `PYTHONPATH=src` to the child env since gridiron uses a src layout without
  requiring an editable install.
- `golden_run.py`: engine (lockfile, input-hash freezing, prod restore,
  CSV/parquet/JSON equivalence diffs) ported unchanged; the baseball
  `TARGETS`/`COLD_PKLS`/`--cold` machinery was dropped — `TARGETS` starts
  empty and `--target custom` is the day-one path. Re-add a cold-fit stash
  mechanism only when a warm-skip model artifact actually exists here.
- Budget test: ceiling starts at 75 (file is ~47 lines) instead of
  inheriting plv's 155; same two-sided ratchet. The numbered-rules check is
  generalized to a single `## Rules` section backed by
  `docs/memory/rules.md`.

## Consequences

Behavior-preserving-refactor discipline is available from day one, but
golden runs need explicit `--cmd/--outputs/--inputs` until the first
pipeline registers a named target. `--cold` support, if ever needed, is a
deliberate re-port from plv_clone, not a latent half-working flag.
