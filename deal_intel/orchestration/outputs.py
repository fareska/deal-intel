"""Typed views over persisted stage outputs. Later stages, replays, and re-renders read agent work
only through these, so a resumed run sees exactly what an uninterrupted one would."""

from collections.abc import Mapping

from pydantic import BaseModel

from deal_intel.contracts.access import AccessScope
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import SnapshotBuild
from deal_intel.contracts.agents.negotiation_strategy import StrategyOutput
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.approvals import PolicyOutput
from deal_intel.contracts.runs import (
    AnalysisOutputs,
    AuthorizeOutput,
    GuardrailsOutput,
    RetrieveOutput,
    StageName,
    StageOutput,
)

type StoredOutputs = Mapping[StageName, StageOutput]


class StageOutputMissing(LookupError):
    """A later stage, replay, or re-render found no stored output it depends on; it refuses
    rather than calling a model to fill the gap."""


def optional_output[ModelT: BaseModel](
    outputs: StoredOutputs, stage: StageName, model: type[ModelT]
) -> ModelT | None:
    stored = outputs.get(stage)
    return None if stored is None else model.model_validate(stored.output_json)


def required_output[ModelT: BaseModel](
    outputs: StoredOutputs, stage: StageName, model: type[ModelT]
) -> ModelT:
    parsed = optional_output(outputs, stage, model)
    if parsed is None:
        raise StageOutputMissing(f"no stored {stage.value} output")
    return parsed


def stored_scope(outputs: StoredOutputs) -> AccessScope:
    scope = required_output(outputs, StageName.AUTHORIZE, AuthorizeOutput).scope
    if scope is None:
        raise StageOutputMissing("the run was denied and has no scope")
    return scope


def stored_retrieval(outputs: StoredOutputs) -> RetrieveOutput:
    return required_output(outputs, StageName.RETRIEVE, RetrieveOutput)


def stored_analysis(outputs: StoredOutputs) -> AnalysisOutputs:
    return AnalysisOutputs(
        snapshot=required_output(outputs, StageName.DEAL_SNAPSHOT, SnapshotBuild).snapshot,
        findings=optional_output(
            outputs, StageName.CONVERSATION_INTELLIGENCE, AgentRun[ConversationFindings]
        ),
        stakeholders=optional_output(outputs, StageName.STAKEHOLDER_MAP, AgentRun[StakeholderMap]),
        strategy=required_output(outputs, StageName.NEGOTIATION_STRATEGY, AgentRun[StrategyOutput]),
    )


def stored_policy(outputs: StoredOutputs) -> PolicyOutput:
    return required_output(outputs, StageName.POLICY, PolicyOutput)


def stored_guardrails(outputs: StoredOutputs) -> GuardrailsOutput:
    return required_output(outputs, StageName.GUARDRAILS, GuardrailsOutput)
