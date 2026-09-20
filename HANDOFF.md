# HANDOFF

State: **milestones 1, 2 and the cloud sync are merged to main (`8fbf468`).
Milestone 3 — the weekly decision board — and milestone 4 — the Game Day
screen — are on `claude/weekly-dashboard`, open as draft PR #4 based on
main. Not merged. The head is named in the PR body and in the verification
section below.** The per-finding history of the five review passes lives in
`docs/DECISIONS.md` and the PR body, not here.

The desktop five-minute sync is **installed but its scheduled task is
DISABLED**, by the owner, and must stay disabled. Refresh runs in the cloud:
`.github/workflows/sleeper-sync.yml` (hourly, gated on the
`GRIDIRON_CLOUD_SYNC_ENABLED` repository variable and on the repo being
private) and `.github/workflows/dashboard-artifact.yml`, which renders the
board AND the Game Day page and uploads both as one private artifact.
Nothing depends on a PC being awake.

No identifiers in this file: the league id and the owner's Sleeper handle live
in `src/gridiron/league_config.py`, and every rendered roster artifact — the
weekly report, the board, the Game Day page, the decision archive — is
gitignored. Nothing here names a player the owner holds.

## The product, in two pages

**The board** (`scripts/weekly/dashboard.py`) is the pregame page. It leads
with what to do, ranked by deadline, each action carrying its real kickoff, a
legal backup and its own ACTIONABLE / WITHHELD verdict; then the inputs and
the gate they drive, what changed since the previous frozen page, the
start/sit comparisons, a short acquisition shortlist, the roster projections,
and the matchup, where the closed-form P(win) sits behind a disclosure marked
UNCALIBRATED and ranks nothing. It freezes a decision-time archive that later
grading reads instead of re-projecting.

**Game Day** (`scripts/weekly/gameday.py`, module `gridiron.gameday`) is the
Sunday page, linked from the top of the board and built beside it. On a phone
it answers, in this order: am I ahead and how current is that; who on each
side is yet to play, playing, final, or unknown; what can I still legally do;
what changed since my last reliable snapshot; what did the board actually
advise and what is still unproven. Every number is a platform actual from
Sleeper's matchup rows — kickers and defenses included, zero and negative
included, a commissioner override included — and a value the platform did
not send is UNKNOWN, never 0. The starters' sum is reconciled against the
platform total and any difference is disclosed, never edited away.

**Game status is observed, never inferred.** A schedule gives an expected
kickoff and nothing else. Whether a game started, is in overtime, was
suspended or ended comes only from Sleeper's per-game status feed
(`livesync.refresh_game_status`, recorded beside the league snapshot), and
only while that feed is within its cadence: a stale feed keeps finals and
cancellations, which are monotone, and demotes every other status to
UNKNOWN. Elapsed hours, a zero and a missing row are never read as final, as
bye, or as not playing. Game status, injury designation, roster eligibility
and the kickoff lock are four columns, shown as four. Where the feed and the
schedule disagree the disagreement is stated and the safer side decides
legality.

**Legal means Sleeper would accept it now.** During games the page re-judges
the board's archived actions by player ID: both players proven unlocked by
the schedule, not under way per the feed, still on the roster and still where
the advice left them; anything the board WITHHELD pregame stays withheld;
pickups are never game-day moves. When every starter has kicked off the page
says no legal change remains. Fresh scores display on their own freshness
while a stale recommendation is withheld.

**The pregame record is joined, never rebuilt.** The newest archived board
for the same season, week, league id and roster id is the record; a record
that predates those tags is matched on season and week and says so; none
means "no pregame record" in so many words. An action counts as decision-time
evidence only if the record was written before that action's own deadline. A
designation that moved after the record is reported with the note that the
later status cannot show what was knowable before kickoff. A bench player
outscoring a starter is descriptive until both games are final, and the live
lineup is an observation, never proof that advice was followed. No live win
probability is computed; the board's pregame P(win) is shown as pregame
context and is not updated by the score; no projection is scaled by the clock
or added to points already earned.

**Delivery is the same private artifact, plus one tap.** The Game Day page is
a single self-contained file. Opened from the artifact it is a dated
SNAPSHOT of what the cloud build cached; reloading the file fetches nothing.
Tapping Refresh sends three read-only GETs to Sleeper from the browser (NFL
state, the week's matchups, the status feed — CORS is open, verified from a
`file://` page in a real headless browser, see Verification), after which the
page is LIVE: it polls only while visible and only while a game can still
move (two minutes during play, ten minutes ahead of a kickoff), backs off on
every failure, keeps the last good data under a STALE banner when a refresh
fails, discards any response dated before the one already applied, stops at a
week rollover, and lists changes like for like against the last reliable
snapshot with a lowered total named a correction. The page's Content Security
Policy lets it connect to Sleeper's host and nothing else, run only its own
script, and load no other resource. No hosting, no auth, no new dependency.
The cloud crons are unchanged and are not real-time; the hourly snapshot is a
starting point, the tap is the refresh.

**A freshness label is not a gate**, box scores gate on COVERAGE rather than
age, kickoff locks are three-valued (LOCKED / OPEN / UNKNOWN) and only OPEN
makes a player movable, a team absent from a week is UNKNOWN unless the
schedule declares a bye (nflverse declares none, so real bye weeks freeze
those players), a player who scores 0 this week is not worth 0, and not on a
roster is not addable. Each of these is pinned in the guarantee tables below.

## Read this first

PR #2 — the dependency-free offensive scoring repair plus the standing league
settings verification — was reviewed independently and merged to main as
`cd9d9ef`. **main has been merged into this branch**, so this branch now
contains that work rather than a second copy of it. The reconciliation kept
main's version of every shared file (it was a strict superset everywhere but
two `scoring.py` docstrings); `scoring.py`'s executable code is byte-identical
to main's. There is one scoring implementation, and it is the reviewed one.

The dependency question is **closed**: the owner approved `nflreadpy`,
`pandas` and `pyarrow` for this project's environment on 2026-09-17. No other
new direct dependency, and no paid API. `docs/review/MILESTONE1_DEPENDENCY_REVIEW.md`
is kept as the record of what each one buys.

## What you can actually run

```
PYTHONPATH=src python scripts/ingest/pull_week.py     # network, read-only
PYTHONPATH=src python scripts/weekly/report.py        # offline, prints
PYTHONPATH=src python scripts/weekly/report.py --write --anonymous
PYTHONPATH=src python scripts/verify_league_settings.py

PYTHONPATH=src python scripts/sync/sleeper_sync.py run       # network, ~1s
PYTHONPATH=src python scripts/sync/sleeper_sync.py run --if-due
PYTHONPATH=src python scripts/sync/sleeper_sync.py status    # offline

PYTHONPATH=src python scripts/weekly/dashboard.py --write            # offline; HTML+JSON+archive
PYTHONPATH=src python scripts/weekly/dashboard.py --write --anonymous --no-archive
PYTHONPATH=src python scripts/weekly/dashboard_scenarios.py --screenshot   # synthetic pages
PYTHONPATH=src python scripts/weekly/dashboard_scenarios.py --only partial_schedule

PYTHONPATH=src python scripts/weekly/gameday.py --write             # offline; Game Day HTML+JSON
PYTHONPATH=src python scripts/weekly/gameday_scenarios.py --screenshot --browser   # A-F, synthetic
```

Game Day writes `data/outputs/dashboard/week{NN}_gameday.html` (plus `.json`
and a `gameday_latest` pair) next to the board, so the board's "Game Day →"
link and the page's "← pregame board" link work from the same folder. To
open it on a phone: download the `dashboard` artifact from the newest
successful *Weekly dashboard artifact* run, unzip, open
`gameday_latest.html`. It shows the cached snapshot; tap **Refresh** once
for live scores. The rendered pages are gitignored: they name the owner's
players.

The dashboard writes `data/outputs/dashboard/week{NN}_dashboard.html` (plus
`.json` and a `dashboard_latest` pair) and the decision archive under
`data/ledger/decisions/season{YYYY}/`. All of it is gitignored: it names
the owner's players. `--now` renders as of a given instant (locks and
freshness), which is how the scenarios and tests pin a Saturday.

## Getting the pages onto a phone

`.github/workflows/dashboard-artifact.yml` runs on GitHub's Linux runners,
refreshes the Sleeper snapshot and the game-status feed, pulls the week's
nflverse frames, renders the board and then the Game Day page, and uploads
both as a **private, repo-scoped artifact** (14-day retention). It carries
the same two gates as the Sleeper sync: it does nothing unless the repository
variable `GRIDIRON_CLOUD_SYNC_ENABLED` is exactly `true` (it already is), and
it refuses if the repository ever stops being private. The Game Day step is
`continue-on-error`, so a missing Game Day page never costs the board.

Cadence is rule #8 shaped, in UTC: Wednesday 08:41, Friday 22:41 and Sunday
14:41, plus manual dispatch with an optional week — passed through `env:`,
never interpolated into a shell command, and refused unless it is a bare
ASCII integer in 1-22. These are best-effort GitHub crons: runs get delayed
and dropped under load, which is why the board gates on input age and why
the Game Day page carries its own refresh instead of relying on a schedule.

**Option A — the artifact download — is the delivery**, unchanged: four taps
on a phone, no service, no hosting, no auth, private by the repository's own
access control. What is new is that the downloaded Game Day file can refresh
itself from Sleeper's public read API with one tap, because Sleeper's API
sends `Access-Control-Allow-Origin: *` and a `file://` page is allowed to
fetch it. Committing the page to a branch or pushing it to a file host were
both declined: each widens where roster data lives, and neither was
authorized.

### Carrying history across runs, and what that retention is really worth

A hosted runner starts from an empty checkout, and the decision ledger is
gitignored because the records name the owner's players (rule #10). So until
now every scheduled cloud run was a FIRST run: "since the last snapshot" had
nothing to compare against and reported no change, and the frozen page died
with the container that made it, which makes grading it next week impossible.

Two steps now sit either side of the render. Before it, `carryover.py restore`
pulls prior records out of a private store; after it, `carryover.py publish`
puts this run's back. The store is a **GitHub Actions cache** keyed
`gridiron-decisions-<run id>` with a `gridiron-decisions-` restore prefix —
the repository's own private storage, needing no token, no service and no new
dependency.

**The same two steps carry the last-good INPUTS**, and they have to. Carrying
records alone left the degraded path broken: both refresh steps are
`continue-on-error`, but on a clean runner the cache they would fall back to
does not exist, so a run whose refresh failed had nothing to render FROM. It
exited 2, wrote no page, failed the artifact upload — and the step summary
said "Built." The carry now moves `manifest.json` and the frames beside it,
bounded to 24 files / 64 MB and refused past 21 days, and every restored entry
keeps the `as_of` of the pull that actually fetched it while being marked
CARRIED FORWARD for this run. That mark is `Entry.error`, the existing
"latest attempt failed" mechanism, so freshness turns the source STALE and the
gate withholds every action resting on it. The result is the page a bad day
should produce: last known, dated to when it was known, recommending nothing.

**A carried manifest is untrusted input down to the field.** The third pass
validated the manifest — season, dating, age, future stamps — and then handed
each entry straight to `ing.Entry(**raw)`, which is not a validator: a
dataclass checks which keys it was given and never what they hold. So an
entry whose `path` was `["bad"]` constructed perfectly and raised `TypeError`
at `Path(entry.path)`, taking down the restore of every sound source beside
it; an entry whose `rows` was `"many"` or whose `as_of` was unparseable got
all the way into the rendered cache and raised inside `Manifest.freshness`,
half a render later. Worst of the three, an ABSOLUTE `path` was basenamed for
the copy but written into the manifest verbatim, and because `dir / "/abs"`
discards the left operand in pathlib, `Manifest.file()` then handed the
renderer a file that never travelled with the carry. Every field is now read
through a total reader before the entry is constructed, a failure rejects
that entry alone with its reason, and `as_of` is passed through byte for byte
because it is the one field the carry must never restate.

**And collisions are judged by destination, not by entry name.** The first
version of that rule compared the carried entry's NAME against the local
cache and then copied by FILENAME, which are not the same check: a carried
`weekly_stats` pointing at `shared.json` displaced a local `sleeper_state`
pointing at the same file, and the local entry — current `as_of`, empty
`error`, so nothing withheld on it — was left describing bytes it had never
seen. A carried entry whose destination the cache already holds is now
refused and names the local owner it would have hit.
A refresh that DOES succeed overwrites the file and clears the mark for that
source, so a run where Sleeper works and nflverse does not carries exactly the
sources that failed. A carried file never displaces one this run pulled.

**Stated honestly, because it is easy to mistake for a backup: it is not
durable storage.** An Actions cache entry is evicted after 7 days without a
read, and evicted early when the repository crosses its 10 GB cache limit. A
gap in the schedule longer than that breaks the chain. When it breaks the page
says it has no previous snapshot rather than reporting that nothing changed —
which is the failure this whole section exists to prevent. The run artifact is
the copy that survives independently, with its own 14-day retention. Neither
is a backup, and the carry is bounded on purpose: 40 records per season, none
older than 45 days.

A restored file is **input, not history**. `gridiron.carryover` validates each
candidate before it may count: the filename must agree with the contents (the
write time, an 8-hex digest of the record, and the week the name files it
under against the week inside it), the season must match, a record stamped in
the future is refused outright — it is the one thing that could outrank the
page being built — and anything past the age limit is left behind. A file that
fails is reported and left exactly where it is; nothing is repaired, and
nothing is deleted.

Every field is read **totally**: a record whose `season` is the string
`"invalid"` is rejected with a reason, not raised on. That was a real defect —
`int(blob.get("season") or 0)` threw `ValueError`, so one hand-edited or
half-written file aborted the whole restore and a run with a perfectly good
previous page carried nothing. One bad file now costs only itself.

Refresh failures in the cloud are **not fatal and never promote last-good data
to current**. The Sleeper and nflverse steps are `continue-on-error`, so a
degraded page still renders from the last good cache — and that page says so,
withholds the actions resting on the failed source, and drops their
imperatives. A run that freezes nothing restamps nothing, so a restored record
keeps the time it was written with.

**The two cadences are different jobs and must stay different jobs.**
`sleeper_sync.py` is five Sleeper GETs — league, users, rosters, current-week
matchups, NFL state — a few hundred KB, run every five minutes. `pull_week.py`
is nflverse frames plus the 16 MB Sleeper player dump, run daily at most; that
dump is on a once-a-day cadence because Sleeper's own documentation asks for
it, and the sync has a test that fails if it ever reaches for it.

### Installing the five-minute sync on the Windows desktop

From the checkout, in PowerShell (no admin, no stored password):

```
powershell -ExecutionPolicy Bypass -File .\scripts\windows\gridiron_task.ps1 `
  -Action Install `
  -RepoRoot "C:\Users\Joshua\Documents\Claude\Projects\Football-Live" `
  -Python   "C:\Users\Joshua\Documents\Claude\Projects\Football\.venv\Scripts\python.exe"
```

`-Action Status` reports both the task's view (last/next run) and the sync's
own view (last success, next due, failure streak, drift). `-Action Uninstall`
removes it. Install is idempotent — re-running replaces, never duplicates.

The task runs with the **Interactive** logon type — no stored credential, no
elevation — on a **time-based repeating trigger starting one minute after
install**, so installing it while already signed in works immediately instead
of waiting for the next logon. Install prints the first run time and the
`NextRunTime` Windows reports, so the two can be compared on the spot.

**Explicit requirement:** this is a scheduled task, not a service. It runs
only while that desktop is powered on and that user is signed in. That is the
price of not storing a password and not asking for admin, and it is the right
trade for a tool whose worst failure is five minutes of staleness that the
status command reports honestly.

**No AI runs on the schedule.** The task starts `python.exe`. Nothing wakes
Claude or Codex per refresh and no tokens are spent.

`pull_week.py` fetches nflverse weekly stats, snap counts, schedules and
injuries, the dynastyprocess id crosswalk, and a read-only Sleeper snapshot
into `data/research/cache/season2026/` (gitignored). `report.py` renders from
that cache and never opens a socket — there is a test that breaks the socket
and re-renders to prove it.

The report prints, per rostered player: lineup slot, opponent and market
implied total, the injury designation with its source, and measured usage
(snap %, targets, target share, carries, opportunities) plus league points
through the last admissible week. It ranks nothing and recommends nothing.

## The six invariants, and the tests that hold them

These are the ways a weekly tool lies. Each is pinned. The first four are
about time; the last two came out of Astra's review of `28ae09a`.

1. **No future leakage.** A report about week N reads weeks ≤ N-1 before the
   slate and ≤ N once it is COMPLETE. The boundary is
   `WeekContext.evidence_boundary`, derived from the report week and the
   phase — *not* from `max(weeks in the cache)`, which is what the code did
   before and which meant re-rendering week 2 in week 6 quietly read weeks
   3–5. `test_a_later_week_in_the_cache_cannot_leak_into_an_earlier_report`
   builds the same report twice, once from a cache stopping at week 1 and
   once from a cache also holding weeks 2–3 at ten times the volume, and
   asserts the numeric columns are identical.
   Weeks past the boundary are listed as WITHHELD in the report, and a
   re-render states that its injury designations and market lines come from
   the latest pull — they are after-the-fact, not what was known at kickoff.

2. **A failed refresh cannot fake freshness.** `Manifest.record_failure`
   records the failure and touches nothing that describes data: `path`,
   `rows`, `weeks` and `as_of` keep pointing at the last pull that actually
   returned rows. Previously a failure was recorded as a fresh entry stamped
   `now`, so a dead network aged as a current pull *and* dropped the good
   cached file over a transient 503. A source whose latest refresh failed can
   never read FRESH; it renders STALE with the failure reason and still shows
   its cached rows. Cache writes are atomic (temp file + `os.replace`), so a
   pull that dies mid-write cannot leave a truncated parquet behind.

3. **Season rollover is a refusal, not a note.** A cache season, or a cached
   Sleeper state season, that disagrees with the requested season exits 2
   rather than rendering last season's roster under this season's header.
   Sleeper reports week 0 between seasons; that exits 2 too, unless `--week`
   is passed to look back at a finished week.

4. **Coverage gaps are visible.** Freshness carries the full covered-week
   set, so a hole inside the range gets its own line (`covers wk1-5 (no
   wk3)` plus a GAP degradation). "Covers through week 5" off weeks
   {1,2,4,5} is true, and every season total built on it is short by a week.

5. **Every source the report reads declares its freshness.** The rule is
   consumption, not convenience: if a value from a source reaches the page,
   that source's as-of line is on the page too. `sleeper_players` was being
   read for the live injury designation, the NFL team, the position and the
   sleeper→gsis overlay, and was missing from `SOURCES` — so a month-old
   player dump with a *failed refresh recorded against it* rendered under
   "All inputs current" and exited 0 under `--fail-on-degraded`.
   It now has an injury-sensitive cadence (24h; 6h on gameday, waiver day
   and designation day), because `injury_status` is live rather than
   week-keyed: the value carries no date, so the age of the pull it rode in
   on is the only thing that distinguishes a month-old "Questionable" from a
   current one. A designation from a non-FRESH pull renders as
   `… — STALE designation from an out-of-date player pull …; NOT a current
   status`, and `AvailabilityNote.current` is False.
   `test_every_source_the_report_reads_declares_its_freshness` re-derives the
   consumed set from `report.py`'s own syntax (every `read_frame`/`read_json`
   /`file` call with a literal name) *and* compares it against a list
   maintained by hand — looping over `SOURCES` to check `SOURCES` is what let
   the omission hide.
   `designation_fresh` defaults to False and `build_report` fails closed when
   the player dump is absent from the freshness list: a caller that never
   established currency does not get the benefit of the doubt.

6. **Points are never published off a frame that cannot support them.**
   `fantasy_points` reads an absent key as zero. That is *right* for a null
   cell — nflverse leaves a running back's `passing_interceptions` null and
   the null genuinely means zero picks — and a wrong number for a missing
   *column*: drop `passing_interceptions` from the parquet and every
   quarterback silently scores a point per pick too high. On the committed
   fixture that is Drake Maye at 15.82 instead of 12.82, under "All inputs
   current", exit 0.
   `scoring.scoring_coverage()` checks a frame's column names against the
   league rules. It is alias-aware (a cache predating the nflreadpy 0.1.x
   rename scores through its legacy columns and is *recorded*, not flagged),
   weight-aware (a term weighted zero cannot move a total, so a missing
   column behind it is not a defect), catches partially present multi-column
   terms, and never reads a value. A gap BLANKS `pts`/`ppg` for the affected
   positions only — the offensive and kicking halves fail independently, and
   usage columns are untouched — names itself in the degraded block, and
   fails `--fail-on-degraded`.
   The check runs in **both** places: the puller persists the verdict to
   `Entry.missing_columns` (a stderr warning dies with the run that printed
   it; the manifest is what the next reader has), and the report re-checks
   the frame actually in hand on every render, which is the only thing that
   catches a cache edited or written by an older schema since. A recorded
   schema defect is deliberately *not* an error: the fetch succeeded, so
   `error` stays empty, the file stays readable and the as-of stands.
   `build_report(scoring=...)` is a required argument — a default would let a
   caller publish points without ever having looked at the schema behind them.

The three honesty invariants from milestone 1 still hold: blank is never zero,
a missing schedule renders `?` and never BYE, and "not on this week's injury
report" reads differently from "no injury report loaded".

## Verification — Game Day milestone (2026-09-20)

Exact head and changed files are in the PR body. Measured on this head:

| check | result |
|---|---|
| `python scripts/ci/smoke.py` | PASS — 27 imports, 30 contract files |
| `run_summary.py -- python -m pytest` | **597 passed** (531 at `4559d81`) |
| `pytest tests/test_gameday.py tests/test_gameday_feed.py tests/test_gameday_cli.py` | 66 passed, all through `build_gameday` or the production CLIs; the browser drive runs when headless Chromium is present and is SKIPPED with reason otherwise |
| `gameday_scenarios.py --screenshot --browser` | 9 scenarios, every page fits at a measured 360 px client width in a 375 px frame (widest element ends at 348 px); PNGs for `pregame`, `mixed`, `injury_after` and a keyboard-focus capture are in `docs/review/gameday/` |
| **real network path, in a real browser** | a page rendered from the real cache, opened as `file://` in headless Chromium: one Refresh → mode LIVE, three read-only GETs answered 200, statuses FINAL / PLAYING / NOT STARTED / SUSPENDED / UNKNOWN as in Sleeper's feed that afternoon (a real suspended game), 38 changes against the 74-hour-old snapshot; a second Refresh → "no change". The proxy CA was added to Chromium's NSS store for the run; TLS verification stayed on. Scores and names from that run are not recorded anywhere |
| `curl` with `Origin: null` against `/v1/state/nfl`, the league matchups and `/schedule/nfl/regular/2026` | `access-control-allow-origin: *` on GET and on the OPTIONS preflight; the status feed's one real response was inspected before it was relied on (statuses seen: pre_game, in_game, complete, suspended, canceled) |
| `python -m compileall src/gridiron scripts` | clean |

### Acceptance matrix

| brief | behaviour | where it is held |
|---|---|---|
| A. pregame, one legal improvement with alternative and deadline | Saturday noon: one AVAILABLE swap with "act before …" and a backup; scores level; every starter NOT STARTED | `test_pregame_offers_one_legal_improvement_with_deadline_and_backup`, `docs/review/gameday/pregame*.png` |
| A. same inputs stale → withheld, scores still display | injuries refresh FAILED: the board withheld, Game Day offers nothing, `sleeper_league` FRESH and the score shown | `test_a_board_that_withheld_its_actions_offers_none_while_the_score_still_shows` |
| A. conflicting kickoff → withheld | damaged schedule rows: UNKNOWN locks, no action offered | `test_a_conflicting_kickoff_means_the_move_cannot_be_shown_legal`, `test_a_locked_or_unknown_player_is_never_offered` |
| B. ahead 15.46, mixed slate, their RB/K/DST unplayed | 62.10 vs 46.64; "they still have … RB, K, DST"; a real zero, a kicker, a negative DST, an unsent value shown UNKNOWN with the reconciliation stating the totals cannot be tied out; no odds anywhere on the score card | `test_mixed_slate_is_ahead_15_46_with_their_rb_k_dst_yet_to_play`, `test_platform_values_are_taken_as_sent_…`, `mixed*.png` |
| B. empty / zero / negative / K / DST / custom total | empty slot named and scoring nothing; override is the total and named with the scored total beside it | `test_an_empty_slot_…`, `test_a_commissioner_override_…`, `test_a_negative_actual_and_an_override_are_shown_as_sent` |
| C. fresh → refresh → correction → 429 → recovery → older response → malformed → rollover | correction applied and the lowered total named a correction; 429 keeps last good under STALE with a 60 s backoff; recovery clears it; an older-dated response is discarded; a malformed body keeps last good; week 4 stops polling with week-3 scores standing | `test_refresh_applies_corrections_keeps_last_good_and_discards_older_responses` (headless browser, local fixture server) |
| D. injury after kickoff, unknown status, postponed, overtime, suspended | designation Questionable → Out reported "cannot show what was knowable"; a game missing from the feed is UNKNOWN "not a bye and not a final"; the word "postponed" passes through as UNKNOWN with the word shown; in_game four hours after kickoff is PLAYING; suspended is SUSPENDED; nothing is called settled | `test_injury_after_kickoff_unknown_status_postponed_and_overtime`, `test_a_game_in_progress_four_hours_after_kickoff_is_still_playing`, `injury_after*.png` |
| E. week rollover, lineup change, archive untouched, no archive | week 4 finds no week-3 record and diffs nothing across the boundary; a second week-4 render lists the score and lineup change; archive files byte-identical after two more Game Day renders; no ledger → "no pregame record" | `test_week_rollover_joins_no_record_and_diffs_only_its_own_week`, `test_the_archived_pregame_record_is_byte_identical_after_the_page_is_built`, `test_no_archive_is_a_clear_absence` |
| F. keyboard, 375 px, real refresh vs fixture | Refresh is a native button, focusable, with a solid focus outline (`mixed-keyboard-focus.png`); 375 px fit measured on all nine scenarios; the fixture drive proves what the page does with each response and the real-endpoint run above proves the network path | browser drive + the row above |
| review: leakage, double counting, false remaining counts, cache corruption, unsupported live claims, XSS, fan-out, privacy | names and points never reach stdout or the run summary; no projection is added to earned points; remaining counts are from observed status only; the page writes nothing to the cache; every unsupported state is UNKNOWN in words; upstream names go through `textContent` and HTML escaping, the embedded JSON is `\u`-escaped, and a nonce CSP allows one host; three GETs per refresh and none until the first tap | `test_upstream_names_cannot_inject_markup_or_script`, `test_the_page_may_only_talk_to_sleeper`, `test_no_live_win_probability_…`, `test_the_stdout_summary_names_no_player`, the browser drive's request log (18 hits for 6 refreshes) |

**Not executed:** no real phone (the fit is a headless-browser measurement
and the keyboard check is programmatic focus, not a screen reader); the
hosted workflow has still never run, so the Game Day step, the feed refresh
in the cloud and the input carry of `game_status.json` are untested on a
runner; the real-endpoint browser run was done from this container through
its egress proxy, not from a phone browser; `ruff` is not installed here.

## Verification (all green, 2026-09-17 — main, before this branch)

| check | result |
|---|---|
| `python scripts/ci/smoke.py` | PASS — 16 imports, 20 contract files |
| `python scripts/ci/run_summary.py -- python -m pytest` | **303 passed** (was 279; main: 239) |
| `node --test scripts/research/warroom/draftroom_logic.test.js` | 10/10 pass |
| `scripts/verify_league_settings.py` (live) | exit 0, 55 constants, no drift |
| `golden_run.py --target weekly_report` A/B | 1/1 byte-IDENTICAL |

Red-before/green-after for the two review blockers: the five new tests that
pin them were run against `28ae09a` in a detached worktree and **all five
fail there**; `test_a_null_stat_cell_is_not_a_missing_column` passes on both,
which is correct — it pins behavior that already worked and had to survive
the change.

The golden A/B here is a **reproducibility** check, not a refactor check: two
independent runs against the same cache produce byte-identical output. The
time-boundary and failed-refresh changes are deliberate behavior changes, so
A/B across them would be meaningless and was not claimed.

Live read-only validation: `pull_week.py` against nflverse and Sleeper, and
`report.py` rendering the real week-2 report. No league transaction of any
kind — `gridiron.sleeper` is GET-only by construction and
`tests/test_sleeper.py::test_module_is_read_only` pins it.

The review fixes were validated against the **real** cache with no new league
query: the live 1118-row, 150-column nflverse frame passes `scoring_coverage`
cleanly (no false positive), and the real render now reports
`sleeper_players STALE … pulled 7h ago, over the 6h gameday limit` — a
Wednesday is waiver day, so the tightened limit applies — where before that
source had no line at all. Three roster rows carry a labelled stale
designation that previously printed as current. Every regression test builds
its own synthetic cache from the committed fixtures.

## Provenance and coverage

| source | via | covers | note |
|---|---|---|---|
| weekly player stats | `nflreadpy.load_player_stats` | wk1 | wk2 lands after the slate |
| snap counts | `nflreadpy.load_snap_counts` | wk1 | PFR-keyed, joined through the crosswalk |
| schedules + market lines | `nflreadpy.load_schedules` | wk1–18 | `total_line` is the implied-total input |
| injuries | `nflreadpy.load_injuries` | wk1–2 | designation + practice status |
| id crosswalk | dynastyprocess `db_playerids.csv` | — | Sleeper's own `gsis_id` overlays gaps only |
| league / rosters / matchups | Sleeper public read API | wk2 | no auth, no writes |
| player dump (`sleeper_players`) | Sleeper public read API | live | live `injury_status`, team, position, gsis overlay; 24h cadence, 6h on gameday/waiver/designation days |

**Missing sources, named rather than worked around:** no DST scoring (nflverse
weekly data is player-level; a DST row carries blank points and an explicit
"n/a (team defense)" note, never 0.0). No blocked-kick mapping — no blocked
kick occurred in the reconciliation week, so whether the league scores it as a
miss is UNVERIFIED and deliberately unmapped. No projections, no rankings, no
start/sit or waiver output: the rule #5 gate is unmet.

Dependency versions in the project environment: pandas 3.0.5, pyarrow 25.0.1,
nflreadpy 0.1.5, polars 1.44.2 (transitive, via nflreadpy), numpy 2.4.6,
pytest 9.1.1, Python 3.11.15.

## Owner data boundary

The rendered weekly report names the owner's actual players, so
`data/outputs/week*_report.*` and `weekly_report_latest.*` are now gitignored
and the previously committed week-2 pair is untracked at this branch's tip.
`tests/test_hygiene_no_roster_in_repo.py` fails if a roster-bearing file is
staged again.

Two things deliberately NOT done: history is not rewritten (the untracked pair
still exists in this branch's earlier commits, and rewriting shared history is
a bigger hazard than the exposure), and the league-wide files already tracked
on main — the draft board, ADP and competition tables — are left alone. They
are keyed by player id and expose no roster; an id column is not a roster, and
treating it as one would ban the public files rule #10 exists to keep.

One item for the owner: some of those pre-existing tracked files under
`data/outputs/` do contain the owner's Sleeper display name in a column. That
predates this branch and is unchanged by it. Widening was avoided; whether to
narrow it is the owner's call.

## Next — the release checklist

1. **Astra review of this head.** Render both pages from the live cache
   (`dashboard.py --write` then `gameday.py --write`), open
   `gameday_latest.html`, tap Refresh, and check the mode line reads LIVE
   with today's statuses. Run `gameday_scenarios.py --screenshot --browser`
   and compare against `docs/review/gameday/`.
2. **Dispatch the dashboard workflow twice, after review.** Nothing needs
   setting. The second run should report "since the last snapshot" against
   the first, `restore-inputs` should lay files down including
   `game_status.json`, and the artifact should hold `gameday_latest.html`
   beside `dashboard_latest.html`. Neither summary may name a player.
3. **Owner phone check on a Sunday:** download the artifact, open the Game
   Day file, tap Refresh, leave it open through a kickoff and a final, and
   confirm the exposure line and the lock counts move with the games.
4. **First graded week** (unchanged): after week-3 finals land, grade the
   week-3 archive by hand with `observed=` for the moves actually made.
5. **DST scoring** and **the rule #5 gate** for anything beyond the
   baseline, unchanged from before.

Merge, deploy, workflow activation and any league write remain the owner's
and the release's decisions; nothing in this branch performs them.

## Live sync: the guarantees, and where each is held

| Guarantee | How | Test |
|---|---|---|
| Snapshot and manifest are always a coherent pair | Every publish writes an **immutable generation** `sleeper_league_<stamp>.json` and repoints the manifest at it atomically; no file is ever rewritten in place | `test_a_successful_sync_publishes_a_generation_the_manifest_points_at`, `test_an_unfinished_generation_is_never_read` |
| One writer at a time, **including the weekly puller** | An **OS-held lock** on a persistent, never-unlinked file: `fcntl.flock` on POSIX, `msvcrt.locking` on Windows, both non-blocking. No create/delete, no age, no takeover — a file-existence lock cannot be made race-free (two processes can both see it abandoned and both replace it), and the kernel drops an OS lock when the holder dies. `sync_once` holds it across state read → fetch → publish → state save, so a slow run cannot finish last and write stale counters over a newer one; the publish path it uses (`_publish_locked`) does not re-acquire, because the lock is not reentrant | `test_two_processes_cannot_both_hold_the_lock` and `test_a_killed_holder_releases_the_lock_with_no_timeout_and_no_reclaim` (real child processes), `test_state_is_read_inside_the_lock_so_a_slow_run_cannot_overwrite_a_newer_one`, `test_the_lock_is_not_reentrant_and_publish_does_not_nest_it` |
| A concurrent writer's entries are never clobbered | The manifest is **re-read inside the lock** and only `sleeper_league` is touched. `pull_week.py` never owns `sleeper_league` at save time — the committed on-disk entry always wins, including over the one that run published, because the 16 MB player fetch is long enough for several syncs to land | `test_a_sync_during_a_slow_player_fetch_is_not_rolled_back_by_the_puller` (real interleaving, executed) |
| The manifest is published atomically | `Manifest.save()` writes a pid-unique temp and `os.replace`s it. A truncated manifest is worse than a stale one: it is the only thing naming the published generation, so losing it orphans every snapshot file. Every `.part` name carries the pid so two writers never share a scratch file | `test_a_manifest_write_that_dies_leaves_the_previous_one_whole` (injected `OSError`), `test_state_writes_do_not_share_a_scratch_file` |
| Failure never loses data | Fetch → validate → *then* write. Nothing opens for writing until a whole payload passed every check | `test_a_failed_sync_preserves_the_last_good_snapshot` (9 failure modes) |
| Fail closed before publication, **both writers** | League id; season on the league object *and* NFL state, with a **missing** season refused rather than assumed; `scoring_settings` and `roster_positions` present; team count; owner present and owning a roster; duplicate roster ids; non-object list members; regular-week matchup roster ids unique and equal to the roster set (a count check accepts twelve copies of one roster). `pull_week.pull_sleeper` runs the same gate | `test_fail_closed_on_a_defective_payload` (8 cases), `test_the_weekly_puller_validates_before_publishing` |
| Sequential GETs are not claimed to be atomic | The read window is timed and recorded in the snapshot; NFL state is read at both ends and a mid-read rollover discards the snapshot and retries once | `test_a_week_rollover_mid_read_discards_the_snapshot`, `test_the_read_window_is_recorded_not_assumed_away` |
| Settings drift is detected, sticky, never auto-verified | Watched keys fingerprinted and diffed each sync; drift is recorded, shouted, and **stays flagged** through later clean syncs until a human clears it. `SETTINGS_VERIFIED` is never written (rule #1) | `test_settings_drift_is_reported_and_never_marks_anything_verified`, `test_settings_drift_stays_sticky_across_later_clean_syncs` |
| Restart-safe | State is a separate file; a corrupt one resets counters but never blocks a sync | `test_state_survives_a_restart_and_a_corrupt_state_file_does_not_block` |
| No league mutation | `gridiron.sleeper` is GET-only by construction; the sync adds no endpoint | `test_sleeper.py::test_module_is_read_only` |

## Weekly decision dashboard: the guarantees, and where each is held

| Guarantee | How | Test |
|---|---|---|
| Offline, from the cache alone | The CLI reads only the manifest's files; no module under it imports a network client | `test_the_dashboard_renders_from_the_cache_with_no_network` (socket broken) |
| Every source read declares its freshness on the page | `SOURCES` is re-derived from the script's own `read_frame`/`read_json`/`file` calls and compared to the freshness table | `test_every_source_the_dashboard_reads_declares_its_freshness` |
| Projections are transparent | `Projection.components` (volume × rate × multiplier = points) and `inputs` (games, observed share, prior, weight) are rendered under every number; the mean is their sum | `test_the_mean_is_the_sum_of_its_printed_components` |
| Projections are chronological | `build_evidence(through_week=...)` cuts BEFORE aggregation; the evaluation predicts each week from the ones before it, and perturbing later weeks moves nothing earlier | `test_evidence_is_cut_before_aggregation_...`, `test_each_week_is_predicted_only_from_the_weeks_before_it` |
| Rule #6 is the model's shape | Usage share shrunk toward the measured role's prior with the documented n0; efficiency is the positional prior × line multiplier; the player's own efficiency is never read | `test_usage_is_shrunk_toward_the_role_prior_...`, `test_the_line_moves_efficiency_not_volume` |
| Abstain, never guess | mean=None with a reason for: no admissible box score, DST, no pooled QB/K prior, unresolved id, incomplete scoring columns; withheld (0, model mean kept visible) for bye / Out / IR | `test_it_abstains_*`, `test_withheld_keeps_the_model_mean_visible`, `test_missing_inputs_abstain_everywhere_and_fill_nothing_in` |
| Rule #11 | Questionable / Doubtful are flagged on the row and NEVER adjusted; a designation from a stale player pull is labelled STALE, and an Out from a stale pull says so | `test_complete_projects_matches_and_recommends_with_labels`, `test_stale_inputs_are_shown_labelled_...` |
| Lineups are legal | Position/FLEX eligibility, nobody twice, nobody off IR, nobody moved after kickoff; an unprojected starter is frozen (not swapped out), an unprojected bench player is never proposed | `test_lineup.py`, `test_the_best_lineup_is_legal_under_the_locks` |
| Lock state unknown ⇒ never movable | Locks are three-valued. No schedule at all ⇒ `kickoff_index` is None ⇒ start/sit and upgrades both refuse. A game with no readable kickoff time ⇒ UNKNOWN for both teams, never a guessed 13:00. A team absent from ANY week ⇒ UNKNOWN unless a bye is declared. Only OPEN is movable | `test_unknown_lock_state_abstains_from_everything`, `test_a_game_with_no_kickoff_time_is_unknown_not_one_oclock`, `test_a_week_that_could_not_be_read_is_never_a_league_wide_bye`, `test_a_partially_readable_week_freezes_only_the_players_it_cannot_time` |
| A bye is DECLARED, never inferred | "No game this week" is OPEN only when the schedule itself declares a bye for that team (`game_type: BYE`). Absence alone is UNKNOWN, with no exceptions: neither "every row parsed" nor "the team plays before and after the gap" is evidence, because deleting one real row from a full season frame produces both of those shapes. nflverse declares no byes, so absences are UNKNOWN in practice | `test_deleting_one_real_game_from_a_full_season_frame_is_not_a_bye`, `test_a_bracketed_gap_is_not_a_bye_either`, `test_a_schedule_that_declares_a_bye_is_believed` |
| A schedule that contradicts itself establishes nothing | Two week rows giving one team two different kickoffs discard BOTH times and mark the team UNKNOWN; a row naming only one team is half a game, so its kickoff is not trusted either; a row listing one team on both sides is dropped | `test_two_rows_giving_one_team_two_kickoffs_establish_neither`, `test_a_row_naming_one_team_is_half_a_game_and_times_nobody`, `test_a_row_listing_one_team_on_both_sides_is_not_a_game` |
| Stale inputs withhold the ACTIONS, not the evidence | `gridiron.gating` gates lineup / waiver / matchup on the league snapshot, player dump, injuries and schedule. A withheld action keeps its comparison, loses its imperative, and names what to verify. The archive records which actions were endorsed | `test_stale_inputs_withhold_every_action_but_keep_the_comparison`, `test_a_fresh_cache_still_endorses_its_actions` |
| A withheld card loses the instruction, not just the badge | A WITHHELD action renders neutral last-known wording ("the last snapshot ranked X above your cheapest legal drop"), anchored to the snapshot's as-of time, above the explanation. "Consider claiming", "Fill it with" and "start X over Y" appear only on endorsed cards | `test_a_withheld_card_states_the_last_known_picture_not_an_instruction`, `test_a_fresh_page_still_gives_the_instruction` |
| Box scores are judged on COVERAGE, never on age | `box_score_blockers`: four-day-old box scores on a Wednesday withhold nothing — that is the publication cadence (rule #8). A source MISSING, whose refresh FAILED, with a hole inside its covered weeks, or more than one week behind the evidence boundary withholds every action, because every action is scored through a projection built from it | `test_box_scores_four_days_old_but_caught_up_still_withhold_nothing`, `test_box_scores_months_behind_the_evidence_boundary_do_withhold`, `test_a_failed_box_score_refresh_withholds_...`, `test_a_hole_inside_the_covered_weeks_withholds` |
| The edge is never claimed to be bigger than it is | z is Δ in multiples of the combined SD, so z = 0.7 means the edge is 0.7× that uncertainty — SMALLER than it. Every swap above the 0.5 noise floor used to assert the opposite. `_edge_sentence` states the ratio; the floor is unchanged | `test_the_edge_is_never_called_larger_than_the_uncertainty_it_is_smaller_than` |
| A file's timestamp is not a parse | A schedule that is FRESH on disk but not fully readable still withholds lineup and waiver actions | `test_an_unreadable_schedule_blocks_actions_even_though_the_file_is_fresh`, `test_a_half_readable_schedule_freezes_by_name_and_invents_nothing` |
| Temporary absence is not zero roster value | Bye / Out / IR / suspended players are excluded from the drop ranking, listed separately with the reason, and the board abstains if nothing droppable is left | `test_a_player_who_is_out_this_week_is_not_the_cheapest_thing_to_drop`, `test_a_bare_ir_projection_without_a_withholding_marker_is_still_protected`, `test_a_board_with_nothing_left_to_drop_abstains_and_says_how_many` |
| Unrostered is not addable | Add eligibility is UNVERIFIED with its evidence and the league's waiver rule; the page never says "add now" | `test_an_unrostered_player_is_never_promised_as_addable`, `test_protected_players_are_named_on_the_page_not_silently_dropped` |
| Uncalibrated P(win) ranks nothing | Actions are ranked by deadline then projected points; P(win) sits behind a disclosure in the matchup section and appears in no action headline | `test_the_page_leads_with_actions_and_demotes_uncalibrated_win_probability` |
| Changes are transitions, not recomputation | `diff_archives` reads two frozen archives as data; a projection must move ≥ 1.5 pts to be listed | `test_a_second_render_reports_what_changed_since_the_first` |
| It fits a phone | Narrow layout below 560 px; the scenario runner measures the rendered page in a 390 px iframe and reports the width it actually achieved | `dashboard_scenarios.py --screenshot` (executed; see below) |
| No same-value churn | Retained starters keep their slots when equally legal | `test_same_value_churn_is_not_reported_as_a_change` |
| Every upgrade names its drop | Add/drop pairs are scored by the change in the best LEGAL lineup; LINEUP vs DEPTH kinds kept apart; a locked starter is never the drop | `test_a_lineup_upgrade_names_the_drop_and_the_slot_it_enters`, `test_the_drop_is_never_a_locked_starter_...` |
| P(win) is never presented as calibrated | Closed form from `gridiron.winprob`, labelled UNCALIBRATED on the page and in the archive; abstains when any non-DST starter on either side is unprojected; `EvaluationReport.pwin_calibrated` is False by construction | `test_complete_...`, `test_missing_...` |
| The archive is the page | `Dashboard.record()` is written atomically at render time; grading reads it and the week's actuals only, never re-projects; a missing actual is `ungradeable`, not zero | `test_the_archive_is_the_page_and_grading_it_leaks_nothing`, `test_decisions.py` |
| An archive is evidence, so it is immutable | The filename carries an 8-hex content digest, so two DIFFERENT pages written in the same second get two files instead of one replacing the other; rewriting identical content is a no-op and writing different content to an existing path raises `ArchiveCollision` | `test_a_second_different_page_cannot_replace_the_first`, `test_rewriting_the_same_page_is_a_no_op_and_a_different_one_raises` |
| "The previous snapshot" is the previous one in TIME | `list_archives` orders by the parsed filename stamp, not by filename. Sorting names put `week05_<september>` after `week04_<october>` and returned a month-old record as the newest; a name that does not parse is skipped rather than ordered by guess | `test_the_previous_archive_is_the_previous_one_in_time_not_by_filename`, `test_an_unparseable_archive_name_is_skipped_...` |
| Grading never claims the owner did anything | An archive records what the page SHOWED. Nothing in this project watches the owner, so every pair is a hypothetical COMPARISON; a `decision` requires an observation passed in from outside, and `decisions` is empty without one. Comparisons the page WITHHELD are excluded from the agreement rate, and a waiver pair whose add was never proven available is excluded too | `test_no_comparison_is_a_decision_without_an_observation`, `test_an_observation_is_the_only_thing_that_makes_a_decision`, `test_advice_the_page_withheld_is_not_scored_as_advice`, `test_grading_a_withheld_page_scores_no_advice_and_claims_no_decision` |
| History survives an ephemeral runner | `gridiron.carryover` restores prior frozen records from a private store before the render and publishes this run's back afterwards, so "since the last snapshot" and later grading work in the cloud. A restored file is INPUT: filename-vs-contents, content digest, season, future stamps and age are all checked, and a failure leaves the file alone | `test_two_ephemeral_runs_carry_one_history_between_them`, `test_an_edited_record_is_refused_by_its_own_digest`, `test_a_renamed_record_is_refused_...`, `test_a_record_stamped_in_the_future_is_refused` |
| A failed refresh cannot become "current" | A run that freezes no page restamps nothing; restored records keep the times they were written with, so last-good data can never present itself as today's | `test_a_failed_refresh_cannot_turn_last_good_into_current` |
| A carried manifest ENTRY is validated field by field, before construction | `ing.Entry(**raw)` checks which KEYS it was handed and never what they hold, so it is not validation. Every field is read through a total reader first: `path` must be a plain filename that travelled with the carry (not a list, not absolute — `dir / "/etc/passwd"` IS `/etc/passwd` — not a traversal, and not whitespace-padded; each of those is REFUSED, never repaired into something that names a different file), `rows` a non-negative count, `as_of` a parseable time, `weeks` a list of weeks, and an unknown field refuses the entry. A bad entry is rejected BY NAME and its sound siblings still carry | `test_a_carried_path_that_is_not_a_string_is_refused_not_raised`, `test_an_absolute_carried_path_cannot_reach_outside_the_cache`, `test_a_traversing_carried_path_is_refused_rather_than_basenamed`, `test_a_padded_carried_path_is_refused_rather_than_trimmed`, `test_carried_fields_the_renderer_reads_are_checked_before_construction`, `test_a_publish_whose_manifest_points_at_a_bad_path_still_stores_the_rest` |
| A carried file never displaces one already here — judged by DESTINATION | The entry NAME is not the collision: a carried `weekly_stats` and a local `sleeper_state` can both point at `shared.json` and share no name. Overwriting left the LOCAL entry — current `as_of`, empty `error`, so the gate did NOT withhold on it — vouching for bytes it had never seen. The check is now the destination filename against everything the cache holds, case-folded on every platform because the store is published by one machine and restored on another; a conflict refuses the carried entry and names the local owner it would have hit | `test_a_carried_file_never_displaces_a_local_one_under_another_name`, `test_a_carried_file_cannot_displace_a_local_one_through_a_case_alias`, `test_two_carried_entries_naming_one_file_do_not_overwrite_each_other`, `test_a_carried_entry_cannot_overwrite_the_manifest_it_is_read_from` |
| A workflow input is data, not script | No `${{ ... }}` expansion appears inside any `run:` block in any workflow — the runner substitutes those before the shell parses the script. The dispatched week arrives through `env:` and is refused unless it is a bare ASCII integer in 1–22 | `test_no_workflow_expands_an_expression_inside_a_shell_script`, `test_everything_else_is_refused_as_data`, `test_the_dispatched_week_reaches_python_through_the_environment` |
| Rule #5 gate is mechanical | `gridiron.models.validated_signals`: an entry in FEATS without VALIDATED evidence fails the import (smoke.py imports it first) | `test_the_rule_5_gate_fails_the_import_for_an_unvalidated_feature` |
| Owner data stays local | `data/outputs/dashboard/` and `data/ledger/decisions/` gitignored; hygiene test scans tracked outputs for dashboard/archive markers; stdout names nobody; opponent is "roster #N" | `test_hygiene_no_roster_in_repo.py`, `test_the_stdout_summary_names_no_player`, `test_the_opponent_is_a_roster_number_...` |

## Game Day: the guarantees, and where each is held

| Guarantee | How | Test |
|---|---|---|
| Every number is a platform actual, as sent | `starters_points` / `players_points` / `points` / `custom_points` read totally; kickers and defenses included; zero and negative kept; a value not sent is None | `test_platform_values_are_taken_as_sent_including_zero_negative_and_kicker`, `test_a_value_the_platform_did_not_send_is_unknown_not_zero` |
| Totals are reconciled and never edited | starters' sum vs platform total; an override is the total and is named; a difference is disclosed with its sign; unknown values make the totals "cannot be reconciled" | `test_an_unexplained_difference_is_disclosed_and_the_total_kept`, `test_a_commissioner_override_is_the_total_and_is_named` |
| Game status only from observed evidence | five feed words map; an unseen word is UNKNOWN with the word shown; a stale feed keeps FINAL/CANCELED and demotes the rest; a missing row is "not a bye and not a final"; in_game after four hours is PLAYING | `test_a_kickoff_that_has_passed_is_never_a_final_by_itself`, `test_observed_statuses_map_and_unknown_words_pass_through`, `test_a_stale_feed_keeps_finals_and_demotes_everything_else`, `test_a_team_missing_from_the_feed_is_unknown_not_a_bye` |
| Feed and schedule disagreements are stated; the safer side locks | pre_game after the scheduled kickoff stays NOT STARTED with the conflict named; in_game before it locks the player | `test_feed_and_schedule_disagreements_are_stated_and_the_safer_side_locks` |
| One alias table joins teams | `gameday.team_key` is `ids.nflverse_team` on every side of every join | `test_team_aliases_are_normalised_through_the_one_table` |
| Owner and opponent by stable ids only | roster by `owner_id`/`co_owners`, opponent by shared `matchup_id`; none, several, or a null id is an explicit unsupported state | `test_opponent_is_found_by_matchup_id_and_never_chosen_arbitrarily` |
| A lead is never called safe | `ScoreView.settled` names who can still score; any UNKNOWN blocks "settled" | `test_a_lead_is_never_called_safe_while_they_have_players_left`, `test_an_unknown_status_anywhere_…` |
| Only Sleeper-legal moves are offered | archived action judged by player id: ACTIONABLE pregame, decided before its deadline, deadline ahead, both players OPEN, not under way, on the roster, lineup unchanged; pickups never | `test_a_legal_pregame_swap_…`, `test_a_withheld_pregame_action_never_becomes_advice_during_games`, `test_a_locked_or_unknown_player_is_never_offered`, `test_the_feed_can_lock_a_player_the_schedule_still_calls_open`, `test_an_action_whose_lineup_already_moved_…`, `test_a_passed_deadline_and_a_pickup_are_never_available` |
| No legal move left is said plainly | `Capacity.sentence` from proven locks; UNKNOWN locks say the page cannot tell | `test_no_legal_move_left_is_said_plainly` |
| The pregame record is joined by ids, never rebuilt | newest archive matching season, week, league id, roster id; untagged records matched on season/week and said to be; none → "no pregame record" | `test_the_record_must_match_season_week_league_and_roster`, `test_an_untagged_record_is_accepted_and_said_to_be`, `test_no_record_means_a_clear_absence_and_nothing_reconstructed` |
| No hindsight | a designation that moved after the record is reported with "cannot show what was knowable"; outcomes are UNPROVEN until both final and descriptive after; the lineup is an observation | `test_a_designation_that_moved_after_the_record_is_reported_without_blame`, `test_outcomes_are_descriptive_and_unproven_until_both_final` |
| No live win odds, no projection arithmetic | the archived P(win) is shown as pregame and UNCALIBRATED; nothing on the score card is a percentage | `test_the_pregame_projection_is_shown_as_pregame_and_never_added_to_points`, `test_no_live_win_probability_and_no_projection_arithmetic_on_the_page` |
| Changes are like for like | same season/week/league/roster/source kind or nothing is compared; a lowered total is a CORRECTION; a vanished feed is a change | `test_a_lowered_total_is_named_a_correction_…`, `test_another_week_or_roster_is_never_compared`, `test_a_feed_that_vanished_is_a_change` |
| Last good survives every failure, in the browser | 429, malformed body and an older-dated response each keep the applied state; STALE is shown; backoff doubles to 20 min; polling stops at rollover | `test_refresh_applies_corrections_keeps_last_good_and_discards_older_responses` |
| Upstream text cannot run | names through `textContent` and `html.escape`; embedded JSON `\u`-escaped; CSP `default-src 'none'`, one connect host, nonce script | `test_upstream_names_cannot_inject_markup_or_script`, `test_the_page_may_only_talk_to_sleeper` |
| The archive is read, never written | Game Day opens the archive read-only; byte-identical after renders | `test_the_archived_pregame_record_is_byte_identical_after_the_page_is_built` |
| The feed is recorded beside the snapshot and a failure keeps the last good copy | `refresh_game_status` validates, writes atomically under the writer lock, records failure without touching `as_of` | `test_gameday_feed.py` |
| Owner data stays local | pages under `data/outputs/dashboard/` (ignored); stdout names nobody; the opponent is a roster number | `test_the_stdout_summary_names_no_player`, `test_the_page_names_the_opponent_by_roster_number_only`, `test_the_page_writer_only_writes_under_data_outputs` |

### Limitations, stated

- **The baseline is unvalidated and says so.** On the synthetic scenario its
  MAE does NOT beat season PPG (that fixture is built from one week copied
  with scaled counts, which is the case PPG is best at). On the real cache
  there is one week of box scores, so nothing can be evaluated yet. The
  page carries the verdict either way; the verdict changes only when the
  cache does.
- **Real byes freeze players, because nothing available here declares one.**
  Absence is no longer read as a bye under any condition, and the nflverse
  schedule carries no bye declaration (`game_type` is REG/WC/DIV/CON/SB). So
  on a genuine bye week, that team's players come back UNKNOWN and are frozen
  — the page says exactly why, per player, and freezes nothing else. This is
  the deliberate cost of the repair: the previous rules were cheaper and both
  were wrong, and the safe direction is to decline rather than to guess. It
  ends the day a source states its byes; `lineup.BYE_GAME_TYPES` is where that
  would be read, and it needs no other change.
- **Provenance is integrity, not authenticity.** The digest check proves a
  restored record has not been edited or renamed since it was written. It is
  not a signature: anyone who could write to the store could also write a
  consistent record. The store is the private repository's own cache, so that
  set is the people who already have the data.
- **Grading claims nothing about conduct, because it cannot.** Every
  comparison is hypothetical unless an observation is supplied by hand.
  Nothing in this project watches Sleeper for what the owner actually did, so
  the first hand-graded week will have to state the owner's real choices as
  input if they are to be graded as choices at all.
- **The box-score coverage gate has one week of slack**, which is the
  publication cadence and not a fit. A source exactly one week behind the
  evidence boundary passes; that is the Tuesday-morning state every week.
- **SD is a literature CV, not a fit.** §2.1's CVs by position; the
  evaluation reports the ±1 SD coverage so a reader can see how wrong that is.
- **P(win) assumes independence** between all starters (no stack / same-game
  correlation, no DST on either side). It is a closed form, not the Monte
  Carlo QUANT §2.5 asks for at |margin| < 15. Labelled on every render.
- **QB and K priors are pooled from the frame**, not from a verified
  constant, and the n0 for those per-game rates (3 games) is stated by
  analogy, not fit. Backup QBs with one thin game are pulled toward the
  starter prior; the "why" panel shows the weight.
- **No FAAB pricing and no rest-of-season value.** Upgrade gains are this
  week's projected points only.
- **Add eligibility cannot be established from this cache, and is not.** The
  league snapshot proves one thing: the id is on no roster at its as-of. The
  waiver clock, pending claims and the drop that created the vacancy live in
  Sleeper's transactions feed, which this repo does not pull. Every candidate
  is marked UNVERIFIED and the owner checks in the app. Adding that feed is
  the obvious next increment and was deliberately not done here.
- **Protected players are protected, not priced.** A bye / Out / IR player is
  kept off the drop list because his one-week 0 is not his value — but the
  page cannot tell you what he IS worth, because that needs a rest-of-season
  model and rule #5 does not allow one to ship unvalidated. A human still has
  to make that call.
- **Deadlines are kickoffs, not roster-lock settings.** The board reads the
  schedule's kickoff times. If the league ever uses a whole-week lock or a
  first-game lock, this would be wrong and nothing here would catch it.
- **No DST projection**, so the DST slot is never optimized and both
  matchup totals exclude it.
- **Availability is a flag, not a probability.** Questionable and Doubtful
  are shown unadjusted (rule #11); a human verifies before kickoff.
- **The archive is graded by hand** until the first settlement has been
  done once; there is no `grade_week.py` yet.
- **Screenshots are of the synthetic scenarios only** (`docs/review/dashboard/`),
  never of the owner's page.
- **The phone check is a synthetic browser check, not a device test.** The
  scenario runner measures the rendered page in a 390 px iframe under headless
  Chromium and reports the layout width it actually achieved (375 px client,
  zero horizontal overflow on all four scenarios). No real phone, no iOS or
  Android browser, and no touch interaction has been tested. The committed
  PNGs are captured at 500 px, not 390, because this headless build clamps its
  own window to a 500 px minimum — asking for 390 silently crops the image and
  makes correct text look clipped.
- **The status feed is undocumented.** `api.sleeper.app/schedule/nfl/regular/<season>`
  is what Sleeper's own app reads; it is not in Sleeper's published API
  documentation and can change shape or vanish without notice. The reader
  maps only the five words it has seen and passes anything else through as
  UNKNOWN; if the feed goes away, every game is UNKNOWN and the page says so.
  It carries no clock or quarter, so "PLAYING" never says how much is left.
- **Live mode is one tap and a visible page.** Nothing is fetched until the
  owner taps Refresh; a page in the background does not poll; a page opened
  and left alone is a dated snapshot. That is deliberate, and the mode line
  says which it is at all times.
- **The browser's clock is the browser's.** Lock estimates in live mode use
  Sleeper's `Date` header when it disagrees with the device by more than
  five minutes, and the page says so; the schedule's kickoffs are still what
  is compared against.
- **Server-side "since the last snapshot" only sees the previous Game Day
  record in the same output folder.** On a clean runner that is nothing; the
  browser's own like-for-like diff against the embedded snapshot is the one
  that matters on a phone.
- **Injury designations on Game Day are the cached player dump's**, dated
  and marked STALE when old; the live refresh does not re-fetch the 16 MB
  dump, by design.
- **A scheduled refresh is not timely injury news.** The cloud workflows keep
  the SNAPSHOT current on a best-effort cron that GitHub may delay or drop.
  A designation can change minutes before kickoff regardless of when the last
  job ran. That is exactly why the page gates its actions on input age rather
  than on the existence of a schedule.

## Not done deliberately

- No lineup change, waiver claim, trade or message. Ever, by construction.
- No projection feature beyond the baseline while the rule #5 gate is
  unmet; the baseline itself is labelled UNVALIDATED on every page. A
  guessed recommendation presented as verified is the failure mode this
  repo exists against.
- No blocked-kick scoring and no DST scoring invented to fill the gap.
- No history rewrite, no merge, no deploy, no PR watcher.
- No rest-of-season model, invented to price a protected player. The page
  says it cannot price him, which is true, rather than producing a number
  rule #5 would not let ship.
- No Sleeper transactions pull, so add eligibility stays UNVERIFIED rather
  than guessed. That pull is a real next increment, not a limitation to
  paper over.
- No FAAB engine, trade engine, season simulation or model tuning in this
  milestone. The projection is byte-for-byte the reviewed baseline.
- Desktop scheduling stays DISABLED. Nothing here re-enables it, and the
  cloud path is deliberately independent of it.
- No durable cloud storage, no database, no object store and no new service
  for the decision records. The carry uses the repository's own Actions cache
  and states its eviction rules rather than implying permanence.
- No signature on a restored record. Integrity is checked; authenticity would
  need a key, and a key would need somewhere to live.
- No inference of what the owner did from what the roster looks like. The
  roster still holding a player proves nobody removed him, not that anyone
  chose to keep him, and grading now says so instead of assuming.
- No league-size constant, bye table, or any other rule that turns an absent
  team into a bye. The schedule declares one or the answer is UNKNOWN.
- No second guess after removing the first. Bracketing was itself the
  replacement for "the week parsed cleanly"; nothing replaced bracketing.
- No live win probability, no "remaining projection", no clock-scaled
  projection. Game Day shows earned points and who can still earn them.
- No inference of a game's state from the clock, from a zero, or from an
  absent row. The feed says it or the page says UNKNOWN.
- No hosting, no auth, no tunnel, no desktop service and no second host in
  the page's connect policy for delivery; the artifact plus one tap is it.
- No fetch on load and no background polling.
