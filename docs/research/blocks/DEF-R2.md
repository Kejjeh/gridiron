# DEF-R2: round 2, DEF-10 + DEF-11 on DEF (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only run
before this was `--coverage-only` (coverage and the leak check, no score).
The selection, the bar and the caveat are in `ROUND2.md`, which this block
follows exactly.

**Correction 2026-11-02, before any round-2 score** (`ROUND2.md`): the
near-miss bug hunt found three bugs in shared helpers after this file was
first committed. They were fixed (`fb26c4b`), every round-1 block that reads
a fixed helper was re-run once (`432deda`), and the selection rule was
re-applied to the corrected results. The parts are unchanged, and neither reads a fixed
helper (DEF-11 uses `availability`, which the fixes do not touch).
Re-measured, the coverage below is unchanged.

## Idea
The union of two round-1 near misses for DEF: DEF-10 and DEF-11, each built by its
own committed module, unchanged (`ROUND2.md` lists their blobs). Both
improved the mean of both metrics in round 1 without winning every fold.

## Features (`COLUMNS`)
Every column of DEF-10 and of DEF-11, as their own pre-registrations define them
(`DEF-10.md`, `DEF-11.md`). Missing enters as 0 plus `<col>__known`.

## Test
- Positions: DEF.
- Seasons: 2019–2025, seven season folds (`block_runner.py`).
- Bar, both parts (`ROUND2.md`): (1) better than the shipped set on start/sit
  pairwise AND MAE in every fold on DEF rows; (2) only if (1) holds, better
  on both metrics pooled over 2026 weeks 4 to the last played week
  (`holdout_2026.py`, fit on all of 2019–2025).
- Coverage seen before registering (no scores):
  - DEF rest_days: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - DEF opp_rest_days: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - DEF opp_tz_crossed: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 97%
  - DEF thursday: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - DEF own_front_out: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - DEF own_back_out: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - DEF own_def_q: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes both parts
Record-only contender for two live weeks after its result is committed
(ground rule 3), then a DECISIONS.md row. Nothing serves after the week-14
board.

## Result

Run 2026-11-02T09:28:01+00:00 at `dbaa63e` (one scored run; `runs/DEF-R2.csv`). Start/sit pairwise and MAE on DEF rows:

| fold | shipped | DEF-R2 |
|---|---|---|
| 2019 | 60.23%, 5.248 | 60.32%, 5.230 |
| 2020 | 60.30%, 4.638 | 60.50%, 4.614 |
| 2021 | 60.28%, 4.801 | 60.97%, 4.754 |
| 2022 | 59.26%, 4.335 | 59.87%, 4.290 |
| 2023 | 60.72%, 4.835 | 60.66%, 4.825 |
| 2024 | 64.66%, 4.461 | 65.05%, 4.442 |
| 2025 | 62.01%, 4.588 | 61.86%, 4.605 |

**Fails** part 1, so part 2 (the 2026 holdout) does not run. Better on both metrics in 5 of 7 folds; not in 2023 (pairwise -0.06 points); 2025 (pairwise -0.15 points, MAE +0.0171). Mean change +0.25 points of pairwise, -0.0206 MAE. Not a contender.
