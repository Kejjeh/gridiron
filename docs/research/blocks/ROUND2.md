# Round 2: combinations of round-1 near misses (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any round-2 score was seen.
Round 2's selection reads the round-1 results, so this is a declared post-hoc
test, never a quiet retry of a failed block (ground rule 1). Its bar has
a second part for that reason.

## Why
In round 1, 58 feature blocks were scored on 2019–2025; none passed the
every-fold bar (`RESULTS.md`). Some improved the mean of both metrics without
winning every fold. A small gain spread over two signals for the same
position might clear the bar together. It might also look good because it
was picked for looking good. The 2026 holdout decides between the two.

## Selection (mechanical, from the committed round-1 results at `c0c174b`)
Per position: the round-1 runner blocks with mean Δpairwise > 0, mean ΔMAE
< 0 and at least 3 folds better on both, ranked by mean Δpairwise. The top
two are taken. A position with only one such block gets no round-2 block.
Tight end has only TE-08, so it is not in round 2.

| block | parts (round-1 module blob, result blob) | position |
|---|---|---|
| QB-R2 | QB-02 (`9328d0e477d0`, `a9edf25dbea4`) + QB-11 (`8bce0ad219f6`, `4682060f744e`) | QB |
| RB-R2 | RB-06 (`44b814a87f7a`, `b1767c7e6b92`) + RB-02 (`16f61772b497`, `915125495b5f`) | RB |
| WR-R2 | WR-07 (`43f2c6df8833`, `906547a351da`) + WR-03 (`f59410083406`, `e91974f9b5cf`) | WR |
| K-R2 | K-05 (`0c2a61da1f30`, `3686cd4546f5`) + K-06 (`1ee6f3dd3226`, `8e3ee3e3769a`) | K |
| DEF-R2 | DEF-10 (`8a969a5381ce`, `ac46a95fa049`) + DEF-11 (`292174116e96`, `d1397c877231`) | DEF |

Each round-2 block is the union of its parts' columns, built by the parts'
own committed modules, unchanged (`scripts/research/blocks/_round2.py`). The
runner adds the usual `__known` flags.

## Bar
Both parts must hold, per block:
1. **Historical.** The runner's bar: better than the shipped set on that
   position's rows on start/sit pairwise AND MAE in every one of the seven
   folds of 2019–2025.
2. **The 2026 holdout.** Only for a block that clears part 1:
   `scripts/research/holdout_2026.py` fits the shipped set and the block on
   all of 2019–2025 and scores 2026 weeks 4 to the last played week. The block
   must be better on both metrics, pooled over those weeks. No test has used
   2026.

A block that clears both is a contender. It then runs record-only for two
live weeks after its result is committed (ground rule 3) before any serving
decision, and nothing changes after the week-14 board (ground rule 8).

## Multiple comparisons, stated
Round 2 picks 5 combinations out of 58 round-1 tests. Part 1 alone is weak
evidence for a picked block. Part 2 is the test: clean 2026 data the
selection never saw.

## Result
Each block's own doc (`<ID>.md`) holds its coverage, its run and its
result, under the bar above.
