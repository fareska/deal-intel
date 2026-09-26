from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Index, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from deal_intel.contracts.llm import ModelRole, StopReason
from deal_intel.db.base import Base
from deal_intel.db.models.checks import values_check

COST_PRECISION = 10
COST_SCALE = 6


class LlmCallRow(Base):
    """`span_id` has no foreign key: with the no-op tracer, or after a dropped span write, the
    span row does not exist, and cost accounting must not depend on tracing."""

    __tablename__ = "llm_calls"
    __table_args__ = (
        values_check("model_role", ModelRole, "ck_llm_calls_model_role"),
        values_check("stop_reason", StopReason, "ck_llm_calls_stop_reason"),
        Index("ix_llm_calls_run_id", "run_id"),
    )

    call_id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str | None]
    span_id: Mapped[str | None]
    agent_name: Mapped[str]
    prompt_version: Mapped[str]
    prompt_hash: Mapped[str]
    input_hash: Mapped[str]
    model: Mapped[str]
    model_role: Mapped[str]
    turn: Mapped[int]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cache_creation_input_tokens: Mapped[int]
    cache_read_input_tokens: Mapped[int]
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(COST_PRECISION, COST_SCALE))
    stop_reason: Mapped[str]
    latency_ms: Mapped[int]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AgentOutputCacheRow(Base):
    __tablename__ = "agent_output_cache"

    cache_key: Mapped[str] = mapped_column(primary_key=True)
    agent_name: Mapped[str]
    prompt_version: Mapped[str]
    prompt_hash: Mapped[str]
    model: Mapped[str]
    input_hash: Mapped[str]
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    raw_text: Mapped[str]
    guardrail_results: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    tool_evidence_ids: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
