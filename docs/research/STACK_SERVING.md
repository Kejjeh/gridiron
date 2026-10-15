# The page serves the stack (2026-10-15)

**Decision.** For a QB, RB, WR or TE whose pre-kickoff Sleeper projection is
in the cache, the page's number is `stack_v1`: the mix of `advanced_v1` and
that number (`advanced_weights.json` `stack_form`,
docs/research/STACK_CALIBRATION.md) on the MEAN scale. Without it, and for
kickers and team defenses, the page keeps `advanced_v1`. A skill player a
complete, fresh Sleeper feed does not list is not projected at all (see
"Missing from the feed" below). The advanced mean is kept in the record
(`inputs.adv_mean`, `contenders.adv_v1`) so the weekly shoot-out keeps
grading the clean model beside the page. Scripts:
`scripts/research/stack_vs_page.py` (the gate), `feed_absence.py` (the
absence rule), `live_breakdown.py`, `live_reproduce_2026.py`,
`early_week_pattern.py` (the diagnosis).

## What we saw live (weeks 4-5, 2026)

The first two live weeks graded the page below its own baseline on the
owner's decision set (roster + free-agent radar, Sleeper >= 5, pairs within
position):

| board | pairs | page | baseline_v1 | Sleeper | stack (mix) | (baseline+Sleeper)/2 |
|---|---|---|---|---|---|---|
| week 4 (13:48 UTC Sunday) | 382 | 57.9% | 60.0% | 53.7% | 56.3% | 57.1% |
| week 5 (13:05 UTC Sunday, pre-PR#12 weights) | 395 | 58.5% | 62.5% | 65.1% | 59.5% | 64.0% |
| week 5 (16:37 UTC Sunday, shipped weights) | 317 | 61.5% | 66.9% | 65.0% | 65.3% | 68.1% |
| pooled, two distinct weeks | 777 | 58.2% | 61.3% | 59.5% | 59.5% | 60.6% |

MAE pooled: page 3.26, baseline 3.15, Sleeper 3.02, stack 2.95.

Three independent analyses, each checked by two reviewers who tried to
refute them:

1. **Where it lost.** TE (page 51.0% vs baseline 67.0% on 100 pairs from
   about ten players a week) and QB (56.0% vs 61.0%; fourteen players supply
   a quarter of all pairs). RB gained (75.0% vs 73.1%), WR was flat. Of the
   142 pairs the advanced layer re-ordered, it was right 59 times. The loss
   sits in the 5-10 Sleeper band (441 pairs: 50.8% vs 54.6%).
2. **It was not a pipeline defect.** The offline harness, fed the same
   weeks, reproduces every live number on the live pool to the decimal
   (page vs harness advanced: median 0.02-0.04 pts apart, none over 0.5 at
   any position); every input block was populated at build time; feature
   availability matched 2025's. On the broad 2026 universe (every skill
   player-week, 30,498 pairs) the same model BEATS the baseline (75.5% vs
   74.7%); the whole edge comes from players the owner never chose among.
3. **History did not predict it, and does not support gating.** Season-fold
   CV 2019-2025: the advanced layer beat the baseline in every week number
   on average (+1.1 pts in weeks 4-6, +0.8 later), at every position
   (2025 held out: QB +0.4, RB +0.2, WR +1.4, TE +2.0), in the 5-10 band
   (+1.7 on 19,299 pairs), and with the full adjustment (k = 1 beats
   k = 0.5 overall and at TE). The live deficits are 1.0-1.2 sigma once
   pairs are clustered by player (P(page <= baseline) 0.84-0.88). Shrinking
   or disabling the layer by position or tier would fit two weeks of noise
   against seven seasons of evidence: refused.

## The gate (rule #5)

`stack_vs_page.py`, season-fold CV 2019-2025 on week >= 4, Sleeper >= 5,
skill positions (421,457 pairs). `adv` is refit out of fold on the shipped
columns; `stack` is `blend_search.py`'s mix_0.5_cal, calibrated on inner
folds, exactly the served form.

| fold | pairs | baseline | Sleeper | adv (page today) | stack | (baseline+Sleeper)/2 |
|---|---|---|---|---|---|---|
| 2019 | 49,034 | 61.75% / 4.364 | 63.95% / 4.341 | 62.67% / 4.258 | **64.01% / 4.179** | 63.23% / 4.191 |
| 2020 | 66,283 | 61.03% / 4.438 | 63.93% / 4.122 | 62.00% / 4.321 | **63.76% / 4.018** | 63.04% / 4.157 |
| 2021 | 67,167 | 63.50% / 4.311 | 65.33% / 4.208 | 64.52% / 4.208 | **65.49% / 3.996** | 64.88% / 4.126 |
| 2022 | 60,026 | 62.66% / 4.101 | 64.71% / 3.833 | 63.55% / 4.040 | **64.76% / 3.720** | 64.18% / 3.886 |
| 2023 | 57,898 | 62.87% / 4.037 | 64.58% / 3.731 | 64.00% / 3.939 | **64.75% / 3.693** | 64.25% / 3.797 |
| 2024 | 60,236 | 62.72% / 4.128 | 64.35% / 3.791 | 63.25% / 4.039 | **64.55% / 3.787** | 64.00% / 3.885 |
| 2025 | 60,813 | 63.13% / 4.016 | 65.42% / 3.770 | 64.17% / 3.882 | **65.44% / 3.644** | 64.99% / 3.797 |
| mean | | 62.52% / 4.199 | 64.61% / 3.971 | 63.45% / 4.098 | **64.68% / 3.862** | 64.08% / 3.977 |

- stack vs the page today (adv): ahead on start/sit in 7 of 7 and on MAE in
  7 of 7; smallest ordering gain +0.75 pts (2023), mean +1.23; MAE −0.236.
- **The served form is the mean-scale one** (`stack_mean`: the same mix
  before its per-position median line). It orders players identically within
  a position (the line is monotone), so every start/sit figure above is its
  own, and it is ahead of adv on MAE in 7 of 7 folds by itself (3.977 vs
  4.098 on the mean; by position QB 6 of 7, RB 7 of 7, WR 5 of 7, TE 5 of 7).
  The median-calibrated number has the better error (3.862), but a median sits
  below the mean of a skewed outcome: summed into a lineup or P(win), or set
  beside an advanced_v1 number, it reads low. The first build that served it
  showed the cost: two waiver "upgrades" added a receiver on advanced_v1 alone
  over a rostered tight end whose stacked median (6.44) sat two points under
  his own advanced number (8.50). The shoot-out's `stack` contender keeps the
  calibrated form, so the ledger stays comparable week to week.
  At every position in every fold (QB +0.1 to +2.2, RB +0.6 to +2.3,
  WR +0.4 to +1.5, TE +0.1 to +3.6). In the 5-10 Sleeper tier 58.8% vs
  57.2% (127,895 pairs, ahead in 7 of 7 folds).
- stack vs Sleeper: 6 of 7 on ordering (2020 behind by 0.18), 7 of 7 on
  MAE. Unchanged from STACK_CALIBRATION.md; this is why the stack was a
  contender and not the page until now. The bar for SERVING it is beating
  what the page serves today, and that it clears everywhere.
- (baseline + Sleeper)/2, the best system on one live board: behind the
  stack on both metrics in every fold.

## Missing from the feed

Sleeper's weekly projection feed lists every player it expects to play,
about 400 a week. On the live boards, skill players the page projected at
5+ points who were NOT in that feed almost never played:

| | missing from the feed | did not play | listed in the feed | did not play |
|---|---|---|---|---|
| 2026 week 4 | 8 | 8 | 62 | 4 |
| 2026 week 5 | 16 | 15 (the one who played scored 1.7) | 58 | 1 |

From the other side, of 18,284 skill player-weeks in 2019-2025 that WERE
played (baseline 5+), 23 were missing from the feed (0.13%), none since
2021 (Sleeper's records for past weeks may carry later revisions, so the
historical rate is a floor). The page had been projecting about 9 points for
these players and offering some as pickups: on the first week-6 build, with
the mean scale fixed, the board still offered one.

Rule (`gridiron.dashboard.feed_absence`): when the feed was fetched before
the player's kickoff and within `STACK_SHADOW_MAX_AGE_HOURS`, and lists at
least `FEED_MIN_PER_POSITION` (20) players at his position (a thinner list is
a failed fetch, not news), a skill player it omits is ABSTAINED: no number,
the reason on the page, "verify his status in Sleeper". Never 0 (rule #11:
absence is not a bye, and not "Out" either); a roster player so marked
makes the lineup total partial, a free agent is counted as missing evidence.
On the week-6 build it removed 94 skill players; the eight highest-projected
were backup quarterbacks and receivers. Two of them were the schedule's
listed week-6 starters, and in both cases the schedule's quarterback field
was the previous game's starter: Sleeper projected the quarterback who had
started the last three games for one team and the regular starter back from
injury for the other.

## What it costs

- The page's number now leans on an outside source for most skill players,
  and on that source's coverage for whether a player is projected at all.
  The record says which system produced each number (`system`), the status
  line says how many got the stack, why the rest did not, and how many were
  not projected because the feed omits them, and the explain string shows
  all three steps (baseline -> advanced -> stacked with Sleeper's N).
- A Sleeper number fetched more than `STACK_SHADOW_MAX_AGE_HOURS` (36 h)
  earlier, or after the player's kickoff, is not stacked; the page keeps
  advanced_v1 for that player and says so. Sleeper revises through the week;
  the shadow is refreshed every 2 h by the pull step.
- Kickers and defenses stay on advanced_v1: their stack added nothing
  (K 57.24% vs 57.31%, DEF 62.02% vs 62.01%, `k_def_evidence`).
- The shoot-out now grades six systems: page, baseline_v1, adv_v1, Sleeper,
  blend, stack. Boards before 2026-10-15 carry adv_v1 only as `page`.

## Reading the live grade from here

The page will track the stack, so "page vs Sleeper" becomes the live test of
the only claim history leaves open (the 2020 fold), and "adv_v1 vs
baseline_v1" keeps testing the advanced layer on the owner's decision set.
One week is noise; the player-cluster bootstrap on 400 pairs has a standard
deviation near 3 points of start/sit. Nothing here is retuned from a live
week (rule #5); a change needs the gate above.
