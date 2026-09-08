# What an injury does to fantasy production (2023–25, this league's scoring)

Script: `scripts/research/injury_effects.py`. Data: nflverse injury reports
(regular season, QB/RB/WR/TE) joined to weekly stats scored with
`gridiron.scoring` under the verified half-PPR rules. "Fantasy-relevant" =
at least 3 games and 6+ PPG before the episode. Small n is flagged; treat
anything under ~10 as a hint.

## 1. Playing through a "Questionable" tag costs about 16%

241 player-seasons where a relevant player played while listed Questionable:
that game averaged **9.5 PPG vs 11.3** in his other games (ratio 0.84,
t = −4.5). By injury on the tag:

| tag injury | n | ratio |
|---|---|---|
| Calf | 12 | 0.70 |
| Shoulder | 19 | 0.70 |
| Foot/Toe | 14 | 0.70 |
| Ankle | 39 | 0.80 |
| Knee | 36 | 0.80 |
| Concussion | 9 | 0.80 |
| Hamstring | 21 | 0.90 |
| Ribs/Core | 21 | 0.90 |
| Illness | 15 | 0.90 |
| Hip, Quad | 11, 9 | ~1.2 (no effect, small n) |

Draft implication: a week-1 Questionable tag is a real one-game discount,
not a season discount. Chase, Nacua, Jeanty, Hall, Nabers, Egbuka and Evans
all carry one tonight.

## 2. After returning from a missed game, the next 6 games run 84–98% of prior PPG

Same-season, next 6 games after return vs games before the episode. The
healthy baseline drift (first 5 games vs games 6–11, n = 444) is 0.95, so
subtract ~5 points of ratio for "regression to the mean".

| injury | n | pre PPG | post PPG | ratio | t | worse |
|---|---|---|---|---|---|---|
| Hamstring | 30 | 12.0 | 10.1 | 0.84 | −1.8 | 70% |
| Ankle | 21 | 11.4 | 9.5 | 0.84 | −2.6 | 76% |
| Shoulder | 18 | 11.5 | 9.9 | 0.86 | −1.4 | 78% |
| Knee | 24 | 10.6 | 9.7 | 0.91 | −1.0 | 67% |
| Ribs/Core | 14 | 10.8 | 10.2 | 0.95 | −0.4 | 50% |
| Concussion | 24 | 10.4 | 10.2 | 0.97 | −0.3 | 50% |
| Foot/Toe | 13 | 10.0 | 9.8 | 0.98 | −0.2 | 62% |

By position, running backs suffer most: **RB ankle 0.77 (n = 11, t = −2.3),
RB knee 0.79 (n = 8, t = −2.4)**, vs WR ankle 0.91 and WR knee 0.87. QB
hamstring is the outlier (0.57, n = 5): a running QB who cannot run.

## 3. Games missed per episode

| injury | n | mean missed | season-ending share |
|---|---|---|---|
| Hamstring | 38 | 2.1 | 16% |
| Knee | 51 | 1.8 | 37% |
| Ankle | 41 | 1.5 | 37% |
| Concussion | 39 | 1.5 | 23% |
| Shoulder | 29 | 1.5 | 28% |
| Foot/Toe | 21 | 1.6 | 38% |

(The season-ending share counts episodes with no return that season, so
late-season injuries inflate it.)

## Tonight's application

| player | tag | number to use |
|---|---|---|
| Jeanty, Love, Henderson (RB, ankle) | Q / out | 0.77 for the first ~6 games, then normal |
| Nabers (WR, ACL) | Q, GTD | 0.87 for 6 games; plus the Q-tag 0.80 in any game he is tagged |
| Chase (WR, knee scare) | Q | Q-tag game 0.80; no absence, so no 6-game discount |
| Hall (RB, groin) | Q | no measurable lasting drop (n = 3) |
| Nacua (WR, core) | Q | 0.95, i.e. nothing beyond normal drift |
| McCaffrey (RB, calf tag) | Q | Q-tag calf games 0.70; season unaffected |
| Egbuka (WR, toe) | Q | 0.98 after return; Q-tag foot games 0.70 |
| Kittle (TE, Achilles, 32) | active | too few cases to measure; snap count is the story |
| LaPorta (TE, hip) | active | hip returns not worse; only the snap count |

These are same-season effects on players who were productive before the
injury. They say nothing about a player's long-term talent, and the Sleeper
projections and expert consensus already carry some of this discount, so
do not double-count: use them to break ties and to size week-1 expectations.
