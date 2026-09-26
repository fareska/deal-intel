"""HTTP request and response contracts. Safe for the CLI to import: no database or orchestration."""

from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import Field, JsonValue

from deal_intel.contracts.access import DENIED_MESSAGE
from deal_intel.contracts.approvals import ApprovalStatus, ApproverRole, Decision, RuleId
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.brief import Brief, BriefSource, EvidenceEntry
from deal_intel.contracts.reference import OPPORTUNITY_ID_PATTERN, USER_ID_PATTERN
from deal_intel.contracts.runs import RunState

DECISION_NOTE_MAX_CHARS = 1000
RUNS_PATH = "/runs"
APPROVALS_PATH = "/approvals"


class BriefFormat(StrEnum):
    MARKDOWN = "md"
    JSON = "json"


class CreateRunRequest(StrictModel):
    opportunity_id: str = Field(pattern=OPPORTUNITY_ID_PATTERN)
    user_id: str = Field(pattern=USER_ID_PATTERN)
    fresh: bool = False


class RunAccepted(StrictModel):
    run_id: str
    state: RunState
    existing: bool


class AgentUsage(StrictModel):
    agent_name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cost_usd: Decimal


class RunStatusResponse(StrictModel):
    run_id: str
    opportunity_id: str
    user_id: str
    state: RunState
    degraded: bool
    fresh: bool
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    duration_ms: int | None
    cost_usd: Decimal
    input_tokens: int
    usage_by_agent: list[AgentUsage]
    pending_approvals: int
    message: str | None = None


class BriefResponse(StrictModel):
    run_id: str
    version: int
    source: BriefSource
    markdown: str | None
    brief: Brief | None


class BriefVersionSummary(StrictModel):
    version: int
    source: BriefSource
    rendered_at: datetime


class TraceSpan(StrictModel):
    span_id: str
    parent_span_id: str | None
    kind: str
    name: str
    started_at: datetime
    ended_at: datetime | None
    status: str
    duration_ms: int | None
    attributes: dict[str, JsonValue]


class TraceResponse(StrictModel):
    run_id: str
    redacted: bool
    spans: list[TraceSpan]


class ApprovalResponse(StrictModel):
    approval_id: str
    run_id: str
    subject_id: str
    recommendation_ids: list[str]
    required_role: ApproverRole
    rule_ids: list[RuleId]
    proposed_values: dict[str, JsonValue]
    summary: str
    recommendation_text: str
    rationale: str
    evidence_ids: list[str]
    evidence_excerpts: list[EvidenceEntry]
    status: ApprovalStatus
    eligible_user_ids: list[str]
    expires_at: datetime
    run_state: RunState
    note: str | None = None


class DecisionRequest(StrictModel):
    user_id: str = Field(pattern=USER_ID_PATTERN)
    decision: Decision
    note: str = Field(default="", max_length=DECISION_NOTE_MAX_CHARS)


class OpportunityChoice(StrictModel):
    opportunity_id: str
    opportunity_name: str


def run_path(run_id: str) -> str:
    return f"{RUNS_PATH}/{run_id}"


def run_brief_path(run_id: str) -> str:
    return f"{run_path(run_id)}/brief"


def run_trace_path(run_id: str) -> str:
    return f"{run_path(run_id)}/trace"


def run_replay_path(run_id: str) -> str:
    return f"{run_path(run_id)}/replay"


def run_resume_path(run_id: str) -> str:
    return f"{run_path(run_id)}/resume"


def approval_decision_path(approval_id: str) -> str:
    return f"{APPROVALS_PATH}/{approval_id}/decision"


def denial_message() -> str:
    return DENIED_MESSAGE


def span_depths(spans: list[TraceSpan]) -> list[int]:
    by_id = {span.span_id: span for span in spans}
    return [span_depth(span, by_id) for span in spans]


def span_depth(span: TraceSpan, by_id: Mapping[str, TraceSpan]) -> int:
    depth = 0
    seen: set[str] = set()
    current = span
    while current.parent_span_id and current.parent_span_id in by_id:
        if current.span_id in seen:
            break
        seen.add(current.span_id)
        current = by_id[current.parent_span_id]
        depth += 1
    return depth
