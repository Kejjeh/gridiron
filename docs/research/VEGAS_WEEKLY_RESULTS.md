# Betting lines and fantasy production: weekly test

Not approved as a draft-time feature. These are historical closing lines, including future weeks unavailable on draft night.

Fit 2020-2022 and test 2023; expand training for 2024 and 2025. Inputs are lagged previous-four-game team offense and opponent defense. The candidate adds own and opposing implied points. Test-year earlier games may enter rolling inputs after they happen; test-year outcomes never enter coefficient fitting. Team-position production uses core league half-PPR scoring and shared kicker weights. Missing lines or insufficient rolling history are excluded on common support.

| Position | Test team-games | Correlation | Baseline RMSE | With lines RMSE | Better seasons |
|---|---:|---:|---:|---:|---:|
| QB | 1632 | 0.334 | 7.314 | 7.079 | 3/3 |
| RB | 1632 | 0.282 | 9.124 | 8.960 | 3/3 |
| WR | 1632 | 0.269 | 11.550 | 11.321 | 3/3 |
| TE | 1632 | 0.123 | 6.727 | 6.697 | 3/3 |
| K | 1632 | 0.150 | 4.805 | 4.754 | 3/3 |

These are team-position totals, not individual player projections. No D/ST scoring test is claimed. The baseline does not contain every production feature; three season clusters, correlated team-games, multiple positions, and historical closing-line timing limit inference. No live weighting is promoted and no betting lines are displayed in the draft room.

Rerun: `$env:PYTHONPATH="src"` then `.venv/Scripts/python.exe scripts/research/test_vegas_weekly.py`. Raw schedules and statistics come from nflverse; input hashes and per-game forecasts accompany the report.