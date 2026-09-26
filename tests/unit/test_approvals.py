"""Approval decisions and the expiry sweep, over runs that reached AWAITING_APPROVAL."""

from datetime import timedelta

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError, OperationalError

from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.approvals import ApprovalStatus, ApproverRole, Decision
from deal_intel.contracts.brief import BriefSource
from deal_intel.contracts.runs import RunState
from deal_intel.db.models import ApprovalEventRow, ApprovalRow, RunEventRow, RunRow, UserRow
from deal_intel.llm.errors import ModelRefusal
from deal_intel.policy.engine import (
    EXPIRED_NOTE,
    ApprovalNotPending,
    NotEligible,
    RunNotAwaitingApproval,
    decide,
    expire_overdue,
)

DEAL_DESK_APPROVER = "USR-5005"
SALES_LEADER = "USR-5004"
OWNER = "USR-5003"
HIGH_DISCOUNT_NOTE = "pricing:PN-4004"
ALTERNATIVE_NOTE = "pricing:PN-4005"
LOCK_TIMEOUT = "SET LOCAL lock_timeout = '200ms'"


@pytest.fixture
def awaiting(run_bench) -> str:
    record = run_bench.run(run_bench.agents("OPP-1003"), OWNER, "OPP-1003")
    assert record.state is RunState.AWAITING_APPROVAL
    return record.run_id


def approval_id(run_bench, run_id: str, subject: str, role: ApproverRole) -> str:
    with run_bench.session_factory() as session:
        return session.scalars(
            select(ApprovalRow.approval_id).where(
                ApprovalRow.run_id == run_id,
                ApprovalRow.subject_id == subject,
                ApprovalRow.required_role == role,
            )
        ).one()


def decide_now(run_bench, approval: str, actor: str, decision: Decision = Decision.APPROVED):
    with run_bench.session_factory.begin() as session:
        return decide(session, approval, actor, decision, "reviewed", run_bench.clock())


def test_decisions_rerender_the_brief_and_complete_the_run(run_bench, awaiting: str) -> None:
    first = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)
    second = approval_id(run_bench, awaiting, ALTERNATIVE_NOTE, ApproverRole.DEAL_DESK)

    approved = decide_now(run_bench, first, DEAL_DESK_APPROVER)

    assert (approved.pending_remaining, approved.run_state) == (1, RunState.AWAITING_APPROVAL)
    brief = run_bench.briefs(awaiting)[-1]
    assert (brief.version, brief.source) == (2, BriefSource.APPROVAL_UPDATE.value)
    decided_on = run_bench.clock().date().isoformat()
    assert f"[APPROVED by {DEAL_DESK_APPROVER} on {decided_on}]" in brief.markdown

    rejected = decide_now(run_bench, second, DEAL_DESK_APPROVER, Decision.REJECTED)

    assert (rejected.pending_remaining, rejected.run_state) == (0, RunState.COMPLETED)
    final = run_bench.briefs(awaiting)[-1]
    assert final.version == rejected.brief_version == 3
    assert "[REJECTED]" in final.markdown
    assert "[ESCALATED: no eligible approver for sales_leader]" in final.markdown
    assert run_bench.record(awaiting).state is RunState.COMPLETED
    assert run_bench.events(awaiting)[-1].detail == {"pending_remaining": 0}


@pytest.mark.parametrize("actor", [SALES_LEADER, OWNER, "USR-9999"])
def test_only_an_eligible_approver_may_decide(run_bench, awaiting: str, actor: str) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)

    with pytest.raises(NotEligible):
        decide_now(run_bench, approval, actor)


def test_revoked_account_access_takes_effect_before_expiry(run_bench, awaiting: str) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)
    with run_bench.session_factory.begin() as session:
        session.execute(
            update(UserRow)
            .where(UserRow.user_id == DEAL_DESK_APPROVER)
            .values(allowed_account_ids=["ACC-2001"])
        )

    with pytest.raises(NotEligible):
        decide_now(run_bench, approval, DEAL_DESK_APPROVER)


def test_decided_approval_cannot_be_decided_again(run_bench, awaiting: str) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)
    decide_now(run_bench, approval, DEAL_DESK_APPROVER)

    with pytest.raises(ApprovalNotPending) as raised:
        decide_now(run_bench, approval, DEAL_DESK_APPROVER, Decision.REJECTED)

    assert raised.value.status is ApprovalStatus.APPROVED


def test_escalated_approval_is_not_decidable(run_bench, awaiting: str) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.SALES_LEADER)

    with pytest.raises(ApprovalNotPending) as raised:
        decide_now(run_bench, approval, SALES_LEADER)

    assert raised.value.status is ApprovalStatus.ESCALATED


def test_overdue_approval_is_not_decidable_before_the_sweep(run_bench, awaiting: str) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)
    run_bench.clock.advance(timedelta(hours=run_bench.settings.approval_expiry_hours))

    with pytest.raises(ApprovalNotPending) as raised:
        decide_now(run_bench, approval, DEAL_DESK_APPROVER)

    assert raised.value.status is ApprovalStatus.EXPIRED


def test_decision_on_a_run_no_longer_awaiting_is_refused(run_bench, awaiting: str) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)
    with run_bench.session_factory.begin() as session:
        session.execute(
            update(RunRow).where(RunRow.run_id == awaiting).values(state=RunState.FAILED.value)
        )

    with pytest.raises(RunNotAwaitingApproval):
        decide_now(run_bench, approval, DEAL_DESK_APPROVER)


def test_concurrent_decision_waits_on_the_row_lock(run_bench, awaiting: str) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)
    now = run_bench.clock()
    with run_bench.session_factory() as holder, run_bench.session_factory() as contender:
        with holder.begin():
            decide(holder, approval, DEAL_DESK_APPROVER, Decision.APPROVED, "first", now)
            with contender.begin():
                contender.execute(text(LOCK_TIMEOUT))
                with pytest.raises(OperationalError):
                    decide(contender, approval, DEAL_DESK_APPROVER, Decision.REJECTED, "x", now)

    with pytest.raises(ApprovalNotPending):
        decide_now(run_bench, approval, DEAL_DESK_APPROVER, Decision.REJECTED)
    with run_bench.session_factory() as session:
        outcomes = session.scalars(
            select(ApprovalEventRow.outcome).where(ApprovalEventRow.approval_id == approval)
        )
        assert list(outcomes) == [ApprovalStatus.APPROVED.value]


def test_expiry_sweep_expires_overdue_approvals_and_settles_the_run(
    run_bench, awaiting: str
) -> None:
    pending = {
        approval_id(run_bench, awaiting, subject, ApproverRole.DEAL_DESK)
        for subject in (HIGH_DISCOUNT_NOTE, ALTERNATIVE_NOTE)
    }
    run_bench.clock.advance(timedelta(hours=run_bench.settings.approval_expiry_hours))

    with run_bench.session_factory.begin() as session:
        expired = expire_overdue(session, run_bench.clock())

    assert set(expired) == pending
    assert run_bench.record(awaiting).state is RunState.COMPLETED
    assert run_bench.briefs(awaiting)[-1].markdown.count("[APPROVAL EXPIRED]") >= len(pending)
    with run_bench.session_factory() as session:
        events = list(
            session.scalars(
                select(ApprovalEventRow).where(ApprovalEventRow.approval_id.in_(pending))
            )
        )
    assert {(event.outcome, event.actor_user_id, event.note) for event in events} == {
        (ApprovalStatus.EXPIRED.value, None, EXPIRED_NOTE)
    }


def test_expiry_sweep_leaves_approvals_inside_their_window(run_bench, awaiting: str) -> None:
    with run_bench.session_factory.begin() as session:
        assert expire_overdue(session, run_bench.clock()) == []

    assert run_bench.record(awaiting).state is RunState.AWAITING_APPROVAL


def test_degraded_run_completes_once_its_human_reviews_are_decided(run_bench) -> None:
    agents = run_bench.agents("OPP-1001")
    agents.failures[AgentName.CONVERSATION_INTELLIGENCE] = ModelRefusal("refused")
    record = run_bench.run(agents, "USR-5001", "OPP-1001")
    with run_bench.session_factory() as session:
        approvals = list(
            session.scalars(
                select(ApprovalRow.approval_id).where(ApprovalRow.run_id == record.run_id)
            )
        )

    results = [decide_now(run_bench, approval, SALES_LEADER) for approval in approvals]

    assert results[-1].run_state is RunState.COMPLETED
    assert run_bench.record(record.run_id).degraded


@pytest.mark.parametrize(
    "statement",
    [
        update(ApprovalEventRow).values(note="rewritten"),
        update(RunEventRow).values(detail={}),
    ],
)
def test_event_tables_are_append_only(run_bench, awaiting: str, statement) -> None:
    approval = approval_id(run_bench, awaiting, HIGH_DISCOUNT_NOTE, ApproverRole.DEAL_DESK)
    decide_now(run_bench, approval, DEAL_DESK_APPROVER)

    with pytest.raises(DBAPIError), run_bench.session_factory.begin() as session:
        session.execute(statement)
