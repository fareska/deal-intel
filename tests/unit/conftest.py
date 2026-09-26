import threading
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.base import AgentRuntime
from deal_intel.agents.deal_snapshot import build_deal_snapshot
from deal_intel.agents.prompt_loader import load_prompt
from deal_intel.config import Settings, get_settings
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import (
    ActionItem,
    ActionSide,
    Conflict,
    ConversationFindings,
    Finding,
    Urgency,
)
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot, SnapshotBuild
from deal_intel.contracts.agents.negotiation_strategy import (
    NegotiationState,
    NextAction,
    ProposedValues,
    SensitivityTag,
    StrategyOutput,
    SummarySentence,
)
from deal_intel.contracts.agents.stakeholder_map import (
    Influence,
    RoleInDeal,
    Stakeholder,
    StakeholderMap,
)
from deal_intel.contracts.evidence import EvidenceChunk, PackBuild
from deal_intel.contracts.guardrails import Confidence
from deal_intel.contracts.llm import LlmResult, LlmUsage, StopReason
from deal_intel.contracts.reference import LowMediumHigh
from deal_intel.contracts.runs import RunEvent, RunRecord, RunRequest, StageOutput
from deal_intel.db.models import BriefRow, TraceSpanRow
from deal_intel.guardrails.validators import cited_fields
from deal_intel.llm.client import LlmClient
from deal_intel.llm.fake_client import FakeLlmProvider
from deal_intel.observability.tracing import PostgresTracer
from deal_intel.orchestration.persistence import (
    create_run,
    get_run,
    latest_outputs,
    run_events,
)
from deal_intel.orchestration.runner import Runner, RunnerDeps
from deal_intel.retrieval.ingest import build_chunks, build_context
from deal_intel.retrieval.reference import ReferenceData, read_reference_data
from deal_intel.retrieval.retriever import ScopedRetriever
from deal_intel.retrieval.sensitivity import SensitivityRule

CHUNK_TEST_SNAPSHOT_ID = "chunk-test"


@pytest.fixture(scope="session")
def reference(synthetic_data: Path) -> ReferenceData:
    return read_reference_data(synthetic_data)


@pytest.fixture(scope="session")
def evidence_chunks(dataset_root: Path, sensitivity: SensitivityRule) -> list[EvidenceChunk]:
    return build_chunks(build_context(dataset_root, CHUNK_TEST_SNAPSHOT_ID, sensitivity))


# Hand-authored agent outputs for the run tests. Every cited id is a real chunk the scenario's
# reader may see, so the runner, policy engine, and renderer work on genuine evidence.
OPP_1001 = "OPP-1001"
OPP_1003 = "OPP-1003"
STUB_MODEL = "stub-model"
STUB_USAGE = LlmUsage(input_tokens=1000, output_tokens=200)
STUB_COST = Decimal("0.01")
STUB_NOW = datetime(2026, 5, 1, 9, tzinfo=UTC)
BARRIER_TIMEOUT_SECONDS = 5

OPP_1001_CHUNK = "sfdc_opp:OPP-1001"
PREP_CALL = "gong_summary:CALL-008"
REVIEW_CALL = "gong_summary:CALL-009"
IRIS_CONTACT = "contact:CON-3003"
AMARA_CONTACT = "contact:CON-3005"
OPP_1003_CHUNK = "sfdc_opp:OPP-1003"
PRESSURE_CALL = "gong_summary:CALL-021"
APPROVAL_PATH_CALL = "gong_summary:CALL-027"
VERBAL_APPROVAL_SLACK = "slack:SLK-1003-02"
HIGH_DISCOUNT_NOTE = "pricing:PN-4004"
ALTERNATIVE_NOTE = "pricing:PN-4005"
DARIN_CONTACT = "contact:CON-3014"
LEAH_CONTACT = "contact:CON-3013"


def summary(chunk_id: str, *texts: str) -> list[SummarySentence]:
    return [
        SummarySentence(text=text, evidence_ids=[chunk_id], confidence=Confidence.HIGH)
        for text in texts
    ]


def finding(statement: str, *chunk_ids: str) -> Finding:
    return Finding(statement=statement, evidence_ids=list(chunk_ids), confidence=Confidence.MEDIUM)


def action(
    action_id: str,
    text: str,
    *chunk_ids: str,
    customer_facing: bool = False,
    tags: tuple[SensitivityTag, ...] = (),
    values: ProposedValues | None = None,
    confidence: Confidence = Confidence.MEDIUM,
) -> NextAction:
    return NextAction(
        id=action_id,
        action=text,
        owner_role="Account Executive",
        rationale="The latest evidence asks for this step.",
        sensitivity_tags=list(tags),
        customer_facing=customer_facing,
        proposed_values=values,
        evidence_ids=list(chunk_ids),
        confidence=confidence,
    )


def person(name: str, title: str, role: RoleInDeal, contact_id: str) -> Stakeholder:
    return Stakeholder(
        name=name,
        title=title,
        role_in_deal=role,
        influence=Influence.MEDIUM,
        sentiment="neutral",
        stance_summary="Wants a predictable renewal path.",
        contact_id=contact_id.split(":")[1],
        evidence_ids=[contact_id],
        confidence=Confidence.HIGH,
    )


def opp_1001_outputs() -> dict[AgentName, BaseModel]:
    """Salesforce and Gong only, so the same outputs suit USR-5001 and the narrow USR-5007."""
    return {
        AgentName.CONVERSATION_INTELLIGENCE: ConversationFindings(
            buyer_goals=[finding("The buyer wants a simple final order form.", REVIEW_CALL)],
            business_drivers=[finding("Regional rollout progress drives timing.", PREP_CALL)],
            objections=[finding("Procurement is focused on the payment schedule.", PREP_CALL)],
            commitments=[finding("The team will send the revised order form.", OPP_1001_CHUNK)],
            urgency=Urgency(
                level=LowMediumHigh.MEDIUM,
                rationale="Signature is expected soon.",
                evidence_ids=[REVIEW_CALL],
                confidence=Confidence.MEDIUM,
            ),
            action_items=[
                ActionItem(
                    description="Send the owner matrix to procurement.",
                    side=ActionSide.VENDOR,
                    owner="Account Executive",
                    evidence_ids=[REVIEW_CALL],
                    confidence=Confidence.MEDIUM,
                )
            ],
        ),
        AgentName.STAKEHOLDER_MAP: StakeholderMap(
            stakeholders=[
                person(
                    "Iris Calder",
                    "Procurement Director",
                    RoleInDeal.COMMERCIAL_APPROVER,
                    IRIS_CONTACT,
                ),
                person("Amara Quinn", "Legal Counsel", RoleInDeal.LEGAL, AMARA_CONTACT),
            ]
        ),
        AgentName.NEGOTIATION_STRATEGY: StrategyOutput(
            executive_summary=summary(
                OPP_1001_CHUNK,
                "The renewal is in its final document review.",
                "Procurement wants a simple order form.",
                "Open items are owners and the payment schedule.",
            ),
            negotiation_state=NegotiationState(
                stage_assessment="Late-stage renewal negotiation.",
                customer_position="The customer wants a simple order form.",
                vendor_position="The team is preparing the signature package.",
                open_items=["Payment schedule owner"],
                evidence_ids=[OPP_1001_CHUNK, REVIEW_CALL],
                confidence=Confidence.MEDIUM,
            ),
            next_actions=[
                action("A1", "Confirm the payment schedule owner with procurement.", REVIEW_CALL),
                action(
                    "A2",
                    "Share the migration success plan with the infrastructure team.",
                    PREP_CALL,
                    customer_facing=True,
                ),
            ],
        ),
    }


def opp_1003_outputs() -> dict[AgentName, BaseModel]:
    return {
        AgentName.CONVERSATION_INTELLIGENCE: ConversationFindings(
            buyer_goals=[finding("The buyer wants renewal certainty.", PRESSURE_CALL)],
            objections=[finding("Procurement is pressing on price.", PRESSURE_CALL)],
            conflicts=[
                Conflict(
                    topic="Claimed pricing sign-off",
                    claim_a="An update reports a verbal sign-off on price.",
                    claim_b="The latest call still routes price for review.",
                    assessment="Both cannot hold until a decision is recorded.",
                    evidence_ids=[VERBAL_APPROVAL_SLACK, APPROVAL_PATH_CALL],
                    confidence=Confidence.MEDIUM,
                )
            ],
        ),
        AgentName.STAKEHOLDER_MAP: StakeholderMap(
            stakeholders=[
                person(
                    "Darin Holt", "Procurement Lead", RoleInDeal.COMMERCIAL_APPROVER, DARIN_CONTACT
                ),
                person("Leah Tan", "General Counsel", RoleInDeal.LEGAL, LEAH_CONTACT),
            ]
        ),
        AgentName.NEGOTIATION_STRATEGY: StrategyOutput(
            executive_summary=summary(
                OPP_1003_CHUNK,
                "The renewal is under price pressure.",
                "Legal review of contract language is open.",
                "Commercial options need a recorded decision.",
            ),
            negotiation_state=NegotiationState(
                stage_assessment="Negotiation with open commercial terms.",
                customer_position="Procurement wants a lower price and a shorter term.",
                vendor_position="The team is preparing options for review.",
                evidence_ids=[OPP_1003_CHUNK, PRESSURE_CALL],
                confidence=Confidence.MEDIUM,
            ),
            next_actions=[
                action(
                    "A1",
                    "Prepare the procurement package internally for review.",
                    HIGH_DISCOUNT_NOTE,
                    tags=(SensitivityTag.PRICING, SensitivityTag.DISCOUNT),
                    values=ProposedValues(discount_pct=Decimal(18)),
                ),
                action("A2", "Schedule the research workflow walkthrough.", PRESSURE_CALL),
            ],
        ),
    }


STUB_OUTPUTS: Mapping[str, Callable[[], dict[AgentName, BaseModel]]] = {
    OPP_1001: opp_1001_outputs,
    OPP_1003: opp_1003_outputs,
}


def cited_ids(output: BaseModel) -> list[str]:
    return [
        chunk_id
        for cited in cited_fields(output)
        for item in cited.items
        for chunk_id in item.evidence_ids
    ]


class StubAgents:
    """An `AgentSuite` returning hand-authored outputs. The deal snapshot is the real one; the
    three agents return `outputs` as if a model had, with every cited id reported as fetched by
    a tool so citation checks downstream see it as retrieved."""

    def __init__(self, outputs: Mapping[AgentName, BaseModel], usage: LlmUsage = STUB_USAGE):
        self.outputs = dict(outputs)
        self.usage = usage
        self.failures: dict[AgentName, Exception] = {}
        self.calls: Counter[AgentName] = Counter()
        self.threads: dict[AgentName, int] = {}
        self.runtimes: dict[AgentName, AgentRuntime] = {}
        # When set, each subagent waits here, so a test can prove both are in flight at once.
        self.subagent_barrier: threading.Barrier | None = None
        self._lock = threading.Lock()

    def deal_snapshot(self, session: Session, retriever: ScopedRetriever) -> SnapshotBuild:
        self._record(AgentName.DEAL_SNAPSHOT)
        return build_deal_snapshot(session, retriever)

    def conversation_intelligence(
        self, runtime: AgentRuntime, pack_build: PackBuild | None
    ) -> AgentRun[ConversationFindings]:
        return self._subagent(AgentName.CONVERSATION_INTELLIGENCE, runtime, pack_build)

    def stakeholder_map(
        self, runtime: AgentRuntime, pack_build: PackBuild | None
    ) -> AgentRun[StakeholderMap]:
        return self._subagent(AgentName.STAKEHOLDER_MAP, runtime, pack_build)

    def negotiation_strategy(
        self,
        runtime: AgentRuntime,
        snapshot: DealSnapshot,
        findings: ConversationFindings | None,
        stakeholders: StakeholderMap | None,
        pack_build: PackBuild | None,
    ) -> AgentRun[StrategyOutput]:
        return self._answer(AgentName.NEGOTIATION_STRATEGY, pack_build)

    def _subagent(
        self, name: AgentName, runtime: AgentRuntime, pack_build: PackBuild | None
    ) -> AgentRun:
        with self._lock:
            self.runtimes[name] = runtime
        if self.subagent_barrier is not None:
            self.subagent_barrier.wait(timeout=BARRIER_TIMEOUT_SECONDS)
        return self._answer(name, pack_build)

    def _record(self, name: AgentName) -> None:
        with self._lock:
            self.calls[name] += 1
            self.threads[name] = threading.get_ident()

    def _answer(self, name: AgentName, pack_build: PackBuild | None) -> AgentRun:
        self._record(name)
        if name in self.failures:
            raise self.failures[name]
        assert pack_build is not None
        output = self.outputs[name]
        model = type(output)
        return AgentRun[model](
            agent_name=name,
            prompt_version=load_prompt(name).version,
            prompt_hash=load_prompt(name).content_hash,
            input_hash=f"stub-{name.value}",
            output=output,
            pack=pack_build.pack,
            retrieval_records=pack_build.records,
            guardrail_results=[],
            llm_result=LlmResult[model](
                output=output,
                raw_text=output.model_dump_json(),
                usage=self.usage,
                cost_usd=STUB_COST,
                model=STUB_MODEL,
                stop_reason=StopReason.END_TURN,
                latency_ms=0,
                cached=False,
                call_ids=[],
                attempts=1,
                tool_calls=0,
                tool_evidence_ids=cited_ids(output),
                guardrail_results=[],
            ),
        )


@dataclass
class Clock:
    now: datetime = STUB_NOW

    def __call__(self) -> datetime:
        return self.now

    def advance(self, delta: timedelta) -> None:
        self.now += delta


@dataclass
class RunBench:
    """The runner over committed test data, with stub agents and a clock the test controls."""

    session_factory: sessionmaker[Session]
    settings: Settings
    llm: LlmClient
    tracer: PostgresTracer
    clock: Clock = field(default_factory=Clock)

    def agents(self, opportunity_id: str) -> StubAgents:
        return StubAgents(STUB_OUTPUTS[opportunity_id]())

    def runner(self, agents: StubAgents, settings: Settings | None = None) -> Runner:
        return Runner(
            RunnerDeps(
                session_factory=self.session_factory,
                llm=self.llm,
                tracer=self.tracer,
                settings=settings or self.settings,
                agents=agents,
                clock=self.clock,
            )
        )

    def create(self, user_id: str, opportunity_id: str, fresh: bool = False) -> str:
        request = RunRequest(user_id=user_id, opportunity_id=opportunity_id, fresh=fresh)
        with self.session_factory.begin() as session:
            return create_run(session, request, self.clock()).run_id

    def run(
        self,
        agents: StubAgents,
        user_id: str,
        opportunity_id: str,
        fresh: bool = False,
        settings: Settings | None = None,
    ) -> RunRecord:
        run_id = self.create(user_id, opportunity_id, fresh)
        return self.runner(agents, settings).run(run_id)

    def record(self, run_id: str) -> RunRecord:
        with self.session_factory() as session:
            return get_run(session, run_id)

    def events(self, run_id: str) -> list[RunEvent]:
        with self.session_factory() as session:
            return run_events(session, run_id)

    def stage_rows(self, run_id: str) -> list[StageOutput]:
        with self.session_factory() as session:
            return list(latest_outputs(session, run_id).values())

    def briefs(self, run_id: str) -> list[BriefRow]:
        with self.session_factory() as session:
            statement = select(BriefRow).where(BriefRow.run_id == run_id).order_by(BriefRow.version)
            return list(session.scalars(statement))

    def spans(self, run_id: str) -> list[TraceSpanRow]:
        with self.session_factory() as session:
            statement = select(TraceSpanRow).where(TraceSpanRow.run_id == run_id)
            return list(session.scalars(statement))


@pytest.fixture
def committed_session(ingested_session: Session) -> Session:
    """The runner reads through its own sessions, so the ingested data must be committed."""
    ingested_session.commit()
    return ingested_session


@pytest.fixture
def run_bench(
    committed_session: Session, session_factory: sessionmaker[Session], tmp_path: Path
) -> RunBench:
    settings = get_settings()
    tracer = PostgresTracer(session_factory)
    llm = LlmClient(
        FakeLlmProvider(tmp_path), settings=settings, session_factory=session_factory, tracer=tracer
    )
    return RunBench(session_factory=session_factory, settings=settings, llm=llm, tracer=tracer)
