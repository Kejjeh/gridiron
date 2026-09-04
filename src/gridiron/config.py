"""Runtime configuration: pydantic-settings, one env prefix (GRIDIRON_).

Credentials live ONLY in the environment or the gitignored .env at the repo
root (see .env.example). Never write a credential into a tracked file.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from gridiron.paths import REPO_ROOT


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GRIDIRON_",
        env_file=str(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ESPN league auth (cookies) — used only by gridiron.espn
    espn_league_id: int | None = None
    espn_s2: str | None = None
    espn_swid: str | None = None

    # Sleeper needs no auth; just the league id if the league lives there.
    sleeper_league_id: str | None = None

    # Betting lines provider (pick one early — see bootstrap doc §3)
    odds_api_key: str | None = None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
