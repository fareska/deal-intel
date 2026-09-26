"""What each stage computes. The runner owns order, state, failures, and transactions.

A stage reads what earlier stages stored and does its work, model calls included, on a session
that holds no transaction. It returns a `Commit`: the writes the runner applies, together with
the stage's output row and run event, in one short transaction (plan change C14).
"""

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session, sessionmaker

from deal_intel.agents.base import AgentRuntime
from deal_intel.agents.pipeline import LLM_AGENT_SPECS, AgentSuite, build_agent_packs
from deal_intel.agents.prompt_loader import load_prompt
from deal_intel.config import Settings
from deal_intel.contracts.access import Denied
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import SnapshotBuild
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.approvals import PolicyOutput
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.brief import BriefSource
from deal_intel.contracts.evidence import PackBuild
from deal_intel.contracts.llm import LlmUsage, ModelRole
from deal_intel.contracts.runs import (
    AuthorizeOutput,
    DenialDetail,
    RenderOutput,
    RetrieveOutput,
    RunRecord,
    RunState,
    StageName,
    StageOutput,
)
from deal_intel.db.models import RunRow
from deal_intel.guardrails.render_checks import review_approval_consistency
from deal_intel.guardrails.validators import EvidenceIndex
from deal_intel.llm.client import LlmClient
from deal_intel.llm.routing import route_for
from deal_intel.observability.tracing import Tracer
from deal_intel.orchestration.outputs import (
    StoredOutputs,
    optional_output,
    required_output,
    stored_analysis,
    stored_retrieval,
    stored_scope,
)
from deal_intel.orchestration.persistence import find_reusable_run, stage_output
from deal_intel.permissions.gate import authorize
from deal_intel.policy.eligibility import eligible_user_ids
from deal_intel.policy.engine import approval_records, evaluate, required_roles
from deal_intel.policy.facts import facts_from_outputs
from deal_intel.policy.rules import Thresholds
from deal_intel.policy.store import add_approvals, pending_approval_count
from deal_intel.rendering.brief import render_and_store
from deal_intel.rendering.evidence import brief_access_level, cited_chunk_ids, load_cited_chunks
from deal_intel.retrieval.hashing import json_sha256
from deal_intel.retrieval.retriever import ScopedRetriever


@dataclass(frozen=True)
class StageCommit:
    """`to_state` and `detail` override the runner's defaults: the next stage's state and a
    `StageCompleted` event."""

    output: StageOutput
    to_state: RunState | None = None
    detail: StrictModel | None = None


type Commit = Callable[[Session, RunRow], StageCommit]


def commit_output(output: StageOutput) -> Commit:
    return lambda _session, _row: StageCommit(output)


@dataclass(frozen=True)
class StageEnv:
    """One attempt's view of a run: the record and outputs as they were when the stage began."""

    run: RunRecord
    attempt: int
    outputs: StoredOutputs
    reused_outputs: StoredOutputs
    read_factory: sessionmaker[Session]
    llm: LlmClient
    tracer: Tracer
    settings: Settings
    agents: AgentSuite
    now: Callable[[], datetime]

    @contextmanager
    def reader(self) -> Iterator[tuple[Session, ScopedRetriever]]:
        """Bound to the snapshot the run retrieved from, so every stage reads the same data."""
        snapshot_id = stored_retrieval(self.outputs).snapshot_id
        with self.read_factory() as session:
            yield (
                session,
                ScopedRetriever(session, stored_scope(self.outputs), snapshot_id=snapshot_id),
            )

    def runtime(self, retriever: ScopedRetriever) -> AgentRuntime:
        return AgentRuntime(
            retriever=retriever,
            llm=self.llm,
            tracer=self.tracer,
            settings=self.settings,
            run_id=self.run.run_id,
            fresh=self.run.fresh,
        )

    def output(self, stage: StageName, payload: StrictModel, **metadata: object) -> StageOutput:
        return stage_output(self.run.run_id, stage, self.attempt, payload, **metadata)


def authorize_stage(env: StageEnv) -> Commit:
    with env.read_factory() as session:
        access = authorize(session, env.run.user_id, env.run.opportunity_id)
    if isinstance(access, Denied):
        output = env.output(StageName.AUTHORIZE, AuthorizeOutput(reason_code=access.reason_code))
        detail = DenialDetail(reason_code=access.reason_code)
        return lambda _session, _row: StageCommit(output, RunState.DENIED, detail)
    return commit_output(env.output(StageName.AUTHORIZE, AuthorizeOutput(scope=access.scope)))


def retrieve_stage(env: StageEnv) -> Commit:
    """Fixes the snapshot, fingerprints the evidence, and builds every pack before any agent."""
    scope = stored_scope(env.outputs)
    with env.read_factory() as session:
        retriever = ScopedRetriever(session, scope)
        evidence = retriever.evidence_hash()
        key = idempotency_key(env.run, evidence.value, env.settings)
        reused = None if env.run.fresh else find_reusable_run(session, key, env.run.run_id)
        packs = build_agent_packs(env.runtime(retriever))
    retrieval = RetrieveOutput(
        snapshot_id=retriever.snapshot_id,
        evidence_hash=evidence.value,
        idempotency_key=key,
        reused_from_run_id=reused,
        packs=packs,
        evidence_record=evidence.record,
    )
    output = env.output(StageName.RETRIEVE, retrieval)

    def commit(_session: Session, row: RunRow) -> StageCommit:
        row.snapshot_id = retrieval.snapshot_id
        row.evidence_hash = retrieval.evidence_hash
        row.idempotency_key = retrieval.idempotency_key
        row.reused_from_run_id = retrieval.reused_from_run_id
        return StageCommit(output)

    return commit


def idempotency_key(run: RunRecord, evidence_hash: str, settings: Settings) -> str:
    """Same reader, same evidence, same prompts, same models: the same brief."""
    return json_sha256(
        {
            "opportunity_id": run.opportunity_id,
            "user_id": run.user_id,
            "evidence_hash": evidence_hash,
            "prompt_hashes": {
                name.value: load_prompt(name, spec.prompt_version).content_hash
                for name, spec in LLM_AGENT_SPECS.items()
            },
            "models": {
                role.value: route_for(role, settings).model_dump(mode="json") for role in ModelRole
            },
        }
    )


def deal_snapshot_stage(env: StageEnv) -> Commit:
    with env.reader() as (session, retriever):
        build = env.agents.deal_snapshot(session, retriever)
    return commit_output(env.output(StageName.DEAL_SNAPSHOT, build))


type SubagentCall = Callable[[AgentSuite, AgentRuntime, PackBuild], AgentRun]

SUBAGENT_CALLS: Mapping[StageName, SubagentCall] = {
    StageName.CONVERSATION_INTELLIGENCE: lambda agents, runtime, pack: (
        agents.conversation_intelligence(runtime, pack)
    ),
    StageName.STAKEHOLDER_MAP: lambda agents, runtime, pack: agents.stakeholder_map(runtime, pack),
}


def subagent_stage(env: StageEnv, stage: StageName) -> Commit:
    reused = env.reused_outputs.get(stage)
    if reused is not None:
        return commit_output(reused_output(env, reused))
    pack = stored_retrieval(env.outputs).packs[AgentName(stage.value)]
    with env.reader() as (_session, retriever):
        run = SUBAGENT_CALLS[stage](env.agents, env.runtime(retriever), pack)
    return commit_output(agent_output(env, stage, run))


def strategy_stage(env: StageEnv) -> Commit:
    findings = optional_output(
        env.outputs, StageName.CONVERSATION_INTELLIGENCE, AgentRun[ConversationFindings]
    )
    stakeholders = optional_output(env.outputs, StageName.STAKEHOLDER_MAP, AgentRun[StakeholderMap])
    degraded = findings is None or stakeholders is None
    reused = env.reused_outputs.get(StageName.NEGOTIATION_STRATEGY)
    if reused is not None and not degraded:
        output = reused_output(env, reused)
    else:
        snapshot = required_output(env.outputs, StageName.DEAL_SNAPSHOT, SnapshotBuild).snapshot
        pack = stored_retrieval(env.outputs).packs[AgentName.NEGOTIATION_STRATEGY]
        with env.reader() as (_session, retriever):
            run = env.agents.negotiation_strategy(
                env.runtime(retriever),
                snapshot,
                findings.output if findings else None,
                stakeholders.output if stakeholders else None,
                pack,
            )
        output = agent_output(env, StageName.NEGOTIATION_STRATEGY, run)

    def commit(_session: Session, row: RunRow) -> StageCommit:
        row.degraded = degraded
        return StageCommit(output)

    return commit


def agent_output(env: StageEnv, stage: StageName, run: AgentRun) -> StageOutput:
    metadata = {
        "input_hash": run.input_hash,
        "prompt_version": run.prompt_version,
        "prompt_hash": run.prompt_hash,
    }
    result = run.llm_result
    if result is None:
        return env.output(stage, run, **metadata)
    return env.output(
        stage,
        run,
        **metadata,
        model=result.model,
        input_tokens=billed_input_tokens(result.usage),
        output_tokens=result.usage.output_tokens,
        cost_usd=result.cost_usd,
    )


def billed_input_tokens(usage: LlmUsage) -> int:
    """Everything the model read, cached or not, which is what the run's budget limits."""
    return usage.input_tokens + usage.cache_creation_input_tokens + usage.cache_read_input_tokens


def reused_output(env: StageEnv, reused: StageOutput) -> StageOutput:
    """Another run's output under this run's id, at no cost: the work was done once."""
    return reused.model_copy(
        update={
            "run_id": env.run.run_id,
            "attempt": env.attempt,
            "input_tokens": 0,
            "output_tokens": 0,
            "cost_usd": Decimal(0),
        }
    )


def policy_stage(env: StageEnv) -> Commit:
    scope = stored_scope(env.outputs)
    analysis = stored_analysis(env.outputs)
    evaluation = evaluate(facts_from_outputs(analysis), Thresholds.from_settings(env.settings))
    with env.reader() as (session, retriever):
        level = brief_access_level(load_cited_chunks(retriever, cited_chunk_ids(analysis)).values())
        eligible = {
            role: eligible_user_ids(session, role, scope.account_id, level)
            for role in required_roles(evaluation.requests)
        }
    # A reader who may not request approvals gets labelled recommendations and no rows.
    records = (
        approval_records(
            env.run.run_id,
            evaluation.requests,
            eligible,
            scope.account_id,
            level,
            env.now(),
            env.settings.approval_expiry_hours,
        )
        if scope.can_request_approval
        else []
    )
    policy = PolicyOutput(
        requests=evaluation.requests,
        requestable=scope.can_request_approval,
        approval_ids=[record.approval_id for record in records],
        fired_rules=evaluation.fired_rules,
        brief_access_level=level,
    )
    output = env.output(StageName.POLICY, policy)

    def commit(session: Session, _row: RunRow) -> StageCommit:
        add_approvals(session, records)
        return StageCommit(output)

    return commit


def guardrails_stage(env: StageEnv) -> Commit:
    analysis = stored_analysis(env.outputs)
    with env.reader() as (_session, retriever):
        chunks = load_cited_chunks(retriever, cited_chunk_ids(analysis))
    review = review_approval_consistency(analysis, EvidenceIndex.from_chunks(chunks.values()))
    return commit_output(env.output(StageName.GUARDRAILS, review))


def render_stage(env: StageEnv) -> Commit:
    """Renders inside the commit: rendering calls no model, and the brief version, its approval
    state, and the run's final state must agree."""

    def commit(session: Session, row: RunRow) -> StageCommit:
        session.flush()
        version = render_and_store(session, row.run_id, BriefSource.RUN, env.now())
        pending = pending_approval_count(session, row.run_id)
        to_state = RunState.AWAITING_APPROVAL if pending else RunState.COMPLETED
        output = env.output(StageName.RENDER, RenderOutput(brief_version=version))
        return StageCommit(output, to_state)

    return commit
