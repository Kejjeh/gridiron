"""ONE auth home for the league platform connection.

Every script that touches the league goes through here — never construct a
League or paste cookies anywhere else. If the league turns out to live on
Sleeper (verify! league_config.PLATFORM), this module gets a sibling
sleeper.py and this one keeps only the ESPN path.
"""
from __future__ import annotations

from gridiron.config import get_settings
from gridiron.league_config import SEASON_YEAR


def get_league():
    """Return an authenticated espn_api football League for SEASON_YEAR.

    Requires GRIDIRON_ESPN_LEAGUE_ID, GRIDIRON_ESPN_S2, GRIDIRON_ESPN_SWID
    in the environment or .env (see .env.example).
    """
    s = get_settings()
    missing = [
        name
        for name, val in (
            ("GRIDIRON_ESPN_LEAGUE_ID", s.espn_league_id),
            ("GRIDIRON_ESPN_S2", s.espn_s2),
            ("GRIDIRON_ESPN_SWID", s.espn_swid),
        )
        if not val
    ]
    if missing:
        raise RuntimeError(
            "ESPN auth incomplete — set in .env (never in code): "
            + ", ".join(missing)
        )

    from espn_api.football import League  # deferred: heavy, network-adjacent

    return League(
        league_id=s.espn_league_id,
        year=SEASON_YEAR,
        espn_s2=s.espn_s2,
        swid=s.espn_swid,
    )
