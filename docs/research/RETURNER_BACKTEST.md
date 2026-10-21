# A number for a player with no game this season: the returner backtest (2026-10-21)

**Short answer: last season's points per game, discounted, clears the
pre-registered bar, and it is NOT shipped.** On 2023–2025 it beats "knows
nothing about him" on error and ranks players. But it projects players who
scored 10+ points per game last season about half of what they score: 2.5
points low at 10–15 (n = 41) and 5.0 points low at 15+ (n = 14). Those are
the players a returner number exists for. Sleeper's own number had the lower
error on the same rows (2.05 vs 2.45). The page names these players instead, with Sleeper's number
(`gridiron.notable`); our own number waits for a design tested on data it
has not seen.

## Why

`gridiron.projection.project` reads THIS season's box scores only. A player
who has not played yet this season (suspension, the exempt list, IR, a
holdout) abstains with "no admissible box scores". He is never ranked on
the Free Agent Radar, and P(win) abstains when he starts. In 2026 week 7 a
running back with no 2026 game was dropped by a league team on Thursday
night (03:00 UTC Friday, 16 Oct). He sat in the free-agent pool for five
days, 33 builds, as one of the radar's "missing evidence" count.
Another manager claimed him at the Wednesday waiver run (07:17 UTC, 21 Oct).
Last season he was RB8 in this league's scoring (14.6 points per game over
15 games, by gsis id). Sleeper projected him 11.7 from Tuesday.

## Method (pre-registered in the script's docstring before any result)

Command: `PYTHONPATH=src python scripts/research/returner_backtest.py --boot 2000`.

- **Population.** Every 2019–2025 player-season whose first regular-season
  box score is in week 2 or later, at QB/RB/WR/TE, with at least 4 games
  the previous regular season. Only that first game is scored: from the
  second game on, this season's evidence exists and the baseline takes
  over. 829 first games (457 in the fit seasons 2019–2022, 372 in the test
  seasons 2023–2025; WR 299, RB 224, TE 186, QB 120). These are players
  who played. Whether a returner plays at all is the availability layer's
  job.
- **Candidates.** Each is scaled by a per-position discount k (grid
  0.30–1.30), fit by minimum MAE on the fit seasons:
  - `ppg_all`: last season's league points per game.
  - `ppg_last8`: the same over his last 8 games.
  - `base_prior`: the page's own `project()` on last season's evidence,
    with this week's implied team total.

  The lowest fit MAE is tested.
- **Null.** The per-position mean actual of returners in the fit seasons.
  The page has no number today, and a constant is the honest version of
  that.
- **Bar.** On the test seasons, the bootstrap CI of the MAE gain over the
  null excludes 0. The pooled within-position Spearman CI excludes 0.
  |bias| ≤ 1.5 points.
- **Context, not a bar.** Sleeper's published projection for the same
  player-weeks, scored with the league's rules, joined by gsis id through
  the crosswalk.

## Result

Fit (2019–2022):

| candidate | k QB | k RB | k TE | k WR | fit MAE |
|---|---|---|---|---|---|
| `ppg_all` (chosen) | 0.30 | 0.30 | 0.34 | 0.37 | 2.671 |
| `ppg_last8` | 0.30 | 0.30 | 0.38 | 0.43 | 2.682 |
| `base_prior` | 0.30 | 0.30 | 0.34 | 0.36 | 2.694 |

Test (2023–2025). Bias is the mean of actual minus prediction:

| system | n | MAE | bias | Spearman |
|---|---|---|---|---|
| null | 372 | 3.317 | −0.51 | — |
| `ppg_all` × k | 372 | 2.448 | +1.37 | 0.478 |
| `ppg_last8` × k | 372 | 2.453 | +1.27 | 0.471 |
| `base_prior` × k | 372 | 2.530 | +1.17 | 0.427 |
| Sleeper | 371 | 2.048 | +1.14 | 0.520 |

MAE gain over the null: CI (0.67, 1.06). Spearman CI (0.39, 0.55). Bias
+1.37. **The bar cleared.**

## Why it is not shipped anyway

The bar was pooled, and the population is mostly backups whose first game
is a cameo: the median actual in the fit seasons is 0.9 for RBs and 1.4
for WRs. A minimum-MAE discount fits that median. It sits at the grid's
floor (0.30) for QB and RB, so the fit wanted lower still. This table was
added after the result was seen. It is descriptive and was never part of
the bar:

| last season's ppg | n | actual mean | ours mean | ours bias | ours MAE | Sleeper mean | Sleeper bias | Sleeper MAE |
|---|---|---|---|---|---|---|---|---|
| 0–5 | 250 | 1.65 | 0.66 | +0.99 | 1.58 | 0.62 | +1.03 | 1.44 |
| 5–10 | 62 | 3.69 | 2.31 | +1.38 | 2.85 | 2.95 | +0.74 | 3.04 |
| 10–15 | 41 | 6.21 | 3.66 | +2.55 | 5.47 | 4.43 | +1.78 | 3.40 |
| 15+ | 14 | 10.38 | 5.40 | **+4.99** | 7.86 | 7.15 | +3.23 | 4.95 |

("Ours" is `ppg_all` × k.) A returner number matters for a player like the
week-7 one: a starter last season, 14.6 points per game. Players like that
sit in the bottom two rows, where it reads about half what they score. On
the radar it would have said "ours 4.4, below your weakest eligible
starter" (k = 0.30 for RBs), which is the same miss reached a different way.

The top band is thin and mostly quarterbacks: 10 QBs, 2 WRs, 1 TE, 1 RB.
Sleeper projected 7 of those QBs at 0.0. They were backups this season, and
4 of the 7 scored under 2 points. Last season's output cannot know this
season's role; Sleeper's number can. Sleeper was less biased than ours in
every band above the lowest and had the lower MAE in the top two. Ours had
the lower MAE at 5–10 points per game.

So the page serves no returner number of its own. It names these players
instead (`gridiron.notable`):

- **Notable free agents.** Last season's starters and this league's recent
  drops, with status, waiver timing and Sleeper's number. A first-screen
  alert appears when one is new or his waiver window is open.
- **Can't project — Sleeper's number.** Every pool player we abstain on
  whom Sleeper projects, labelled as Sleeper's.

Replayed on the 41 archived builds from 15–21 Oct (real pool ids, real
league transactions, real 2025 box scores, first-seen carried build to
build), the week-7 player was on the first screen in all 33 builds from the
first one after his drop (06:18 UTC, 16 Oct) to the last one before the
waiver run (04:32 UTC, 21 Oct). He was first listed as a 2025 starter (RB8)
and as dropped by roster #5. Two to five rows were on the first screen at
any time, and none after the waiver run.

## What a second attempt must do (declared now, before any such run)

A returner number is still worth having for the owner's own players coming
off IR (P(win) abstains when one starts). A second design, `returner_v2`:

- **Population.** Restricted to last season's starters (`notable.STARTER_RANK`
  by league ppg), since the number is meant for them.
- **Fit.** Least squares, on the mean scale the page uses everywhere else.
- **Bar.** The same three conditions, plus |bias| ≤ 1.5 within each prior-ppg
  band with n ≥ 10.
- **Test data.** The 2023–2025 seasons have now been looked at by band, so
  they can no longer be a clean test for v2. Its test is the 2026 season's
  returners, scored at season end. Until then the page abstains, as it does
  today.

## Files

- `scripts/research/returner_backtest.py`: the pre-registration (docstring),
  the run, and the post-hoc table (`post_hoc_by_prior_ppg` in the JSON).
- `data/research/cache/tables/returner_backtest.json`: gitignored output.
- `src/gridiron/notable.py`: what shipped instead.
