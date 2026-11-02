# RB-R2: round 2, RB-06 + RB-02 on RB (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only run
before this was `--coverage-only` (coverage and the leak check, no score).
The selection, the bar and the caveat are in `ROUND2.md`, which this block
follows exactly.

## Idea
The union of two round-1 near misses for RB: RB-06 and RB-02, each built by its
own committed module, unchanged (`ROUND2.md` lists their blobs). Both
improved the mean of both metrics in round 1 without winning every fold.

## Features (`COLUMNS`)
Every column of RB-06 and of RB-02, as their own pre-registrations define them
(`RB-06.md`, `RB-02.md`). Missing enters as 0 plus `<col>__known`.

## Test
- Positions: RB.
- Seasons: 2019–2025, seven season folds (`block_runner.py`).
- Bar, both parts (`ROUND2.md`): (1) better than the shipped set on start/sit
  pairwise AND MAE in every fold on RB rows; (2) only if (1) holds, better
  on both metrics pooled over 2026 weeks 4 to the last played week
  (`holdout_2026.py`, fit on all of 2019–2025).
- Coverage seen before registering (no scores):
  - RB rb_vac_early: 2019 100%, 2020 100%, 2021 100%, 2022 99%, 2023 100%, 2024 100%, 2025 99%
  - RB rb_vac_pd: 2019 100%, 2020 100%, 2021 99%, 2022 99%, 2023 100%, 2024 99%, 2025 99%
  - RB rb_vac_gl: 2019 99%, 2020 98%, 2021 98%, 2022 98%, 2023 99%, 2024 99%, 2025 99%
  - RB ret_back: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - RB ret_missed: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - RB ret_soft: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - RB mate_back_opp: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes both parts
Record-only contender for two live weeks after its result is committed
(ground rule 3), then a DECISIONS.md row. Nothing serves after the week-14
board.

## Result
