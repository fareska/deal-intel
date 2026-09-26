from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Numeric, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from deal_intel.contracts.runs import RunState, StageName, StageStatus
from deal_intel.db.base import Base
from deal_intel.db.models.checks import values_check
from deal_intel.db.models.llm import COST_PRECISION, COST_SCALE

COST = Numeric(COST_PRECISION, COST_SCALE)


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (
        values_check("state", RunState, "ck_runs_state"),
        Index("ix_runs_state", "state"),
        Index("ix_runs_idempotency_key", "idempotency_key"),
    )

    run_id: Mapped[str] = mapped_column(primary_key=True)
    opportunity_id: Mapped[str]
    user_id: Mapped[str]
    fresh: Mapped[bool]
    state: Mapped[str]
    degraded: Mapped[bool]
    snapshot_id: Mapped[str | None]
    evidence_hash: Mapped[str | None]
    # Not unique: a `fresh` run repeats the key of the run it deliberately does not reuse.
    idempotency_key: Mapped[str | None]
    reused_from_run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id"))
    input_tokens: Mapped[int]
    cost_usd: Mapped[Decimal] = mapped_column(COST)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunEventRow(Base):
    """Append-only: `orchestration.persistence` only ever adds rows, and a database trigger
    rejects UPDATE and DELETE."""

    __tablename__ = "run_events"
    __table_args__ = (
        values_check("to_state", RunState, "ck_run_events_to_state"),
        values_check("from_state", RunState, "ck_run_events_from_state"),
        Index("ix_run_events_run_id", "run_id", "event_id"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"))
    attempt: Mapped[int]
    from_state: Mapped[str | None]
    to_state: Mapped[str]
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class StageOutputRow(Base):
    __tablename__ = "stage_outputs"
    __table_args__ = (
        values_check("stage", StageName, "ck_stage_outputs_stage"),
        values_check("status", StageStatus, "ck_stage_outputs_status"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"), primary_key=True)
    stage: Mapped[str] = mapped_column(primary_key=True)
    attempt: Mapped[int] = mapped_column(primary_key=True)
    status: Mapped[str]
    output_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    input_hash: Mapped[str | None]
    model: Mapped[str | None]
    prompt_version: Mapped[str | None]
    prompt_hash: Mapped[str | None]
    input_tokens: Mapped[int]
    output_tokens: Mapped[int]
    cost_usd: Mapped[Decimal] = mapped_column(COST)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
