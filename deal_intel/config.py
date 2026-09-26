from decimal import Decimal
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import PostgresDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from deal_intel.contracts.evidence import ChunkKind
from deal_intel.contracts.llm import Effort, ModelPrice
from deal_intel.contracts.reference import LowMediumHigh

# Confirmed against platform.claude.com/docs/en/about-claude/models/overview (2026-09-26).
DEFAULT_MODEL_STRATEGY = "claude-opus-5-5"
DEFAULT_MODEL_EXTRACTION = "claude-haiku-4-5-20251001"


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
    model_strategy: str = DEFAULT_MODEL_STRATEGY
    model_extraction: str = DEFAULT_MODEL_EXTRACTION
    strategy_effort: Effort = Effort.HIGH
    # Read from the environment as JSON, for example
    # MODEL_PRICES_USD_PER_MTOK='{"<model>": {"input": 1, "output": 5, ...}}'.
    # cache_write is the 5-minute rate, the only cache TTL the client requests.
    model_prices_usd_per_mtok: dict[str, ModelPrice] = {
        DEFAULT_MODEL_STRATEGY: ModelPrice(
            input=Decimal("4"),
            output=Decimal("20"),
            cache_write=Decimal("5"),
            cache_read=Decimal("0.20"),
        ),
        DEFAULT_MODEL_EXTRACTION: ModelPrice(
            input=Decimal("1"),
            output=Decimal("5"),
            cache_write=Decimal("1.25"),
            cache_read=Decimal("0.10"),
        ),
    }
    max_tool_calls: int = 4
    llm_feedback_retries: int = 2
    llm_refusal_fallback: bool = False
    # Generous because adaptive thinking at high effort can run for minutes on one turn.
    llm_timeout_seconds: float = 600.0
    llm_sdk_max_retries: int = 3
    llm_fixtures_root: Path = Path("tests/fixtures/llm")
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
    # Evidence pack budgets per agent, in estimated tokens.
    budget_conversation_intelligence_tokens: int = 12_000
    budget_stakeholder_map_tokens: int = 6_000
    budget_negotiation_strategy_tokens: int = 6_000

    # The strategy prompt names these fields and the policy engine reads them, so a threshold
    # shown to the model can never differ from the one that routes approvals.
    policy_discount_deal_desk_threshold_pct: Decimal = Decimal(10)
    policy_discount_sales_leader_threshold_pct: Decimal = Decimal(15)
    policy_renewal_uplift_floor_pct: Decimal = Decimal(0)

    app_env: AppEnv = AppEnv.DEV
    log_level: str = "INFO"
    api_base_url: str = "http://localhost:8000"
    api_timeout_seconds: float = 30.0
    api_max_request_body_bytes: int = 65_536

    @field_validator("reliability_weights")
    @classmethod
    def require_weight_for_every_kind(
        cls, weights: dict[ChunkKind, float]
    ) -> dict[ChunkKind, float]:
        missing = sorted(set(ChunkKind) - set(weights))
        if missing:
            raise ValueError(f"reliability_weights is missing {missing}")
        return weights

    @model_validator(mode="after")
    def require_price_for_routed_models(self) -> Self:
        unpriced = sorted(
            {self.model_strategy, self.model_extraction} - set(self.model_prices_usd_per_mtok)
        )
        if unpriced:
            raise ValueError(f"model_prices_usd_per_mtok has no price for {unpriced}")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
