"""The only module that imports the Anthropic SDK; every provider specific stays here.

Confirmed against the Anthropic docs on 2026-09-26:
- Structured outputs: `output_config.format = {"type": "json_schema", "schema": ...}` on
  `messages.create`, with the schema from the SDK's `transform_schema`. The `parse()` helper is
  not used because it validates every text block, including text on tool-use turns; the shared
  client validates the final text against the output model itself, as it does for fixtures.
- Thinking: Opus 5.5 always thinks adaptively and rejects `disabled` or `budget_tokens`; the
  strategy route sends `thinking: {"type": "adaptive"}` and `output_config.effort`. Haiku 4.5
  does not support effort, so the extraction route sends neither.
- Tools: `tool_use` blocks in the reply, `tool_result` blocks (first in the next user message)
  in the answer. Opus 5.5 rejects forced tool choice, so the model is steered by the prompt and
  withdrawn tools use `tool_choice: {"type": "none"}`; the tools stay declared because the
  history still holds `tool_use` blocks. Assistant content, thinking blocks included, is sent
  back unmodified.
- Refusals: `stop_reason == "refusal"`; server-side fallback is a beta, opt-in via
  `llm_refusal_fallback`.
"""

import time
from collections.abc import Sequence
from typing import Any

import anthropic
from anthropic.types import Message, Usage

from deal_intel.config import Settings
from deal_intel.contracts.llm import (
    ConversationEntry,
    LlmUsage,
    ModelRoute,
    ProviderRequest,
    ProviderTurn,
    StopReason,
    ToolCall,
    ToolResult,
    ToolResults,
    ToolSpec,
    UserText,
)
from deal_intel.llm.errors import ProviderError

USER_ROLE = "user"
ASSISTANT_ROLE = "assistant"
TEXT_BLOCK = "text"
TOOL_USE_BLOCK = "tool_use"
TOOL_RESULT_BLOCK = "tool_result"
JSON_SCHEMA_FORMAT = "json_schema"
EPHEMERAL_CACHE = {"type": "ephemeral"}
ADAPTIVE_THINKING = {"type": "adaptive"}
NO_TOOL_CHOICE = {"type": "none"}
BETA_HEADER = "anthropic-beta"
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"
REFUSAL_FALLBACK_DEFAULT = "default"
MILLISECONDS_PER_SECOND = 1000
KNOWN_STOP_REASONS = frozenset(reason.value for reason in StopReason)


class AnthropicProvider:
    def __init__(self, settings: Settings, sdk_client: anthropic.Anthropic | None = None) -> None:
        self._refusal_fallback = settings.llm_refusal_fallback
        self._sdk = sdk_client or build_sdk_client(settings)

    def send(self, request: ProviderRequest) -> ProviderTurn:
        arguments = request_arguments(request, self._refusal_fallback)
        started = time.perf_counter()
        try:
            message = self._sdk.messages.create(**arguments)
        except anthropic.APIError as error:
            raise ProviderError(describe_api_error(error)) from error
        latency_ms = round((time.perf_counter() - started) * MILLISECONDS_PER_SECOND)
        return to_provider_turn(message, latency_ms)


def build_sdk_client(settings: Settings) -> anthropic.Anthropic:
    """The SDK retries rate limits and server errors itself. Without a configured key it reads
    ANTHROPIC_API_KEY from the environment."""
    api_key = settings.anthropic_api_key
    return anthropic.Anthropic(
        api_key=api_key.get_secret_value() if api_key else None,
        max_retries=settings.llm_sdk_max_retries,
        timeout=settings.llm_timeout_seconds,
    )


def request_arguments(request: ProviderRequest, refusal_fallback: bool) -> dict[str, Any]:
    arguments: dict[str, Any] = {
        "model": request.route.model,
        "max_tokens": request.max_tokens,
        # The system prompt is the stable prefix; evidence follows it in the user message.
        "system": [{"type": TEXT_BLOCK, "text": request.system, "cache_control": EPHEMERAL_CACHE}],
        "messages": [to_message(entry) for entry in request.conversation],
        "output_config": output_config(request),
        **thinking_arguments(request.route),
        **tool_arguments(request.tools, request.tools_enabled),
    }
    if refusal_fallback:
        arguments["extra_body"] = {"fallbacks": REFUSAL_FALLBACK_DEFAULT}
        arguments["extra_headers"] = {BETA_HEADER: REFUSAL_FALLBACK_BETA}
    return arguments


def output_config(request: ProviderRequest) -> dict[str, Any]:
    config: dict[str, Any] = {
        "format": {
            "type": JSON_SCHEMA_FORMAT,
            "schema": anthropic.transform_schema(request.output_model),
        }
    }
    if request.route.effort is not None:
        config["effort"] = request.route.effort.value
    return config


def thinking_arguments(route: ModelRoute) -> dict[str, Any]:
    return {"thinking": ADAPTIVE_THINKING} if route.adaptive_thinking else {}


def tool_arguments(tools: Sequence[ToolSpec], tools_enabled: bool) -> dict[str, Any]:
    if not tools:
        return {}
    arguments: dict[str, Any] = {"tools": [tool_definition(tool) for tool in tools]}
    if not tools_enabled:
        arguments["tool_choice"] = NO_TOOL_CHOICE
    return arguments


def tool_definition(tool: ToolSpec) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "input_schema": anthropic.transform_schema(tool.input_schema),
        "strict": True,
    }


def to_message(entry: ConversationEntry) -> dict[str, Any]:
    match entry:
        case UserText():
            return {"role": USER_ROLE, "content": entry.text}
        case ProviderTurn():
            return {"role": ASSISTANT_ROLE, "content": entry.provider_blocks}
        case ToolResults():
            return {"role": USER_ROLE, "content": [tool_result_block(r) for r in entry.results]}


def tool_result_block(result: ToolResult) -> dict[str, Any]:
    return {
        "type": TOOL_RESULT_BLOCK,
        "tool_use_id": result.call_id,
        "content": result.content,
        "is_error": result.is_error,
    }


def to_provider_turn(message: Message, latency_ms: int) -> ProviderTurn:
    texts = [block.text for block in message.content if block.type == TEXT_BLOCK]
    return ProviderTurn(
        model=message.model,
        stop_reason=stop_reason_of(message.stop_reason),
        usage=usage_of(message.usage),
        tool_calls=[
            ToolCall(call_id=block.id, name=block.name, arguments=block.input)
            for block in message.content
            if block.type == TOOL_USE_BLOCK
        ],
        output_text="".join(texts) if texts else None,
        latency_ms=latency_ms,
        provider_blocks=[block.to_dict() for block in message.content],
    )


def stop_reason_of(raw: str | None) -> StopReason:
    return StopReason(raw) if raw in KNOWN_STOP_REASONS else StopReason.OTHER


def usage_of(usage: Usage) -> LlmUsage:
    return LlmUsage(
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
        cache_read_input_tokens=usage.cache_read_input_tokens or 0,
    )


def describe_api_error(error: anthropic.APIError) -> str:
    status = getattr(error, "status_code", None)
    request_id = getattr(error, "request_id", None)
    return f"{type(error).__name__} status={status} request_id={request_id}: {error.message}"
