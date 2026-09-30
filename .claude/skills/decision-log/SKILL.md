---
name: decision-log
description: Grade a finished week's Gridiron advice against actual points and keep a season-long accuracy ledger — which archived board to grade, actual points from nflverse, the owner's confirmed moves, and the season-to-date hit rates. Use after a week's games are final (Tuesday onward), or when the owner asks whether the advice has been any good.
---

# decision-log

Rule #7: grade the choice with the rejected side, from numbers frozen at
decision time. `gridiron.decisions.grade_archive` does the grading; this
skill runs it every week so accuracy accumulates.

## Steps

1. Archives: `PYTHONPATH=src python scripts/weekly/fetch_record.py` prints an
   `archives:` path (the decision-time records the cloud carries). Locally
   they are under `data/ledger/decisions/`.
2. Ask the owner which moves they actually made that week (adds, drops,
   swaps), unless they already said. Only those become `--acted` keys; the
   program never infers what the owner did.
3. Grade the board as it stood before the Sunday early kickoff (13:00 ET =
   17:00 UTC during EDT, 18:00 UTC after the November clock change):

       PYTHONPATH=src python scripts/weekly/grade_week.py --week <W> \
           --archives <archives path> --before <Sunday kickoff, UTC ISO> \
           --actuals-nflverse [--acted <key> ...] [--declined <key> ...]

   Keys are printed in the table (`start_sit:FLEX:<held>:<alternative>`,
   `waiver:<slot>:<drop>:<add>`). Without network, use `--actuals-cache
   data/research/cache/season<YYYY>` or `--actuals-record <later record>`
   (the latter misses adds another manager has since claimed).
4. Report: the week's summary line, the projection-direction rate, the
   roster projection MAE, and the season-to-date line. Name the biggest
   misses from the table (a large projected edge that went the other way).
5. Commit `data/ledger/grades/season<YYYY>.csv` — aggregate counts only, no
   names or ids — with the week's result in the message.

## Reading the numbers

- `agree/scorable` counts only advice the page ENDORSED (gate open, add not
  UNVERIFIED). It is often empty: on game days the page withholds.
- `direction_agree/direction_n` is the projection's directional hit rate on
  every graded comparison, advice or not — the model's report card.
- A single week is noise. Do not retune anything from one week's grade;
  a model change needs an out-of-sample win over the full baseline (rule #5).

## Never

- Never submit anything to the league, and never record an `--acted` key
  the owner did not confirm.
- Never grade from the archive's own projections as if they were actuals,
  and never re-project a past week.
- Never commit per-player grade tables (stdout only).
