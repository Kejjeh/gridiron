# Historical draft-theory tests

Readiness: no new numerical weights approved for the live draft model. These are exploratory partial tests.

## Findings

Conditioning uncertainty on position and draft-rank thirds changed mean quantile loss by -2.26% and breakout Brier loss by -2.52% versus pooled uncertainty. This supports further work on outcome ranges; it does not validate numerical point-projection adjustments.

Simple historical SOS did not consistently help early WR or playoff RB forecasts. WR weeks 5-14 RMSE changed -0.43%, among 12 examined position/window cells. No blanket RB discount or live SOS weight is approved.

## What was actually tested

Six dated preseason PPR ranking cohorts (2020-2025), scored using the verified league half-PPR formula. Point projections archived before those seasons were not obtained. Ranking-derived expectations are a proxy and do not replicate the linked projection-error studies. Current player names/teams/ages are not features; the current crosswalk is used only for stable identity. Unknown IDs are excluded and logged. Ranked players with resolved identity and no recorded regular-season production remain zero outcomes.

Training expands chronologically: 2020-2022 -> 2023, 2020-2023 -> 2024, 2020-2024 -> 2025. Within each training block, leave-one-season-out residuals calibrate distributions; none use the target season. The mean baseline fits points to log positional rank separately for QB/RB/WR/TE. Residual candidates pool all positions, separate positions, or separate position and fixed rank thirds. Each subgroup receives 20 fixed quantiles from the pooled residual distribution as shrinkage. All choices were fixed before viewing test scores; no tuning on held-out outcomes.

Outcome is individual W1-W17 core offensive production, including missed weeks. Return/recovery touchdowns and other special-teams scoring are outside this canonical offensive formula. No optimal hindsight replacements, weekly lineup decisions, waiver competition or championship-probability claims. The ranking archive uses PPR, while outcomes use half-PPR; this limits transferability.

## Outcome distribution and upside results

Lower loss is better. Pinball is the average quantile loss at 5%, 10%, ..., 95%. Upside Brier scores the probability of exceeding max(baseline expected points, estimated replacement points) by more than 25 points. Replacement ranks are QB16/RB40/WR48/TE20, not optimized league waiver levels.

| Test | Candidate | Baseline | Candidate loss | Change | Season-block 95% interval | Better seasons |
|---|---|---:|---:|---:|---|---:|
| pinball | position | 17.9352 | 17.7888 | -0.1464 | [-0.2100, -0.1102] | 3/3 |
| pinball | position_rank | 17.9352 | 17.5300 | -0.4053 | [-0.6209, -0.0464] | 3/3 |
| upside_brier | position | 0.2039 | 0.2027 | -0.0012 | [-0.0026, +0.0002] | 2/3 |
| upside_brier | position_rank | 0.2039 | 0.1988 | -0.0051 | [-0.0074, -0.0021] | 3/3 |

| Model | 80% interval coverage | Mean interval width (points) |
|---|---:|---:|
| pooled | 79.2% | 146.6 |
| position | 78.8% | 148.6 |
| position_rank | 79.2% | 145.8 |

## Schedule test

This tests prior-year fantasy points allowed, NOT Subvertadown proprietary SOS, ESPN line win rates, PFF grades or personnel adjustments. The baseline is a fitted prior-year team-position points-per-game model. The added feature is the average prior-year opponent points allowed minus the league position mean. Both models fit only earlier seasons, separately by position and window. Every completed team game is represented, including zero-production positions. Canceled games are excluded. Final historical schedules are used for opponent/week assignment, so rescheduling revisions are a limitation; scores only identify completed games and never enter a predictor.

| Position | Window | Baseline RMSE | With SOS RMSE | MSE change | Season-block 95% interval | Better seasons |
|---|---|---:|---:|---:|---|---:|
| RB | all | 3.854 | 3.860 | +0.049 | [-0.142, +0.161] | 1/3 |
| RB | early | 5.384 | 5.383 | -0.013 | [-0.043, +0.009] | 2/3 |
| RB | middle | 4.308 | 4.315 | +0.064 | [-0.182, +0.333] | 1/3 |
| RB | playoffs | 6.537 | 6.562 | +0.337 | [-0.772, +0.959] | 1/3 |
| TE | all | 2.771 | 2.770 | -0.005 | [-0.014, +0.012] | 2/3 |
| TE | early | 3.400 | 3.411 | +0.072 | [-0.018, +0.236] | 2/3 |
| TE | middle | 3.349 | 3.354 | +0.033 | [-0.026, +0.085] | 1/3 |
| TE | playoffs | 4.677 | 4.694 | +0.162 | [-0.049, +0.347] | 1/3 |
| WR | all | 4.954 | 4.921 | -0.331 | [-1.324, +0.348] | 2/3 |
| WR | early | 6.315 | 6.362 | +0.591 | [-4.788, +4.866] | 1/3 |
| WR | middle | 6.025 | 5.999 | -0.311 | [-0.621, -0.110] | 3/3 |
| WR | playoffs | 8.764 | 8.763 | -0.017 | [-0.083, +0.085] | 2/3 |

## Coverage

| Season | Position | Snapshot | Ranked | ID matched | Zero outcomes |
|---|---|---|---:|---:|---:|
| 2020 | QB | 2020-09-03 | 24 | 24 | 0 |
| 2020 | RB | 2020-09-03 | 72 | 72 | 3 |
| 2020 | TE | 2020-09-03 | 36 | 36 | 0 |
| 2020 | WR | 2020-09-03 | 96 | 95 | 1 |
| 2021 | QB | 2021-09-03 | 24 | 24 | 0 |
| 2021 | RB | 2021-09-03 | 72 | 72 | 2 |
| 2021 | TE | 2021-09-03 | 36 | 36 | 0 |
| 2021 | WR | 2021-09-03 | 96 | 96 | 2 |
| 2022 | QB | 2022-09-02 | 24 | 24 | 0 |
| 2022 | RB | 2022-09-02 | 72 | 72 | 0 |
| 2022 | TE | 2022-09-02 | 36 | 36 | 0 |
| 2022 | WR | 2022-09-02 | 96 | 96 | 1 |
| 2023 | QB | 2023-09-01 | 24 | 24 | 1 |
| 2023 | RB | 2023-09-01 | 72 | 72 | 0 |
| 2023 | TE | 2023-09-01 | 36 | 36 | 1 |
| 2023 | WR | 2023-09-01 | 96 | 96 | 1 |
| 2024 | QB | 2024-08-30 | 24 | 24 | 0 |
| 2024 | RB | 2024-08-30 | 72 | 72 | 0 |
| 2024 | TE | 2024-08-30 | 36 | 36 | 0 |
| 2024 | WR | 2024-08-30 | 96 | 96 | 2 |
| 2025 | QB | 2025-08-29 | 24 | 24 | 0 |
| 2025 | RB | 2025-08-29 | 72 | 72 | 3 |
| 2025 | TE | 2025-08-29 | 36 | 36 | 0 |
| 2025 | WR | 2025-08-29 | 96 | 96 | 4 |

## Interpretation limits and promotion gate

- Three held-out season clusters are too few for a strong generalization claim. Bootstrap intervals are descriptive; they do not create more independent seasons. Multiple candidate/window comparisons were not corrected.
- The hypotheses were inspired by articles that already considered seasons through 2025. These years are held out from our fitting, but not a pristine independent confirmation of the published theories.
- A low-risk position is not necessarily the best draft pick. Replacement supply, roster needs, price, within-team correlations and weekly lineup decisions remain untested.
- No calibrated mean discount, added upside bonus, roster win probability, or production feature is justified by this run.
- Required next data: preseason point/stat projections with immutable dates, stable IDs, scoring definitions, and coverage of players who later missed the season; historical SOS forecasts and archived schedule releases.
- Then compare against ALL live-model features, use additional unseen seasons, validate weekly roster decisions and evaluate replacement assumptions before any live promotion.

## Data provenance and reproducibility

The JSON manifest records SHA-256 hashes of every local input, research/scoring code, library versions, parameters and fixed seed. Raw downloads are cached and excluded from git. Historical statistics were retrieved now and may include corrections; ranking scrape dates come from the archive and were screened before each kickoff. Independent pre-kickoff commit attestation for each ranking snapshot was not established.

[Ranking archive](https://github.com/DynastyProcess/data) · [NFL data](https://github.com/nflverse/nflverse-data) · [Schedule archive](https://github.com/nflverse/nfldata)

```powershell
$env:PYTHONPATH="src"
# Optional, only if raw caches are absent:
# .venv/Scripts/python.exe scripts/research/test_draft_theories.py --fetch
.venv/Scripts/python.exe scripts/research/test_draft_theories.py
.venv/Scripts/python.exe scripts/ci/smoke.py
.venv/Scripts/python.exe scripts/ci/run_summary.py -- .venv/Scripts/python.exe -m pytest
```
