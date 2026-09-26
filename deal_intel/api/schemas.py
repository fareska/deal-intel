"""Wire contracts shared by the API and its HTTP clients.

Imports nothing from the database or orchestration packages, so the CLI can load it.
"""

from enum import StrEnum

from deal_intel.contracts.api import (
    APPROVALS_PATH,
    RUNS_PATH,
    AgentUsage,
    ApprovalResponse,
    BriefFormat,
    BriefResponse,
    BriefVersionSummary,
    CreateRunRequest,
    DecisionRequest,
    OpportunityChoice,
    RunAccepted,
    RunStatusResponse,
    TraceResponse,
    TraceSpan,
    approval_decision_path,
    run_brief_path,
    run_path,
    run_replay_path,
    run_resume_path,
    run_trace_path,
)
from deal_intel.contracts.base import StrictModel

HEALTHZ_PATH = "/healthz"
READYZ_PATH = "/readyz"


class HealthStatus(StrEnum):
    OK = "ok"
    DATABASE_UNAVAILABLE = "database_unavailable"


class HealthResponse(StrictModel):
    status: HealthStatus


class ErrorCode(StrEnum):
    INVALID_INPUT = "INVALID_INPUT"
    NOT_FOUND = "NOT_FOUND"
    FORBIDDEN = "FORBIDDEN"
    METHOD_NOT_ALLOWED = "METHOD_NOT_ALLOWED"
    CONFLICT = "CONFLICT"
    PAYLOAD_TOO_LARGE = "PAYLOAD_TOO_LARGE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(StrictModel):
    code: ErrorCode
    message: str
    request_id: str


class ErrorResponse(StrictModel):
    error: ErrorBody


__all__ = [
    "APPROVALS_PATH",
    "HEALTHZ_PATH",
    "READYZ_PATH",
    "RUNS_PATH",
    "AgentUsage",
    "ApprovalResponse",
    "BriefFormat",
    "BriefResponse",
    "BriefVersionSummary",
    "CreateRunRequest",
    "DecisionRequest",
    "ErrorBody",
    "ErrorCode",
    "ErrorResponse",
    "HealthResponse",
    "HealthStatus",
    "OpportunityChoice",
    "RunAccepted",
    "RunStatusResponse",
    "TraceResponse",
    "TraceSpan",
    "approval_decision_path",
    "run_brief_path",
    "run_path",
    "run_replay_path",
    "run_resume_path",
    "run_trace_path",
]
