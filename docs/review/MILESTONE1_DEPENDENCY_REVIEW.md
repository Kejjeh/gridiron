# Milestone 1 — dependency scope review

Written 2026-09-17 in response to the review blocker on PR #1: `requirements.txt`
adds `nflreadpy`, `pandas` and `pyarrow` against a no-new-dependencies
constraint. Nothing further was installed and nothing was merged. The existing
"Build step 2 (ingest) will add: …" comment in `requirements.txt` is NOT read
here as approval — it is a plan from 2026-09-04, and this file exists so the
decision is made explicitly instead of inherited from a comment.

The three are not one decision. They differ in whether they are new at all.

## 1. pandas — already required on `main`; this line is a declaration, not an addition

**Pre-PR fact.** `src/gridiron/draft.py:16` and `src/gridiron/ledger.py:12`
both `import pandas as pd` at module scope on `main` (commit 83d3255), and both
modules are in `scripts/ci/smoke.py`'s pre-PR `IMPORTS` list.
`scripts/ci/golden_run.py` imports it too. So `python scripts/ci/smoke.py`
could not run on `main` without pandas installed. It was an **undeclared**
dependency; `requirements.txt` mentioned it only inside the planning comment.

**What in this PR needs it.** Module scope: `usage.py`, `weekly.py`. Deferred
(inside a function): `ingest.read_frame`. Plus both drivers.

**Alternative using the existing stack.** Possible, not advisable. The weekly
frame is ~1,100 rows, so stdlib `csv` + dicts is fast enough. The cost is that
`season_to_date`'s group-by and `attach_snaps`' merge would be hand-rolled —
roughly 150–200 lines replacing ~120, re-implementing a join and an aggregation,
which is precisely where silent wrongness lives in this domain. It would also
not remove pandas from the repo: `draft.py` and `ledger.py` still import it.

**Recommendation.** Treat as a declaration of existing state. If the reviewer
disagrees, the honest alternative is to delete the line and leave pandas
undeclared — which is the status quo on `main`, not a reduction in dependencies.

## 2. nflreadpy — new to `requirements.txt`, not new to the repo; confined to one driver

**Pre-PR fact.** `scripts/research/pull_nflverse_2026.py:3` already imports it
on `main`, and `docs/DECISIONS.md` (2026-09-04) explicitly chose it over the
deprecated `nfl_data_py`. It was undeclared.

**What in this PR needs it.** `scripts/ingest/pull_week.py` only — the four
nflverse pulls (weekly stats, snap counts, schedules, injuries). **No module in
`src/gridiron/` imports it.**

**Alternative using the existing stack.** Real and bounded: nflverse publishes
the same files as static artifacts on GitHub releases, and `ids.py` already
fetches its crosswalk over stdlib `urllib` for exactly this reason (nflreadpy's
own fetch 403s through the egress proxy). `pull_week.py` could do the same. The
trade is that release-tag pinning, URL construction and schema selection move
into code we maintain — the same surface whose drift caused the scoring bug
this PR fixes.

**Note.** It pulls `polars` transitively; that weight is currently undeclared.

**Recommendation.** Separable. Blocking it blocks only ingest, not the fixes.

## 3. pyarrow — the only genuinely new dependency, and the cheapest to drop

**Pre-PR fact.** Not used anywhere on `main`.

**What in this PR needs it.** The parquet cache, and nothing else:
`pull_week.py`'s `to_parquet`, `ingest.read_frame`'s `read_parquet`, and three
tests in `tests/test_ingest.py`.

**Why parquet.** Dtype fidelity. A CSV round-trip turns an integer count into a
float and a blank into an empty string, and "blank is not zero" is invariant #2
of this milestone.

**Alternative using the existing stack.** CSV, read with NA preserved and never
`.fillna(0)` on a measurement column. Precedent exists: `ids.normalize_id`
already repairs the `'1234.0'` float round-trip for ids. The residual risk is
NA-vs-0 drift in numeric columns, which is what the existing tests
(`test_a_player_with_no_snap_row_is_blank_not_zero`, and the blank-not-zero
tests in `test_weekly.py`) already guard.

**Recommendation.** Drop first if only one must go.

## What can ship dependency-free, today, separated from all of the above

Five of the seven new modules import **stdlib only** — verified by reading their
import blocks: `scoring.py`, `ids.py`, `sleeper.py`, `freshness.py`, and
`ingest.py` (its single pandas import is deferred inside `read_frame`). Only
`usage.py` and `weekly.py` need pandas at module scope. That line splits the PR
cleanly.

**Separable PR A — the scoring repair (zero new dependencies).**
`src/gridiron/scoring.py` imports nothing but `collections.abc`, `dataclasses`
and `gridiron.league_config`. It fixes a bug that is **silently wrong on `main`
right now**: the module reads `interceptions`, `fumbles_lost` and
`two_point_conversions`, none of which exist in the current nflverse weekly
schema, so every interception, lost fumble and two-point conversion scores zero.
Contents: `scoring.py`, `tests/test_scoring_nflverse.py`, the two CSV fixtures
it reads (`weekly_offense_wk1.csv`, `weekly_kickers_wk1.csv`, ~4 KB total), the
`smoke.py` PATTERNS line, and the matching `DECISIONS.md` rows.

  One caveat, stated rather than glossed: the test **as written** reads those
  fixtures through the pandas conftest fixtures. Converting it to stdlib
  `csv.DictReader` is contained (~20 lines) but is not a no-op — `_num` coerces
  None and NaN to zero but `float('')` raises, so an empty CSV cell needs an
  explicit coercion in the test helper.

**Separable PR B — settings verification (zero new dependencies).**
`scripts/verify_league_settings.py` (stdlib, via `gridiron.sleeper`, which is
stdlib `urllib` only), the rule #1 correction in `CLAUDE.md` and
`docs/memory/rules.md`, the pinned values in `tests/test_league_config.py`, and
`tests/test_verify_league_settings.py`. This carries the 55/55 live
re-verification and the AST test that keeps the checker write-free.

**What stays blocked.** `usage.py`, `weekly.py`, `ingest.py`'s parquet path, both
drivers, and their tests — i.e. the ingest-and-report half. It is not partially
shippable: the report has no inputs without the ingest.

## Also corrected in this pass

`scoring.py`'s docstring claimed "DST rows carry Sleeper's actual points and no
projection". That was **false**. Nothing reads `players_points` back into the
report; that endpoint was used once, offline, to reconcile the scoring weights.
A DST row is carried with the team, opponent, market implied total and an
explicit "n/a (team defense)" note, and **every points and usage cell blank** —
confirmed in the committed `data/outputs/week02_report.csv`, and pinned by
`tests/test_weekly.py::test_team_defenses_are_carried_not_dropped`. Both
`scoring.py` and `weekly.py` now document the real behavior. The earlier chat
summary ("those rows carry no points") was the accurate one.

Separately, registering `weekly_report` in `golden_run.py` was broken: the
target held a shell string where `resolve_target` expects an argv list (it
shlex-splits only a custom `--cmd`), so `list("python …")` split it into
characters and the target could not run. Fixed; the A/B below is the first
successful run of it.

## Validation of this pass

- `python scripts/ci/smoke.py` — PASS
- `python scripts/ci/run_summary.py -- python -m pytest` — 231 passed
- `golden_run.py --target weekly_report` A/B — 1/1 outputs byte-IDENTICAL
  (the doc corrections are behavior-preserving; the golden_run argv fix had to
  land first for phase A to run at all)
