# WR-R2: round 2, WR-07 + WR-03 on WR (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only run
before this was `--coverage-only` (coverage and the leak check, no score).
The selection, the bar and the caveat are in `ROUND2.md`, which this block
follows exactly.

## Idea
The union of two round-1 near misses for WR: WR-07 and WR-03, each built by its
own committed module, unchanged (`ROUND2.md` lists their blobs). Both
improved the mean of both metrics in round 1 without winning every fold.

## Features (`COLUMNS`)
Every column of WR-07 and of WR-03, as their own pre-registrations define them
(`WR-07.md`, `WR-03.md`). Missing enters as 0 plus `<col>__known`.

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
  - WR wr_vac_tgt_new: 2019 99%, 2020 99%, 2021 99%, 2022 100%, 2023 100%, 2024 99%, 2025 99%
  - WR wr_vac_route_new: 2019 90%, 2020 90%, 2021 91%, 2022 92%, 2023 93%, 2024 91%, 2025 92%
  - WR wr_vac_tgt_all: 2019 98%, 2020 99%, 2021 99%, 2022 99%, 2023 99%, 2024 99%, 2025 99%
  - leak check: passed (2021 week 9, 2024 week 14)

## If it passes both parts
Record-only contender for two live weeks after its result is committed
(ground rule 3), then a DECISIONS.md row. Nothing serves after the week-14
board.

## Result
