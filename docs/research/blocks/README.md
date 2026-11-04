# Position blocks: how each roadmap idea is tested

Every feature block in `docs/research/MODEL_ROADMAP.md` goes through one
harness, `scripts/research/block_runner.py`, in this order. The order is the
point: the test is written down before any score exists.

1. **Write the block** as `scripts/research/blocks/<ID>.py` (ID as in the
   roadmap, e.g. `RB-03.py`): `ID`, `TITLE`, `COLUMNS` (positions → feature
   columns) and `build(ctx)`. `build` returns one row per target row
   (`ctx.keys_for(position)`), keyed `(season, week, gsis_id)` (DEF:
   `(season, week, team)`), using only what was known before that week's
   games.
2. **Check it without scoring:** `PYTHONPATH=src python
   scripts/research/block_runner.py --block <ID> --coverage-only`. This
   prints each column's coverage by season and runs the leak check: the
   block is rebuilt with every post-game frame cut before week w (pre-game
   frames after week w), and week w's features must not change. No score is
   printed, so developing a block does not show its result.
3. **Pre-register** in `docs/research/blocks/<ID>.md` from the template
   below, and **commit** the doc and the module together. The runner refuses
   to score uncommitted or modified files.
4. **Run it once:** `PYTHONPATH=src python scripts/research/block_runner.py
   --block <ID>`. The result goes to `results/<ID>.json`, and one line is
   appended to `runs/<ID>.csv` with the blob hashes of the
   pre-registration and the module. Commit both. Fill in the doc's Result
   section from the JSON, below the pre-registration and without editing it.
5. A second run is legitimate only to fix a bug. Note it in the doc's
   Result section; the run ledger shows every run. A redesign after seeing a
   result is a new block with a new pre-registration, never a quiet retry.

## The bar (ground rule 2)

Seven season folds, 2019–2025, with every other season training. The rows,
universe and metrics are FEATURE_EXPANSION's: weeks 4–18, players Sleeper
projected, start/sit pairwise among players Sleeper projected 5+, and MAE on
every row. For each position a block touches, it must beat the shipped set
on that position's rows on **both** pairwise and MAE in **every** fold. Kickers
and defenses use the same folds on the `k_def_backtest.py` tables, with
pairwise among all kickers (or all defenses) in a week. A column with any
missing value enters as the value (0 where missing) plus `<col>__known`.
It is never filled with the training mean (ground rule 4).

Passing is necessary, not sufficient. A passing block runs **record-only**
for at least two live weeks (ground rule 3) before it may serve, and nothing
serves after the week-14 board (ground rule 8).

The harness reproduces the shipped set from the frozen tables: seven-fold
63.45% and 4.098, as stored. See `RESULTS.md` for the reproduction and every
block's outcome.

## Pre-registration template

```markdown
# <ID>: <title> (pre-registered <date>)

**Status: PRE-REGISTERED.** Written before any score was seen.

## Idea
<the roadmap row; what the shipped set already carries that overlaps>

## Hypothesis
<one or two sentences: what should move, for whom, why — volume vs
efficiency (rule #6)>

## Features (`COLUMNS`)
| column | position(s) | definition, as of the weeks before the row's week | source |
|---|---|---|---|

Missing when: <when a row has no value; it enters as 0 plus `__known`>

## Test
- Positions: <...>
- Seasons: 2019–2025, seven season folds (block_runner.py)
- Bar: better than the shipped set on start/sit pairwise AND MAE in every fold, per
  position; pooled not worse in any fold.
- Coverage seen before registering (no scores): <paste the coverage lines>

## If it passes
Record-only contender for two live weeks (ground rule 3), then a
DECISIONS.md row. Nothing serves after the week-14 board.

## Result
<filled in after the run, below this line, never above it>
```
