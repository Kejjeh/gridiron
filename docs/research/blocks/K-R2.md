# K-R2: round 2, K-05 + K-06 on K (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only run
before this was `--coverage-only` (coverage and the leak check, no score).
The selection, the bar and the caveat are in `ROUND2.md`, which this block
follows exactly.

**Correction 2026-11-02, before any round-2 score** (`ROUND2.md`): the
near-miss bug hunt found three bugs in shared helpers after this file was
first committed. They were fixed (`fb26c4b`), every round-1 block that reads
a fixed helper was re-run once (`432deda`), and the selection rule was
re-applied to the corrected results. The parts are unchanged. K-05's weather columns now
treat the five 2019 international games as unknown, so the coverage
below is re-measured: `wind10` and `precip3h` in 2019 drop to 98%.

## Idea
The union of two round-1 near misses for K: K-05 and K-06, each built by its
own committed module, unchanged (`ROUND2.md` lists their blobs). Both
improved the mean of both metrics in round 1 without winning every fold.

## Features (`COLUMNS`)
Every column of K-05 and of K-06, as their own pre-registrations define them
(`K-05.md`, `K-06.md`). Missing enters as 0 plus `<col>__known`.

## Test
- Positions: K.
- Seasons: 2019–2025, seven season folds (`block_runner.py`).
- Bar, both parts (`ROUND2.md`): (1) better than the shipped set on start/sit
  pairwise AND MAE in every fold on K rows; (2) only if (1) holds, better
  on both metrics pooled over 2026 weeks 4 to the last played week
  (`holdout_2026.py`, fit on all of 2019–2025).
- Coverage seen before registering (no scores):
  - K wind10: 2019 98%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 98%
  - K precip3h: 2019 98%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 98%
  - K elev_km: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 98%
  - K fg50_share_oe: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - K fg50_share_prior_oe: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - K k_fg_oe: 2019 100%, 2020 100%, 2021 100%, 2022 100%, 2023 100%, 2024 100%, 2025 100%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes both parts
Record-only contender for two live weeks after its result is committed
(ground rule 3), then a DECISIONS.md row. Nothing serves after the week-14
board.

## Result
