# 2026 draft plan — slot 1 of 12, "Take Mahomes, Country Road"

Built 2026-09-08, ~4 hours before a 9:00 PM ET Sleeper snake draft. This is
the record of what the board said and why; the live tool is the published
"1.01 War Room" artifact, the data is `data/outputs/draft2026_board.csv`, and
the code is `scripts/research/draft_board_2026.py`.

## League (verified from the Sleeper API, `league_config.py`)

12 teams, snake, 15 rounds, 60 s clock. Starters QB / 2 RB / 2 WR / TE /
2 FLEX (RB-WR-TE) / K / DEF, 5 bench, 1 IR. Half-PPR, INT −1, fumble −2,
no bonuses. Playoffs 6 teams from week 15; trade deadline week 13.
My picks: 1, 24, 25, 48, 49, 72, 73, 96, 97, 120, 121, 144, 145, 168, 169.

## Inputs

| source | what | freshness |
|---|---|---|
| Sleeper `projections/nfl/2026` | season stat-line projections + Sleeper ADP (3,304 players) | live |
| FantasyPros half-PPR ECR | expert consensus rank, sd, min/max, tier (978 players) | 9/08 |
| FantasyFootballCalculator ADP | 12-team half-PPR, 1,837 drafts | 9/03–9/08 |
| nflverse 2025 season stats | prior-year PPG under this league's scoring | final |
| Sleeper player dump + trending adds | injury tags, depth chart, 48-hour add counts | live |
| ~25 web sources | injury/suspension/depth-chart news | 9/08 |

## Method

1. **Projection** = ½ Sleeper stat-line scored with `gridiron.scoring` under
   the real rules + ½ "consensus-implied": the ECR position rank mapped onto
   the Sleeper points curve. Known missed games (Henderson, Tyson, Jacobs,
   Monangai, Kamara, season-enders) are applied to the Sleeper leg only.
2. **Replacement level** by order-statistic fill of the actual lineup
   (QB12, RB24, WR24, TE12, then 24 flex by projection). The flex filled
   16 WR / 8 RB, so replacement = RB33 138.6, WR41 139.0, TE13 128.6,
   QB13 292.2. VOR = projection − replacement.
3. **Availability**: blended ADP (60% Sleeper, 40% FFC, because the room
   drafts on Sleeper) with sd = 0.57 + 0.11·ADP fitted on FFC's own
   per-player spread; P(available at pick k) = 1 − Φ((k − ½ − ADP)/sd).
4. **Monte Carlo**: 300 drafts per policy. Eleven opponents draft off their
   own noisy copy of the ADP board with roster caps (no K/DEF before round
   12, one QB/TE before round 11). My policies were scored by optimal
   starting-lineup points plus a small bench term.

## Results

| policy (first three rounds) | mean lineup pts | p10 |
|---|---|---|
| **best static VOR each pick** | **1866** | 1849 |
| RB then TE then WR | 1759 | 1740 |
| dynamic value-over-next-available | 1759 | 1741 |
| RB-RB-WR | 1756 | 1739 |
| RB-WR-WR | 1752 | 1734 |
| RB-WR-QB | 1747 | 1727 |
| RB-RB-RB | 1742 | 1718 |
| WR at 1.01 | 1715 | 1694 |

The static-VOR rule wins by ~100 points, almost all of it from taking the
elite TE at the 2/3 turn and a top-8 QB in rounds 6–7 instead of waiting.

Most common picks under the winning rule:

| pick | most common | share |
|---|---|---|
| 1 | Jahmyr Gibbs | 100% |
| 24 | Brock Bowers (else Nico Collins) | 77% |
| 25 | Javonte Williams / George Pickens / Bowers | 47 / 32 / 19% |
| 48 | David Montgomery / Waddle / Burden / Swift | 34 / 20 / 16 / 14% |
| 49 | Luther Burden / McLaurin | 57 / 17% |
| 72 | Brian Thomas / Harrison / Hurts / Daniels | 49 / 19 / 10 / 5% |
| 73 | Daniels / Hurts / Harrison | 23 / 22 / 17% |
| 96–97 | MarShawn Lloyd, Jayden Reed, Lawrence, Pittman, Mason | |
| 120–145 | Coker, Shakir, Meyers, Doubs, Rodriguez, Boston, Boutte, Shaheed | |
| 168–169 | DEF then K | |

## Caveats

- Replacement level treats the season as one number; RB injury churn
  (§6.6 of QUANT_FOUNDATIONS: replacement is a forward max) means late RB
  handcuffs carry more value than VOR shows. The plan leans on that in
  rounds 9–13.
- Sleeper's projections list `gp = 18` for everyone; the ECR leg carries the
  injury discounting.
- FFC's half-PPR sample is thin for some players (Bowers n small, ADP 40 vs
  Sleeper 23.5). The 60/40 blend leans Sleeper on purpose.
- The dynamic policy underperformed static because its need weights were
  hand-set; it is not evidence against dynamic VOR in general.

## Revision: manager history (added 2026-09-08, 7:30 PM)

`analyze_competition.py` profiled every manager from the league's 2023–25
Sleeper history and `draft_board_2026.py` now shifts each opponent's board
by how early they take their first QB/TE/RB/WR versus the market of the day
(`data/outputs/competition_shifts_2026.csv`). Results moved:

| at pick 24 | ADP-only | with history |
|---|---|---|
| Bowers | 97% | ~0% |
| McBride | 99% | ~0% |
| Josh Allen | 55% | 22% |
| Kenneth Walker | 4% | 35% |
| Ashton Jeanty | 4% | 32% |
| Omarion Hampton | 3% | 27% |
| Nico Collins | 14% | 41% |
| George Pickens | 38% | 65% |

The turn is therefore RB/WR, not TE: best RB left (Walker, Jeanty, Hampton)
or Collins/Pickens at 24, then Pickens/Collins/Olave/Nabers or Javonte/Kyren/
Hall at 25. TE waits for the LaPorta/Kraft tier at 72; QB for Daniels/Hurts
at 72–73 or Lawrence at 96. Full room profiles: `COMPETITION_2026.md`.
