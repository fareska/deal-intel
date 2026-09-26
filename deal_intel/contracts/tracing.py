from datetime import datetime
from enum import StrEnum

from deal_intel.contracts.base import StrictModel

MAX_ATTRIBUTE_CHARS = 512
# Attribute strings are identifiers, codes, and hashes. Whitespace is excluded so no sentence,
# and therefore no evidence text, can ever pass as an attribute value.
ATTRIBUTE_VALUE_PATTERN = r"^[A-Za-z0-9_.:/-]+$"

type AttributeValue = str | int | float | bool | list[str]


class SpanKind(StrEnum):
    RUN = "run"
    STAGE = "stage"
    AGENT_CALL = "agent_call"
    LLM_REQUEST = "llm_request"
    RETRIEVAL = "retrieval"
    TOOL = "tool"
    GUARDRAIL = "guardrail"
    APPROVAL = "approval"


class SpanStatus(StrEnum):
    RUNNING = "running"
    OK = "ok"
    ERROR = "error"


class SpanAttribute(StrEnum):
    """The attribute whitelist: a key outside this enum is never stored."""

    RUN_ID = "run_id"
    OPPORTUNITY_ID = "opportunity_id"
    USER_ID = "user_id"
    STAGE = "stage"
    AGENT_NAME = "agent_name"
    PROMPT_VERSION = "prompt_version"
    PROMPT_HASH = "prompt_hash"
    INPUT_HASH = "input_hash"
    MODEL = "model"
    MODEL_ROLE = "model_role"
    CALL_ID = "call_id"
    TURN = "turn"
    ATTEMPT = "attempt"
    STOP_REASON = "stop_reason"
    INPUT_TOKENS = "input_tokens"
    OUTPUT_TOKENS = "output_tokens"
    CACHE_READ_TOKENS = "cache_read_tokens"
    CACHE_WRITE_TOKENS = "cache_write_tokens"
    COST_USD = "cost_usd"
    LATENCY_MS = "latency_ms"
    CACHED = "cached"
    TOOL_NAME = "tool_name"
    EVIDENCE_IDS = "evidence_ids"
    TRUNCATED = "truncated"
    GUARDRAIL_PASSED = "guardrail_passed"
    GUARDRAIL_DROPPED = "guardrail_dropped"
    GUARDRAIL_MODIFIED = "guardrail_modified"
    GUARDRAIL_WARNINGS = "guardrail_warnings"
    FEEDBACK_COUNT = "feedback_count"
    ERROR_CODE = "error_code"
    REASON_CODE = "reason_code"


class TraceSpanRecord(StrictModel):
    span_id: str
    parent_span_id: str | None
    run_id: str | None
    kind: SpanKind
    name: str
    started_at: datetime
    ended_at: datetime | None
    status: SpanStatus
    attributes: dict[SpanAttribute, AttributeValue]
