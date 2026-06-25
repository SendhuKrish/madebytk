"""
Central configuration — reads settings from .env.config (non-sensitive) and
.env.secret (credentials).  No fallback to legacy .env file.
"""

import os
from functools import lru_cache
from pydantic_settings import BaseSettings


# Keys whose env-var must come from the env files if the shell exported a blank
# value.  On Windows, GUI tools sometimes write empty strings that shadow file
# values.
_REQUIRED_KEYS = {
    "SUPABASE_URL",
    "SUPABASE_KEY",
}


def _clear_empty_env_overrides() -> None:
    """Remove blank env-var entries so pydantic-settings falls back to .env files."""
    for key in _REQUIRED_KEYS:
        if os.environ.get(key) == "":
            del os.environ[key]


class Settings(BaseSettings):
    # ── App behaviour ─────────────────────────────────────────────────────────
    environment:    str
    tz:             str

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url:   str
    supabase_key:   str

    # ── Scheduler (APScheduler, all times in TZ) ─────────────────────────────
    predict_hour:   int
    predict_minute: int
    results_hour:   int
    results_minute: int
    predict_days:   str
    results_days:   str

    # ── Scraper ───────────────────────────────────────────────────────────────
    scraper_timeout: int

    class Config:
        env_file          = (".env.config", ".env.secret")
        env_file_encoding = "utf-8"
        extra             = "ignore"


@lru_cache
def get_settings() -> Settings:
    _clear_empty_env_overrides()
    return Settings()


settings = get_settings()
