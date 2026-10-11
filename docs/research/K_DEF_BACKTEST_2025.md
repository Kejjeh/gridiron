# Kickers and team defenses in the advanced model (2026-09-30)

Short answer: **both beat what we had, out of sample. Defenses edge Sleeper
by a little, and kickers beat it by more.** Kickers go from 53.6% start/sit
(this repo's baseline) to 56.8%, against Sleeper's 54.2%. Team defenses had
no projection at all (the board abstained); they now reach 61.5%, against
54.0% for points-per-game and 61.0% for Sleeper. The same caveat as the
skill positions applies: Sleeper's historical records may carry post-game
revisions, so the live shoot-out (`grade_week.py`, now graded for K and DEF
too) is the real scoreboard.

## Method

`PYTHONPATH=src python scripts/research/k_def_backtest.py --train 2023 2024 --test 2025 [--save]`.
One ridge regression each for K and DEF, trained on 2023–2024 and scored on
2025 weeks 4–18. Every system is scored on the same rows. Each feature for
week *w* comes from weeks before *w* only, built by the same functions the
dashboard runs:

- `gridiron.models.advanced.kicker_features_as_of`;
- `gridiron.models.advanced.defense_features_as_of`.

Start/sit = every pair of kickers (or of defenses) in the same week, and
whether the system ordered them the way the points did.

**Kicker inputs:**
- this repo's baseline kicker projection;
- the kicker's points per game, for the season and the last 3 games;
- his team's field-goal and PAT attempts per game;
- the implied team total;
- the spread (+ = favoured);
- dome or closed roof.

**Defense inputs:**
- the defense's league points per game, for the season and the last 3 games;
- sacks and takeaways per game;
- points allowed per game;
- the opponent's implied total;
- the opponent's sacks allowed and giveaways per game;
- home or away;
- the spread.

### Scoring a team defense

A team defense needed a scoring implementation first, and rule #2 still
holds: `gridiron.scoring.defense_points` is the one implementation. It maps
nflverse team-weekly columns onto the league's Sleeper weights
(`league_config.DEFENSE_SCORING`) and adds the points-allowed tier from the
opponent's final score.

`scripts/research/defense_scoring_reconcile.py` compared it with Sleeper's
own 2025 DEF points:

| check | result |
|---|---|
| team-weeks that match exactly | 67.8% |
| team-weeks within 1 point | 95.4% |
| MAE | 0.40 |
| bias | +0.19 |

Two mapping fixes were needed. Fumble-return touchdowns count as defensive
TDs, and blocked PATs count as blocked kicks. The remaining gaps are
special-teams fumble plays that nflverse's team table does not separate out
(`scoring.DEFENSE_UNSCORED`).

## Results (2025, out of sample)

| K (427 kicker-weeks, 5,526 pairs) | MAE | start/sit | close (<= 2 pts) |
|---|---|---|---|
| naive (ppg so far) | 4.02 | 53.5% | 52.6% |
| baseline (ours before) | 3.92 | 53.6% | 52.6% |
| sleeper | 3.86 | 54.2% | 52.4% |
| **adv** | **3.82** | **56.8%** | 56.0% |
| stack (adv + sleeper) | 3.82 | 56.9% | 56.1% |

adv beat the baseline in 11 of 15 weeks.

| DEF (448 team-weeks, 6,138 pairs) | MAE | start/sit | close (<= 2 pts) |
|---|---|---|---|
| naive (ppg so far) — the full baseline | 4.92 | 54.0% | 50.8% |
| sleeper | **4.51** | 61.0% | 56.4% |
| **adv** | 4.57 | **61.5%** | **57.3%** |
| stack (adv + sleeper) | 4.57 | 61.5% | 57.1% |

adv beat naive in 13 of 15 weeks.

Residual SD, which the board uses as each projection's spread: K 4.63,
DEF 5.80.

### What the models lean on

These are standardised weights: points per 1 SD of each input.

- **K:**
  - our baseline (+1.24);
  - the spread (+0.63, favourites kick more);
  - a dome (+0.46);
  - the implied total (+0.31).

  The kicker's own points per game gets a *negative* weight once the
  baseline is in. That is shrinkage: kicker scoring barely persists from
  week to week.
- **DEF:**
  - the opponent's implied total (−1.42, by far the largest);
  - the opponent's sacks allowed (+0.43);
  - the spread (+0.36);
  - takeaways (+0.26).

  Last season's DEF points per game adds almost nothing. That is the usual
  "stream defenses against bad offenses" rule, now measured.

## Reading it honestly

- Kicker scoring is close to noise: even the best system orders only 57% of
  kicker pairs correctly. The gain is real (+3.2 points, better in 11 of 15
  weeks) but a K decision is still a small edge.
- Defenses are more predictable, and the matchup is most of it. Sleeper is
  close (61.0%) and has the lower MAE, so this is a tie on history with a
  slight ordering edge, and the forward shoot-out decides.
- The stack adds nothing for K or DEF: Sleeper's number carries no
  information the features lack. The page uses adv; the stack stays a
  contender in the record.

## Where it lives

- **Weights:**
  - `src/gridiron/models/advanced_weights.json`, keys `K` and `DEF`;
  - `meta.resid_sd`;
  - `meta.k_def_evidence`.
- **Registered in `gridiron.models.validated_signals`:**
  - `advanced_v1_k`;
  - `advanced_v1_def`.
- **Inputs:**
  - `pull_week` saves nflverse team stats as `defense.parquet` beside the
    other model inputs, best effort and outside the manifest.
  - Kicker features come from the box scores already in the cache.
- **The board:**
  - A kicker with a feature row is refined like any skill player.
  - A team defense is projected by team, its stable id (rule #3).
  - With no row, a defense stays unknown and is never a zero.
  - Free-agent defenses enter the Free Agent Radar pool only when they can
    be projected (`waivers.available_ids(include_defense=...)`).
- **Grading:**
  - `grade_week.py` runs separate K and DEF shoot-outs.
  - Defense actuals are keyed `DEF:<team>`.

## Update 2026-10-01 — refit on 2019–2024

`k_def_backtest.py --train 2019 2020 2021 2022 2023 2024 --test 2025 --save`
(docs/research/FEATURE_EXPANSION.md: more seasons help). Same 2025 test rows:

| | K adv (2 train seasons) | K adv (6) | DEF adv (2) | DEF adv (6) |
|---|---|---|---|---|
| start/sit | 56.8% | **57.3%** | 61.5% | **62.0%** |
| MAE | 3.82 | **3.81** | 4.57 | 4.59 |
| weeks adv beats reference | 11/15 | 11/15 | 13/15 | **14/15** |

Sleeper on the same rows: K 54.2%, DEF 61.0%. Residual SDs now K 4.483,
DEF 5.96.
