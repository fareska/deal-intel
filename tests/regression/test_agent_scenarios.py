"""M3 scenario checks over live-recorded fixtures, replayed through the fake client.

The assertions compare structure and citations, never free text, so a re-recording that words
things differently still passes. Until `scripts/record_fixtures.py` has been run the module
skips; once fixtures exist, a missing one means they are stale and the test fails.
"""

from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.base import AgentRuntime
from deal_intel.agents.pipeline import AgentOutputs, run_all_agents
from deal_intel.config import get_settings
from deal_intel.contracts.access import Allowed, SourceType
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import LLM_AGENTS
from deal_intel.contracts.agents.stakeholder_map import VENDOR_SPEAKER_PREFIX, StakeholderMap
from deal_intel.contracts.evidence import CHUNK_SOURCE_TYPES, chunk_kind_of
from deal_intel.contracts.reference import SlackAuthorRole
from deal_intel.evaluation.scenarios import (
    FIXTURES_NOT_RECORDED,
    FIXTURES_STALE,
    RECORDED_PAIRS,
    DemoPair,
)
from deal_intel.guardrails.validators import EvidenceIndex, cited_fields
from deal_intel.llm.client import LlmClient
from deal_intel.llm.errors import FixtureMissing
from deal_intel.llm.fake_client import FakeLlmProvider
from deal_intel.llm.fixtures import fixtures_recorded
from deal_intel.observability.tracing import NoopTracer
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.retriever import ScopedRetriever

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"
PAIRS_BY_OPPORTUNITY = {pair.opportunity_id: pair for pair in RECORDED_PAIRS}

OPP_1002_CLOSEOUT_CONFLICT = "slack:SLK-1002-02"
OPP_1002_OFF_CRM_SITE_LEAD = "slack:SLK-1002-01"
OPP_1003_VERBAL_APPROVAL = "slack:SLK-1003-02"
PRIMARY_CONFLICT_SOURCES = frozenset({SourceType.GONG, SourceType.SALESFORCE})
OPP_1003_DISCOUNTS = frozenset({Decimal(12), Decimal(18)})
ACCOUNT_TEAM_LABELS = frozenset(role.value for role in SlackAuthorRole)


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


def replay_opportunity(
    session: Session, session_factory: sessionmaker[Session], opportunity_id: str
) -> AgentOutputs:
    return replay(session, session_factory, PAIRS_BY_OPPORTUNITY[opportunity_id])


def cited_ids(output: BaseModel) -> set[str]:
    return {
        chunk_id
        for cited in cited_fields(output)
        for item in cited.items
        for chunk_id in item.evidence_ids
    }


def named_people(stakeholders: StakeholderMap) -> list[str]:
    people = [*stakeholders.stakeholders, *stakeholders.unmatched_speakers]
    named_off_crm = [person.name for person in stakeholders.off_crm_people if person.name]
    return [person.name for person in people] + named_off_crm


def cites_primary_source(chunk_ids: list[str]) -> bool:
    return any(
        CHUNK_SOURCE_TYPES[chunk_kind_of(chunk_id)] in PRIMARY_CONFLICT_SOURCES
        for chunk_id in chunk_ids
    )


@pytest.mark.parametrize("pair", RECORDED_PAIRS, ids=lambda pair: pair.label)
def test_every_cited_id_is_in_the_pack_or_tool_results(
    ingested_session: Session, session_factory: sessionmaker[Session], pair: DemoPair
) -> None:
    outputs = replay(ingested_session, session_factory, pair)

    runs: list[AgentRun] = [outputs.findings, outputs.stakeholders, outputs.strategy]
    for run in runs:
        assert cited_ids(run.output) <= run.citable_ids(), run.agent_name


@pytest.mark.parametrize("pair", RECORDED_PAIRS, ids=lambda pair: pair.label)
def test_stakeholder_names_occur_in_evidence_and_no_vendor_speaker_is_listed(
    ingested_session: Session, session_factory: sessionmaker[Session], pair: DemoPair
) -> None:
    run = replay(ingested_session, session_factory, pair).stakeholders

    evidence = EvidenceIndex.from_chunks(run.pack.chunks)
    names = named_people(run.output)
    titles = [person.title for person in run.output.stakeholders]
    assert names
    assert all(evidence.mentions(name) for name in names)
    assert not any(
        label.casefold().startswith(VENDOR_SPEAKER_PREFIX) or label in ACCOUNT_TEAM_LABELS
        for label in [*names, *titles]
    )


def test_opp_1002_closeout_conflict_cites_slack_and_a_primary_source(
    ingested_session: Session, session_factory: sessionmaker[Session]
) -> None:
    findings = replay_opportunity(ingested_session, session_factory, "OPP-1002").findings.output

    assert any(
        OPP_1002_CLOSEOUT_CONFLICT in conflict.evidence_ids
        and cites_primary_source(conflict.evidence_ids)
        for conflict in findings.conflicts
    )


def test_opp_1002_map_lists_the_off_crm_site_it_lead(
    ingested_session: Session, session_factory: sessionmaker[Session]
) -> None:
    stakeholders = replay_opportunity(
        ingested_session, session_factory, "OPP-1002"
    ).stakeholders.output

    assert any(
        OPP_1002_OFF_CRM_SITE_LEAD in person.evidence_ids for person in stakeholders.off_crm_people
    )


def test_opp_1003_verbal_approval_is_a_conflict_and_never_a_commitment(
    ingested_session: Session, session_factory: sessionmaker[Session]
) -> None:
    findings = replay_opportunity(ingested_session, session_factory, "OPP-1003").findings.output

    assert any(OPP_1003_VERBAL_APPROVAL in conflict.evidence_ids for conflict in findings.conflicts)
    assert not any(OPP_1003_VERBAL_APPROVAL in item.evidence_ids for item in findings.commitments)


def test_opp_1003_strategy_has_an_internal_pricing_action(
    ingested_session: Session, session_factory: sessionmaker[Session]
) -> None:
    strategy = replay_opportunity(ingested_session, session_factory, "OPP-1003").strategy.output

    assert any(
        action.is_pricing_sensitive()
        and action.proposed_values is not None
        and action.proposed_values.discount_pct in OPP_1003_DISCOUNTS
        and not action.customer_facing
        for action in strategy.next_actions
    )
