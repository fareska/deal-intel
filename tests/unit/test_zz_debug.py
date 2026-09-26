"""M4 demo-scenario checks over live-recorded fixtures.

Until `scripts/record_fixtures.py` has been run the module skips; once fixtures exist, a
missing one means they are stale and the test fails. Hand-authored stub runs cover the same
Done-when items deterministically in `test_policy`, `test_approvals`, and `test_runner`.
"""

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.base import AgentRuntime
from deal_intel.agents.pipeline import AgentOutputs, run_all_agents
from deal_intel.config import get_settings
from deal_intel.contracts.access import Allowed
from deal_intel.contracts.agents.common import LLM_AGENTS, AgentName
from deal_intel.contracts.approvals import ApprovalStatus, ApproverRole, Decision
from deal_intel.contracts.brief import BriefSource
from deal_intel.contracts.runs import RunState
from deal_intel.db.models import ApprovalRow
from deal_intel.evaluation.scenarios import FIXTURES_NOT_RECORDED, FIXTURES_STALE, DemoPair
from deal_intel.llm.client import LlmClient
from deal_intel.llm.errors import FixtureMissing
from deal_intel.llm.fake_client import FakeLlmProvider
from deal_intel.llm.fixtures import fixtures_recorded
from deal_intel.observability.tracing import NoopTracer
from deal_intel.permissions.gate import authorize
from deal_intel.policy.engine import decide
from deal_intel.retrieval.retriever import ScopedRetriever

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"
PILOT_PAIR = DemoPair(user_id="USR-5001", opportunity_id="OPP-1001")
ECLIPSE_PAIR = DemoPair(user_id="USR-5003", opportunity_id="OPP-1003")
PILOT_SEQUENCING_SLACK = "slack:SLK-1001-03"
HIGH_DISCOUNT_NOTE = "pricing:PN-4004"
DEAL_DESK_APPROVER = "USR-5005"
CI = AgentName.CONVERSATION_INTELLIGENCE
SM = AgentName.STAKEHOLDER_MAP
NS = AgentName.NEGOTIATION_STRATEGY

pytestmark = pytest.mark.skipif(
    not fixtures_recorded(COMMITTED_FIXTURES, LLM_AGENTS), reason=FIXTURES_NOT_RECORDED
)


def replay(
    session: Session, session_factory: sessionmaker[Session], pair: DemoPair
) -> AgentOutputs:
    access = authorize(session, pair.user_id, pair.opportunity_id)
    assert isinstance(access, Allowed)
    settings, tracer = get_settings(), NoopTracer()
    llm = LlmClient(
        FakeLlmProvider(COMMITTED_FIXTURES),
        settings=settings,
        session_factory=session_factory,
        tracer=tracer,
    )
    runtime = AgentRuntime(
        retriever=ScopedRetriever(session, access.scope),
        llm=llm,
        tracer=tracer,
        settings=settings,
    )
    try:
        return run_all_agents(session, runtime)
    except FixtureMissing as error:
        pytest.fail(f"{pair.label}: {FIXTURES_STALE} ({error})")


def stub_replayed(run_bench, pair: DemoPair):
    with run_bench.session_factory() as session:
        outputs = replay(session, run_bench.session_factory, pair)
    agents = run_bench.agents(pair.opportunity_id)
    agents.outputs[CI] = outputs.findings.output
    agents.outputs[SM] = outputs.stakeholders.output
    agents.outputs[NS] = outputs.strategy.output
    return agents


def approval_rows(run_bench, run_id: str) -> list[ApprovalRow]:
    with run_bench.session_factory() as session:
        statement = (
            select(ApprovalRow)
            .where(ApprovalRow.run_id == run_id)
            .order_by(ApprovalRow.subject_id, ApprovalRow.required_role)
        )
        return list(session.scalars(statement))


def test_usr_5001_opp_1001_completes_or_awaits_pilot_sequencing_review(run_bench) -> None:
    agents = stub_replayed(run_bench, PILOT_PAIR)

    record = run_bench.run(agents, PILOT_PAIR.user_id, PILOT_PAIR.opportunity_id)

    assert record.state in {RunState.COMPLETED, RunState.AWAITING_APPROVAL}
    if record.state is RunState.AWAITING_APPROVAL:
        reviews = [
            row
            for row in approval_rows(run_bench, record.run_id)
            if row.required_role == ApproverRole.HUMAN_REVIEWER
        ]
        assert reviews
        assert any(PILOT_SEQUENCING_SLACK in row.evidence_ids for row in reviews)


def test_usr_5003_opp_1003_awaits_then_completes_after_deal_desk_decisions(run_bench) -> None:
    record = run_bench.run(
        stub_replayed(run_bench, ECLIPSE_PAIR), ECLIPSE_PAIR.user_id, ECLIPSE_PAIR.opportunity_id
    )

    assert record.state is RunState.AWAITING_APPROVAL
    rows = approval_rows(run_bench, record.run_id)
    deal_desk = [
        row
        for row in rows
        if row.required_role == ApproverRole.DEAL_DESK and row.status == ApprovalStatus.PENDING
    ]
    assert any(
        row.subject_id == HIGH_DISCOUNT_NOTE and DEAL_DESK_APPROVER in row.eligible_user_ids
        for row in deal_desk
    )
    roles = {(row.required_role, row.status) for row in rows}
    assert (ApproverRole.SALES_LEADER.value, ApprovalStatus.ESCALATED.value) in roles
    assert (ApproverRole.LEGAL.value, ApprovalStatus.ESCALATED.value) in roles

    for approval in deal_desk:
        with run_bench.session_factory.begin() as session:
            decide(
                session,
                approval.approval_id,
                DEAL_DESK_APPROVER,
                Decision.APPROVED,
                "reviewed",
                run_bench.clock(),
            )

    assert run_bench.record(record.run_id).state is RunState.COMPLETED
    briefs = run_bench.briefs(record.run_id)
    assert briefs[-1].version >= 2
    assert briefs[-1].source == BriefSource.APPROVAL_UPDATE.value
    assert "[APPROVED by" in briefs[-1].markdown
