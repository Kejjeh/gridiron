# Quantitative Foundations

Status: **partially verified** — 2026-09-04.

| section | research | adversarial verification |
|---|---|---|
| §1 Vegas → projections | done | **done** (`verify_vegas.py`, 279 checks) |
| §2 win probability / variance | done | **done** (`verify_winprob.py`, N = 1e6) |
| §3 season leverage | derived in-repo | covered by `tests/test_season.py` |
| §4 data sources | done | **done** (live re-probe of every endpoint) |
| §5 usage stabilization | done | script ran: **381/382** — effectively verified |
| §6 replacement level / VOR | done | script ran: **177 pass / 12 fail** — flex margin and man-games shifts rejected |
| §7 FAAB | done | script ran: **66 pass / 9 fail** — winner's-curse direction inconclusive |

Sections 1, 2 and 4 had **14 claims corrected** by their verifiers (§9). The
§5–7 verifier agents were killed by a usage limit after writing and running
their scripts but before reconciling the results into the prose, so §5–7
carry inline ⚠ flags where their own checker disagrees with the text.

How this was produced: six research agents (web-sourced constants +
first-principles derivations, 2023–25 NFL seasons preferred) each fed an
adversarial verifier that had to reproduce every number in a deterministic
script. The scripts are committed under `scripts/research/verify_*.py`
(numpy only, seeded, no absolute paths; run from the repo root with
`.venv/Scripts/python.exe`). Where a verifier corrected the researcher, the
CORRECTED value is the one stated here and the correction is logged in §9.

League placeholders (rule #1): 12-team H2H, full PPR (1 / 0.1 / 6), lineup
1QB/2RB/2WR/1TE/1FLEX/1DST/1K, 7 bench, $100 FAAB, 14-week regular season.
Every point-denominated number below scales with scoring and lineup size;
CVs, correlations and the algebra do not.

---

## 1. Vegas lines → weekly projections  (verified: `verify_vegas.py`, 279 checks)

### 1.1 The identity (exact)

For game total T and expected margin M for team A: A + B = T, A − B = M, so
A = (T + M)/2. Sportsbooks quote the spread S = −M (favorite negative), so

    implied_team_total = (T − S)/2          ← `gridiron.vegas.implied_team_total`

**nflverse uses the opposite sign**: `spread_line > 0` means the HOME team
is favored (DATASETS.md; verified corr(spread_line, home margin) = +0.487 on
2024, sign agrees with moneyline ordering in 99.3% of games). So
`I_home = (total_line + spread_line)/2` — use
`gridiron.vegas.implied_totals_from_nflverse` at the ingest boundary. ESPN's
scoreboard `spread` field is quoted from the home side (negative = home
favored) — the negative of nflverse. All 16 2026 week-1 nflverse lines equal
ESPN's DraftKings CLOSE exactly, so nflverse lines are the DK close.

### 1.2 League scoring anchors, per team-game (nflverse REG, StatMuse cross-checked)

| season | pts | off TD | pass TD | rush TD | FGM | def/ST TD |
|---|---|---|---|---|---|---|
| 2023 | 21.77 | 2.252 | 1.386 | 0.864 | 1.675 | 0.142 |
| 2024 | 22.91 | 2.432 | 1.487 | 0.939 | 1.722 | 0.118 |
| 2025 | 23.01 | 2.430 | 1.491 | 0.938 | 1.711 | 0.134 |
| pooled | 22.56 | 2.371 | | | 1.703 | 0.131 |

Points share: offensive TDs incl. conversions 73.6%, FGs 22.6%, def/ST TDs
3.5%, safeties 0.2%. Points per offensive TD incl. conversions = 7.00
(all-TD basis 6.95 — the physically cleaner number; difference < 1%).
Scoring identity `pts = 6·TD_all + XP + 2·2pt + 3·FGM + 2·safety` reconciles
to < 0.01 pt/game every season.

### 1.3 Implied total → scoring (pooled OLS, n = 1,632 team-games; **all pass leave-one-season-out vs a mean-only baseline**)

| target | fit | r | resid SD | LOSO skill |
|---|---|---|---|---|
| points | −1.93 + 1.112·I | 0.415 | 9.04 | 0.175 |
| offensive TD | −0.945 + 0.1505·I | 0.396 | 1.30 | 0.159 |
| pass TD | −0.511 + 0.0892·I | 0.290 | 1.09 | 0.086 |
| rush TD | −0.430 + 0.0610·I | 0.238 | 0.92 | 0.058 |
| FGM | 1.40 + 0.0139·I | 0.041 | 1.25 | 0.002 |

Identity check: 1.112 ≈ 7.0·0.1505 + 3·0.0139 = 1.096. **Field goals do
not scale with the implied total** (inverted-U across buckets, flat OLS).
Kicker points move only through XPs: ≈ 6.7 at I = 19, 7.8 at I = 25.
Actual totals run +1.07 above closing totals 2023–25.

Implied-total distribution (closing, 2023–25): p5/25/50/75/95 =
16 / 19.5 / 22 / 24.8 / 28, SD 3.7, mean 22.03; P(I ≥ 24) = 33%, P(I ≥ 27) = 10%.

### 1.4 THE headline: Vegas moves efficiency, not volume

Pooled team-game regressions on own implied total I:

| quantity | fit on I | r | LOSO skill |
|---|---|---|---|
| targets / game | 29.83 + 0.067·I | 0.03 | **−0.001** |
| pass attempts | 32.14 + 0.031·I | 0.015 | **−0.001** |
| rush attempts (excl. kneels) | 19.94 + 0.280·I | 0.14 | 0.015 |
| PPR per target | 0.791 + 0.0420·I | 0.36 | **0.129** |
| pts per carry | 0.210 + 0.0196·I | 0.27 | 0.075 |
| team receiving PPR | 24.06 + 1.319·I | 0.31 | 0.088 |
| team rushing pts | 2.13 + 0.684·I | 0.28 | 0.077 |

Delta-method decomposition of the receiving slope: 1.716·0.067 (volume) +
31.3·0.0420 (efficiency) = 0.115 + 1.316 → **92% of the Vegas signal on
receivers flows through points-per-target; 74% for rushing**. Volume
regressions on the line have essentially zero out-of-sample skill.

Consequence for build step 3: the bootstrap doc's "opportunity share × team
implied total" baseline has the wrong shape. The right baseline is

    volume      = usage prior (team pace × player share, from stabilization §5)
    efficiency  = positional prior × line multiplier
    line mult   = (0.791 + 0.0420·I) / (0.791 + 0.0420·22.0)  → 1.00 at I=22, 1.07 at 25, 0.93 at 19
                                                           ← `gridiron.vegas.receiving_efficiency_multiplier`

Within-team (team-season demeaned) slopes retain 67–84% of the pooled effect
(within/between 51–74%), but an expanding-window OOS test showed the pooled
line-only multiplier beats "team prior + within adjustment" mid-season for
every efficiency target (the closing line prices team quality better than a
4–13 game sample). A 50/50 blend is best for team receiving PPR and rush pts.

### 1.5 Game script is a second-half phenomenon

Dropback rate by score differential, posteam perspective (2023–25 pooled):

| | trail 17+ | trail 9–16 | trail 1–8 | tied | lead 1–8 | lead 9–16 | lead 17+ |
|---|---|---|---|---|---|---|---|
| 1st half | 0.696 | 0.649 | 0.613 | 0.579 | 0.610 | 0.625 | 0.593 |
| 2nd half | 0.732 | 0.733 | 0.643 | 0.592 | 0.495 | 0.443 | 0.360 |

Second-half slope ≈ **−1.2 pp of dropback rate per point of lead** over
−8..+16 (corrected from −0.9). PFF/Clay Q4: 84% pass trailing by 2+ scores,
30% leading. Pre-game, the spread barely moves pass attempts (−0.07/pt) and
moves rush attempts +0.25/pt (kneel-excluded; +0.29 if kneels are counted —
never credit kneels to a player). Big dogs pass at a higher rate but run
fewer plays (57.9 vs 63.4), netting ~equal attempts.

League-mean volume functions (S sportsbook convention, T total; these are
league means at a line, NOT per-game predictors — OOS skill ≈ 0):

    pass_att(S,T)   = 24.70 + 0.068·S + 0.184·T      (33.1 at −3/47)
    targets         = 0.954 · pass_att                 (31.6)
    rush_att(S,T)   = 29.68 − 0.250·S − 0.081·T       (26.6, kneels excluded)

Volume anchors: plays/game 63.2 / 62.2 / 61.3 (2023/24/25; pass att + sacks +
rush att; team-game SD 8.4); pass att 33.7 / 32.7 / 32.1; dropback rate
0.604 / 0.597 / 0.594; neutral early-down dropback rate 0.544 / 0.534 / 0.532
(not a whole-game rate — do not multiply plays by it). Pin ONE play
definition before any volume constant ships (2-pt tries and kneels in/out).

### 1.6 Positional efficiency per opportunity (nflverse 2023–25, pooled)

| | targets | catch | yds/tgt | TD/tgt | aDOT | PPR/tgt (1/0.1/6) | half-PPR |
|---|---|---|---|---|---|---|---|
| WR | 30,251 | 0.630 | 7.93 | 0.0493 | 10.8 | 1.72 (per-tgt SD 1.09) | 1.40 |
| TE | 11,512 | 0.720 | 7.30 | 0.0505 | 6.3 | 1.75 | 1.39 |
| RB | 8,991 | 0.784 | 5.79 | 0.0304 | −0.15 | 1.55 | 1.15 |

RB rushing: 4.29 yds/carry, 0.0309 TD/carry → 0.614 pts/carry (35,363
carries). QB: 7.06 Y/A, 0.0441 TD/A, 0.0223 INT/A, 64.7% comp → 0.414 pts/att
(4 / −2 / 0.04). Targets per official attempt 0.954.

PPR/target is nearly FLAT across WR aDOT bands (1.67 at aDOT 2–6 → 1.82 at
15+; catch rate falls ~1.5 pp per yard of aDOT while Y/T and TD/T rise).
aDOT changes variance (reception floor vs TD ceiling), not the mean.
`gridiron.vegas.points_per_target` computes these from the components using
the ONE scoring implementation's weights (rule #2), so they follow the
league's real scoring once rule #1 flips.

### 1.7 Worked example (favorite −3 / total 47, WR with 24% target share)

I = 25.0 → pass TD 1.72, rush TD 1.10, off TD 2.82, FGM 1.75, pts 25.9;
pass att 33.1, targets 31.6, rushes 26.6; WR targets 7.59 → 4.78 rec,
60.2 yds, 0.374 TD → **13.0 PPR flat, 14.0 with the pooled line multiplier;
empirical neighbourhood (I ∈ [24,26], favored 1–5, n = 168) 13.4**.

**Corrected weekly SD**: a ~13.5-PPR WR has weekly SD ≈ 7.5 (CV ≈ 0.55;
median 7.44 across 70 player-seasons; SD ≈ 2.13 + 0.42·mean over 366 WR
seasons), not the 4.5–5 the bottom-up terms suggest — real share and
per-target output are over-dispersed. The Vegas bump (+0.7 to +1.0 PPR) is
~0.1 of one week's SD. This matters for ΔP(win) (rule #7) far more than the
bump itself.

### 1.8 Signal strength

Game level (closing, n = 816): corr(result, spread) 0.477, SD(result − spread)
12.64 (published long-run 13.4–13.9: Stern, PFR, nfelo — use 12.6–13.9);
corr(total, line) 0.305, SD 12.9. Team-week PPR (QB+RB+WR+TE) vs I: r = 0.41,
2.62 PPR per implied point (LOSO r 0.41); by position QB 0.70/pt (r .33),
RB 0.71 (r .27), WR 0.97 (r .26), TE 0.24 (r .11). Spread/total split:
RB output leans on the spread (0.39·M + 0.27·T), WR/QB on the total
(WR 0.37·M + 0.71·T), TE nearly line-insensitive.

Opener vs closer (sourced only; nflverse has closers only): nfelo 55.9% vs
55.1% ATS against its model; arXiv 1211.4000 no significant MSE difference;
Moskowitz 2021 open-to-close moves reversed on average. Use the closer for
its news content; expect little accuracy gain from the move.

---

## 2. Matchup win probability, score distributions, variance strategy  (verified: `verify_winprob.py`, N = 1e6 MC)

### 2.1 Positional weekly distributions

CV = SD/mean for startable tiers (three independent sources agree; full-PPR
sits at the low end):

| pos | CV | gamma shape k = 1/CV² | skew = 2·CV |
|---|---|---|---|
| QB (top 12) | 0.36–0.45 | 5.7 | 0.84 |
| RB (top 24) | 0.47–0.60 | 3.3–3.7 | 1.0–1.1 |
| WR (top 36) | 0.48–0.67 | 3.0–3.3 | 1.1–1.2 |
| TE (top 12) | 0.57–0.75 | 2.4 | 1.3 |
| K (top 12) | 0.55 (9.3 PPG, SD 5.1) | 3.3 | 1.1 |
| DST (top 12) | 0.89 (7.3 PPG, SD 6.4) | shifted gamma, shift −5, k ≈ 3.5 | 1.1 |

Recommended marginals: Gamma(k, θ = mean·CV²). Wilson-Hilferty medians:
WR ≈ 0.90·mean, QB ≈ 0.94·mean. DST needs the shift because negative weeks
occur (unshifted k would be 1.3, near-exponential). Lognormal skews
(1.3 / 1.8 / 2.2) are heavier than anything sourced supports. The gamma
family is the recommendation, not a measurement — no public skew statistics
exist; check against nflverse weekly data once ingested (expect QB ~0.8,
WR ~1.1, TE ~1.3).

### 2.2 Correlation recipe (18 starters = own 9 + opponent 9)

Sources disagree by conditioning: season-long all-weeks data (RotoWire
2022–25: QB-WR1 0.31, QB-TE 0.27, QB-RB 0.07, WR-WR −0.02) vs
DFS-slate-conditioned (0.47–0.53). Engine values (season-long end, est. =
estimated from ordinal sources):

| pair | ρ | | pair | ρ |
|---|---|---|---|---|
| QB–own WR1 | 0.35 | | QB–opp DST | −0.30 |
| QB–own WR2 | 0.25 | | QB–opp QB | 0.30 (est.) |
| QB–own TE1 | 0.30 | | QB–opp WR1 | 0.20 (est.) |
| QB–own RB1 | 0.07 | | WR1–opp WR1 | 0.15 (est.) |
| WR1–WR2 same team | 0.00 | | pass-catcher–opp DST | −0.15 (est.) |
| RB–WR same team | −0.07 | | K–own DST | 0.15 (est.) |
| RB1–own DST | 0.10 | | QB–own K | 0.10 (est.) |

All else 0. Verified PSD in every configuration tested (min eigenvalue
0.31–1.0; Higham never triggers). **Copula attenuation**: fed as
Gaussian-copula parameters over gamma marginals, realised Pearson
correlations come out 3–6% smaller (0.35 → 0.339). Decide whether the recipe
ρ is the copula or the linear correlation. Untestable until nflverse weekly
data are ingested with roles labelled by prior-week usage (rule #4).

### 2.3 Lineup and margin

Slot model (mean / CV): QB 20/.42, RB1 16.5/.52, RB2 13/.55, WR1 17/.55,
WR2 14/.58, TE 11/.65, FLEX 12.5/.58, K 9/.55, DST 7.5/.90 → lineup mean
120.5, **SD 22.9 independent** (CV 0.19; Stathole 2024 sourced anchor
122 / 22.6). QB+WR1 stack → 24.0; +TE → 24.8. K+DST = 13% of variance and
10% of skew (NOT "most of the fat tail" — refuted; WR1/RB1/WR2/QB dominate
the third cumulant).

Head-to-head margin: s = √(σ_A² + σ_B² − 2·Cov(A,B)) = **32.3 (29–34)**.
Cross-lineup terms move it by < 1 pt; note a NEGATIVE cross correlation
(my QB vs their DST) RAISES s (32.85), positive ones lower it (corrected
sign error in the research).

### 2.4 Leverage and the variance sign flip  (`gridiron.winprob`)

    P(win) = Φ(d/s),   d = μ_A − μ_B
    dP/dμ_A   = φ(d/s)/s                                    → 1.23 pp per projected point at d=0 (1.17–1.38 for s 34–29);
                                                              1.02 pp/pt at |d|=20; 0.75 at |d|=32
    dP/dσ_A   = −φ(d/s) · d · (σ_A − ρσ_B) / s³             → sign = −sign(d): variance helps underdogs, hurts favorites

Both derivatives verified against finite differences to relative error
< 1e-8; the package functions match the verifier's closed forms to 0.0.

**Variance-flip table** — ΔP(win) in pp when the underdog scales every slot
SD by (1+f) at fixed mean, s₀ = 32.3. Analytic (Φ) | Monte Carlo (gamma
marginals, common random numbers, N = 1e6):

| deficit | f = 0.1 | f = 0.2 | f = 0.3 |
|---|---|---|---|
| 0 | 0.00 \| −0.38 | 0.00 \| −0.71 | 0.00 \| −1.13 |
| 5 | +0.30 \| −0.08 | +0.58 \| −0.11 | +0.84 \| −0.27 |
| 10 | +0.57 \| +0.21 | +1.12 \| +0.47 | +1.63 \| +0.59 |
| 20 | +1.00 \| +0.78 | +1.96 \| +1.52 | +2.88 \| +2.13 |
| 30 | +1.20 \| +1.11 | +2.37 \| +2.19 | +3.51 \| +3.11 |

Why MC is lower: scaling a right-skewed marginal's SD at fixed mean LOWERS
the lineup median (−0.32 / −0.64 / −1.05 pts for f = .1/.2/.3), which
costs ≈ leverage × |Δmedian|. **Practical breakeven for chasing variance is a
deficit of ≈ 6 pts (≈ 0.19·s), not 0**; at a 5-pt deficit every SD
multiplier still lowers P(win) by 0.1–0.3 pp. Φ overstates the underdog's
gain by 0.4–1.1 pp at deficits ≤ 10 and gets the SIGN wrong at deficit 5.

### 2.5 When the closed form is enough

Φ vs MC error for P(win) itself: ≤ 0.25 pp for similar-shape lineups over
|d| ≤ 40 (margin skew ≈ 0 because both sides are skewed); ≤ 0.5 pp stacked
vs unstacked with the correlated SD (using the independent SD adds up to
1.0 pp at d = −30); up to 0.5 pp when shapes differ materially.

Engine rule: Φ for point estimates and mean-leverage; full MC when the
decision is a variance/skew trade at |d| < 15 (Φ error is the same order as
the decision), when stack structures differ, for DST/K tail questions, and
for season-level playoff probability (rank-of-many-sums; by argument, not
yet measured).

MC design: Gaussian copula + gamma marginals (Iman-Conover rank mapping
when scipy is absent gives exact marginals); N = 62,500 for ±0.2 pp (1 SE),
240,100 for 95%; persist one uniform tensor per week so alternatives share
draws. **Common random numbers cut the SE of a ΔP comparison by ~4× (not
10×)**: the floor is the ~3% of simulations whose win indicator flips, so
expect ≈ 0.04 pp at N = 2e5.

---

## 3. Season-level leverage  (derived in-repo: `gridiron.season`, tests in `tests/test_season.py`)

Future wins K over W remaining weeks with heterogeneous p_w follow a
Poisson-binomial distribution (exact O(W²) DP). If the playoffs require at
least k more wins INCLUDING this week:

    P(playoffs | win) − P(playoffs | lose) = P(K_rest ≥ k−1) − P(K_rest ≥ k) = P(K_rest = k−1)

This week's game is worth exactly the probability that the rest of the
schedule lands on the knife edge: ≈ 0 when clinched or eliminated, maximal
in a tight race (4 coin-flips left, need 3 → leverage 6/16). Playoff-equity
value of one projected point this week = win_leverage × φ(d/s)/s.
Stated simplification: a fixed k stands in for the stochastic cutoff set by
the other teams; the step-6 engine Monte-Carlos the league to get a
distribution over k and mixes these primitives across it.

---

## 4. Data sources, September 2026  (verified live; 17/21 findings and 25/29 constants confirmed outright, rest corrected)

**The bootstrap doc §3 is wrong on `nfl_data_py`**: deprecated in favour of
`nflreadpy`, repo archived 2025-09-25, last release 0.3.3 (2024-09-20), and
it pins numpy < 2 / pandas < 2 so it cannot even co-install here.

| need | source | status / notes |
|---|---|---|
| weekly player stats (150 cols incl. target_share, wopr, fantasy_points_ppr) | `nflreadpy.load_player_stats(seasons, summary_level="week")` | polars; keyed `player_id` (gsis) |
| snap counts | `load_snap_counts` | **keyed `pfr_player_id`, no gsis** — join via `ff_playerids.pfr_id` |
| depth charts | `load_depth_charts` | **timestamped snapshots (`dt`), no week column** |
| injuries | `load_injuries` | gsis_id, week, report_status, practice_status |
| id crosswalk | `load_ff_playerids()` | 12,492 × 35 (gsis, sleeper, espn, pfr, fantasypros, yahoo, sportradar…) |
| betting lines | `load_schedules()` | spread_line (home-positive), total_line, moneylines, odds, roof/surface — DK close via ESPN; 2026 lookahead lines populated only ~7 weeks out |
| intraweek / lookahead lines | ESPN `site.web.api.espn.com/.../scoreboard?week=N&seasontype=2&dates=2026` | undocumented; DraftKings open+close; `site.api.espn.com` 403s browser UAs only — plain script UA works on both; do NOT spoof a browser UA |
| lines, documented fallback | The Odds API | 500 credits/mo free; spreads+totals = 2 credits/pull → ~108/season |
| ECR (FantasyPros) | `load_ff_rankings(type="week")` via DynastyProcess `db_fpecr` (1.83M rows, 2019-12-27 → today, weekly to 2025-12-26) | free; no ADP |
| FantasyPros API | official, `x-api-key` | free tier non-production "sample data"; Premium $8.99/mo; limits unpublished |
| projection archives (for evaluation) | ESPN `kona_player_info` (weekly proj beside actuals, 2024 complete, 2023 wk-1 gap); Sleeper `/projections/nfl/{season}/{week}` (Rotowire, ~1,363 rows/wk, 2023→) | both undocumented, may vanish |
| ADP | FFC API (free, 7,681 drafts as of 09-04) — **FFC-internal ids only, would need name-matching (rule #3 hazard)**; Sleeper projections carry `adp_dd_ppr` keyed by sleeper_id | |
| league (ESPN) | `espn-api` 0.46.0 (2026-03-23), espn_s2 + SWID cookies | issue #547 "403 for all leagues" is a stale-version symptom; pins `urllib3<=2.2.3` |
| league (Sleeper) | REST, no auth, < 1000 calls/min | **Sleeper's own gsis_id is populated for only 31.6% of active skill players (19.6% of those with a team)** — map sleeper_id → gsis via `rosters_weekly` (72.9% coverage) / `ff_playerids` |
| weather forecasts | Open-Meteo (no key, 16-day, 10k/day) or NWS (no key, User-Agent required, 7-day) | nflverse temp/wind are post-game observations only (0/272 filled for 2026) |
| stadium lat/lon | greerreNFL/stadiums `stadiums.csv` (67 rows; `stadium_id` matches nflverse 41/41 since 2020) | join `team_stadiums` on `team_fastr`, not `team` (OAK/LAR vs LV/LA) |

Pip list for build step 2: `nflreadpy>=0.1.5 polars>=1.0 pyarrow>=15
pandas>=2.0,<4 requests>=2.28` (+ `espn-api>=0.46` only if the league is on
ESPN). `pandas>=2.0` resolves to 3.0.x today (copy-on-write, new string
dtype) — test before trusting. `rosters_weekly.gsis_id` has 11 empty-string
rows: treat `""` as null when joining.

---

> **⚠ Sections 5–7 are PARTIALLY verified.** Their verifier agents were killed
> by a usage limit *after* writing and running their scripts but *before*
> returning a structured verdict. The scripts survive and were re-run on
> 2026-09-04; their pass/fail counts are below, but **no agent has reconciled
> the failures back into the prose**, so the text may still assert things its
> own checker rejected. Failures known at the time of writing are flagged
> inline as ⚠. Numbers marked *(sourced)* or *(estimate)* were not machine-
> checkable at all. **Nothing in §5–7 ships to a user-facing output until the
> reconciliation runs** (relaunch the workflow with
> `args: ["stabilization","faab","vor"]`).
>
> | section | script | result |
> |---|---|---|
> | §5 | `verify_stabilization.py` | **381 / 382 pass** — the one failure is a rounding nit (25/41 = 0.6098 vs a claimed 0.612). Treat §5 as effectively verified. |
> | §6 | `verify_vor.py` | **177 pass, 12 fail** — the flex-margin and man-games numbers are wrong; see ⚠ below. |
> | §7 | `verify_faab.py` | **66 pass, 9 fail** — all 9 concern the common-value shading direction, and the solver *also* failed its own convergence check, so that test is inconclusive rather than damning. |

## 5. Usage stabilization + empirical-Bayes shrinkage  — UNVERIFIED

### 5.1 Machinery (derived; implemented in `gridiron.shrinkage`)

x | p ~ Binomial(n, p), p ~ Beta(a, b) → posterior mean
(x + a)/(n + a + b) = w·(x/n) + (1−w)·m, with w = n/(n + n₀), n₀ = a + b.
Data and prior weigh equally exactly at n = n₀. Posterior variance
m′(1−m′)/(n + n₀ + 1) — use it to give the Monte Carlo a distribution over
a player's share rather than a point estimate.

Two conversions do all the work:

    from a split-half reliability r at n per half:   n₀ = n(1 − r)/r
    from first-k-weeks vs rest-of-season r:          r² = [n_e/(n_e+n₀)]·[n_l/(n_l+n₀)]
    method of moments from a population:             n₀ = m(1−m)/v_true − 1,
                                                     v_true = Var(p_i) − mean[p_i(1−p_i)/n_i]

Unit conversion: **31.3 team targets and 26.9 team carries per team-game**
(2023–25, 1,632 team-games).

### 5.2 Usage stabilizes fast; efficiency does not  *(nflverse)*

Pure sampling noise (odd vs even weeks, player-seasons ≥ 10 games):

| metric | split-half r | n per half | n₀ | in games |
|---|---|---|---|---|
| WR/TE target share | 0.922 | 222 targets | **19** [CI 16–22] | ~0.6 |
| RB carry share | 0.952 | 198 carries | **10** [8–12] | ~0.4 |
| RB target share | 0.850 | — | 40 targets | ~1.3 |
| catch rate | 0.405 | 36 targets | ~53 | |
| yards/target | 0.221 | 36 targets | **127** [78–256] | ~4 |
| PPR/target | 0.184 | 36 targets | 161 [88–443] | ~5 |
| rec TD/target | 0.135 | 36 targets | **~232** | ~7.5 |
| RB yards/carry | 0.309 | 82 carries | 182 [98–448] | ~7 |
| RB rush TD/carry | 0.248 | 82 carries | ~247 | ~9 |

**Efficiency stabilizes 5–20× slower than usage.** Route-denominated
published counts agree in direction *(sourced, snippets only — the site was
down)*: WR targets-per-route ~185 routes, WR yards-per-route ~350, RB
TD-per-route 880–1,060.

Including real in-season role drift (not just sampling noise), the
first-k-weeks vs rest-of-season correlation for WR/TE target share runs
0.702 / 0.816 / 0.828 / 0.839 / 0.842 / 0.853 at k = 1/2/3/4/6/8, i.e. it
reaches ~97% of its ~0.84 plateau **by week 3** — quantifying Hermsmeyer's
"three games". Drift-inclusive n₀ is 26–44 team targets. The plateau at
r² ≈ 0.70 means **~30% of rest-of-season share variance is genuinely
unpredictable drift** — the ceiling on any usage projection.

### 5.3 What to actually use in-season  *(nflverse, out-of-sample grid)*

Optimal n₀ chosen by out-of-sample RMSE against rest-of-season share, prior =
last season's same-team share (this is the rule-5 spirit applied properly):

| metric | optimal n₀ | note |
|---|---|---|
| WR/TE target share | **90 team targets (~3 games)** | flat 60–120; RMSE 0.0412 vs naive 0.0487 |
| RB target share | 180–300 team targets | far noisier weekly than the season role |
| RB carry share | 45 (wk 1–2) → 30 (wk 3) → 20 (wk 4) → ~0 (wk 6) | prior dies fast |
| catch rate | ~50 targets | |
| yards/target | ~130 targets | |
| TD/target, TD/carry | ~230 / ~250 | shrink 80–90% to positional mean |
| snap share, route rate | ~65–100 team snaps | **(estimate by analogy — no snap data cached)** |

Week-1 blending: last season's same-team share is worth **~80 team targets
(2.5–3 games)** for WR/TE, but only **~28 team carries (~1 game)** for RBs,
and nothing after week 4. Team-changers keep r = 0.76 (N = 44).
**Preseason has no published validation as a usage signal** (PFF's study
covers grades only) — let it move depth-chart rank, not share.

### 5.4 Role priors for week 1  *(nflverse, method of moments)*

| role | mean share | sd(true) | n₀_pop |
|---|---|---|---|
| WR1 | 0.249 | 0.042 | 106 |
| WR2 | 0.179 | 0.045 | 73 |
| WR3 | 0.123 | 0.036 | 81 |
| TE1 | 0.166 | 0.044 | 71 |
| TE2 | 0.073 | 0.027 | 93 |
| RB1 target share | 0.103 | 0.038 | 62 |
| RB2 target share | 0.062 | 0.036 | 43 |
| RB1 **carry** share | 0.535 | 0.099 | 25 |
| RB2 **carry** share | 0.264 | 0.104 | 17 |

WR1 p10/p50/p90 = 0.185 / 0.246 / 0.304; RB1 carry share 0.405 / 0.554 / 0.654.

### 5.5 TD rate is the fakest thing in fantasy  *(nflverse + sourced)*

Year-over-year, a top-decile WR/TE receiving TD rate (10.4%) falls to 5.8% —
it **retains 11% of its excess**. RB rushing TD rate retains 33%. By
contrast target share retains **82%** and carry share 73%. Published
agreement is unusually strong: 4for4 WR TD-rate YoY r = 0.19, RB 0.05;
Sharp Football WR TD% R² = 0.0155; ESPN's expected-TD reports flagged 151
over-scorers 2016–2025 and **137 (90.7%) scored fewer TDs the next year**.

### 5.6 How much of fantasy scoring is opportunity?  *(nflverse)*

Raw opportunity counts alone (targets and carries, no intercept) explain:

| horizon | WR | TE | RB | pooled |
|---|---|---|---|---|
| single week | 0.60 | 0.63 | 0.66 | 0.63 (0.61 out-of-sample 2025) |
| season total | 0.89 | 0.87 | 0.93 | |
| per-game log decomposition | 0.72 | 0.72 | 0.83 | |

**Verdict on the bootstrap doc's "~70% opportunity"**: right for WR/TE at the
season/per-game level, understated for RB (83%), and overstated for a single
week (60–66%).

---

## 6. Replacement level, VOR, flex  — UNVERIFIED

### 6.1 Three baselines, three formulas (derived)

- **Draft** (season-long, availability-weighted):
  `VOR = Σ_w λ_w · a_{i,w} · (μ_{i,w} − R_p(w)) ≈ G · a_i · (μ_i − R_p)`
  with G = 13 played games (14 weeks − 1 bye). The baseline sits at the
  **man-games rank** `N_p = L·(s_p + F_p/L) / (a_p · 16/17)`, which for this
  league is RB 31.5, WR 42.0, TE 14.6, QB 15.5.
- **Weekly start/sit**: lineup-marginal value against the best legal own-bench
  alternative for that slot.
- **Waiver**: `R_p^FA(w) = max over free agents of the FORWARD projection`
  over the decision horizon. Being a max over a pool, it is bounded below by
  the rank-band values and jumps whenever any free agent has a fresh role
  change. **Never a season-total rank.**

### 6.2 The flex is a WR slot  *(nflverse)*

Filling the 12 flex slots from the merged residual pool {RB25+, WR25+, TE13+}
gives, in full PPR: **~10 WR, ~1–1.5 RB, ~0–1 TE**. Effective last starters
are therefore **RB25–26, WR34–35, TE12–13**, and the flex margin
T ≈ **10.7–11.0 PPG** — the value at which R_RB = R_WR = R_TE, so
last-starter VOR is directly comparable across flex-eligible positions.
Sensitive to scoring: half-PPR moves 2–4 flex slots back to RB.

> ⚠ **`verify_vor.py` rejects the flex-margin numbers.** It recomputes the
> 12th-flex value as **9.32 PPG on projections and 7.19 PPG on realized
> weekly scores**, against the claimed 10.85; the mean-based RB flex count as
> **1.87** against 1.25; and WR/TE starters per week as 33.7 / 11.2 against
> 34.4 / 12.1. The qualitative finding (the flex is overwhelmingly a WR slot)
> survives; the *level* of T does not. Do not use T = 11.0 as a replacement
> baseline until this is reconciled — a 1.5–3.7 PPG error in replacement
> level propagates into every VOR number downstream.

### 6.3 Positional curves, PPG by rank (3-year means 2023–25, ≥ 8 games)  *(nflverse)*

| rank | 1 | 6 | 12 | 24 | 36 | 48 | 60 |
|---|---|---|---|---|---|---|---|
| QB | 23.8 | 19.8 | 17.2 | 13.5 | | | |
| RB | 24.0 | 17.9 | 15.8 | 12.4 | 9.5 | 6.6 | 4.5 |
| WR | 23.5 | 18.2 | 16.0 | 13.6 | 11.9 | 9.9 | 8.2 |
| TE | 16.7 | 12.6 | 10.3 | 7.8 | | | |
| K | 11.7 | 9.5 | 8.5 | | | | |

Slopes (PPG per rank): RB1–3 is an elite tier at 2.0/rank, then RB6–48 is a
**straight 0.27/rank with no interior cliff**; WR1–6 is 1.06/rank then
WR24–48 flattens to **0.15/rank** — which is exactly why WR fills the flex;
TE1–5 elite then 0.38/rank to TE12; QB near-linear 0.45/rank, no cliff;
K flat past K6.

### 6.4 Streaming (waiver) baselines

| position | baseline PPG | source |
|---|---|---|
| QB | 12.9 (naive QB22–24 rank) → 16.3 (matchup-selected) | *(nflverse)* / Fantasy Footballers |
| TE | 7.4–9.1 | *(nflverse)* / sharksnip |
| DST | 8.5–10.4 | ESPN — best-matchup streamers (10.4) **beat top-5 season DSTs (9.6) in each of the last 4 seasons** |
| K | 8.5–9.0 (dome 9.1 vs outdoor 8.2) | RotoWire / *(nflverse)* |

### 6.5 Availability  *(sourced)*

Top-24 ADP RBs miss 2.4 games/season, WRs 2.2; rounds 6–8 miss 3.8 / 3.3.
Games missed per time-loss injury (2017–22, 2,523 injuries): QB 5.4, RB 4.9,
WR 5.5, TE 5.7. Players who suit up while on the injury report lose
**QB −8.1%, RB −15.7%, WR −22.2%, TE −17.6%** of production — an argument
for rule #11 in numbers. Availability a_p ≈ RB 0.86, WR 0.87, QB 0.82,
TE 0.88. Applying availability plus byes lowers the draft baseline by
RB −1.4, WR −1.5, QB −1.0, TE −0.6 PPG.

> ⚠ **`verify_vor.py` rejects three of those four shifts**, recomputing them
> from its own curve as WR **−0.59** (not −1.5), TE **−1.59** (not −0.6) and
> QB **−3.17** (not −1.0); only RB survives. The QB error is the one that
> matters: a −3.2 PPG shift moves the QB baseline enough to change the
> "16-PPG QB is at replacement" conclusion in §6.6.

### 6.6 Worked examples (derived)

Draft VOR at the man-games baseline, G = 13: a **16-PPG RB = +59 season
points**; a **16-PPG QB = −2 (at replacement)**; a **12-PPG TE = +26**.
Robust to baseline choice (worst-starter gives 56 / −13 / 19). On waivers the
same RB is worth 9.5–12 PPG over the best free agent versus 0–3 for the QB.

**Why season-rank replacement gives the wrong waiver answer** *(nflverse)*:
TreVeyon Henderson was RB42 by season-to-date total through week 8 of 2025
(6.6 PPG, below every season-rank baseline) and then scored **17.0 PPG in
weeks 9–17**. The forward-looking rule values him around +80 points; the
season-rank rule says drop him. Reverse case: Keenan Allen was WR8 by weeks
1–8 total, then 6.5 PPG after. This is the bootstrap doc's fixed-replacement
scar in its NFL form.

---

## 7. FAAB auction math  — UNVERIFIED

### 7.1 The equilibrium and why it doesn't apply (derived)

Symmetric first-price sealed-bid with n iid uniform private values gives
b(v) = v·(n−1)/n — a 8% shade at n = 12. Every assumption fails here, and
the directions are known:

| violation | direction |
|---|---|
| affiliated/common values (shared rankings) → winner's curse | shade MORE ⚠ |
| hard non-replenishing budget → shadow price λ_t on every dollar | shade MORE, especially early |
| only 2–5 *interested* bidders, not 12 | shade MORE (n_eff = 3 → 33% shade, not 8%) |
| rival budgets visible on ESPN/Sleeper | cap at max rival budget + $1 |
| integer bids + priority tie-breaks | **bid $1 above focal numbers** ($11, $26, $51) |

Tie-breaks matter: both ESPN and Sleeper resolve tied bids by waiver priority
and send any claim winner to the back of the queue, so after your first win
you lose essentially every tie.

> ⚠ **The winner's-curse direction is the one claim `verify_faab.py` could
> not confirm.** Its common-value auction simulation found equilibrium bids
> at n = 3/5/8 that were *not* systematically below the private-value
> benchmark. But the same run failed its own convergence check ("all five bid
> functions converged" → false), so the result is inconclusive, not a
> refutation. Milgrom–Weber is standard theory and probably right; the sim
> needs fixing before either way is asserted. The other four violation
> directions all passed.

### 7.2 Dollars → points → wins (derived, anchored to §2)

    ΔWins = Σ_w X · S_w · φ(z_w)/s_w      = X·S·W·0.01247 at even matchups (s = 32)

A +3 pt/week add started 80% of 10 remaining weeks is worth **+0.30 expected
wins**. Playoff weeks should carry a premium k (estimate 2, range 1–3) times
P(alive).

### 7.3 What adds actually clear for  *(sourced: FFPC $1000 leagues, 2024)*

| | % of budget |
|---|---|
| median winning bid (season / week 1) | 1.4% / 1.1% |
| positional medians QB/RB/WR/TE/K/DST | 2.1 / 2.1 / 2.9 / 2.0 / 0.3 / 1.0 |
| **dead zone — rarely returns a starter** | **10–19%** |
| difference-maker splash band | 30–60% (max observed 76.6%) |
| home leagues, RB who became the starter (wks 4–8) | $8–40 per $100 (IQR, n = 17) |

Hit rates: a handcuff is a weekly top-24 RB **34%** of the time when the
starter is out, but only **11%** of backups of first-round RBs ever post a
top-24 season; ambiguous committee backfields produce a top-24 season **65%**
of the time. So a speculative pre-injury handcuff is worth roughly **5–10%**
of the same back's post-injury price.

### 7.4 Budget pacing: spend LATER, not earlier  *(estimate — a toy dynamic program)*

Backward induction over 14 runs (impact add arrives w.p. 0.5 through week 8
then declining; lognormal price, median 20% of budget) gives a shadow price
of $10 falling from **0.074 wins in week 1 to 0.005 by week 10 and ~0 by
week 13**. Consequently the optimal bid on a generic impact add *rises*
through the season — ~30% of budget in weeks 1–4, ~41% by week 8, ~49% at
week 10, 60% at week 12, 100% at the final run — **even though the add's
value falls 4×**, because the option value of holding cash collapses faster
than the add's value does. Hoarding beats spending only when an add's
wins-per-dollar is below λ_t: an add worth 0.10 wins justifies ~$13 in week 1
but ~$55 in week 8. A $100 budget played optimally is worth ~1.1–1.45 wins
per season. **All of this rests on estimated arrival rates and prices — it is
the least-supported material in this document.**

### 7.5 Policy summary

$0–$1 on one-week streamers; ≤ 5% on speculative adds with P(role) < 25%;
5–10% or 20–30% on committee backs — **never 10–19%**; 30–60% on a true
lead-back promotion or a usage-confirmed breakout; > 50% only from week 9 on,
or for a rest-of-season top-24 asset on a contender; contenders spend to zero
in weeks 12–14. Note advice columns systematically recommend far less
(2–10%) than winning splashes actually clear at.

---

## 8. Reproduction

    .venv/Scripts/python.exe scripts/research/verify_vegas.py          # ~600 lines; Part B needs the nflverse files in data/research/cache/nflverse/ (gitignored)
    .venv/Scripts/python.exe scripts/research/verify_winprob.py        # seed 20260904, N=1e6, ~10 s, ~600 MB
    .venv/Scripts/python.exe scripts/research/verify_stabilization.py  # 382 checks, ~4 s (needs the nflverse cache)
    .venv/Scripts/python.exe scripts/research/verify_vor.py            # 189 checks, ~8 s (needs the nflverse cache)
    .venv/Scripts/python.exe scripts/research/verify_faab.py           # 75 checks, ~35 s; exits 1 on the unconverged common-value sim

The nflverse cache is not committed (rule #10). Re-download it before running
the data-dependent scripts; §4 lists the release URLs.

## 9. Corrections ledger (researcher → verifier)

| domain | claim | correction |
|---|---|---|
| vegas | 2nd-half dropback slope −0.9 pp/pt | **−1.2 pp/pt** (bucket-midpoint and play-level OLS agree) |
| vegas | rushes(S,T) = 30.31 − 0.289·S − 0.076·T | includes kneel-downs; fantasy-carry version **29.68 − 0.250·S − 0.081·T** |
| vegas | within-team retains "55–70%" of pooled effect | within/pooled 0.67–0.84; and pooled line-only beats team-prior+within OOS |
| vegas | WR weekly SD ~4.5–5 at 13.5 PPR | **~7.5** (CV 0.55) from 70 real player-seasons |
| vegas | catch rate −1.1 pp per yard of aDOT | −1.4 to −1.6 pp/yd |
| winprob | margin SD with QB-vs-oppDST ρ = −0.3 → 31.8 | sign error: negative cross-ρ RAISES s → **32.85** |
| winprob | K+DST supply "most of the fat tail" | refuted: 13% of variance, 10% of third cumulant |
| winprob | variance breakeven deficit 5–6 | **≈ 6 (6.1–6.5)**; at deficit 5 variance still hurts |
| winprob | Φ error ≤ 0.4 pp stacked vs unstacked | ≤ 0.5 pp (0.49 measured) |
| winprob | CRN cuts ΔP SE ~10× | **~4×** (flip-fraction floor ~3%) |
| data2026 | site.api.espn.com returns 403 | only to browser-like UAs; script UAs get 200 on both hosts |
| data2026 | FantasyPros "429 on rate limit" | not on any cited page — unverified |
| data2026 | espn-api #547 pointer "from the maintainer" | from contributor dtcarls; conclusion unchanged |
| data2026 | pip `pandas>=2.0` | resolves to 3.0.5 — pin `<4`, test on 3.x |

## 10. Open questions carried forward

- Within-team efficiency slope may be partly personnel-driven (line moves
  when a QB is out) — separate with an injury join.
- Position-specific efficiency-vs-line slopes not computed (team-level slope
  applied uniformly in the worked example).
- Ingest an opener/closer feed from day one so opener-vs-closer can be
  answered in-house.
- Correlation constants and positional CV ranges are untestable until
  nflverse weekly data are ingested with prior-week-usage role labels.
- DST: fit a two-part model (normal base + Poisson TD/turnover bursts) if
  tail questions matter; the −5 shift is an estimate.
- Slot means in the lineup model are tier-typical guesses; league-derived
  starter means rescale lineup SD, s and pp-per-point proportionally.
