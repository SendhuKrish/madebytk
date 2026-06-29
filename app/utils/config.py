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
    "ANTHROPIC_API_KEY",
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

    # ── Anthropic ─────────────────────────────────────────────────────────────
    anthropic_api_key: str

    # ── Supabase ──────────────────────────────────────────────────────────────
    supabase_url:   str
    supabase_key:   str

    # ── Scheduler (APScheduler, all times in TZ) ─────────────────────────────
    predict_hour:   int
    predict_minute: int
    results_hour:   int
    results_minute: int
    results_retry_until_hour: int = 22
    results_retry_interval_min: int = 60
    predict_days:   str
    results_days:   str

    # ── Scraper ───────────────────────────────────────────────────────────────
    scraper_timeout: int
    scraper_user_agent: str

    # ── Data Sources ─────────────────────────────────────────────────────────
    singapore_pools_url: str
    lottolyzer_url: str
    lottery_extreme_url: str
    toto_api_url: str

    # ── Claude AI ────────────────────────────────────────────────────────────
    claude_model: str
    claude_max_tokens: int
    pally_api_timeout: int

    class Config:
        env_file          = (".env.config", ".env.secret")
        env_file_encoding = "utf-8"
        extra             = "ignore"


@lru_cache
def get_settings() -> Settings:
    _clear_empty_env_overrides()
    return Settings()


settings = get_settings()
