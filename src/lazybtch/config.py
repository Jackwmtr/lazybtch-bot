"""Environment configuration (pydantic-settings)."""
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str
    encryption_key: str
    group_policy: Literal["off", "mentions", "all"] = "mentions"
    db_path: Path = Path("data") / "lazybtch.db"

    groq_base_url: str = "https://api.groq.com/openai/v1"
    groq_model: str = "whisper-large-v3-turbo"
    http_timeout_s: float = 60.0

    # Hard limits (Bot API getFile <= 20 MB, Groq free tier <= 25 MB)
    bot_api_max_file_mb: int = 20
    groq_max_file_mb: int = 25

    cache_ttl_days: int = 7
    heartbeat_interval_s: int = 3600
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()
