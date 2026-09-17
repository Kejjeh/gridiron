# Weekly report

**2026 week 2 (pregame) — usage/box scores through week 1 (lag 1w)**
generated 2026-09-17T14:02:55+00:00

> NO PROJECTION MODEL SHIPPED — this report ranks nothing. Columns are measured usage and market lines only (rule #5: no feature ships without beating the full baseline out-of-sample).

## Input freshness

```
sleeper_league  FRESH    as-of 2026-09-17 13:03 UTC  covers wk2  pulled 1h ago
injuries        FRESH    as-of 2026-09-17 13:03 UTC  covers wk2  pulled 1h ago
schedules       FRESH    as-of 2026-09-17 13:03 UTC  covers wk18  pulled 1h ago
weekly_stats    FRESH    as-of 2026-09-17 13:03 UTC  covers wk1  pulled 1h ago
snap_counts     FRESH    as-of 2026-09-17 13:03 UTC  covers wk1  pulled 1h ago
crosswalk       FRESH    as-of 2026-09-17 13:03 UTC  covers —  pulled 1h ago
```

All inputs current.

## Roster

| lineup | player              | pos | nfl | opp  | implied | availability                                                                                                    | g | pts   | ppg   | snap% | tgt | tgt_sh | car | opp_n | y/tgt | catch% |
|--------|---------------------|-----|-----|------|---------|-----------------------------------------------------------------------------------------------------------------|---|-------|-------|-------|-----|--------|-----|-------|-------|--------|
| START  | D'Andre Swift       | RB  | CHI | MIN  | 26.5    | Questionable (Ankle); practice: Limited Participation in Practice                                               | 1 | 31.9  | 31.9  | 0.57  | 1   | 0.04   | 18  | 19    | 10    | 1      |
| START  | Jahmyr Gibbs        | RB  | DET | @BUF | 24.5    | no designation on the wk2 report                                                                                | 1 | 31.1  | 31.1  | 0.74  | 5   | 0.13   | 29  | 34    | 6     | 1      |
| START  | Trey McBride        | TE  | ARI | SEA  | 19      | no game-status designation (Not injury related - resting player); wk2 practice: Did Not Participate In Practice | 1 | 20    | 20    | 0.8   | 13  | 0.35   | 0   | 13    | 7.31  | 0.69   |
| START  | Nico Collins        | WR  | HOU | CIN  | 24.5    | Questionable (Hamstring); practice: Limited Participation in Practice                                           | 1 | 17.7  | 17.7  | 0.72  | 10  | 0.27   | 1   | 11    | 7.5   | 0.7    |
| START  | Drake Maye          | QB  | NE  | PIT  | 23.5    | no designation on the wk2 report                                                                                | 1 | 12.82 | 12.82 | 1     | 0   | 0      | 7   | 7     |       |        |
| START  | Rhamondre Stevenson | RB  | NE  | PIT  | 23.5    | no designation on the wk2 report                                                                                | 1 | 12    | 12    | 0.85  | 6   | 0.19   | 18  | 24    | 7.33  | 0.83   |
| START  | Garrett Wilson      | WR  | NYJ | GB   | 20.5    | no designation on the wk2 report                                                                                | 1 | 10.9  | 10.9  | 0.75  | 7   | 0.3    | 0   | 7     | 11.29 | 0.86   |
| START  | Chris Boswell       | K   | PIT | @NE  | 18      | no designation on the wk2 report                                                                                | 1 | 8     | 8     | 0     | 0   | 0      | 0   | 0     |       |        |
| START  | Jayden Reed         | WR  | GB  | @NYJ | 24      | no designation on the wk2 report                                                                                | 1 | 3.5   | 3.5   | 0.57  | 7   | 0.17   | 0   | 7     | 2.86  | 0.43   |
| START  | PIT DST             | DEF | PIT | @NE  | 18      | n/a (team defense)                                                                                              |   |       |       |       |     |        |     |       |       |        |
| BENCH  | Devaughn Vele       | WR  | NO  | @BAL | 19      | no designation on the wk2 report                                                                                | 1 | 16.4  | 16.4  | 0.91  | 9   | 0.17   | 0   | 9     | 7.67  | 0.78   |
| BENCH  | Dontayvion Wicks    | WR  | PHI | @TEN | 23.25   | no designation on the wk2 report                                                                                | 1 | 14.3  | 14.3  | 0.91  | 4   | 0.18   | 0   | 4     | 18.25 | 0.5    |
| BENCH  | Caleb Douglas       | WR  | MIA | @SF  | 15.5    | no designation on the wk2 report                                                                                | 1 | 11.9  | 11.9  | 0.91  | 7   | 0.26   | 0   | 7     | 13.43 | 0.71   |
| BENCH  | Jakobi Meyers       | WR  | JAX | @DEN | 21.5    | Questionable (Thumb); practice: Limited Participation in Practice                                               | 1 | 11.2  | 11.2  | 0.65  | 2   | 0.1    | 1   | 3     | 20    | 1      |
| BENCH  | Quentin Johnston    | WR  | LAC | LV   | 25      | no designation on the wk2 report                                                                                | 1 | 2.7   | 2.7   | 0.78  | 6   | 0.22   | 0   | 6     | 2.83  | 0.33   |

## Column meanings

- `snap%`, `tgt`, `tgt_sh`, `opp` — OPPORTUNITY (fast-moving, real in-season signal).
- `y/tgt`, `catch%` — EFFICIENCY (slow-moving prior; a two-week delta is noise, rule #6).
- `pts`/`ppg` — league scoring via `gridiron.scoring`, the one implementation (rule #2).
- `implied` — market implied team total for the report week; blank when no line is posted.
- `availability` — the DESIGNATION and its source. Not a start/sit verdict (rule #11).