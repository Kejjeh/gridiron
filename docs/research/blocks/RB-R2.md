# RB-R2: round 2, RB-06 + RB-02 on RB (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before any score was seen; the only run
before this was `--coverage-only` (coverage and the leak check, no score).
The selection, the bar and the caveat are in `ROUND2.md`, which this block
follows exactly.

**Correction 2026-11-02, before any round-2 score** (`ROUND2.md`): the
near-miss bug hunt found three bugs in shared helpers after this file was
first committed. They were fixed (`fb26c4b`), every round-1 block that reads
a fixed helper was re-run once (`432deda`), and the selection rule was
re-applied to the corrected results. The parts are unchanged. Both now build on the fixed
helpers (RB-06: a released back counts as away in every season; RB-02:
only injury lists count as injuries). Re-measured, the coverage below is
unchanged.

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

Run 2026-11-02T09:27:57+00:00 at `dbaa63e` (one scored run; `runs/RB-R2.csv`). Start/sit pairwise and MAE on RB rows:

| fold | shipped | RB-R2 |
|---|---|---|
| 2019 | 65.54%, 4.349 | 65.56%, 4.298 |
| 2020 | 64.79%, 4.365 | 65.47%, 4.349 |
| 2021 | 63.83%, 4.521 | 64.84%, 4.484 |
| 2022 | 64.62%, 4.190 | 64.64%, 4.165 |
| 2023 | 64.75%, 4.071 | 65.01%, 4.027 |
| 2024 | 65.80%, 4.131 | 66.55%, 4.074 |
| 2025 | 66.91%, 4.122 | 67.09%, 4.104 |

**Passes part 1** (the historical bar). Better on both metrics in all 7 folds; mean change +0.42 points of pairwise, -0.0357 MAE; pooled numbers not worse in any fold: True. The MAE gain holds in every fold (-0.016 to -0.058), but two folds win the ordering by a hair: 2019 by 2 of 11,726 pairs (+0.02 points) and 2022 by 3 of 14,913 (+0.02). Under `ROUND2.md` this is weak evidence on its own (a picked combination); part 2, the 2026 holdout, decides.
