from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.approvals import APPROVAL_EVENT_OUTCOMES, ApprovalStatus, ApproverRole
from deal_intel.db.base import Base
from deal_intel.db.models.checks import values_check


class ApprovalRow(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        values_check("status", ApprovalStatus, "ck_approvals_status"),
        values_check("required_role", ApproverRole, "ck_approvals_required_role"),
        values_check("access_level", AccessLevel, "ck_approvals_access_level"),
        # One decision per subject and role (plan change C12).
        UniqueConstraint("run_id", "subject_id", "required_role", name="uq_approvals_subject_role"),
        Index("ix_approvals_status_expires_at", "status", "expires_at"),
        Index("ix_approvals_run_id", "run_id"),
    )

    approval_id: Mapped[str] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id"))
    subject_id: Mapped[str]
    recommendation_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    rule_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    required_role: Mapped[str]
    account_id: Mapped[str]
    access_level: Mapped[str]
    eligible_user_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    status: Mapped[str]
    proposed_values: Mapped[dict[str, Any]] = mapped_column(JSONB)
    summary: Mapped[str]
    evidence_ids: Mapped[list[str]] = mapped_column(ARRAY(Text))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalEventRow(Base):
    """Append-only, like `run_events`: added by `policy.engine` only, guarded by a trigger."""

    __tablename__ = "approval_events"
    __table_args__ = (
        values_check("outcome", APPROVAL_EVENT_OUTCOMES, "ck_approval_events_outcome"),
        Index("ix_approval_events_approval_id", "approval_id", "event_id"),
    )

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    approval_id: Mapped[str] = mapped_column(ForeignKey("approvals.approval_id"))
    outcome: Mapped[str]
    actor_user_id: Mapped[str | None]
    note: Mapped[str]
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
