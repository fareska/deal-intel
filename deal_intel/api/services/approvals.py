"""Approval listing and decisions. UI and HTTP routes call these."""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import any_, select
from sqlalchemy.orm import Session

from deal_intel.api.errors import Forbidden, NotFound
from deal_intel.contracts.api import ApprovalResponse, DecisionRequest
from deal_intel.contracts.approvals import ApprovalRecord, ApprovalStatus
from deal_intel.contracts.brief import Brief, EvidenceEntry
from deal_intel.contracts.runs import RunRecord
from deal_intel.db.models import ApprovalRow
from deal_intel.observability.tracing import utc_now
from deal_intel.orchestration.persistence import RunNotFound, get_run
from deal_intel.permissions.read import can_read_run
from deal_intel.policy.engine import (
    ApprovalNotFound,
    NotEligible,
    decide,
    expire_overdue,
)
from deal_intel.policy.store import approval_record, latest_events
from deal_intel.rendering.brief import latest_brief

LISTABLE_STATUSES: tuple[ApprovalStatus, ...] = (
    ApprovalStatus.PENDING,
    ApprovalStatus.ESCALATED,
)


def approvals_for(
    session: Session,
    user_id: str,
    status: ApprovalStatus | None = None,
    now: datetime | None = None,
) -> list[ApprovalResponse]:
    expire_overdue(session, now or utc_now())
    session.flush()
    wanted = (status,) if status is not None else LISTABLE_STATUSES
    rows = list(session.scalars(eligible_approvals_query(user_id, wanted)))
    events = latest_events(session, [row.approval_id for row in rows])
    views: list[ApprovalResponse] = []
    for row in rows:
        record = approval_record(row)
        try:
            run = get_run(session, record.run_id)
        except RunNotFound:
            continue
        if not can_read_run(session, user_id, run):
            continue
        note = events[record.approval_id].note if record.approval_id in events else None
        views.append(approval_response(session, record, run, note))
    return views


def eligible_approvals_query(user_id: str, statuses: Sequence[ApprovalStatus]):
    return (
        select(ApprovalRow)
        .where(
            user_id == any_(ApprovalRow.eligible_user_ids),
            ApprovalRow.status.in_([item.value for item in statuses]),
        )
        .order_by(ApprovalRow.created_at, ApprovalRow.approval_id)
    )


def decide_approval(
    session: Session,
    approval_id: str,
    request: DecisionRequest,
    now: datetime | None = None,
) -> ApprovalResponse:
    try:
        result = decide(
            session,
            approval_id,
            request.user_id,
            request.decision,
            request.note,
            now or utc_now(),
        )
    except ApprovalNotFound as error:
        raise NotFound() from error
    except NotEligible as error:
        raise Forbidden() from error
    session.commit()
    run = get_run(session, result.approval.run_id)
    return approval_response(session, result.approval, run, request.note)


def approval_response(
    session: Session, record: ApprovalRecord, run: RunRecord, note: str | None
) -> ApprovalResponse:
    brief = loaded_brief(session, record.run_id)
    return ApprovalResponse(
        approval_id=record.approval_id,
        run_id=record.run_id,
        subject_id=record.subject_id,
        recommendation_ids=record.recommendation_ids,
        required_role=record.required_role,
        rule_ids=record.rule_ids,
        proposed_values=record.proposed_values,
        summary=record.summary,
        recommendation_text=record.summary,
        rationale=action_rationale(brief, record.recommendation_ids),
        evidence_ids=record.evidence_ids,
        evidence_excerpts=evidence_excerpts(brief, record.evidence_ids),
        status=record.status,
        eligible_user_ids=record.eligible_user_ids,
        expires_at=record.expires_at,
        run_state=run.state,
        note=note,
    )


def loaded_brief(session: Session, run_id: str) -> Brief | None:
    row = latest_brief(session, run_id)
    return None if row is None else Brief.model_validate(row.json)


def action_rationale(brief: Brief | None, recommendation_ids: Sequence[str]) -> str:
    if brief is None:
        return ""
    wanted = set(recommendation_ids)
    for action in brief.next_actions.actions:
        if action.id in wanted:
            return action.rationale.text
    return ""


def evidence_excerpts(brief: Brief | None, evidence_ids: Sequence[str]) -> list[EvidenceEntry]:
    if brief is None:
        return []
    wanted = set(evidence_ids)
    return [entry for entry in brief.source_evidence.entries if entry.chunk_id in wanted]
