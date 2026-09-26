"""Run state machine contracts: states, stages, persisted records, and stage payloads."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, JsonValue

from deal_intel.contracts.access import AccessScope, DenialReason
from deal_intel.contracts.agents.agent_run import AgentRun
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.deal_snapshot import DealSnapshot
from deal_intel.contracts.agents.negotiation_strategy import StrategyOutput
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import PackBuild, RetrievalRecord
from deal_intel.contracts.guardrails import GuardrailResult
from deal_intel.contracts.reference import OPPORTUNITY_ID_PATTERN, USER_ID_PATTERN


class RunState(StrEnum):
    QUEUED = "QUEUED"
    AUTHORIZING = "AUTHORIZING"
    DENIED = "DENIED"
    RETRIEVING = "RETRIEVING"
    ANALYZING = "ANALYZING"
    SYNTHESIZING = "SYNTHESIZING"
    VALIDATING = "VALIDATING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


WORKING_STATES: frozenset[RunState] = frozenset(
    {
        RunState.AUTHORIZING,
        RunState.RETRIEVING,
        RunState.ANALYZING,
        RunState.SYNTHESIZING,
        RunState.VALIDATING,
    }
)
# QUEUED is included because the executor's queue lives in memory: after a restart nothing
# will ever pick a queued run up unless it is made resumable.
INTERRUPTIBLE_STATES: frozenset[RunState] = WORKING_STATES | {RunState.QUEUED}
STARTABLE_STATES: frozenset[RunState] = frozenset({RunState.QUEUED, RunState.FAILED})
FINISHED_STATES: frozenset[RunState] = frozenset(
    {RunState.DENIED, RunState.COMPLETED, RunState.FAILED}
)
REPLAYABLE_STATES: frozenset[RunState] = frozenset({RunState.COMPLETED, RunState.AWAITING_APPROVAL})
WAIT_STATES: frozenset[RunState] = frozenset(
    {RunState.COMPLETED, RunState.AWAITING_APPROVAL, RunState.DENIED, RunState.FAILED}
)


class StageName(StrEnum):
    AUTHORIZE = "authorize"
    RETRIEVE = "retrieve"
    DEAL_SNAPSHOT = AgentName.DEAL_SNAPSHOT.value
    CONVERSATION_INTELLIGENCE = AgentName.CONVERSATION_INTELLIGENCE.value
    STAKEHOLDER_MAP = AgentName.STAKEHOLDER_MAP.value
    NEGOTIATION_STRATEGY = AgentName.NEGOTIATION_STRATEGY.value
    POLICY = "policy"
    GUARDRAILS = "guardrails"
    RENDER = "render"


# Declaration order is the execution order.
STAGE_STATES: dict[StageName, RunState] = {
    StageName.AUTHORIZE: RunState.AUTHORIZING,
    StageName.RETRIEVE: RunState.RETRIEVING,
    StageName.DEAL_SNAPSHOT: RunState.ANALYZING,
    StageName.CONVERSATION_INTELLIGENCE: RunState.ANALYZING,
    StageName.STAKEHOLDER_MAP: RunState.ANALYZING,
    StageName.NEGOTIATION_STRATEGY: RunState.SYNTHESIZING,
    StageName.POLICY: RunState.VALIDATING,
    StageName.GUARDRAILS: RunState.VALIDATING,
    StageName.RENDER: RunState.VALIDATING,
}
STAGE_ORDER: tuple[StageName, ...] = tuple(STAGE_STATES)
SUBAGENT_STAGES: tuple[StageName, ...] = (
    StageName.CONVERSATION_INTELLIGENCE,
    StageName.STAKEHOLDER_MAP,
)


class StageStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RunErrorCode(StrEnum):
    """Codes the runner originates; model-call failures keep their own `LlmErrorCode`."""

    INTERRUPTED = "INTERRUPTED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"
    SCOPE_VIOLATION = "SCOPE_VIOLATION"
    APPROVAL_ASSERTION = "APPROVAL_ASSERTION"
    LEAKAGE_DETECTED = "LEAKAGE_DETECTED"
    UNEXPECTED_ERROR = "UNEXPECTED_ERROR"


class RunRequest(StrictModel):
    opportunity_id: str = Field(pattern=OPPORTUNITY_ID_PATTERN)
    user_id: str = Field(pattern=USER_ID_PATTERN)
    fresh: bool = False


class RunRecord(StrictModel):
    """Field names equal the `runs` columns."""

    run_id: str
    opportunity_id: str
    user_id: str
    fresh: bool
    state: RunState
    degraded: bool
    snapshot_id: str | None
    evidence_hash: str | None
    idempotency_key: str | None
    reused_from_run_id: str | None
    input_tokens: int
    cost_usd: Decimal
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class RunEvent(StrictModel):
    event_id: int
    run_id: str
    attempt: int
    from_state: RunState | None
    to_state: RunState
    detail: dict[str, JsonValue]
    at: datetime


class StageOutput(StrictModel):
    """Field names equal the `stage_outputs` columns."""

    run_id: str
    stage: StageName
    attempt: int = Field(ge=1)
    status: StageStatus
    output_json: dict[str, JsonValue]
    input_hash: str | None = None
    model: str | None = None
    prompt_version: str | None = None
    prompt_hash: str | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: Decimal = Decimal(0)


class AttemptStarted(StrictModel):
    attempt: int


class StageCompleted(StrictModel):
    stage: StageName
    attempt: int
    status: StageStatus


class DenialDetail(StrictModel):
    """The only detail a denied run records: no account, opportunity, or scope data."""

    reason_code: DenialReason


class FailureDetail(StrictModel):
    error_code: str
    stage: StageName | None = None


class ApprovalsResolved(StrictModel):
    pending_remaining: int


class LeakIncident(StrictModel):
    """The canary's hash, never its value: the value is content the reader may not see."""

    incident: RunErrorCode = RunErrorCode.LEAKAGE_DETECTED
    kind: str
    canary_sha256: str


class StageError(StrictModel):
    """The output of a failed stage row."""

    error_code: str


class AuthorizeOutput(StrictModel):
    """Exactly one of the two is set; a denied run keeps only the reason code."""

    scope: AccessScope | None = None
    reason_code: DenialReason | None = None


class RetrieveOutput(StrictModel):
    snapshot_id: str
    evidence_hash: str
    idempotency_key: str
    reused_from_run_id: str | None
    packs: dict[AgentName, PackBuild]
    evidence_record: RetrievalRecord


class AnalysisOutputs(StrictModel):
    """What the agents produced; a subagent that failed is None and the run is degraded."""

    snapshot: DealSnapshot
    findings: AgentRun[ConversationFindings] | None
    stakeholders: AgentRun[StakeholderMap] | None
    strategy: AgentRun[StrategyOutput]

    def agent_runs(self) -> list[AgentRun]:
        runs: list[AgentRun | None] = [self.findings, self.stakeholders, self.strategy]
        return [run for run in runs if run is not None]


class GuardrailsOutput(StrictModel):
    """`withheld_item_refs` are `<agent>.<field>[<index>]` refs the renderer leaves out."""

    results: list[GuardrailResult]
    withheld_item_refs: list[str]


class RenderOutput(StrictModel):
    brief_version: int
