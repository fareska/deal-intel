"""Provider-neutral model-call contracts. Nothing here names an SDK type.

`LlmRequest`, `Tool`, and `ProviderRequest` hold classes and callables, so they are in-process
contracts only; `RecordedTurn` is the fixture format and the only one written to disk.
"""

from collections.abc import Callable
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, Field, JsonValue

from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.guardrails import GuardrailResult

TOOL_NAME_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"


class ModelRole(StrEnum):
    EXTRACTION = "extraction"
    STRATEGY = "strategy"


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class StopReason(StrEnum):
    END_TURN = "end_turn"
    TOOL_USE = "tool_use"
    MAX_TOKENS = "max_tokens"
    STOP_SEQUENCE = "stop_sequence"
    PAUSE_TURN = "pause_turn"
    REFUSAL = "refusal"
    MODEL_CONTEXT_WINDOW_EXCEEDED = "model_context_window_exceeded"
    CACHED = "cached"
    OTHER = "other"


class ModelPrice(StrictModel):
    """USD per million tokens."""

    input: Decimal = Field(ge=0)
    output: Decimal = Field(ge=0)
    cache_write: Decimal = Field(ge=0)
    cache_read: Decimal = Field(ge=0)


class ModelRoute(StrictModel):
    model: str
    adaptive_thinking: bool
    effort: Effort | None


class LlmUsage(StrictModel):
    """`input_tokens` excludes cached tokens, as the provider reports it, so the four counts
    never overlap."""

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cache_creation_input_tokens: int = Field(default=0, ge=0)
    cache_read_input_tokens: int = Field(default=0, ge=0)

    def plus(self, other: "LlmUsage") -> "LlmUsage":
        return LlmUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_creation_input_tokens=self.cache_creation_input_tokens
            + other.cache_creation_input_tokens,
            cache_read_input_tokens=self.cache_read_input_tokens + other.cache_read_input_tokens,
        )


class ToolSpec(StrictModel):
    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    input_schema: dict[str, JsonValue]


class ToolCall(StrictModel):
    call_id: str
    name: str
    arguments: dict[str, JsonValue]


class ToolResult(StrictModel):
    call_id: str
    content: str
    is_error: bool


class ToolOutput(StrictModel):
    chunks: list[PackChunk] = []
    note: str | None = None


class Tool[ArgsT: BaseModel](StrictModel):
    """A read-only callable the model may invoke; `arguments` validates what the model sends."""

    name: str = Field(pattern=TOOL_NAME_PATTERN)
    description: str
    arguments: type[ArgsT]
    run: Callable[[ArgsT], ToolOutput]

    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.name,
            description=self.description,
            input_schema=self.arguments.model_json_schema(),
        )


class UserText(StrictModel):
    text: str


class ProviderTurn(StrictModel):
    model: str
    stop_reason: StopReason
    usage: LlmUsage
    tool_calls: list[ToolCall] = []
    output_text: str | None = None
    latency_ms: int = Field(default=0, ge=0)
    # Opaque provider content sent back verbatim on the next turn; some providers reject a
    # conversation whose earlier reasoning blocks were altered or dropped.
    provider_blocks: list[dict[str, JsonValue]] = []


class ToolResults(StrictModel):
    results: list[ToolResult]


type ConversationEntry = UserText | ProviderTurn | ToolResults


class RecordedTurn(StrictModel):
    """One provider turn as stored in a fixture file; a fixture is a JSON list of these."""

    model: str
    stop_reason: StopReason
    usage: LlmUsage
    tool_calls: list[ToolCall] = []
    output: JsonValue = None


class ProviderRequest(StrictModel):
    agent_name: str
    input_hash: str
    route: ModelRoute
    system: str
    conversation: tuple[ConversationEntry, ...]
    tools: tuple[ToolSpec, ...]
    tools_enabled: bool
    output_model: type[BaseModel]
    max_tokens: int = Field(gt=0)


class LlmRequest[OutputT: BaseModel](StrictModel):
    agent_name: str
    prompt_version: str
    prompt_hash: str
    model_role: ModelRole
    system: str
    user_message: str
    output_model: type[OutputT]
    max_tokens: int = Field(gt=0)
    tools: tuple[Tool, ...] = ()
    run_id: str | None = None
    fresh: bool = False


class LlmCallRecord(StrictModel):
    """One provider call; field names equal the `llm_calls` columns."""

    call_id: str
    run_id: str | None
    span_id: str | None
    agent_name: str
    prompt_version: str
    prompt_hash: str
    input_hash: str
    model: str
    model_role: ModelRole
    turn: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cache_creation_input_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(ge=0)
    cost_usd: Decimal
    stop_reason: StopReason
    latency_ms: int = Field(ge=0)


class CachedOutput(StrictModel):
    """A finished agent call; field names equal the `agent_output_cache` columns."""

    cache_key: str
    agent_name: str
    prompt_version: str
    prompt_hash: str
    model: str
    input_hash: str
    output_json: dict[str, JsonValue]
    raw_text: str
    guardrail_results: list[GuardrailResult]
    tool_evidence_ids: list[str]


class LlmResult[OutputT: BaseModel](StrictModel):
    output: OutputT
    raw_text: str
    usage: LlmUsage
    cost_usd: Decimal
    model: str
    stop_reason: StopReason
    latency_ms: int = Field(ge=0)
    cached: bool
    call_ids: list[str]
    attempts: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    tool_evidence_ids: list[str]
    guardrail_results: list[GuardrailResult]
