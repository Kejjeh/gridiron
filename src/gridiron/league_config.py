"""SEASON_YEAR + league constants. Season rollover = bump ONE number here.

STATUS: VERIFIED 2026-09-08 against the Sleeper API (league 1389720742551093249,
"Take Mahomes, Country Road") and Josh's settings screenshots. Every value
below was read from `scoring_settings` / `roster_positions` on the league
object; the pull script is scripts/research/pull_sleeper.py and the raw JSON
is cached (gitignored) under data/research/cache/draft2026/.

RE-VERIFIED 2026-09-17 (league status `in_season`, week 2) — every constant
here still matches the live league object field for field. Re-run the check
any time with `python scripts/verify_league_settings.py`; it reports drift and
exits nonzero, and it never edits this file. Flipping SETTINGS_VERIFIED is a
human decision backed by a pull, never a way to unblock a run (rule #1).
"""
from __future__ import annotations

from dataclasses import dataclass

SEASON_YEAR = 2026

# Which platform hosts the league. Sleeper: public read API, no auth needed.
PLATFORM = "sleeper"
LEAGUE_NAME = "Take Mahomes, Country Road"
# Public identifier (Sleeper's read API needs no auth), not a credential.
# Override with GRIDIRON_SLEEPER_LEAGUE_ID; see gridiron.sleeper.resolve_league_id.
SLEEPER_LEAGUE_ID = "1389720742551093249"
# The owner's Sleeper handle (public; used only to find which roster is his).
MY_SLEEPER_USERNAME = "Kejjeh"

SETTINGS_VERIFIED = True  # flipped 2026-09-08 in the same commit as the values below

NUM_TEAMS = 12

# Starting lineup slots, straight from Sleeper roster_positions:
# QB RB RB WR WR TE FLEX FLEX K DEF + 5 BN (+1 IR slot, not a roster slot).
ROSTER_SLOTS: dict[str, int] = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 2,  # RB/WR/TE
    "DST": 1,
    "K": 1,
    "BENCH": 5,
}
IR_SLOTS = 1
FLEX_ELIGIBLE: tuple[str, ...] = ("RB", "WR", "TE")

# Draft: snake, 15 rounds, 60 s pick clock, Josh holds slot 1 of 12.
DRAFT_ROUNDS = 15
DRAFT_TYPE = "snake"
MY_DRAFT_SLOT = 1

# Season shape (rule #8): waivers clear Wed 3 AM ET, 2-day waiver period,
# trade deadline after week 13, 6-team playoffs starting week 15.
PLAYOFF_TEAMS = 6
PLAYOFF_START_WEEK = 15
TRADE_DEADLINE_WEEK = 13
REGULAR_SEASON_WEEKS = 14

# Waivers, read off the live league `settings` block 2026-09-17:
# waiver_type 2 = FAAB, waiver_day_of_week 2 = Wednesday (0 = Sunday),
# waiver_clear_days 2, waiver_budget 100, waiver_bid_min 0, trade_review_days 2.
# Rule #8 cadence: a claim entered Monday clears Wednesday ~3 AM ET.
WAIVER_TYPE = "faab"
WAIVER_BUDGET = 100
WAIVER_MIN_BID = 0
WAIVER_CLEAR_WEEKDAY = 2          # 0 = Sunday, Sleeper's convention
WAIVER_CLEAR_DAYS = 2
TRADE_REVIEW_DAYS = 2
MAX_KEEPERS = 1


@dataclass(frozen=True)
class ScoringRules:
    """Point weights for offensive stats. Frozen so nothing mutates scoring
    mid-pipeline; a rules change is a new instance and a new commit.

    Sleeper keys, for the record: pass_yd .04, pass_td 4, pass_int -1,
    rush_yd .1, rush_td 6, rec .5, rec_yd .1, rec_td 6, fum_lost -2,
    pass_2pt / rush_2pt / rec_2pt 2. No yardage or long-TD bonuses.
    """

    pass_yd: float = 0.04          # 1 pt / 25 yards
    pass_td: float = 4.0
    interception: float = -1.0     # Sleeper default, NOT the ESPN -2
    rush_yd: float = 0.1           # 1 pt / 10 yards
    rush_td: float = 6.0
    reception: float = 0.5         # HALF-PPR (highlighted as non-standard on Sleeper)
    rec_yd: float = 0.1
    rec_td: float = 6.0
    fumble_lost: float = -2.0
    two_pt: float = 2.0


DEFAULT_SCORING = ScoringRules()

# Kicker / team-defense weights are Sleeper's defaults, verified identical to
# the league. Kept as plain dicts keyed by Sleeper stat name because nothing
# in nflverse weekly data carries these as columns.
KICKING_SCORING: dict[str, float] = {
    "fgm_0_19": 3.0, "fgm_20_29": 3.0, "fgm_30_39": 3.0, "fgm_40_49": 4.0,
    "fgm_50p": 5.0, "xpm": 1.0, "fgmiss": -1.0, "xpmiss": -1.0,
}
DEFENSE_SCORING: dict[str, float] = {
    "def_td": 6.0, "sack": 1.0, "int": 2.0, "fum_rec": 2.0, "safe": 2.0,
    "ff": 1.0, "blk_kick": 2.0, "def_st_td": 6.0, "def_st_ff": 1.0,
    "def_st_fum_rec": 1.0, "st_td": 6.0, "st_ff": 1.0, "st_fum_rec": 1.0,
    "pts_allow_0": 10.0, "pts_allow_1_6": 7.0, "pts_allow_7_13": 4.0,
    "pts_allow_14_20": 1.0, "pts_allow_21_27": 0.0, "pts_allow_28_34": -1.0,
    "pts_allow_35p": -4.0,
}
