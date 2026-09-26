from enum import StrEnum
from functools import lru_cache
from typing import Literal

from pydantic import PostgresDsn, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from deal_intel.contracts.evidence import ChunkKind
from deal_intel.contracts.reference import LowMediumHigh


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

    pricing_not_required_status: str = "not_required"
    pricing_high_risk_level: LowMediumHigh = LowMediumHigh.HIGH
    # Read from the environment as JSON, for example RELIABILITY_WEIGHTS='{"slack": 0.6, ...}'.
    reliability_weights: dict[ChunkKind, float] = {
        ChunkKind.SFDC_OPP: 1.0,
        ChunkKind.SFDC_ACCOUNT: 1.0,
        ChunkKind.CONTACT: 1.0,
        ChunkKind.PRICING: 1.0,
        ChunkKind.POLICY: 1.0,
        ChunkKind.GONG_SUMMARY: 0.9,
        ChunkKind.TRANSCRIPT: 0.85,
        ChunkKind.SLACK: 0.7,
    }
    search_k: int = 10

    app_env: AppEnv = AppEnv.DEV
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"

    @field_validator("reliability_weights")
    @classmethod
    def require_weight_for_every_kind(
        cls, weights: dict[ChunkKind, float]
    ) -> dict[ChunkKind, float]:
        missing = sorted(set(ChunkKind) - set(weights))
        if missing:
            raise ValueError(f"reliability_weights is missing {missing}")
        return weights


@lru_cache
def get_settings() -> Settings:
    return Settings()
