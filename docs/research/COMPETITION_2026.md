# The room: manager profiles from league history (2023–25)

Source: Sleeper public API via `scripts/research/pull_sleeper_history.py`
(the league's `previous_league_id` chain: 2023 8-team → 2024 10-team →
2025 12-team → 2026), scored against FantasyFootballCalculator ADP of the
same season by `scripts/research/analyze_competition.py`. Outputs:
`data/outputs/competition_2026.csv`, `competition_picks_2023_2025.csv`,
`competition_results_2023_2025.csv`, `competition_shifts_2026.csv`.
"Reach" = ADP-of-the-day minus pick number, so +12 means taken 12 picks
before the market would have.

## Draft order and tendencies

| slot | manager | picks | R1 history | first QB (rd) | first TE (rd) | reach style | record 23/24/25 | in-season |
|---|---|---|---|---|---|---|---|---|
| 1 | Kejjeh | 1·24·25 | TE/WR/RB | 2/5/3 | 1/10/7 | value (−5.0 avg) | 10-4 (champ) / 7-7 / 5-9 | 23 claims, $159 |
| 2 | sallymcbride | 2·23·26 | WR/WR | 4/3 | 7/6 | value (−4.0) | – / 8-6 (champ) / 7-7 | never a waiver claim |
| 3 | bogeman | 3·22·27 | WR/WR/RB | 4/10/3 | 7/14/11 | neutral, 16% big reaches | 7-7 / 4-10 / 7-7 | most active: 36 claims, $285, 3 trades |
| 4 | MaxSchussler | 4·21·28 | RB/RB/WR | 9/5/10 | 8/4/4 | TE 18 picks early | 9-5 / 7-7 / 6-8 | 12 claims, $202 |
| 5 | LaisterSmith | 5·20·29 | WR | 3 | 9 | 20% rookies | 7-7 | 2 claims |
| 6 | mikedonutgang200 | 6·19·30 | WR/WR/WR | 3/4/7 | 7/5/6 | QB 9 early, 16% big reaches | 12-2 / 5-9 / 7-7 (champ 2025) | never a waiver claim |
| 7 | clyvejohnson | 7·18·31 | RB/RB/RB | 5/6/4 | 7/9/8 | at market | 7-7 / 8-6 / 10-4 | never a waiver claim |
| 8 | chaguy2457 | 8·17·32 | RB | 11 | 2 | Bowers at 21 (+13) | 9-5 | 16 claims, $100 |
| 9 | glavoile | 9·16·33 | WR/WR/WR | 5/7/5 | 7/3/3 | TE 17 early, QB 12 early | 3-11 / 6-8 / 6-8 | 3 claims, 2 trades |
| 10 | reijcage | 10·15·34 | WR/WR | 4/6 | 7/8 | QB 10 late | 6-8 / 10-4 | 11 claims, $65 |
| 11 | pbrady98 | 11·14·35 | RB/RB/RB | 5/8/7 | 7/6/5 | TE 16 early, 20% big reaches, 2 QBs by R10 | 5-9 / 7-7 / 5-9 | 9 claims, $121 |
| 12 | SirChadius (was coochiemoocher22) | 12·13·36 | RB/WR/RB | 5/10/11 | 2/8/6 | biggest reacher (+5.4 avg; Pitts +84) | 7-7 / 8-6 / 5-9 | 32 claims, $153 |

## What the room does differently from ADP

- **Tight ends go early.** Five of the eleven managers picking ahead of 24
  take their first TE 12–20 picks before the market (Max, LaisterSmith,
  chaguy, glavoile, pbrady, SirChadius). Last year the first two TEs went at
  21 and 26. In the history-aware simulation Bowers and McBride are gone
  before pick 24 in essentially every draft; the ADP-only model had Bowers
  there 97% of the time.
- **Running backs go late.** Nine of twelve take their first RB at or after
  the market. Walker, Hampton and Jeanty reach pick 24 in 27–35% of sims
  (ADP-only: 0–4%).
- **Quarterbacks split the room.** glavoile, mikedonutgang and bogeman take
  QBs 3–12 picks early; chaguy, SirChadius, reijcage, Max, LaisterSmith wait.
  Six QBs were gone by pick 50 last year. Allen at 24: 22%. Daniels/Hurts at
  72: 83% / 72%. Lawrence at 96: 87%.
- **Half the league never touches waivers.** sallymcbride, mikedonutgang200
  and clyvejohnson have zero waiver claims in three seasons (free-agent
  pickups only). The waiver competition is bogeman, SirChadius, Max, chaguy.
  FAAB budget is $100 (Sleeper `waiver_type` 2), clears Wednesday.
- **Trades are rare**: six in three seasons (bogeman 3, glavoile 2,
  SirChadius 1). Deadline week 13.

## Effect on the slot-1 plan

Simulation with per-manager shifts (mean lineup points, 300 drafts):

| policy | ADP-only | with history |
|---|---|---|
| best static VOR each pick | 1866 | 1893 |
| RB-RB-WR | 1756 | 1780 |
| RB, TE, WR | 1759 | 1748 |

Most common history-aware picks: 1 Gibbs; 24 Walker / Collins / Hampton /
Jeanty; 25 Pickens / Collins / Jeanty / Javonte; 48–49 Montgomery / Irving /
Judkins / Swift / Burden; 72 LaPorta or Kraft (the TE2 tier survives because
the room already spent on TEs); 73 Brian Thomas / Hurts; 96 Lawrence; 97
Lloyd. The elite-TE-at-the-turn idea is dead in this room; the RB-slide is
the opportunity.

## Caveats

- Manager shifts are means over 1–3 seasons in 8/10/12-team formats; the
  single-season managers (LaisterSmith, chaguy2457) are one data point each.
- Historical ADP is FFC's, matched by name; 7–27% of picks per manager had no
  ADP match (mostly K/DEF and deep sleepers) and are excluded from reach.
- Sleeper does not flag autopicks, so timer-expired picks look like choices.

## Deeper cuts (`analyze_competition_deep.py`, added 8:05 PM)

**Loyalties.** Repeat drafters: sallymcbride has taken Josh Allen two years
running (and Aubrey twice); mikedonutgang200 took Kamara three times, Chase
and Taylor twice; MaxSchussler Bijan, London, Montgomery, Dak twice and
Falcons 5.6× the league rate; SirChadius Gibbs, Evans, Higgins twice;
bogeman St. Brown, Collins, Kupp, Kirk, Hurts twice; pbrady98 Saquon,
Metcalf, McLaurin, Garrett Wilson twice. Team tilts: reijcage Bucs 6.4×,
mikedonutgang Saints 6×, sallymcbride Bills 5.6×, pbrady Jets 4.3×.

**What won.** 30 team-seasons: champions opened RB-light (2 RB in rounds
1–6) with a QB in round 2–7 and a TE in round 1–7. Rookie count is the
only shape variable with a real signal (r = −0.47 with PPG); early QB is
mildly positive (r = −0.22 on QB round). RB-vs-WR mix in rounds 1–6 does
not predict anything here.

**Draft skill** (points of drafted players vs what the pick slot usually
returns, rounds 1–10): sallymcbride +35 per pick, mikedonutgang200 +32,
glavoile +16, reijcage +16, clyvejohnson +14; Kejjeh −7, bogeman −11,
LaisterSmith −13, SirChadius −16. The biggest single wins in league history
are all quarterbacks taken in rounds 3–5 (Allen ×3, Lamar, Hurts ×2).

**Weekly scoring, 2025 (12 teams).** Team-week mean 111.1, sd 20.8; 80th
percentile 127, 20th 94. Average margin 24.8; only 20% of games are decided
by under 10. ~120 a week wins 70% of matchups.

**Waiver market ($100 FAAB).** 144 winning claims in three seasons, 80 of
them at $0–1. Winning bids: RB median $10 (max $43), WR $6 (max $51), TE
$0.50, QB $0 (but $50 for Geno in 2024), DEF/K $0. Claim volume: bogeman
36, SirChadius 32, Kejjeh 23, chaguy 16, Max 12; sallymcbride,
mikedonutgang, clyvejohnson never. Half the room does not compete for
adds, so bench upside can be chased at the draft and cleaned up in-season
cheaply.
