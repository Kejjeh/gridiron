# HANDOFF

Updated: 2026-09-04 (research/math session; build step 1 done, and step 3's
shape settled by research before step 2 starts)

## State

**Code** — `python scripts/ci/smoke.py` green; 9 contract files, 73 tests:
- Bootstrap skeleton from 09-03: paths/config/league_config/scoring/espn,
  the three ported CI scripts, the CLAUDE.md budget ratchet, 12 rules.
- Pure-math modules, each with implementation-independent tests:
  - `winprob.py` — Φ closed form, leverage-per-point, the variance-flip
    derivative (both derivatives pinned against finite differences).
  - `shrinkage.py` — empirical-Bayes posterior, reliability→n₀ conversion,
    method-of-moments Beta fit, plus the §5 priors (marked UNVERIFIED).
  - `season.py` — exact Poisson-binomial win distribution and the playoff
    leverage identity `P(playoffs|win) − P(playoffs|lose) = pmf_rest(k−1)`.
  - `vegas.py` — implied-total identity, nflverse sign conversion, and the
    verified line→scoring/efficiency coefficients with the worked example
    pinned to the verifier's own recomputation.

**Research** — `docs/research/QUANT_FOUNDATIONS.md`, six domains:

| section | verified? |
|---|---|
| §1 Vegas, §2 win probability, §4 data sources | yes — 14 claims were corrected |
| §3 season leverage | derived in-repo, covered by tests |
| §5 stabilization | mostly — its script passes 381/382 |
| §6 VOR | **partly** — 177 pass, 12 fail (flex margin, man-games shifts) |
| §7 FAAB | **partly** — 66 pass, 9 fail (common-value sim did not converge) |

The §5–7 verifier agents were killed by a usage limit *after* writing and
running their scripts but *before* reconciling results into the prose, so the
failures above are flagged inline in the doc but not yet fixed. All five
verifier scripts are committed under `scripts/research/`.
Research drafts (gitignored, survive interrupted runs):
`docs/research/drafts/*.md`.

**Five findings that change the plan:**
1. The Vegas signal reaches receivers 92% through points-per-target, 8%
   through volume; volume-on-line has zero out-of-sample skill. Step 3's
   baseline is `usage prior × (positional efficiency × line multiplier)`.
2. Chasing variance only pays when trailing by ≳6 projected points; the
   closed-form derivative is wrong-signed at a 5-point deficit.
3. `nfl_data_py` is dead → `nflreadpy`; betting lines are free in nflverse
   schedules; snap counts are keyed by PFR id; Sleeper's own gsis ids cover
   only 20–32% of skill players, so every join goes through `ff_playerids`.
4. WR/TE target share stabilizes at ~90 team targets (~3 games) while
   touchdown rate retains only 11% of a top-decile season year over year.
5. ~10 of the 12 flex slots in a 12-team full-PPR league go to WR, which
   sets replacement level at RB25–26 / WR34–35 / TE12–13.

## BLOCKING before build step 3

League settings are still guesses (`league_config.SETTINGS_VERIFIED = False`).
Need: the platform (ESPN or Sleeper) and league id; real scoring and roster
slots pulled from the platform; `.env` credentials if it is ESPN. Note every
PPG/points number in §6 and §7 is scoring-dependent — half-PPR alone moves
2–4 flex slots back to RB.

## Next

1. Reconcile the 21 known §5–7 verification failures (relaunch the persisted
   workflow with `args: ["stabilization","faab","vor"]`, or just read the
   scripts' output and fix the prose). The two that matter: the flex-margin
   replacement level T may be 7.2–9.3 PPG rather than the claimed 11.0, and
   the man-games QB shift may be −3.2 rather than −1.0. Then drop the
   UNVERIFIED banner in `shrinkage.py`.
2. Build step 2 (ingest). nflverse 2023–25 play-by-play and weekly stats are
   ALREADY cached in `data/research/cache/nflverse/` (gitignored) from the
   verifier run, so this can start offline. Add `nflreadpy` weekly + snaps
   (PFR join) + `ff_playerids` crosswalk + schedules lines; write contract
   tests on column sets, since nflreadpy 0.1.x schemas drift. Pin
   `pandas>=2.0,<4`.
3. Build step 3 with the corrected baseline shape; register the pipeline as
   a `golden_run.py` target.

## Not done deliberately

- No skills yet (rule #12).
- The research loop was ended at Josh's request.
- Scratchpad helper for future batches: `split_batch.py <workflow output>
  <outdir>` turns a workflow result into per-domain markdown.
