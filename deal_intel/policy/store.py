"""Approval rows in and out. Kept apart from the engine so the renderer can read approvals
without depending on the code that decides them."""

from collections.abc import Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from deal_intel.contracts.approvals import (
    ApprovalEvent,
    ApprovalRecord,
    ApprovalStatus,
    ApprovalView,
)
from deal_intel.db.models import ApprovalEventRow, ApprovalRow
from deal_intel.db.writes import column_values


def add_approvals(session: Session, records: Iterable[ApprovalRecord]) -> None:
    session.add_all(ApprovalRow(**column_values(record)) for record in records)


def approval_record(row: ApprovalRow) -> ApprovalRecord:
    return ApprovalRecord.model_validate(row, from_attributes=True)


def approval_views(session: Session, approval_ids: Sequence[str]) -> list[ApprovalView]:
    """In the order given, which is the order the policy stage raised them."""
    rows = session.scalars(select(ApprovalRow).where(ApprovalRow.approval_id.in_(approval_ids)))
    by_id = {row.approval_id: row for row in rows}
    last_events = latest_events(session, approval_ids)
    return [
        ApprovalView(
            approval=approval_record(by_id[approval_id]), last_event=last_events.get(approval_id)
        )
        for approval_id in approval_ids
        if approval_id in by_id
    ]


def latest_events(session: Session, approval_ids: Sequence[str]) -> dict[str, ApprovalEvent]:
    statement = (
        select(ApprovalEventRow)
        .where(ApprovalEventRow.approval_id.in_(approval_ids))
        .order_by(ApprovalEventRow.event_id)
    )
    return {
        row.approval_id: ApprovalEvent.model_validate(row, from_attributes=True)
        for row in session.scalars(statement)
    }


def pending_approval_count(session: Session, run_id: str) -> int:
    statement = select(func.count()).where(
        ApprovalRow.run_id == run_id, ApprovalRow.status == ApprovalStatus.PENDING.value
    )
    return session.scalar(statement) or 0
