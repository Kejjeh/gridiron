# Rules — full text

Headlines live in CLAUDE.md; this file carries the evidence and the origin
of each rule. Numbering is load-bearing (docs and commits cite "rule #N");
retire in place, never renumber. Most originate as plv_clone scars — see
docs/BOOTSTRAP_FROM_PLV.md for the full history.

1. **League settings are unverified placeholders.** `league_config.py` was
   written 2026-09-03 with a standard 12-team full-PPR guess because the real
   league's platform and settings weren't available at bootstrap. Every
   engine that produces user-facing output (projections, VOR, start/sit,
   waivers) must check `SETTINGS_VERIFIED` and refuse to run while it is
   False. Flipping it requires pulling the actual settings from the platform
   (ESPN `league.settings` or Sleeper league endpoint) and correcting
   `ROSTER_SLOTS`, `ScoringRules`, `NUM_TEAMS`, and `PLATFORM` in the same
   commit.

2. **One scoring implementation.** plv_clone needed two dedicated hygiene
   tests (`test_sp_fp_formula_copies`, `test_no_hardcoded_scoring_weights`)
   because formula copies drifted silently in scripts. Here the formula is
   born centralized: `gridiron.scoring.fantasy_points(stats, rules)`. A
   script that needs points imports it. If you find yourself typing `* 0.1`
   next to a yards column, stop.

3. **ID discipline.** Anchor every join on nflverse `gsis_id` / `player_id`.
   Platform ids (ESPN/Sleeper) map through ONE cached crosswalk built during
   ingest (build step 2). Name-matching and `.str.contains` are banned join
   strategies — names collide (two Josh Allens), get suffixed (Jr./III), and
   get reformatted across sources.

4. **Role comes from usage, not the tag.** The plv_clone `pitcher_role`
   lesson: the platform's position label describes eligibility, not usage.
   A "WR" running 90% of routes from the slot at TE depth is priced by his
   routes. Snap %, route %, target share, carry share, red-zone share are
   the role; the tag only determines which lineup slots he can fill.

5. **Validated-signals gate.** The rh3/rp3-v2 lesson: a candidate feature
   evaluated against a stripped-down baseline over-claimed its lift 4×.
   Every candidate signal must beat a baseline containing ALL existing
   features, out-of-sample, before entering a FEATS list. The gate is an
   import-time assert in the model's validated_signals module (so smoke.py
   catches drift), not reviewer discipline.

6. **Opportunity vs efficiency.** FF inverts the baseball prior: volume is
   ~70% of the projection and moves fast (injuries, depth charts, game
   script), so in-season usage deltas are real and fast-stabilizing.
   Efficiency (yards/route, YAC over expected) is the slow skill layer —
   in-season efficiency deltas are mostly noise and get shrunk hard toward
   the prior. Model the two layers separately.

7. **ΔP(win) denomination + decision ledger.** Optimize P(win)/playoff
   equity, not projected points: variance flips sign with seeding safety
   (chase ceiling as underdog, floor as favorite). Log every start/sit/
   waiver/trade decision with the REJECTED side at decision time; settle
   weekly against actuals; grade the choice given the information available,
   not the outcome. Define the ungradeable terminal state up front (plv #54:
   some rejected sides never get actuals — a dropped player on a bye who is
   then cut has no counterfactual week).

8. **Transition-only alerts, cadence-aware freshness.** "X has been out
   since Week 3" must fire once, at the transition, not weekly. Freshness
   checks know the NFL week's shape — waivers Tue night/Wed, practice
   reports Wed–Fri, designations Fri, inactives ~90 min before each kickoff
   — a flat TTL either spams or sleeps through Sunday morning.

9. **Credential hygiene.** `.env` at repo root, gitignored; `.env.example`
   committed; prefix `GRIDIRON_`; read exclusively via `gridiron.config.
   get_settings()`. League auth constructed only in `gridiron/espn.py` (or a
   future `sleeper.py`).

10. **Data commit policy.** `data/research/cache/` (bulk pulls) is
    gitignored. `data/outputs/` weekly projection CSVs and `data/ledger/`
    are committed — git history doubles as the point-in-time archive
    (plv_clone's `recover_rp3_git_snapshots.py` trick, cheap at FF scale).

11. **No convenience traps.** The plv ADR-0004 `injured_players()` lesson:
    an accessor that makes the wrong interpretation ergonomic will be
    called. FF equivalents: treating Questionable as Out, and treating
    rostered as startable (byes, IR slots, lineup locks). APIs expose the
    raw designation and make the caller decide.

12. **Skill discipline.** plv_clone grew 94 skills organically (with a
    registry test keeping them honest). Start here with five: roster-audit,
    waiver-board, start-sit, matchup, decision-log — plus the registry test
    from the first skill onward.
