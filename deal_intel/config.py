from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LlmClientKind(StrEnum):
    ANTHROPIC = "anthropic"
    FAKE = "fake"


class AppEnv(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class Settings(BaseSettings):
    # extra="ignore": .env is shared with the Postgres container and holds POSTGRES_* variables.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: PostgresDsn
    test_database_url: PostgresDsn | None = None

    anthropic_api_key: SecretStr | None = None
    llm_client: LlmClientKind = LlmClientKind.ANTHROPIC
    model_strategy: str | None = None
    model_extraction: str | None = None
    strategy_effort: Literal["low", "medium", "high", "max"] = "high"
    max_tool_calls: int = 4
    run_input_token_budget: int = 80_000
    daily_cost_budget_usd: float = 20.0
    record_fixtures: bool = False

    run_executor_workers: int = 2
    approval_expiry_hours: int = 168
    embeddings_enabled: bool = False

    app_env: AppEnv = AppEnv.DEV
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"


@lru_cache
def get_settings() -> Settings:
    return Settings()
