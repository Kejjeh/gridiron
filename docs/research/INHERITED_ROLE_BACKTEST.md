# Starter out, backup inherits the job: the week-10 miss and an inherited-role candidate (2026-11-18)

**Short answer: we missed Emmett Johnson for three reasons, none of them
bad luck.**
- Our model never moved him: 6.29 → 6.09. The whole +2.2 came from
  Sleeper's number in the stack.
- No alert could fire on "Questionable + did not practise Friday", and the
  rules say that is right (rule #11). Since the owner's decision of
  2026-11-18 (§3) an RB role-at-risk watch row does: a research row, not a
  move, and never an Out.
- The one watch that does fire on Out only re-reads Sleeper's tags once a
  day, so it fired about seven hours after another team had claimed him.

**The candidate does not ship.** Pricing the inherited role into the
weekly projection was tested over 2019–2025 on exactly the starter
Out / Doubtful / Friday-DNP player-weeks. It fails the owner's bar on every
check: 2025 MAE worse by 0.15, weighted MAE gain −0.11, sign-flip p = 0.39.
- **The inheritance is real.** When the starter sat, RB backups went from
  5.9 to 12.1 points a game.
- **For Out and Doubtful the model already prices part of it** through the
  depth chart and `vacated_pickup`.
- **For a Friday DNP the starter still played 78% of the time.** The honest
  Friday number moves about one point.
- **What actually loses the pickup is timing:** the board does not learn
  Saturday's ruling until Sunday. That fix, and whether to alert at all, is
  the owner's call: see the issue.

## 1. Diagnosis

### What the board said, build by build (week-10 archives, `ledger/decisions/season2026`)

Archives from the cloud run 0293 record (`scripts/weekly/fetch_record.py`).
`baseline -> advanced -> stacked (Sleeper)` is the radar's own explain line
for Johnson.

| build (UTC) | Walker on the inputs | Johnson | baseline → ours → served (Sleeper) | verdict |
|---|---|---|---|---|
| Tue 10 07:26 – Fri 13 15:27 (13 builds) | no week-10 report yet, then "no designation" | 6.24–6.69 | 4.55 → 6.29 → 6.42 (5.96) | BELOW |
| **Fri 13 21:32** (injury pull 21:32; Sleeper player pull 21 h old) | "no designation on the wk10 report": no row for him at all | **8.48** | 4.64 → **6.09** → 8.48 (**8.62**) | LINEUP, +0.34 at FLEX over a bench WR (Sleeper's revision, not ours) |
| Sat 14 01:41, 08:05, 14:58 | "Questionable (Groin)", from the Sleeper pull of 01:41 (no practice field); injury pull still Fri 21:32 | 8.48 | unchanged | LINEUP; change log: "Refreshed, not revised" |
| Sat 14 19:52 | Questionable (Sleeper pull still 01:41) | — | — | "now owned: claimed by a roster since the previous record" |
| Sat 14 23:26 | next injury pull: the first that could carry nflverse's Friday DNP + Questionable row (published with its ~07:00 UTC Saturday update) | owned | — | — |
| **Sun 15 02:59** (first Sleeper pull after his Out) | **Out** | owned | — | opening watch names the next two KC backs; **Johnson is already gone** |
| Sun 15 17:26 | Out | owned | 4.64 → 6.09 → 13.50 (14.95) | actual: 88% snaps, 22 carries, 7 targets, **35.1** |

### Why the weekly model moved him −0.2, not +20

Reproduced with a deterministic harness that drives `features_as_of` with a
team shaped like the week-9 KC box scores: Walker 24 opportunities a game at
75% of snaps, Johnson 5 at 25%, the receivers, the tight end and the
quarterback.

```
Final report: Questionable + DNP (nflverse, Saturday): Johnson vacated_pickup = 0.00 of Walker's 24.0 opps -> +0.00 pts
counterfactual: Out on the report: Johnson vacated_pickup = 3.53 of Walker's 24.0 opps -> +1.92 pts
  (RB weight 0.545/opp); WR1 gets 5.65, QB1 2.82
```

1. **Questionable is not a vacancy.** `vacated_pickup`
   (`gridiron.models.advanced.features_as_of`) counts only teammates listed
   Out or Doubtful on the report. Walker's final report was Questionable
   with a Friday DNP, so the feature was 0 even once that row arrived (the
   board's Friday-night inputs had no row for him at all; corrected
   2026-11-18, see the table). The model's own practice flags (`practice_dnp`,
   `questionable`) describe the player himself, not his teammates.
2. **Saturday's Out never reaches the model.** The model reads the nflverse
   final report, which is Friday's report. A ruling made on Saturday is in
   Sleeper's tag, not in that report.
3. **Even an Out would have bought +1.9 points.** When the feature fires,
   it shares the starter's opportunities among *every* active teammate at
   *every* position, in proportion to each player's trailing volume. A
   backup with little volume therefore inherits little. In the harness the
   top WR gets more of Walker's carries than Johnson does. In the box score
   Johnson had 29 opportunities (22 carries, 7 targets): more than Walker's
   whole 24-a-game workload.
4. **The +2.2 the page showed was Sleeper's.** On Friday night Sleeper moved
   from 5.96 to 8.62 and the stack carries about 0.7 of Sleeper's number
   (`stack_v1`). On Sunday morning Sleeper was at 14.95 while ours still
   read 6.09.

### Did an alert fire on Walker's Friday transition? (rule #8)

No.
- **The radar change log** (`radar_changes`) logged "BELOW → LINEUP:
  +0.34 at FLEX" on Fri 21:32. That is a change-log line, not an alert.
- **Waivers gate:** on Friday night the waiver gate was closed because the
  Sleeper player pull was 21 h old, past the 6 h designation-day limit.
- **Saturday's actions:** on Saturday the gate was open. With Johnson's
  edge at +0.34, the only acquisitions named were two defenses (+3.12 and
  +2.58).
- **The opening watch** (`gridiron.openings`, the "role opening" rows in
  `watch.work`) is the alert built for this case, after the week-9 misses.
  It fires only on Sleeper designations Out, IR, PUP, SUS, NA, COV and
  Doubtful (`VACATING_DESIGNATIONS`). By design, "Questionable" is not a
  vacancy (rule #11). It also reads Sleeper's tag from the player pull,
  which ran at Sat 01:41 and next at Sun 02:59 (`sleeper_players`
  max age 24 h, `gridiron.freshness`). Walker's Out first appeared in the
  Sun 02:59 build. The watch then named Jaydn Ott and Nate Carter. Johnson
  had been claimed by Sat 19:52.
- **No input carried a Friday-DNP transition** for a starter whose backup is
  in the pool, so nothing could alert on it.

### Hypotheses, as tested

| # | hypothesis | prediction | result |
|---|---|---|---|
| 1 | the model needs Out/Doubtful; Questionable + DNP gives 0 | `vacated_pickup` = 0 with Q + DNP | **confirmed** (harness: 0.00) |
| 2 | when it fires, the volume is diluted across positions | Out gives the RB2 a small share | **confirmed** (3.53 of 24; WR1 5.65) |
| 3 | the Out came too late for the watch (once-a-day player pull) | first opening row after the claim | **confirmed** (Sun 02:59 build vs claim by Sat 19:52) |
| 4 | the board moved him, but only by Sleeper's revision | ours flat, stacked +2.2 | **confirmed** (6.29 → 6.09; stack 6.42 → 8.48) |
| 5 | the projection was right and he just boomed | an inherited-role price would also say about 8 | rejected: Walker's full workload at RB points per opportunity is far above 8 (see the backtest) |

## 2. The candidate: an inherited-volume projection

Command: `PYTHONPATH=src python scripts/research/inherited_role_backtest.py`.
It is deterministic and runs in about 7 minutes. Season tables are cached
at `data/research/cache/tables/season<YYYY>_inherit.parquet`, which is
ignored.

### The subset: exactly the player-weeks an alert would name

- **Starter**, read from the box scores before the week:
  - an RB averaging ≥ 60% of offensive snaps over his last 3 games; or
  - the team's top WR (≥ 5 targets a game) or top TE (≥ 4) by targets over
    his last 3 games;
  - in either case, his last game is within 2 weeks.
- **Trigger**, from that week's final injury report:
  - class `out`: Out or Doubtful;
  - class `dnp`: did not participate in the week's last practice, and not
    Out or Doubtful.
  - Questionable alone is not a trigger (rule #11).
- **Backup**: the teammate at the same position with the highest snap share
  over his own last 3 games. He must have played in the last 3 weeks and
  not be Out or Doubtful himself. At WR he must be below a starter's 65% of
  snaps. Role comes from usage, never from the position tag (rule #4).
- **Row scored**: the backup's week, weeks ≥ 4, when he has a stat line and
  a Sleeper number (the harness universe).

| | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 | all |
|---|---|---|---|---|---|---|---|---|
| trigger rows | 86 | 83 | 99 | 90 | 91 | 88 | 86 | 623 |
| scored backup rows | 60 | 63 | 74 | 72 | 71 | 69 | 62 | 471 |

| class | rows | starter did not play |
|---|---|---|
| `out` (Out / Doubtful) | 342 | 99.4% |
| `dnp` (Friday DNP, not ruled out) | 281 | **21.7%** |

### What a backup actually inherits when the starter sits

These are the 401 starter-sat rows; the backup played in 90% of them.

"Before" is the backup's average over his last 3 games; "that week" is the
week his starter sat. The share inherited is the backup's extra
opportunities divided by the starter's.

| position | rows | starter opp/g | backup opp/g before → that week | share inherited | backup snaps before → that week | backup pts before → that week |
|---|---|---|---|---|---|---|
| RB | 94 | 20.0 | 8.3 → 16.8 | 43% | 34% → 61% | 5.9 → **12.1** |
| TE | 111 | 5.8 | 2.4 → 3.7 | 22% | 48% → 70% | 2.9 → 5.5 |
| WR | 154 | 8.6 | 3.4 → 4.8 | 16% | 49% → 66% | 4.3 → 5.9 |

RB backup point gain when the starter sat, by quantile: 10th −3.4, 25th
+1.0, median **+5.2**, 75th +10.8, 90th +16.5. 61% reached 60% of snaps and
34% scored 15+.

In the `dnp` class the backup's gain was +4.5 when the starter sat (57
rows) and −0.1 when he played (176 rows). At 22% that is about +1 point
expected on Friday.

### Candidates (2; the bar's chance level is split between them)

| name | what it adds |
|---|---|
| `+inherit` | shipped feature set + `inherit_pickup` = P(starter sits given his report class) × starter's opportunities a game: a ridge feature, 0 for everyone else (rule #5: the baseline holds ALL existing features) |
| `resid` | shipped prediction + k[pos] × the same quantity, with k fitted on the training seasons' subset residuals: a direct inherited-volume correction |

P(sits | class), k and the ridge weights are all fitted on the training
seasons only, and each season is held out in turn.
- **P(sits | class), per fold:** `out` 0.993–0.997, `dnp` 0.205–0.231.
- **Fitted value per vacated opportunity:** RB 0.12–0.16 points, WR
  0.08–0.14, TE 0.20–0.34.

### Results, the backup rows (ours = `advanced_v1` refit in-fold)

Pairwise compares the backup with every relevance-universe player at his
position that week.

| held out | rows | MAE shipped | MAE `+inherit` | MAE `resid` | pairwise shipped | `+inherit` | `resid` | bias shipped → `+inherit` | Sleeper MAE (post hoc) |
|---|---|---|---|---|---|---|---|---|---|
| 2019 | 60 | 4.996 | 4.990 | 4.998 | 65.54% | 65.54% | 65.33% | −1.54 → −0.60 | 4.726 |
| 2020 | 63 | 4.971 | 4.964 | 4.962 | 63.33% | 64.57% | 64.69% | −1.90 → −0.81 | 4.515 |
| 2021 | 74 | 4.676 | 4.840 | 4.841 | 65.42% | 66.54% | 66.51% | −1.08 → −0.08 | 4.397 |
| 2022 | 72 | 3.377 | 3.800 | 3.797 | 69.54% | 67.02% | 67.18% | +0.63 → +1.62 | 3.204 |
| 2023 | 71 | 3.782 | 3.683 | 3.679 | 69.76% | 69.96% | 69.86% | −1.21 → −0.52 | 3.467 |
| 2024 | 69 | 4.244 | 4.306 | 4.324 | 70.66% | 71.14% | 71.01% | −1.41 → −0.10 | 3.922 |
| **2025** | 62 | **3.826** | 3.975 | 3.985 | 68.04% | 68.39% | 68.39% | −1.79 → −0.93 | 3.728 |

### The bar

The bar is `recency_bar`, mirroring `ros_backtest.recency_bar` from
docs/DECISIONS.md 2026-11-18. It has three checks: win 2025 on pairwise AND
MAE; recency-weighted mean gain (half-life 2 seasons) > 0 on both;
sign-flip p of the weighted pairwise gain ≤ 0.05 / 2.

| candidate | 2025 pairwise | 2025 MAE gain | weighted pairwise | weighted MAE gain | p (pairwise) | p (MAE) | verdict |
|---|---|---|---|---|---|---|---|
| `+inherit` | +0.0035 | −0.149 | +0.0013 | −0.107 | 0.391 | 0.883 | **fails** all three |
| `resid` | +0.0035 | −0.159 | +0.0010 | −0.113 | 0.398 | 0.922 | **fails** all three |

These are post-hoc slices, for context only; they are not candidates and
the bar never judges them. MAE pooled over the folds:

| slice | rows | shipped | `+inherit` | Sleeper (post hoc) |
|---|---|---|---|---|
| RB / dnp | 46 | 6.449 | 6.391 | **4.883** |
| RB / out | 70 | 6.003 | 6.090 | 5.462 |
| TE / dnp | 36 | 2.250 | 2.366 | 1.664 |
| TE / out | 89 | 3.575 | 3.731 | 3.750 |
| WR / dnp | 116 | 3.848 | 3.905 | 3.637 |
| WR / out | 114 | 3.845 | 4.020 | 3.944 |

### Reading it honestly

- **The shipped model under-projects these backups by about 1.5 points**,
  and the candidate removes most of that bias. It still loses on MAE: the
  outcome is so spread out (RB 10th–90th percentile −3 to +17) that moving
  everyone's mean up by the average costs more on the duds than it gains on
  the booms. Start/sit barely moves (+0.1 to +0.4 percentage points of pairwise).
- **For Out and Doubtful the model already has the news.** The depth chart
  before kickoff (`depth_rank`) and `vacated_pickup` move the backup.
  `+inherit` adds nothing there.
- **For a Friday DNP the right Friday number barely moves.** Four in five of
  those starters play. Johnson's Friday price under `+inherit`, with the
  2025 fold's weights, is 0.223 × 24 × 0.118 ≈ **+0.6 points**. Even with
  Walker listed Out it is 0.993 × 24 × 0.118 ≈ +2.8. No honest
  Friday projection makes Johnson a must-add.
- **Sleeper's post-hoc number wins the RB-DNP slice by 1.6 MAE** because
  it was revised after Saturday's ruling. Both the miss and the fix are
  about *when* we learn the starter sits, not about how we project the
  backup.
- **Caveats.**
  - Backups with no stat line, such as inactives, are not scored. This is
    the same for every system.
  - Picking the backup by trailing snap share can name the wrong back in a
    committee.
  - Sleeper's historical numbers are post-hoc records, so they are context,
    not a scoreboard.
  - nflverse's `practice_status` is the week's last practice.

## 3. What would have caught Johnson, and what it would cost

**Owner decided (2026-11-18), [Kejjeh/gridiron#29](https://github.com/Kejjeh/gridiron/issues/29):**
item 1, the alert: "make ths automatic", "RB only (Recommended)", "Board
watch row", and then, on the trigger, "DNP or Questionable". Item 3, the
projection: unchanged (A). Built as the RB role-at-risk watch row
(`gridiron.openings.role_risks`, `gridiron.notable.RiskRow`; tests in
`tests/test_role_risk.py`):

- **Who:** an RB on 45%+ of his team's offensive snaps over his last 3
  games. Out or Doubtful stays with the role-opening watch.
- **Fires on**, once a week per starter, on the transition (rule #8):
  - DID NOT PRACTISE: the week's final report gives him a game status and a
    DNP; or
  - QUESTIONABLE — NOT OUT: a FRESH Sleeper pull tags him Questionable
    (a stale pull's tag is never read). A Questionable that later gains the
    DNP is carried with its first sighting, and the row says which fired.
- **Says:** the next back by snap share and his share, a committee flag, who
  holds him (pool with the waiver clock, yours, another roster). Never a
  move, a projection or a claim.
- **Week 10:** nothing at Fri 21:32 (no report row, Sleeper pull 21 h old);
  fires at **Sat 01:41** on the fresh Questionable tag, about 18 h before
  the backup was claimed (by Sat 19:52). DNP-only would have fired at
  Sat 23:26, too late.
- **False alarms** (`scripts/research/role_risk_sit_rates.py`; nflverse's
  final-report Questionable stands in for Sleeper's tag, whose history is not
  kept). Of 45%+ snap RB starters listed Questionable, 2019–2025:

  | subset | n | sat |
  |---|---|---|
  | all Questionable | 269 | 78 (29%) |
  | Questionable, did not practise | 51 | 31 (61%) |
  | Questionable, practised | 218 | 47 (22%) |
  | 2026 (weeks 1–10), all | 8 | 2 |

  So roughly 7 in 10 Questionable rows are for a starter who plays. This is
  a different subset from §2's `dnp` class (21.7% sat): that class is RB,
  WR and TE starters at a 60% RB bar, with any final-report DNP not ruled
  out, whether or not he was given a game status.
- **What counts as new** (review fixes, 2026-11-18): "newly Questionable"
  means the tag changed between fresh pulls, not that the week did. A tag
  carried from last week's game shows the row without NEW; a tag a fresh
  pull clears and later re-posts is NEW. A build that cannot read the
  report or the tags carries every first sighting, so the next good build
  does not re-fire. A declared bye shows no row (an unknown schedule is not
  a bye). A back whose snap counts are unknown is named as UNKNOWN, never
  as "nobody behind him" or a committee, and a back whose last game was
  for another team is not named for this one.

**Owner re-check pending (2026-11-18):** item 2, whether to treat a starter
dropping out of Sleeper's projections feed as a vacancy (a faster Out).
Tracked in [Kejjeh/gridiron#29](https://github.com/Kejjeh/gridiron/issues/29).
The facts each option rested on:

- **Friday DNP alert ("role at risk").** Across 2019–2025 about 40
  high-snap starters a season had a Friday DNP without an Out. About 22% of
  them sat; when one did, his RB backup gained a median +5 points. Over all
  NFL teams that is about 2.3 a week. The pool filter removes rostered
  backups.
- **Faster Out signal.**
  - The opening watch reads Sleeper's tag from the player map. Sleeper asks
    for that map at most once a day (`gridiron.sleeper`,
    `gridiron.freshness`), so a Saturday Out is read the next day.
  - Sleeper's weekly projections feed is fetched every build. A starter who
    drops out of it is already treated as a strong availability signal: in
    2026 weeks 4–5, 23 of 24 such players did not play (`advanced.py`,
    `FEED_MIN_PER_POSITION`).
  - How fast that feed reacts to a Saturday ruling is **not measured** here.
    The archives keep Sleeper numbers only for owned and pool players, not
    for the starter.
- **Serve the inherited projection anyway.** Not eligible under the bar.

