# P7 live check: RB-13 and TE-10 on 2026 weeks 9 and 10 (pre-registered 2026-11-02)

**Status: PRE-REGISTERED.** Written before week 9's first game (Thursday
5 November 2026, 01:15 UTC on the 6th) and before any week-9 or week-10
number exists. Ground rule 3 of `MODEL_ROADMAP.md`: a block that passes runs
record-only for at least two live weeks after its result is committed before
any serving decision. RB-13 and TE-10 passed their 2019-2025 bars and their
frozen 2026 weeks 4-7 holdouts (`RB-13-2026.md`, `TE-10-2026.md`; results
committed in `6d5a425` and `cb62b19` on 2026-11-02, before week 9). Weeks 9
and 10 are the first two weeks played after those commits. K-03 failed its
holdout and DEF-03 failed history, so neither is here.

## What is tested
For each of RB-13 and TE-10 separately: the frozen pipeline of its 2026
holdout, unchanged, scored on 2026 weeks 9 and 10 only.

- **Frozen:** every fitted number comes from the 2019-2025 fit recorded in
  `results/<ID>-2026.json` (the block's (a, b); the fitted normal's (a, b);
  the shipped ridge for the page's mean, fit on the frozen 2019-2025 season
  tables). Nothing is refit on any 2026 row.
- **Rows:** the 2026 rows of the block's position in weeks 9 and 10, in
  block_runner.py's universe (players Sleeper projected), each with its
  inputs from the 2026 games before its week, as in history.
- **Window rule:** a week counts only when every scheduled game of it is in
  the rebuilt 2026 tables (the holdouts' rule). The check runs after week 10
  is final; if either week is incomplete at that point, it waits. The window
  is exactly weeks 9 and 10; it does not grow and it does not include week 8.

## Bar (decided now, per block, on unrounded numbers)
The holdout's bar, applied once and pooled over weeks 9 and 10:
1. the block's mean CRPS over the window's rows is lower than the page's
   normal's;
2. the pooled P(win) Brier over the window's synthetic leagues is not worse
   than the page's (<=), the leagues dealt exactly as in the holdout (32
   seeded leagues a week from the window's rows of every position, 384
   matchups; a tied margin is skipped; the block's position carries the
   block, every other starter keeps the page's normal).

Reported, never the bar: the fitted normal's CRPS and Brier (the cheaper
change for P(win); on weeks 4-7 it took 96% of RB-13's Brier gain and 91% of
TE-10's), each week separately, the week-clustered bootstrap (two clusters:
crude), pinball losses, and interval coverage against each system's own
nominal share.

## Procedure
- The harness gains a live mode in `distribution_backtest.py`, additive
  (the historical folds and the weeks 4-7 holdout reproduce exactly), and it
  is committed, with any change to this doc's Procedure section, before the
  scored run. One scored run per block; a second only to fix a bug, said in
  the Result section.
- Data: the 2026 tables are rebuilt after week 10 is final, from 2026 files
  downloaded after that point (the research cache keeps nflverse files for
  30 days, so 2026 files cached before week 10 must not be reused). The
  frozen 2019-2025 season tables are not rebuilt; their digest must match
  the holdout's. If the rebuild cannot reproduce the holdout's weeks 4-7
  rows, the run stops and the reason is written here; nothing is refit.
- Refusals as in the holdouts: this doc, the block's module, every helper,
  the harness and the holdout's results committed and unchanged; the window
  complete; the leak check passing at weeks 9 and 10.

## What a result means
- **Pass (both parts):** the block is ELIGIBLE for a serving decision. That
  decision is a DECISIONS.md row, never automatic. It weighs the block
  against refitting the page's own RB or TE sd, which on weeks 4-7 matched
  most of its P(win) gain at no new model. A change serves from the next
  board after the decision and never after the week-14 board (ground rule
  8).
- **Fail:** not served this season. The offseason re-tests it on the full
  2026 season.

## Result
