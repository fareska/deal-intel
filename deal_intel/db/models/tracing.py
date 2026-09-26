from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from deal_intel.contracts.tracing import SpanKind, SpanStatus
from deal_intel.db.base import Base
from deal_intel.db.models.checks import values_check


class TraceSpanRow(Base):
    """No foreign keys: a span write that fails is dropped, and its children must still land."""

    __tablename__ = "trace_spans"
    __table_args__ = (
        values_check("kind", SpanKind, "ck_trace_spans_kind"),
        values_check("status", SpanStatus, "ck_trace_spans_status"),
        Index("ix_trace_spans_run_started", "run_id", "started_at"),
    )

    span_id: Mapped[str] = mapped_column(primary_key=True)
    parent_span_id: Mapped[str | None]
    run_id: Mapped[str | None]
    kind: Mapped[str]
    name: Mapped[str]
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str]
    attributes: Mapped[dict[str, Any]] = mapped_column(JSONB)
