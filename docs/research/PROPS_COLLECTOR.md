# Prop-market collector, market projection and book ranking (2026-11-02)

**Status: BUILT, RECORD-ONLY.** Workstream A of `docs/research/MODEL_ROADMAP.md`.
The owner approved the roadmap's four decisions as written on 2026-11-02.
Nothing here changes a number on the page. The market projection is a
contender in the weekly grade and nothing more, and this change has no
serving path. Serving it would need, at the least: ground rule 3 (two live
weeks in the grade), rule #5 (beating the shipped numbers out of sample),
identity rows the owner has reviewed, and a decision made before the
week-14 board, after which nothing on the page changes (decision 3).

## What is collected

- **Provider:** The Odds API, free plan (decision 4: a sanctioned free API
  tier, never a sportsbook scrape). Host `https://api.the-odds-api.com`.
- **When:** one snapshot per game. A game is **due** when
  `0 < kickoff − now ≤ 150 minutes` and it has no snapshot yet. Kickoffs come
  from the nflverse 2026 schedule, read through `gridiron.lineup.kickoff_index`
  (the board's own reader). A game without a usable kickoff time is never
  due. Each snapshot records its lead time. The cloud runs hourly at :41,
  so two or three runs fall inside every window and the first one takes the
  snapshot, 90 to 150 minutes before kickoff. That is before inactives are
  announced (90 minutes), which is a known limit.
- **Calls:** in a run with a game due, `GET /v4/sports/americanfootball_nfl/events`
  once, to find the provider's event id. The v4 guide, read 2026-11-02:
  "This endpoint does not count against the usage quota." Then, for each due
  game, `GET /v4/sports/americanfootball_nfl/events/{eventId}/odds` with
  `regions=us` and `oddsFormat=american`.
- **Markets:**
  - core, every game: `player_pass_yds`, `player_pass_tds`,
    `player_rush_yds`, `player_receptions`, `player_reception_yds`,
    `player_anytime_td`;
  - secondary, only with slack (below): `player_kicking_points`,
    `player_pass_attempts`, `player_pass_completions`,
    `player_pass_interceptions`, `player_rush_attempts`;
  - team defenses: no market.
- **Weeks:** 1 to 17 (the league's playoffs end in week 17). Forward only:
  the free plan has no historical backfill, so a week that is not
  collected is gone.

## Credits, with this season's numbers

Cost of one call = unique markets **returned** × regions. An empty response
costs nothing and is not a snapshot (the next run retries). So a core
snapshot costs at most 6 credits and a full snapshot at most 11. The free
plan gives 500 credits a month.

The months below are the ledger's: UTC, with the assumed reset on the 1st.
A Monday-night or Thursday-night game at 01:15 UTC belongs to the next UTC
day, so week 12's Monday game counts in December and week 17's Thursday
game in January.

| Period (2026 nflverse schedule, weeks ≤ 17, by UTC kickoff) | Games | Core credits (×6) | Slack of 500 | Secondary snapshots the slack buys (×5) |
|---|---|---|---|---|
| Nov 6–30 UTC (week 9's Thursday game to week 12's Sunday) | 57 | 342 | 158 | 31 |
| December (week 12's Monday game, weeks 13–16) | 62 | 372 | 128 | 25 |
| January 1–5 (week 17) | 16 | 96 | 404 | all 16 |
| One week (weeks 9–17: 15, 14, 13, 16, 14, 15, 16, 16, 16 games) | 13–16 | 78–96 | — | — |

The ledger (`data/research/cache/props/derived/credits.json`) records the
provider's own `x-requests-remaining`, `x-requests-used` and
`x-requests-last` headers after every call. Before each call:

- **Reserve.** The rest of the month's core snapshots need 6 credits per
  game until the quota resets. A call that would leave fewer credits than
  that is refused and counted. In a shortfall this skips the earlier games
  so the later ones stay funded.
- **Slack.** The five secondary markets are added only when the credits
  left after the core call and the reserve are at least 5. In practice the
  first games of each month get them.
- **Unknown credits.** With no reading of the current quota period (a
  lost ledger, a new month, or an event list that sends no headers, which
  is UNVERIFIED either way), one core call per run is allowed in order to
  read the headers. That call costs at most 6 credits without a check.
  Refusing it instead would never learn the count, and the collector would
  stop for good. No further call is made in that run until a reading
  exists. A paid response without headers is charged its full requested
  cost.
- **Reset day: UNVERIFIED.** The provider does not publish when the monthly
  quota resets. The ledger assumes the 1st (UTC) until it sees
  `x-requests-used` fall, and then uses the day of that reading, the
  latest the reset can have been, so the reserve errs large. If the
  provider's account page shows the day, set the optional repository
  variable `GRIDIRON_PROPS_RESET_DAY` (a bare day, 1 to 28). It wins over
  both.

## Storage, and why

The provider's terms, verbatim:

- prohibited: "Do not resell, repackage, or redistribute our data as a
  standalone data product", with "offering our data through your own API,
  data feed, downloadable files" given as an example;
- allowed: "Displaying our data in a UI, website, or mobile app, including
  for commercial use", "Calculating and displaying values you derive from
  our data", "Storing our data and retaining it indefinitely".

Attribution is not required. This repository, its run logs, its artifacts
and its Actions caches are **public**. A cache can be restored by any run on
the default branch, and by a fork's pull-request run through the base
branch. So:

| What | Where | Public? |
|---|---|---|
| Raw responses, one file per game (`<game_id>__<UTC stamp>.json`) and the per-book de-vigged table (`book_lines.csv`) | `data/research/cache/props/raw/season2026/weekNN/` (gitignored). In the cloud: one file, `.props-vault/raw.tar.gz.enc`, in the Actions cache prefix `gridiron-props-vault-`, encrypted with `openssl enc -aes-256-cbc -pbkdf2 -iter 200000` under the secret `GRIDIRON_ODDS_ARCHIVE_KEY` | **Never.** Never committed, never an artifact, never printed, never cached in the clear |
| Derived values: per game, per player (by gsis id) the expected stat line and the number of books behind each stat; counts of unmatched names; the credit ledger; the nflverse schedule copy | `data/research/cache/props/derived/` (`season2026/weekNN.json`, `credits.json`, `last_run.json`); in the cloud the cache prefix `gridiron-props-derived-` | Yes: values derived from the data, which the terms allow. No line, price, book or name |
| `market_v1` points per player | the board's decision record (`contenders`), so the dashboard artifact | Yes: derived |
| Aggregate book grades | the book ranking's output | Yes: derived |

- **No archive key.** If the secret `GRIDIRON_ODDS_ARCHIVE_KEY` is absent,
  nothing raw is kept between runs and the step summary says so. Each
  snapshot is still derived inside its own run.
- **Logs.** They print counts and credits only.
- **The key never leaves the process.** It comes from
  `gridiron.config.get_settings().odds_api_key` (rule #9). No URL is
  printed. An exception's text never reaches the log: failures carry a
  status code or an exception type, and they are raised outside the
  `except` block so no traceback chains the original. Every message passes
  `scrub`, which removes the `apiKey` parameter and the key in raw and
  URL-encoded form. `tests/test_pull_props.py` pins all of this.
- **One step holds the API key.** In `.github/workflows/props-collect.yml`
  it is the collect step. The archive key reaches `openssl` only as the name
  of an environment variable (`-pass env:`).

## Identity (decision 1, rule #3)

Odds providers send names, not ids.

- **The table.** A name becomes a gsis id only through the committed table
  `data/reference/odds_player_names.csv`, with columns `odds_name, team,
  gsis_id, basis, added`. The key is the name **exactly as sent** plus the
  nflverse team (`gridiron.props.IdentityTable`). Every later join is on
  gsis id alone.
- **The 32 teams.** The provider's full team names map onto nflverse
  abbreviations through the 32-row constant `gridiron.props.TEAM_ABBR`.
- **`exact` rows** are made by machine: `scripts/ingest/odds_names.py
  propose`. A row is written only when the name equals, string for string,
  the nflverse weekly-roster `full_name` of exactly one player on one of
  the event's two teams that week, and that player's position is
  consistent with every market the name was priced in. The tool never
  fuzzy-matches, never folds case and never strips a suffix. When the
  week's roster is not published yet, it proposes nothing. For every name
  it cannot match, it lists the candidate players on those two teams.
- **`reviewed` rows** are rows a person checked: the owner flips `exact` to
  `reviewed`, or adds a row by hand for a spelling the machine cannot match.
- **Abstain, never guess.** A name the table does not resolve on the
  event's two teams abstains and is counted: `unmatched`, or `ambiguous`
  when it maps to two players. Names appear only in local output, never in
  a public log or file.
- **The review rule.** Record-only numbers may run on `exact` rows. Anything
  that SERVES a number must wait for the owner's review of the table.
  `IdentityTable.serving_id` raises `UnreviewedIdentity` for any row that is
  not `reviewed` (`tests/test_props.py`). There is no serving path yet.
  This guard is the gate one would have to pass.
- **Re-derivation.** A row added later also resolves earlier snapshots:
  every run rebuilds the derived files from the raw archive (when it is
  kept) with the current table. A board already built keeps the numbers it
  froze.

**Seeding the table.** The table starts empty. The cloud archive can be
read only inside a workflow run, and a workflow must not print names, so
the provider's spellings come from your own machine:

1. Once props are posted for the coming week (Thursday to Saturday), run
   `PYTHONPATH=src python scripts/ingest/pull_props.py names` on your
   machine. For every game of the next week it asks one market, the
   anytime TD, which lists nearly every quarterback, back, receiver and
   tight end a book prices. That is 1 credit a game, about 15 a week, from
   the same monthly quota. It keeps the core reserve, never asks a game
   twice, and stores the lists under `data/research/cache/props/raw/names/`
   (gitignored). A list is never a snapshot: nothing is derived from it.
   It refuses to run inside GitHub Actions, whose logs are public.
2. Run `PYTHONPATH=src python scripts/ingest/odds_names.py propose`. It
   reads the name lists and any local snapshots. It appends the exact
   matches and prints the candidates for every other name, on your
   screen only.
3. Review the appended rows, mark the ones you checked `reviewed`, add rows
   by hand for the names it could not match, and commit the table.
4. Repeat for a later week only for new names (a trade, a call-up). The
   cloud re-derives every archived snapshot with the table as it is on
   `main`.

Kickers are not in the anytime-TD market. `pull_props.py names --kickers`
also asks the kicking-points market, for 2 credits a game. Use it on the
first run, because a game that already has a list is not asked again.
Kickers matter only where the secondary markets were bought (see Credits).

## The projection math

Everything below is in `gridiron.props`, which is pure (no network, no
clock, no writes).

1. **Parse.** Every outcome becomes a row: event, kickoff, book, market,
   player name as sent, side, point, American price.
2. **De-vig, per book.** The implied probabilities of a two-way market are
   scaled to sum to one (multiplicative). When a book offers more than one
   line, the one whose P(over) is nearest 0.5 is its main line. An
   anytime-TD "Yes" with a "No" is de-vigged the same way. A "Yes" alone is
   divided by (1 + that book's median overround over its two-way props in
   the same snapshot). A book with no two-way prop there abstains on it, and
   the player's entry says so.
3. **Consensus** per (player, market): the median across books of the line
   and of the de-vigged P(over), and the number of books.
4. **Expected stat** from (line, P(over)):
   - **Yardage and kicking points:** a gamma with one fitted shape *k* per
     stat. The scale is solved so P(X > line) = P(over), which gives
     mean = k · line / Q⁻¹(k, P(over)).
   - **Counts:** a negative binomial with one fitted size per stat, or a
     Poisson where history shows no overdispersion, solved the same way. A
     whole-number line pushes, so the push is taken out.
   - **Anytime TD:** expected touchdowns λ = −ln(1 − P(yes)).
   - **Out-of-band lines:** a main line with P(over) outside 0.05–0.95, or
     an anytime-TD probability outside 0.005–0.95, abstains.
5. **Points.** The league's scoring is linear (half-PPR, no bonuses), so
   E[points] = points(E[stats]), scored by `gridiron.scoring.fantasy_points`
   (rule #2). Expected TDs split into rushing and receiving in proportion to
   expected yards. That split moves no point while both score 6, and it is
   tested to matter if they ever differ.
6. **Stats no market covers** come from the player's own nflverse per-game
   rate this season (weeks up to the board's evidence boundary): lost
   fumbles, two-point conversions, a receiver's rushing, a missing receiving
   market. The record lists them per player (`market_v1_detail.from_rates`).
7. **Abstentions.** These players get no market number:
   - a player whose position-core market is missing (QB: passing yards;
     RB: rushing yards; WR/TE: receptions or receiving yards);
   - a player with no game yet this season, whose rates are unknown, never
     zero;
   - a player whose priced, scored market could not be read: no own-rate
     stand-in is passed off as the market's view.
8. **Kickers.** The kicking-points market is read with the books'
   definition (3 per field goal, 1 per extra point). It is split into
   league points by the kicker's own season: makes are scaled so their book
   points equal the market's expectation, and the distance mix and the
   misses come from his per-game rates. Scored by
   `gridiron.scoring.kicker_points`. **UNVERIFIED:** the books' definition
   is the standard one at US books, not confirmed for each book.

### The fitted parameters (`src/gridiron/props_params.json`)

`scripts/research/prop_distribution_fit.py` fits these on the 2019–2025
regular seasons:

- **Expectation:** Sleeper's pre-game projection of the stat (the rotowire
  feed cached under `data/research/cache/sleeper_proj_<season>/`), joined
  to gsis ids through the one crosswalk. 99.97% of relevant projections
  resolve. It is scaled per stat so its mean matches the outcomes'.
- **Outcome:** the nflverse weekly box score. A player who took an
  offensive snap (a kicker: a special-teams snap) and has no box-score row
  counts as a zero (5,996 such rows).
- **Population:** the players a book posts the prop for, set by a floor on
  the projection (for example, receiving yards ≥ 15).

**Gamma shapes.** The fit is least squares on log quantiles over the band
0.35–0.65, where de-vigged main lines sit. A relative quantile error there
is exactly the relative error of the implied mean. Matching the median
alone was tried first and rejected: passing yards are nearly symmetric but
wide, which no gamma is. The median-only shape was 26, and it missed the
held-out 25/75% quantiles by 0.21.

| Stat | Shape | n | Outcomes' mean / median | In-sample band error (= implied-mean error) | Held-out coverage error, band | Held-out, 0.25/0.75 too | Moment shape | Censored-MLE shape |
|---|---|---|---|---|---|---|---|---|
| passing_yards | 13.91 | 3,386 | 1.013 | ≤ 3.0% | 0.065 | 0.065 | 8.76 | 6.38 |
| rushing_yards | 2.03 | 7,671 | 1.166 | ≤ 3.0% | 0.025 | 0.045 | 1.56 | 1.20 |
| receiving_yards | 1.63 | 18,551 | 1.207 | ≤ 4.4% | 0.039 | 0.064 | 1.28 | 0.77 |
| kicking_points | 5.01 | 3,563 | 1.046 | ≤ 5.0% | 0.062 | 0.063 | 3.76 | 2.88 |

**Held-out seasons keep their own scale.** Each held-out season is scored
with the shape fit on the other six. Only the shape transfers to a market,
because a line brings its own location. Sleeper's season-level bias moved
the passing-yards scale between 0.90 and 1.03, and scoring that against the
shape would blame the shape for it.

**Count models.** The size is fit by moments. The negative binomial is used
only where its held-out log loss, over five lines around the expectation,
is lower in more than half of the scored seasons.

| Stat | Model | Size | n | Variance / mean | NB better held out |
|---|---|---|---|---|---|
| passing_tds | Poisson | — | 3,386 | 0.87 | 0/7 |
| attempts | negative binomial | 23.0 | 3,386 | 2.41 | 7/7 |
| completions | negative binomial | 24.3 | 3,383 | 1.88 | 7/7 |
| passing_interceptions | negative binomial | 16.6 | 2,500 | 1.04 | 3/5 (no 2019–20 projections) |
| carries | negative binomial | 10.0 | 7,136 | 2.23 | 7/7 |
| receptions | negative binomial | 10.7 | 19,027 | 1.35 | 7/7 |
| fg_made | Poisson | — | 3,563 | 0.89 | 0/7 |

**Anytime TD.** λ = −ln(1 − p) assumes a Poisson. On 24,780 player-weeks
with projected TDs ≥ 0.1 it is calibrated within 0.005 in every quintile:
stated 0.112, 0.168, 0.230, 0.305, 0.436 against observed 0.114, 0.165,
0.228, 0.302, 0.440.

**Examples (P(over) → mean):**

| Line | P(over) | Expected |
|---|---|---|
| 245.5 passing yards | 0.52 | 254.9 |
| 62.5 rushing yards | 0.50 | 74.3 |
| 54.5 receiving yards | 0.48 | 64.9 |
| 4.5 receptions | 0.55 | 5.17 |
| 1.5 pass TDs | 0.45 | 1.52 |
| anytime TD | 0.40 | λ 0.511 |
| 7.5 kicking points | 0.50 | 8.03 |

**Caveats, in the direction they push:**

- **The shapes are if anything too wide for a market.** A projection is a
  weaker expectation than a market, so the outcomes spread more around it.
  The yardage means are therefore slightly overstated.
- **One-sided anytime-TD Yes.** Books' margins on a lone "Yes" are usually
  larger than on their O/U props, so de-vigging it with the O/U overround
  overstates P(yes) and λ.
- **The book ranking will measure both** once weeks are collected.

## The record-only contender, and the grade

- **On the board.** When this week's derived file exists, the board adds
  `market_v1` to `contenders` (`gridiron.dashboard.contenders_block` via
  `gridiron.props.market_block`), with `market_v1_detail` (counts,
  abstention reasons, identity basis, rate-filled stats).
  - It applies only to a snapshot taken **before the player's kickoff**.
    A file for another season or week is no file.
  - Nothing reads it to project, rank, gate or recommend.
    `tests/test_market_contender.py` renders the board with and without the
    file and finds every page field identical.
  - In the cloud, the dashboard workflow restores the derived cache
    read-only, in one step. The restore path is the collector's save path,
    string for string, because a cache's version is computed from its
    paths.
- **Which games the grade sees.** The graded board is the last one built
  before the first kickoff of the week's main day
  (`gridiron.grading.main_slate_kickoff`). A game's snapshot is taken 90
  to 150 minutes before its own kickoff. So the market can be graded only
  on the games snapped before that board:
  - the Thursday game, and the Sunday games in the first kickoff slot;
  - in a week with a morning London game, only that game and Thursday's;
  - never the late-afternoon, Sunday-night or Monday games.

  Grading those games would mean comparing the market's later number with
  the page's earlier one. That is not a like-for-like comparison, so it is
  left out.
- **In the grade.** `scripts/weekly/grade_week.py` lists `market_v1` in
  `SYSTEMS`, but the main shoot-out does **not** require it. The market
  covers only players with props, so requiring it would shrink the page's
  own grade to those players. It is scored in a second pass on exactly the
  players it covers, with the page and Sleeper on the same players:
  - columns `market_v1_pairwise`, `market_v1_mae`, `mkt_n`, `mkt_pairs`,
    `mkt_page_*` and `mkt_sleeper_*`;
  - for kickers apart, `k_mkt_*`.
- **The ledger stays backward compatible.** Rows written before these
  columns existed, including the committed
  `data/ledger/grades/season2026.csv`, load, upsert and add up with the new
  columns blank. The page's report card does not show the market:
  comparing its pairs-weighted rate with the page's full-population rate
  would mislead.

## Book ranking

`scripts/research/book_ranking.py` runs on the local raw archive, or a
decrypted copy of it. Per book and market, and for a `consensus` row, it
reports:

- the line error against the actual stat: mean absolute error and bias;
- the Brier score and log loss of the de-vigged P(over) (pushes and void
  props left out);
- the mean distance from the consensus, in line and in P(over).

With fewer than four collected weeks it prints "needs 4+ collected weeks"
and still runs. The cloud archive can be decrypted only inside a run, so a
manual dispatch of the collector with **rank_books** ticked runs the
ranking there and writes the aggregate grades (derived values) to the step
summary. Firm conclusions wait for the offseason, with a full season in
hand.

## What the owner must do

1. Get the free API key at https://the-odds-api.com (the free plan, 500
   credits a month).
2. Add it to `.env` as `GRIDIRON_ODDS_API_KEY=...` (rule #9; `.env` is
   gitignored). This is for local runs and for seeding the name table.
3. In GitHub: Settings → Secrets and variables → Actions → **Secrets**. Add
   `GRIDIRON_ODDS_API_KEY` with the same key.
4. In the same place, add the secret `GRIDIRON_ODDS_ARCHIVE_KEY`: a long
   random string, for example the output of `openssl rand -base64 48`.
   Keep a copy offline: without it the archive cannot be read again.
5. Under **Variables**, add the repository variable
   `GRIDIRON_PROPS_ENABLED` = `true`. The workflow does nothing until then.
   Optionally add `GRIDIRON_PROPS_RESET_DAY` (see Credits).
6. **Merge this change to `main` first.** GitHub runs a scheduled workflow
   only from the default branch, so the hourly collector does not exist
   until `props-collect.yml` is on `main`. A manual dispatch also needs
   the file on the default branch before GitHub offers it. A run started
   on another branch keeps its caches on that branch, where the dashboard
   on `main` never sees them.
7. Seed and review the name table (above). Commit only rows you are happy
   with. Mark a row `reviewed` only after you have checked it.

## When something breaks

Collecting is the time-critical part, and the board is the product, so
every failure below stops as little as it can and says so in the step
summary (by type and count, never by name):

- **The archive will not decrypt** (for example, the archive key was
  changed). The job fails at the decrypt step before it spends a credit.
  It keeps failing, and it never overwrites the archive it could not read.
  To recover, put the old key back. Otherwise delete the
  `gridiron-props-vault-` caches (Actions → Caches) and accept the loss of
  the raw archive. The derived values are kept either way.
- **The name table is unreadable** (for example, a typo in a hand edit on
  `main`). The run still collects, every name abstains, and nothing
  archived is re-derived until the table reads again, because an empty
  table would erase the derived values.
- **A snapshot cannot be derived.** It gets a marker entry, so it is not
  bought again. A later re-derivation keeps whatever entry the game has.
- **The board meets a malformed derived file.** `market_v1` is empty or
  records its error, and the page is built unchanged
  (`tests/test_market_contender.py`).
- **The book ranking fails.** It prints the error type only.

## Files

| Path | What |
|---|---|
| `src/gridiron/props.py` | Parse, de-vig, consensus, distributions, identity table, derived entries, market projection |
| `src/gridiron/props_params.json` | The fitted shapes and sizes, with their evidence |
| `scripts/research/prop_distribution_fit.py` | The fit (`--save` rewrites the JSON) |
| `scripts/ingest/pull_props.py` | The collector; subcommands `rederive`, `summary` and the local `names` |
| `scripts/ingest/odds_names.py` | `propose` and `check` for the name table (local only) |
| `data/reference/odds_player_names.csv` | The committed name table (header only until seeded) |
| `.github/workflows/props-collect.yml` | The hourly cloud collector |
| `scripts/research/book_ranking.py` | The book ranking |
| `tests/test_props.py`, `tests/test_pull_props.py`, `tests/test_odds_names.py`, `tests/test_market_contender.py`, `tests/test_book_ranking.py`, `tests/test_workflow_inputs.py` | The tests (fictional players, books and ids) |
