# HANDOFF

Updated 2026-09-08 after the draft-readiness repair.

## Current state

Local repository repaired; outputs regenerated. No push or deployment.
League settings are verified for Sleeper 1389720742551093249, 12-team
half-PPR, slot 1, fifteen rounds, two FLEX, K and DEF.
Current instructions and limitations: docs/research/DRAFT_2026_PLAN.md.

The earlier roughly 100-point strategy advantage was invalid: competing
policies missed starter slots. All simulated teams now complete their
rosters; temporarily ineligible players can be reconsidered. Identical
seeds and identical downstream policy isolate scripted openings.
Static VOR is 1890.4 versus dynamic 1885.1 in the rebuilt 300-draw results.
These are exploratory projected outcomes, not validated predictions.

Room survival uses a separate 1,000-draft neutral-market scenario with
actual selections removed, rather than ghosting your choices. It is
labeled a pre-draft snapshot. ADP is the default page mode, also unvalidated.
The manifest stores input fingerprints, timestamps, seeds, results and
board hash; the page builder checks that hash before publishing an artifact.

The HTML now contains all 983 board records, keeps historical correction
indices, validates Mine/Gone against your turn, shows save failures, and
supports JSON backup/restore plus unlisted players. Explicitly starred
alternatives are included in the pick backup. Old saved data is validated
before use; the page never silently overwrites an unreadable save.

Production pandas/numpy and optional research libraries are declared.
The user authorized installing them into the existing repository .venv.
Verification: full Python suite 104 passing and JavaScript 15 passing;
required offline smoke checks pass. Browser checks cover recording, correcting,
backup/restore, refresh persistence, and previously missing player search.

## Next

1. Before the draft, follow the checklist in DRAFT_2026_PLAN.md. Use the
   HTML as a manual companion and confirm all decisions in Sleeper.
2. After the draft, run record_draft_2026.py. It identifies your selections
   by draft slot (including autopicks) and evaluates ADP/room on common
   nonmissing support. Its alternative is a retrospective benchmark.
   Keep the page JSON backup for the actual alternatives considered.
3. Repair the remaining source-ID crosswalk, incomplete cache producers,
   minor scoring-stat coverage and injury/projection validation before
   treating the output as a reliable decision engine.
4. Continue the in-season ingest/baseline work described in the existing
   architecture and research docs; no new model signal was validated here.

## Caveats

No fresh news pull during this repair. Cached injury exclusions are
scenario assumptions; player-specific causal discounts remain unproven.
Older COMPETITION_2026.md and INJURY_EFFECTS.md are historical exploratory
research. Their earlier probability and causal claims are not current
validation. CLAUDE.md's placeholder-settings headline predates the verified
league configuration; the settings gate remains enforced in the builders.
