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

## Correction 2026-11-02, before any round-2 score
After this file was first committed (`6cd528a`) and before any round-2 block
was scored, a bug hunt over the round-1 near misses found three bugs in
shared helpers. Fixed in `fb26c4b`:

- `_p2a_events_offense.roster_away`: from 2020 a released player whom no team
  signed counted as available (the 2019 rosters keep his CUT row, later
  seasons do not). Read by QB-06, QB-07, RB-06, TE-06, TE-07, WR-02, WR-03.
- `_p2b_events_ramps_defense.hurt`: the COVID-19 list and suspensions counted
  as injuries from 2020, against the pre-registrations. Read by RB-02,
  WR-13, TE-12.
- `_p5_schedule_weather.weather`: the observed reanalysis (a post-game
  observation) stood in for the forecast at the five 2019 international
  games. Read by QB-11, K-05, DEF-08, WR-11.

Each of those 14 round-1 blocks was re-run once (`432deda`; each Result
section says why). All 14 still fail. The selection rule above, unchanged,
re-applied to the corrected results (folds better on both, mean Δpairwise in
points, mean ΔMAE):

| position | qualifying round-1 blocks, ranked | top two |
|---|---|---|
| QB | QB-11 (5/7, +0.13, -0.0248), QB-02 (4/7, +0.10, -0.0156) | QB-02 + QB-11, unchanged |
| RB | RB-06 (4/7, +0.31, -0.0266), RB-02 (4/7, +0.11, -0.0103), RB-10 (3/7, +0.09, -0.0069) | RB-06 + RB-02, unchanged |
| WR | WR-07 (5/7, +0.28, -0.0088), WR-11 (3/7, +0.100, -0.0047), WR-03 (3/7, +0.096, -0.0107) | **WR-07 + WR-11** (was WR-07 + WR-03) |
| TE | TE-08 (3/7, +0.12, -0.0008) | none, unchanged |
| K | K-05 (4/7, +0.86, -0.0073), K-06 (4/7, +0.14, -0.0003), K-02 (3/7, +0.09, -0.0028) | K-05 + K-06, unchanged |
| DEF | DEF-10 (4/7, +0.17, -0.0130), DEF-11 (3/7, +0.14, -0.0074), DEF-08 (3/7, +0.11, -0.0015) | DEF-10 + DEF-11, unchanged |

So WR-R2 is now WR-07 + WR-11. WR-11 and WR-03 are 0.004 points apart, close
to a tie: which one is second says little, and the 2026 holdout (part 2 of
the bar below) is the check on that. The parts the round-2 runs use (module
blob unchanged; result blob after the re-runs, at `432deda`):

| block | parts (round-1 module blob, result blob) |
|---|---|
| QB-R2 | QB-02 (`9328d0e477d0`, `a9edf25dbea4`) + QB-11 (`8bce0ad219f6`, `f6781aeacc4e`) |
| RB-R2 | RB-06 (`44b814a87f7a`, `9d524eab26b3`) + RB-02 (`16f61772b497`, `47f4f19ddf58`) |
| WR-R2 | WR-07 (`43f2c6df8833`, `906547a351da`) + WR-11 (`64e9a715610a`, `35614ef9ed65`) |
| K-R2 | K-05 (`0c2a61da1f30`, `cc3ffe465a2f`) + K-06 (`1ee6f3dd3226`, `8e3ee3e3769a`) |
| DEF-R2 | DEF-10 (`8a969a5381ce`, `ac46a95fa049`) + DEF-11 (`292174116e96`, `d1397c877231`) |

Every round-2 block builds on the fixed helpers. Each one's doc carries a
dated correction, and its coverage lines come from a coverage-only run on the
fixed helpers (all five leak checks passed again).

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
