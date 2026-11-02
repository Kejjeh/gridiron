# WR-R2: round 2, WR-07 + WR-11 on WR (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only run
before this was `--coverage-only` (coverage and the leak check, no score).
The selection, the bar and the caveat are in `ROUND2.md`, which this block
follows exactly.

**Correction 2026-11-02, before any round-2 score** (`ROUND2.md`): the
near-miss bug hunt found three bugs in shared helpers after this file was
first committed. They were fixed (`fb26c4b`), every round-1 block that reads
a fixed helper was re-run once (`432deda`), and the selection rule was
re-applied to the corrected results. For WR it now ranks WR-11 second
(+0.100 points of mean pairwise change) and WR-03 third (+0.096), so this
block's parts changed from WR-07 + WR-03 (as first committed in `6cd528a`)
to WR-07 + WR-11. The two are close to a tie, which is what part 2 of the
bar (the 2026 holdout) guards against. Everything below is written for the
corrected parts; the coverage is from a coverage-only run on the fixed
helpers.

## Idea
The union of two round-1 near misses for WR: WR-07 and WR-11, each built by
its own committed module, unchanged (`ROUND2.md` lists their blobs). Both
improved the mean of both metrics in round 1 without winning every fold.

## Features (`COLUMNS`)
Every column of WR-07 and of WR-11, as their own pre-registrations define them
(`WR-07.md`, `WR-11.md`). Missing enters as 0 plus `<col>__known`.

## Test
- Positions: WR.
- Seasons: 2019–2025, seven season folds (`block_runner.py`).
- Bar, both parts (`ROUND2.md`): (1) better than the shipped set on start/sit
  pairwise AND MAE in every fold on WR rows; (2) only if (1) holds, better
  on both metrics pooled over 2026 weeks 4 to the last played week
  (`holdout_2026.py`, fit on all of 2019–2025).
- Coverage seen before registering (no scores):
  - WR tprr_prior: 2019 75%, 2020 75%, 2021 82%, 2022 81%, 2023 78%, 2024 79%, 2025 80%
  - WR tprr_prior_x_snap_l3: 2019 75%, 2020 75%, 2021 82%, 2022 81%, 2023 78%, 2024 79%, 2025 79%
  - WR wind10: 2019 98%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 98%
  - WR wind10_x_deep: 2019 97%, 2020 99%, 2021 100%, 2022 99%, 2023 99%, 2024 99%, 2025 97%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes both parts
Record-only contender for two live weeks after its result is committed
(ground rule 3), then a DECISIONS.md row. Nothing serves after the week-14
board.

## Result
