from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, read from environment variables and an optional .env file."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/prices"
    redis_url: str = "redis://localhost:6379/0"

    deribit_base_url: str = "https://www.deribit.com/api/v2"
    # The upper bounds are part of the retry budget (see app/worker/tasks.py): raising
    # them could make a task attempt outlive the one-minute slot.
    deribit_timeout_seconds: float = Field(default=10.0, gt=0, le=10)
    database_connect_timeout_seconds: float = Field(default=5.0, gt=0, le=5)
    database_command_timeout_seconds: float = Field(default=5.0, gt=0, le=5)


@lru_cache
def get_settings() -> Settings:
    """Build settings once per process; FastAPI and Celery both obtain them through this call."""
    return Settings()
