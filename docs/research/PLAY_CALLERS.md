# Play-caller table, 2019–2026 (workstream B, built 2026-11-02)

`data/reference/play_callers.csv`: who ran each team, by week. One row per
(season, team, role, person, week range):

| role | meaning | where it comes from |
|---|---|---|
| HC | head coach | nflverse schedules (`home_coach`/`away_coach`), split by sourced in-season changes |
| OPC | the offensive play-caller: who called the plays in games | a public source that says so explicitly |
| OC | offensive coordinator, the title (not always the play-caller) | a public source |
| DC | defensive coordinator, the title | a public source |

Columns: `season, team, role, name, week_from, week_to, basis, source, quote`.
Team codes are nflverse's player-stats codes (LV for the Raiders in every
season). Names are the person's key, one spelling per person, and they
follow a coordinator who changes teams. **A missing row means unknown.**
A consumer never assumes the OC called plays.

## How each row was confirmed

- **Research.** One agent per season searched the web and opened each page
  it cited. Each row carries the URL and a verbatim quote of at most 30
  words that supports the person, the team, the season and the role. For
  OPC, the quote itself must say he calls (or called) the plays. Claims no
  page supported were listed as unsourced and left out.
- **Mechanical check** (`scripts/research/check_sources.py`). Each page was
  fetched again and stripped to text. A row passes when the quote is on the
  page (after normalising case, spaces, quote marks and dashes), the quote
  has the role's words (calling plays, a coordinator, a firing or an
  interim), and the person's surname is on the page. 805 of 810 sourced
  rows passed.
- **A second reader.** 2024's 101 rows were also re-read by a separate
  agent before the usage limit stopped that step. All 101 were confirmed.
- **By hand.** A person read the five rows the check could not confirm
  (`data/reference/play_callers_manual.json`). Four were accepted: two pages
  block scripts, and two quotes say the role in context. One was dropped:
  the 2019 Chargers' play-caller for weeks 1–8, whose cited PDF never names
  him.
- **Head-coach changes.** nflverse lists one head coach for some team-seasons
  that changed coaches mid-season (all of 2024 and 2025). 18 in-season
  changes in 2019–2025 were researched and each quote checked on its page.
  None was found in 2026 through week 8. Eleven match nflverse's weeks
  exactly. Seven are absent from nflverse: 2019 CAR, 2022 DEN, 2024 NYJ,
  NO and CHI, and 2025 TEN and NYG. Each one's first week fits the date in
  its quote.

`basis` names the confirmation for every row.

## Coverage

Team-seasons with at least one row, of 32:

| season | HC | OPC | OC | DC |
|---|---|---|---|---|
| 2019 | 32 | 32 | 29 | 30 |
| 2020 | 32 | 32 | 29 | 30 |
| 2021 | 32 | 31 | 31 | 31 |
| 2022 | 32 | 31 | 29 | 29 |
| 2023 | 32 | 31 | 31 | 29 |
| 2024 | 32 | 32 | 31 | 31 |
| 2025 | 32 | 32 | 32 | 31 |
| 2026 | 32 | 32 | 32 | 31 |

No confirmed play-caller: 2021 MIA, 2022 JAX, 2023 NYG. 127 of the 298 OPC
rows name the team's head coach.

The 2026 rows are as of 2026-11-02, through week 8. A change after that
appears only when the table is rebuilt.

## Rebuild

```
python -I scripts/research/check_sources.py RESEARCH.json CHECK.json --kind roles
python -I scripts/research/check_sources.py HC.json HC_CHECK.json --kind hc
PYTHONPATH=src python scripts/research/build_play_callers.py --research RESEARCH.json \
    --checks CHECK.json --manual data/reference/play_callers_manual.json \
    --hc-changes HC.json --hc-checks HC_CHECK.json
```

The research files are the research workflows' outputs. The builder refuses
to write the table when a check fails: overlapping head-coach or
play-caller weeks, a week outside the season, or an unknown team. Shared
coordinator titles may overlap; Minnesota had two DCs in 2020.
