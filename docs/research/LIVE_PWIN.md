# Live fantasy P(win) — calibration for nfl-gamezone (live_pwin_v1)

Date: 2026-11-15 · Script: `scripts/research/live_pwin_calibration.py`.
Constants: `data/reference/live_pwin_v1.json`. Fixtures:
`data/reference/live_pwin_fixtures.json`. Consumer: `Kejjeh/nfl-gamezone`, a
static browser app. Handoff: `docs/handoff/GAMEZONE_LIVE_PWIN.md`.

## The model, as the browser will run it

For every starter *i* on both sides of a matchup:

- `f_i` is the share of his game still to play: 1 before kickoff, 0 when
  final, on bye or with no game. Live, it comes from the game clock.
- `mu_i = proj_i * f_i` is the points still to come.
- `sd_i = f_i^(g/2) * (a_pos + b_pos * proj_i)` is their spread.

Then

    P(side A wins) = Phi( (now_A - now_B + sum mu_A - sum mu_B) / (k * sqrt(sum sd_i^2)) )

- `now` is Sleeper's live `players_points`, which Game Zone already reads as
  truth.
- `proj` is Sleeper's weekly projection (`api.sleeper.com/projections/nfl/
  <season>/<week>`, CORS-open), scored as a **dot product with the league's
  `scoring_settings`**: Game Zone's `season.scoreWith` rule, plus one derived
  key. The league scores a single `fgmiss`, and projections split misses by
  distance (`fgmiss_30_39`, ...), so `fgmiss` = the sum of the `fgmiss_*`
  keys. Team-defense projections carry points-allowed tiers as indicators
  (`pts_allow_14_20: 1`), so the plain dot product handles them.

## Shipped constants (fitted on 2023-2024 only)

| pos | a | b | at proj 10, f = 1 |
|---|---|---|---|
| QB | 4.02 | 0.185 | sd 5.9 |
| RB | ~0 | 0.587 | sd 5.9 |
| WR | ~0 | 0.607 | sd 6.1 |
| TE | 4.49 | 0.149 | sd 6.0 |
| K | 4.93 | ~0 | sd 4.9 |
| DEF | 3.47 | 0.394 | sd 7.4 |

- **g = 0.78**: the spread shrinks as `f^0.39`, faster early in a game than
  `sqrt(f)` would.
- **k = 1.0**. The 2023-24 argmin was 0.85, but across 0.85-1.10 the
  2023-24 log loss moves by under 0.003 (noise). 1.0 is best on the held-out
  seasons, and it is the board's TESTED pre-game convention, so Game Zone
  and the board agree at kickoff.

## Data

- **This league's real regular-season matchups, 2023-2026:** 2026 through
  its last finished week. 71,482 starter x checkpoint rows.
- **Checkpoints:** one minute before every kickoff slot of the week, and 95
  minutes into each slot. Thursday, international mornings, Sunday 1/4 PM,
  Sunday night and Monday night are all covered.
- **Points so far** at each checkpoint come from nflverse play-by-play under
  the league's scoring:
  - passing, rushing, receiving, 2-pt conversions and fumbles lost;
  - kickers by field-goal distance band, plus extra points;
  - team defenses: sacks, INTs, fumble recoveries, forced fumbles, safeties,
    blocks, defensive and special-teams TDs, plus the points-allowed tier
    *at that moment*.
- **Wall clock to game clock:** 190 real minutes per game. Game Zone will
  use the real ESPN clock, which is more accurate than this approximation.
- **Rebuild audit (final rebuild vs Sleeper's own `players_points`):**
  5,121 of 5,243 starter-games exact (97.7%), 99 within 2 points, 23 further
  off. The misses are mostly return and special-teams scoring the rebuild
  does not credit to individual players. A checkpoint at or after a game's
  end uses Sleeper's number.

## Results on held-out seasons 2025-2026 (1,884 matchup x checkpoint states)

Log loss (lower is better; a coin flip is 0.693) / Brier:

| stage (share of projected points still to come) | live_pwin_v1 | pre-game P frozen at kickoff | points-share bar Game Zone shows today |
|---|---|---|---|
| all | **0.424 / 0.143** | 0.680 / 0.244 | 1.153 / 0.258 |
| late (<25%) | **0.215 / 0.063** | 0.675 / 0.241 | 0.594 / 0.202 |
| mid (25-60%) | **0.455 / 0.151** | 0.690 / 0.248 | 0.626 / 0.218 |
| early (60-90%) | **0.573 / 0.197** | 0.682 / 0.245 | 1.224 / 0.284 |
| pre (>90%) | **0.663 / 0.237** | 0.681 / 0.245 | 2.547 / 0.375 |

**Calibration, held out, live, excluding final:**

| predicted (bin) | n | mean predicted | actually won |
|---|---|---|---|
| 0.0-0.1 | 288 | 0.02 | 0.03 |
| 0.1-0.3 | 239 | 0.21 | 0.23 |
| 0.3-0.5 | 409 | 0.40 | 0.46 |
| 0.5-0.7 | 344 | 0.59 | 0.61 |
| 0.7-0.9 | 247 | 0.80 | 0.85 |
| 0.9-1.0 | 284 | 0.97 | 0.99 |

The 0.3-0.5 bin is 2.3 standard errors off; the others are within noise.

## What did not help (checked, not shipped)

- **A separate spread per position:** one shared spread scores 0.4252 against
  0.4242. Per-position is kept because it costs nothing, not because it
  measurably helps.
- **A wider pre-game spread** (k 1.3-2.5 before kickoff): 2023-24 gets worse
  at every setting, and 2025-26 improves by at most 0.005. The weakness
  before kickoff is in the projections themselves: Sleeper's pre-game
  projection is only slightly better than a coin flip in this league
  (0.68). The number becomes sharp as games are played. Game Zone should say
  so in words, not with extra decimals.

## Known limits / v2 candidates (each must beat v1 on held-out seasons, rule #5)

- **No in-game injury tags in the history.** A starter who leaves hurt is
  modelled as still projected here. Live, Game Zone will zero him from
  Sleeper's tag, so live accuracy should be at least this good.
- **Projection bias:** Sleeper projects WRs about 6% high in this league on
  the held-out seasons (11.24 projected vs 10.55 scored), RBs about 4% high,
  and K, DEF and QB within 1%. A per-position scale is a v2 candidate.
- Not modelled yet: the QB-receiver correlation (+0.34 measured,
  2026-11-09), game script from ESPN game win probability, and simulation
  for right-skewed outcomes.
- Not modelled: a starter inactive before kickoff whose projection is still
  > 0 (live, Game Zone can zero him from the tag), and overtime (treat f as
  ~0 once regulation ends).
