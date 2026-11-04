# QB-R2: round 2, QB-02 + QB-11 on QB (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only run
before this was `--coverage-only` (coverage and the leak check, no score).
The selection, the bar and the caveat are in `ROUND2.md`, which this block
follows exactly.

**Correction 2026-11-02, before any round-2 score** (`ROUND2.md`): the
near-miss bug hunt found three bugs in shared helpers after this file was
first committed. They were fixed (`fb26c4b`), every round-1 block that reads
a fixed helper was re-run once (`432deda`), and the selection rule was
re-applied to the corrected results. The parts are unchanged. QB-11's weather columns now
treat the five 2019 international games as unknown (no observed
reanalysis), so the coverage below is re-measured: `wind10` and
`wind10_x_deep` in 2019 drop to 98% and 97%.

## Idea
The union of two round-1 near misses for QB: QB-02 and QB-11, each built by its
own committed module, unchanged (`ROUND2.md` lists their blobs). Both
improved the mean of both metrics in round 1 without winning every fold.

## Features (`COLUMNS`)
Every column of QB-02 and of QB-11, as their own pre-registrations define them
(`QB-02.md`, `QB-11.md`). Missing enters as 0 plus `<col>__known`.

## Test
- Positions: QB.
- Seasons: 2019–2025, seven season folds (`block_runner.py`).
- Bar, both parts (`ROUND2.md`): (1) better than the shipped set on start/sit
  pairwise AND MAE in every fold on QB rows; (2) only if (1) holds, better
  on both metrics pooled over 2026 weeks 4 to the last played week
  (`holdout_2026.py`, fit on all of 2019–2025).
- Coverage seen before registering (no scores):
  - QB qb_designed_pg: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - QB qb_scramble_pg: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - QB wind10: 2019 98%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 98%
  - QB wind10_x_deep: 2019 97%, 2020 99%, 2021 99%, 2022 100%, 2023 100%, 2024 100%, 2025 97%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes both parts
Record-only contender for two live weeks after its result is committed
(ground rule 3), then a DECISIONS.md row. Nothing serves after the week-14
board.

## Result

Run 2026-11-02T09:27:39+00:00 at `dbaa63e` (one scored run; `runs/QB-R2.csv`). Start/sit pairwise and MAE on QB rows:

| fold | shipped | QB-R2 |
|---|---|---|
| 2019 | 60.20%, 6.362 | 60.94%, 6.295 |
| 2020 | 62.48%, 6.319 | 61.49%, 6.324 |
| 2021 | 62.44%, 6.142 | 62.77%, 6.079 |
| 2022 | 66.50%, 5.520 | 66.52%, 5.476 |
| 2023 | 63.03%, 5.848 | 63.73%, 5.784 |
| 2024 | 62.50%, 6.160 | 63.05%, 6.145 |
| 2025 | 59.54%, 6.244 | 60.14%, 6.202 |

**Fails** part 1, so part 2 (the 2026 holdout) does not run. Better on both metrics in 6 of 7 folds; not in 2020 (pairwise -0.99 points, MAE +0.0044). Mean change +0.28 points of pairwise, -0.0416 MAE. Not a contender.
