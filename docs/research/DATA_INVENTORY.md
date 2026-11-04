# Free data inventory (probed 2026-11-02)

Every free source the model roadmap (`MODEL_ROADMAP.md`) leans on, as probed
on 2026-11-02 (the Monday of week 8, before the Monday-night game). The
nflverse numbers come from `nflreadpy` through the research cache
(`data/research/cache/nflreadpy/`, gitignored). Everything listed for
2018–2025 is already downloaded there.

## nflverse

| Source (`nflreadpy`) | Seasons used | 2026 now | Used for | Notes |
|---|---|---|---|---|
| `load_pbp` | 2018–2025 | 20,841 plays, through week 8 | Roles, red zone, scrambles, pace, 4th downs, returns | `xpass`/`pass_oe` included. Team codes of the time (OAK in 2018–2019); the block runner maps them to today's. |
| `load_player_stats` (week) | 2018–2025 | 8,295 rows, week 8 | Box scores; the evaluation rows | `player_id` is the gsis id. |
| `load_snap_counts` | 2018–2025 | 10,073 rows, week 8 | Snap shares | Keyed by PFR id, so joined through the crosswalk. |
| `load_ftn_charting` (free subset) | 2022–2025 | 18,374 plays, week 8 | Screens, sneaks, box counts, interception-worthy throws, play action, no-huddle | Every flag filled on every row, 2022–2026. Blocks built on it are scored on the 2022–2025 folds. |
| `load_participation` | 2016–2025 | **none** ("Season must be between 2016 and 2025") | Personnel, players on the field, coverage shell, pressure | On regular-season pass plays: `route` (the targeted receiver's route) 81–86% filled; `defense_man_zone_type` none in 2016–2017, 84–85% in 2018–2022, 99.6–99.9% in 2023–2025; `was_pressure` 84–85%, then 100% from 2023; `offense_players` 100%. **Prior-season use only for 2026.** |
| `load_injuries` | 2018–2025 | 2,459 rows, week 8 | Practice status, game designations | A pre-game source, published before the week's games. |
| `load_depth_charts` | 2018–2025 | 731,119 timestamped rows | Starters and depth rank | Weekly layout through 2024; timestamped snapshots from 2025 (`A.depth_from_charts` and the block runner both handle it). |
| `load_rosters_weekly` | 2018–2025 | 19,865 rows, week 8 | Reserve lists, roster adds | |
| `load_team_stats` (week) | 2018–2025 | 238 rows, week 8 | Team volume and pace | |
| `load_ff_opportunity` (weekly) | 2018–2025 | 2,284 rows, week 8 | Expected fantasy points | Already in the shipped set (`xfp_*`). |
| `load_nextgen_stats` | 2018–2025 | 255 passing rows, week 8 | Separation, time to throw, aDOT | Week 0 rows are season totals; reading them for a week's features would be a leak. |
| `load_pfr_advstats` (week) | 2018–2025 | 857 rushing rows, week 8 | Yards before and after contact, pressure, drops | Rows only above a volume threshold. |
| `load_schedules` | 2018–2025 | all 272 games | Lines (spread, total, moneylines), roof, surface, rest days, coaches, starting QBs | `temp` and `wind` are observed at the game, a stand-in for a forecast. The `*_qb_id` columns name who actually started, a post-game fact. |
| `load_officials` | — | week 1 only | — | Not used. |
| `load_trades`, `load_contracts`, `load_draft_picks`, `load_combine`, `load_players` | all | current to 2026 | Transactions, draft capital, rookie priors | Static or dated tables, not weekly snapshots. |

`load_ff_playerids` fails with a 403. The crosswalk comes instead from the
same DynastyProcess file as a CSV on raw.githubusercontent.com
(`data/research/cache/crosswalk_db_playerids.csv`, the file
`scripts/ingest/pull_week.py` uses).

## Other free sources

| Source | What | Status | Terms |
|---|---|---|---|
| Sleeper projections | Weekly projections for every position, 2019–2025 (research cache) and live 2026 | In use | Public read API. The player map is requested at most once per 24 hours. |
| Open-Meteo | Observed weather (`archive-api.open-meteo.com`) and archived forecasts (`historical-forecast-api.open-meteo.com`) | Both reachable from the cloud session | Free tier for non-commercial use only: under 10,000 calls a day, 5,000 an hour, 600 a minute and 300,000 a month. Data under CC BY 4.0. |
| The Odds API (free "Starter") | Player props per event (`/v4/sports/americanfootball_nfl/events/{id}/odds`) | Needs a free key from the owner | 500 credits a month. An event-odds call costs the number of markets returned × regions; the event list costs nothing ("does not count against the usage quota"). Raw data may not be redistributed "as a standalone data product", including as "downloadable files". Displaying it and showing derived values are allowed. Details: `PROPS_COLLECTOR.md`. |
| ParlayAPI (free) | 1,000 credits a month, 48-hour lookback | **Not usable here** | Its pricing page lists "public display, redistribution, resale, sublicensing" as needing a separate signed agreement. This repo and its pages are public. |
| Kalshi (public API, no key) | NFL player-prop ladders from about Oct 2025, hourly price history | **Not usable** (checked 2026-11-04) | Developer Agreement v1.1, section 3: API use "is expressly limited to facilitating a members own trading on the Exchange; all other usages are disallowed"; 3.1 forbids "collecting, caching, aggregating, or storing" API data except for that; 4.1 requires Kalshi membership. Probe downloads deleted. |
| Polymarket (public API, no key) | NFL prop markets from the Jan 2025 playoffs, price history | **Not cleared** (checked 2026-11-04) | Its terms bar data-mining tools and "harvest or otherwise collect information" from its interfaces, and grant only a personal, limited licence. Not a sanctioned bulk-history source (owner decision 4). Probe downloads deleted. |
| OddsPapi (free key) | 250 requests a month; history from Jan 2026, up to 3 books a call | **Open: needs the key in a session** | Terms forbid redistributing data "as a standalone product" only. Whether NFL player props are in its history is unchecked. |

## What this means for the roadmap

- Participation does not exist for 2026, so QB-05 (in part), QB-10, WR-03
  (in part), WR-05, WR-07, TE-02, TE-04 and DEF-05 (in part) test as
  prior-season tendencies.
- FTN-based ideas (QB-09, WR-09, DEF-07, and RB-08's box counts) have four
  scored seasons, not seven.
- Weather ideas can use archived forecasts instead of observed weather. The
  shipped `+line` block used observed weather, the only kind the schedule
  carries.
- `route` in participation is the targeted receiver's route, one per play.
  Routes run by every receiver come from `offense_players` on dropbacks, as
  the shipped `+routes` block built them.
- Props have no free backfill. The market workstream learns only from the
  weeks collected after the owner adds a key.
