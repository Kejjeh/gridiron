# Handoff: add a live fantasy win probability to nfl-gamezone

For a Claude session with **push access to `Kejjeh/nfl-gamezone`**. The
calibration is done and merged in `Kejjeh/gridiron`, which is public and
readable without auth:

- `data/reference/live_pwin_v1.json`: constants, provenance, held-out metrics.
- `data/reference/live_pwin_fixtures.json`: 8 held-out real snapshots (no
  ids or names), each with `expected_p` and the real outcome.
- `docs/research/LIVE_PWIN.md`: the study (read it first: data, results,
  limits).
- `scripts/research/live_pwin_calibration.py`: the study script; the Python
  formulas are the reference implementation.

Raw URL base:
`https://raw.githubusercontent.com/Kejjeh/gridiron/main/`. Copy the two JSON
files into Game Zone. **Do not fetch them at runtime.** Game Zone's own
`CLAUDE.md` rules apply throughout: no build step, no dependencies, defensive
parsing, measured claims, tests are plain Node scripts in `tools/`, and the
phone media query is kept in sync.

## What it is worth (held-out 2025-2026, this league, 1,884 states)

Log loss (coin flip = 0.693): **live 0.424** vs the points-share bar the
header shows today 1.153, and the pre-game number frozen at kickoff 0.680.

| Stage (share of projected points still to come) | Live log loss |
|---|---|
| Late (<25%) | 0.215 |
| Mid (25-60%) | 0.455 |
| Early (60-90%) | 0.573 |
| Pre (>90%) | 0.663 (only slightly better than a coin flip) |

Calibrated within noise across the bins. Say in the UI that before kickoff
the number is soft.

## The model (implement exactly; verified against the fixtures)

```
for each starter i on side A and side B:
  f    = share of his game left: 1 pre-game; 0 final / bye / no game / empty slot;
         live: ((4 - period) * 900 + clockSeconds) / 3600, clamped 0..1;
         overtime (period > 4): 0.02
  proj = scoreWith(projStats, leagueScoring) with fgmiss = sum of fgmiss_* keys
         (0 if no projection row)
  if Sleeper tags him Out/IR/Doubtful/Inactive during the week or game: proj = 0
  mu   = proj * f
  sd   = f^(g/2) * (a[pos] + b[pos] * max(proj, 0))
P(A wins) = Phi( (nowA - nowB + sum muA - sum muB) / (k * sqrt(sum sd^2)) )
```

- `now*` = Sleeper `matchups` `points`. This is already `s.myPoints` and
  `s.oppPoints` in `js/sleeper.js`. Keep it as truth; never recompute.
- `pos` comes from the slim player cache (`sleeper.playerPos`). DEF slots are
  team abbreviations: pos `DEF`.
- `Phi` (normal CDF): write a small erf approximation (Abramowitz-Stegun
  7.1.26 is plenty). No dependency.
- Constants: `a`, `b` (per pos), `g` = 0.7828, `k` = 1.0, from
  `live_pwin_v1.json`. Keep the file's `version` string visible in
  Diagnostics.

## Where the inputs come from (all free, CORS-open, already partly loaded)

| Input | Source | Notes |
|---|---|---|
| Live points, starters | `sleeper.refreshMatchups` (already polled, 25 s) | `myLineup` / `oppLineup` in slot order |
| Game clock / state per starter | `espn.gameForTeam(sleeper.playerTeam(pid) \|\| pid)` -> `state`, `period`, `clock` (`"M:SS"`) | `matchup.lineupStates` already does this lookup |
| Projections | `https://api.sleeper.com/projections/nfl/{SEASON}/{week}?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF` | ~2 MB raw, CDN-cached 10 min, `Access-Control-Allow-Origin: *`. Fetch once per week per day, keep only the 20-ish starters' `stats`, store via `store.js` with a TTL (pattern: `sleeper.loadPlayers` slimming 14.6 MB to 115 KB). Score with `season.scoreWith(stats, season.settings())` after adding the derived `fgmiss`. |
| In-game Out tags | `https://api.sleeper.com/stats/nfl/{SEASON}/{week}?season_type=regular` rows carry `player.injury_status` (week-10 Maye flipped to `Out` mid-game here) | Heavy (~1-2 MB). Alternatives: poll only while a starter's game is `in`, at most every 2-3 minutes; or add a manual "mark out" tap on a My Team row (stored per week). **Never request `/players/nfl` for this**: it is once a day by Sleeper's own guidance. |

Mind `state.week` vs the displayed week (Game Zone's `CLAUDE.md`: the
transaction week runs ahead on Tuesdays). Use the same week the matchup
strip uses.

## Build order (each step its own commit; CI green each time)

1. **`js/winprob.js`**: pure functions, no DOM, no fetch:
   - `remainingFraction(game)`;
   - `projectedPoints(stats, scoring)` (with `fgmiss`);
   - `starterTerms(...)`;
   - `pWin(sideA, sideB, consts)` -> `{ p, margin, sd, muA, muB }`;
   - `leverage(...)`, see step 4.

   Copy the two JSON files to `js/live_pwin_v1.json` (or inline as a JS
   module) and `tools/live-pwin-fixtures.mjs`.
2. **`tools/winprob-test.mjs`** (offline, deterministic):
   - every fixture's `expected_p` reproduced to ±0.0005;
   - `remainingFraction` cases: pre, `Q2 7:30` = 0.625, halftime
     (period 2, 0:00) = 0.5, final, OT, missing clock;
   - `fgmiss` derivation;
   - a DEF slot;
   - an empty slot.

   Add it to `.github/workflows/tests.yml` and `npm test`, and **state its
   fixed pass count in CLAUDE.md** like the other harnesses.
3. **Projections fetch** in `sleeper.js` (or a new `projections.js`): load at
   boot and on week change, cache slim per week with the rules tag in the key
   (same reasoning as `wkpts:` in `season.js`), and fail soft. With no
   projections, show "win % unavailable", never 50%.
4. **UI:**
   - **Header (`matchup.render`):** a "Win 64%" chip with the change since
     the previous poll (▲6), and the projected final on hover/tap
     ("107.5-94.5"). Replace the points-share bar's meaning with win
     probability, or keep the bar and add the chip, whichever stays readable
     at 375 px. Check 375x812 and 812x375.
   - **My Team panel:** each starter's projected remaining points, plus a
     "matters most" marker for the starters whose next 10 points move P(win)
     most. Leverage = P(win | +10 for him) - P(win), computed by re-running
     `pWin`.
   - **Sparkline:** P(win) through the day, kept in `state` and in
     localStorage per week.
5. **In-game Out handling** (the Stats feed tag or the manual tap), then
   **alerts on change only** through `alerts.js`: crossing 50%, or a swing of
   15+ points in one poll. Never on every poll.
6. **Diagnostics** (`main.js`): show model version, projections as-of, how
   many starters have a projection, and P(win) at kickoff. That last value
   should sit near the gridiron board's pre-game P(win) for the same matchup;
   the board uses the same Phi form with k = 1.
7. *(Later)* The iOS widget and the push Worker can carry the same number.
   Deploying the Worker is the owner's call.

## Pitfalls already found

- **Kickers:** a plain `scoreWith` on projections drops missed field goals.
  The projection keys are `fgmiss_<range>`, but the league scores `fgmiss`.
  Derive it.
- **Defenses:** the projection's `pts_allow_*` tier is a 0/1 indicator and
  the dot product handles it. Do not compute points allowed yourself.
- **Before kickoff** the number is barely better than a coin flip (Sleeper's
  projections are weak in this league), so label it ("pre-game estimate").
- **Sleeper's live points lag about one play.** That is already accepted in
  Game Zone; the model inherits it.
- A starter on bye, with no game, or an empty slot (`"0"` in `starters`)
  contributes `f = 0`, `proj = 0`.
- **IR-slot trap** (gridiron ADR-0002): a player in the IR slot without an
  eligible tag locks the whole Sleeper lineup. A one-line warning in the
  header is cheap and would have saved week 10. Optional, but recommended:
  the roster's `reserve` array plus the player's tag is all it needs.

## Definition of done

- The `winprob-test.mjs` count is stated in `CLAUDE.md`, CI runs it, and
  `for f in js/*.js; do node --check "$f"; done` is clean.
- The page loads with `?week=9` (a finished week): every starter has
  `f = 0`, and P(win) is 0 or 1 matching the result.
- On a live Sunday the chip moves with scores, and the README gains a
  "Win probability" section with the numbers above and the limits.
- No new runtime dependency, no key, no server.
