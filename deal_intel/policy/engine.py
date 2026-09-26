"""Rule evaluation, approval rows, decisions, and the expiry sweep.

`evaluate` is pure. Everything else works in the caller's transaction: a decision locks its
approval row, appends an event, re-renders the brief, and completes the run once nothing is
pending. Escalated approvals never block completion (plan change C8).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from deal_intel.contracts.access import AccessLevel
from deal_intel.contracts.approvals import (
    ApprovalRecord,
    ApprovalRequest,
    ApprovalStatus,
    ApproverRole,
    Decision,
    DecisionResult,
    RuleId,
)
from deal_intel.contracts.brief import BriefSource
from deal_intel.contracts.runs import ApprovalsResolved, RunState
from deal_intel.db.models import ApprovalEventRow, ApprovalRow
from deal_intel.orchestration.persistence import get_run, latest_attempt, lock_run, transition
from deal_intel.permissions.lookups import find_user
from deal_intel.policy.eligibility import is_eligible
from deal_intel.policy.facts import RecommendationFacts
from deal_intel.policy.rules import Rule, Thresholds, fired_rules
from deal_intel.policy.store import approval_record, pending_approval_count
from deal_intel.rendering.brief import render_and_store

RULE_ORDER: tuple[RuleId, ...] = tuple(RuleId)
EXPIRED_NOTE = "approval window elapsed"


class ApprovalNotFound(LookupError):
    pass


class NotEligible(PermissionError):
    pass


class ApprovalNotPending(RuntimeError):
    def __init__(self, status: ApprovalStatus) -> None:
        super().__init__(f"approval is {status.value}, not pending")
        self.status = status


class RunNotAwaitingApproval(RuntimeError):
    pass


@dataclass(frozen=True)
class Evaluation:
    requests: list[ApprovalRequest]
    fired_rules: dict[str, list[RuleId]]


@dataclass(frozen=True)
class Settlement:
    pending_remaining: int
    run_state: RunState
    brief_version: int


def evaluate(facts: Sequence[RecommendationFacts], thresholds: Thresholds) -> Evaluation:
    fired = {item.recommendation_id: fired_rules(item, thresholds) for item in facts}
    by_id = {item.recommendation_id: item for item in facts}
    groups: dict[tuple[str, ApproverRole], list[tuple[RecommendationFacts, Rule]]] = {}
    for item in facts:
        for rule in fired[item.recommendation_id]:
            for role in rule.required_roles:
                groups.setdefault((item.subject_id, role), []).append((item, rule))
    requests = [
        approval_request(subject, role, members, by_id[subject])
        for (subject, role), members in groups.items()
    ]
    fired_ids = {rid: [rule.id for rule in rules] for rid, rules in fired.items() if rules}
    return Evaluation(requests=requests, fired_rules=fired_ids)


def approval_request(
    subject: str,
    role: ApproverRole,
    members: Sequence[tuple[RecommendationFacts, Rule]],
    subject_facts: RecommendationFacts,
) -> ApprovalRequest:
    rule_ids = unique(rule.id for _, rule in members)
    return ApprovalRequest(
        subject_id=subject,
        recommendation_ids=unique(item.recommendation_id for item, _ in members),
        required_role=role,
        rule_ids=sorted(rule_ids, key=RULE_ORDER.index),
        proposed_values=subject_facts.proposed_values,
        summary=subject_facts.summary,
        evidence_ids=unique(chunk_id for item, _ in members for chunk_id in item.evidence_ids),
    )


def unique[ItemT](items: Iterable[ItemT]) -> list[ItemT]:
    return list(dict.fromkeys(items))


def required_roles(requests: Iterable[ApprovalRequest]) -> list[ApproverRole]:
    return unique(request.required_role for request in requests)


def approval_records(
    run_id: str,
    requests: Sequence[ApprovalRequest],
    eligible: Mapping[ApproverRole, list[str]],
    account_id: str,
    brief_level: AccessLevel,
    now: datetime,
    expiry_hours: int,
) -> list[ApprovalRecord]:
    return [
        ApprovalRecord(
            approval_id=uuid4().hex,
            run_id=run_id,
            subject_id=request.subject_id,
            recommendation_ids=request.recommendation_ids,
            rule_ids=request.rule_ids,
            required_role=request.required_role,
            account_id=account_id,
            access_level=brief_level,
            eligible_user_ids=eligible[request.required_role],
            status=initial_status(eligible[request.required_role]),
            proposed_values=request.proposed_values,
            summary=request.summary,
            evidence_ids=request.evidence_ids,
            created_at=now,
            expires_at=now + timedelta(hours=expiry_hours),
        )
        for request in requests
    ]


def initial_status(eligible_user_ids: Sequence[str]) -> ApprovalStatus:
    return ApprovalStatus.PENDING if eligible_user_ids else ApprovalStatus.ESCALATED


def decide(
    session: Session,
    approval_id: str,
    actor_user_id: str,
    decision: Decision,
    note: str,
    now: datetime,
) -> DecisionResult:
    row = lock_approval(session, approval_id)
    require_pending(row, now)
    require_awaiting_run(session, row.run_id)
    require_eligible(session, row, actor_user_id)
    record_outcome(session, row, ApprovalStatus(decision.value), actor_user_id, note, now)
    settlement = settle_run(session, row.run_id, now)
    return DecisionResult(
        approval=approval_record(row),
        pending_remaining=settlement.pending_remaining,
        run_state=settlement.run_state,
        brief_version=settlement.brief_version,
    )


def lock_approval(session: Session, approval_id: str) -> ApprovalRow:
    row = session.get(ApprovalRow, approval_id, with_for_update=True)
    if row is None:
        raise ApprovalNotFound(approval_id)
    return row


def require_pending(row: ApprovalRow, now: datetime) -> None:
    status = ApprovalStatus(row.status)
    if status is not ApprovalStatus.PENDING:
        raise ApprovalNotPending(status)
    # The sweep may not have run yet; an overdue approval is not decidable either way.
    if row.expires_at <= now:
        raise ApprovalNotPending(ApprovalStatus.EXPIRED)


def require_awaiting_run(session: Session, run_id: str) -> None:
    if get_run(session, run_id).state is not RunState.AWAITING_APPROVAL:
        raise RunNotAwaitingApproval(run_id)


def require_eligible(session: Session, row: ApprovalRow, actor_user_id: str) -> None:
    """Checked against the stored list and the actor's current profile, so a revoked
    permission takes effect before the approval expires."""
    actor = find_user(session, actor_user_id)
    if (
        actor is None
        or actor_user_id not in row.eligible_user_ids
        or not is_eligible(
            actor, ApproverRole(row.required_role), row.account_id, AccessLevel(row.access_level)
        )
    ):
        raise NotEligible(actor_user_id)


def record_outcome(
    session: Session,
    row: ApprovalRow,
    outcome: ApprovalStatus,
    actor_user_id: str | None,
    note: str,
    now: datetime,
) -> None:
    session.add(
        ApprovalEventRow(
            approval_id=row.approval_id,
            outcome=outcome.value,
            actor_user_id=actor_user_id,
            note=note,
            at=now,
        )
    )
    row.status = outcome.value


def settle_run(session: Session, run_id: str, now: datetime) -> Settlement:
    """Re-renders the brief for the new approval state and completes the run once no approval
    is pending."""
    run = lock_run(session, run_id)
    pending = pending_approval_count(session, run_id)
    if pending == 0 and run.state == RunState.AWAITING_APPROVAL.value:
        attempt = latest_attempt(session, run_id)
        transition(
            session, run, RunState.COMPLETED, attempt, ApprovalsResolved(pending_remaining=0), now
        )
    version = render_and_store(session, run_id, BriefSource.APPROVAL_UPDATE, now)
    return Settlement(
        pending_remaining=pending, run_state=RunState(run.state), brief_version=version
    )


def expire_overdue(session: Session, now: datetime) -> list[str]:
    """Marks overdue pending approvals expired and settles every run left waiting on them."""
    statement = (
        select(ApprovalRow)
        .where(
            ApprovalRow.status == ApprovalStatus.PENDING.value,
            ApprovalRow.expires_at <= now,
        )
        .order_by(ApprovalRow.approval_id)
        .with_for_update(skip_locked=True)
    )
    expired = list(session.scalars(statement))
    for row in expired:
        record_outcome(session, row, ApprovalStatus.EXPIRED, None, EXPIRED_NOTE, now)
    for run_id in sorted({row.run_id for row in expired}):
        if get_run(session, run_id).state is RunState.AWAITING_APPROVAL:
            settle_run(session, run_id, now)
    return [row.approval_id for row in expired]
