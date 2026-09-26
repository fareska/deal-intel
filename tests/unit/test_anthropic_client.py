import ast
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import anthropic
import httpx
import pytest
from anthropic.types import Message

from deal_intel.config import Settings, get_settings
from deal_intel.contracts.agents.negotiation_strategy import StrategyOutput
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.llm import (
    ConversationEntry,
    LlmUsage,
    ModelRole,
    ProviderRequest,
    ProviderTurn,
    StopReason,
    ToolCall,
    ToolResult,
    ToolResults,
    ToolSpec,
    UserText,
)
from deal_intel.llm.anthropic_client import (
    ADAPTIVE_THINKING,
    BETA_HEADER,
    EPHEMERAL_CACHE,
    NO_TOOL_CHOICE,
    REFUSAL_FALLBACK_BETA,
    AnthropicProvider,
    stop_reason_of,
)
from deal_intel.llm.errors import ProviderError
from deal_intel.llm.routing import route_for

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "deal_intel"
SDK_MODULE = "anthropic"
ADAPTER = PACKAGE_ROOT / "llm" / "anthropic_client.py"
SYSTEM = "You extract facts."
THINKING_BLOCK = {"type": "thinking", "thinking": "Checking the evidence.", "signature": "sig"}
TOOL_USE = {"type": "tool_use", "id": "toolu_1", "name": "search_evidence", "input": {"q": "x"}}


class CityFacts(StrictModel):
    city: str
    population: int


class StubMessages:
    def __init__(self, reply: Message | Exception) -> None:
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    def create(self, **arguments: Any) -> Message:
        self.calls.append(arguments)
        if isinstance(self._reply, Exception):
            raise self._reply
        return self._reply


class StubSdk:
    def __init__(self, reply: Message | Exception) -> None:
        self.messages = StubMessages(reply)


def sdk_message(content: list[dict[str, Any]], stop_reason: str = "end_turn") -> Message:
    return Message.model_validate(
        {
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5-5",
            "content": content,
            "stop_reason": stop_reason,
            "stop_sequence": None,
            "usage": {
                "input_tokens": 120,
                "output_tokens": 40,
                "cache_creation_input_tokens": None,
                "cache_read_input_tokens": 900,
            },
        }
    )


def provider_request(
    settings: Settings,
    role: ModelRole,
    conversation: Sequence[ConversationEntry] = (UserText(text="Name a city."),),
    tools: Sequence[ToolSpec] = (),
    tools_enabled: bool = True,
) -> ProviderRequest:
    return ProviderRequest(
        agent_name="test_agent",
        input_hash="hash",
        route=route_for(role, settings),
        system=SYSTEM,
        conversation=tuple(conversation),
        tools=tuple(tools),
        tools_enabled=tools_enabled,
        output_model=CityFacts,
        max_tokens=256,
    )


def sent(
    request: ProviderRequest, settings: Settings, reply: Message | None = None
) -> dict[str, Any]:
    sdk = StubSdk(reply or sdk_message([{"type": "text", "text": '{"city": "Oslo"}'}]))
    AnthropicProvider(settings, sdk_client=sdk).send(request)  # type: ignore[arg-type]
    (arguments,) = sdk.messages.calls
    return arguments


def search_spec() -> ToolSpec:
    return ToolSpec(
        name="search_evidence",
        description="Search evidence.",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]},
    )


@pytest.fixture
def settings() -> Settings:
    return get_settings()


def imports_sdk(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            modules = [node.module or ""]
        else:
            continue
        if any(module.split(".")[0] == SDK_MODULE for module in modules):
            return True
    return False


def test_only_the_adapter_imports_the_sdk() -> None:
    importers = {
        path
        for path in PACKAGE_ROOT.rglob("*.py")
        if imports_sdk(ast.parse(path.read_text(encoding="utf-8")))
    }

    assert importers == {ADAPTER}


def test_strategy_route_sends_adaptive_thinking_and_effort(settings: Settings) -> None:
    arguments = sent(provider_request(settings, ModelRole.STRATEGY), settings)

    assert arguments["model"] == settings.model_strategy
    assert arguments["thinking"] == ADAPTIVE_THINKING
    assert arguments["output_config"]["effort"] == settings.strategy_effort.value


def test_extraction_route_sends_no_thinking_or_effort(settings: Settings) -> None:
    arguments = sent(provider_request(settings, ModelRole.EXTRACTION), settings)

    assert arguments["model"] == settings.model_extraction
    assert "thinking" not in arguments
    assert "effort" not in arguments["output_config"]


def test_output_schema_and_cached_system_prefix(settings: Settings) -> None:
    arguments = sent(provider_request(settings, ModelRole.EXTRACTION), settings)

    output_format = arguments["output_config"]["format"]
    assert output_format["type"] == "json_schema"
    assert output_format["schema"]["required"] == ["city", "population"]
    assert output_format["schema"]["additionalProperties"] is False
    assert arguments["system"] == [
        {"type": "text", "text": SYSTEM, "cache_control": EPHEMERAL_CACHE}
    ]
    assert "extra_headers" not in arguments


def test_harness_only_flag_is_not_in_the_schema_sent(settings: Settings) -> None:
    request = provider_request(settings, ModelRole.STRATEGY).model_copy(
        update={"output_model": StrategyOutput}
    )

    schema = sent(request, settings)["output_config"]["format"]["schema"]

    assert "negotiation_state" in schema["properties"]
    assert "no_evidence" not in schema["properties"]


def test_tools_are_strict_and_withdrawn_tools_stay_declared(settings: Settings) -> None:
    offered = sent(provider_request(settings, ModelRole.STRATEGY, tools=[search_spec()]), settings)
    withdrawn = sent(
        provider_request(settings, ModelRole.STRATEGY, tools=[search_spec()], tools_enabled=False),
        settings,
    )

    assert [tool["name"] for tool in offered["tools"]] == ["search_evidence"]
    assert offered["tools"][0]["strict"] is True
    assert "tool_choice" not in offered
    assert withdrawn["tools"] == offered["tools"]
    assert withdrawn["tool_choice"] == NO_TOOL_CHOICE


def test_history_sends_provider_blocks_back_and_maps_tool_results(settings: Settings) -> None:
    earlier = ProviderTurn(
        model=settings.model_strategy,
        stop_reason=StopReason.TOOL_USE,
        usage=LlmUsage(),
        tool_calls=[ToolCall(call_id="toolu_1", name="search_evidence", arguments={"q": "x"})],
        provider_blocks=[THINKING_BLOCK, TOOL_USE],
    )
    conversation = [
        UserText(text="Find it."),
        earlier,
        ToolResults(results=[ToolResult(call_id="toolu_1", content="<evidence/>", is_error=False)]),
    ]

    arguments = sent(
        provider_request(settings, ModelRole.STRATEGY, conversation, [search_spec()]), settings
    )

    assert arguments["messages"] == [
        {"role": "user", "content": "Find it."},
        {"role": "assistant", "content": [THINKING_BLOCK, TOOL_USE]},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "toolu_1",
                    "content": "<evidence/>",
                    "is_error": False,
                }
            ],
        },
    ]


def test_reply_maps_to_a_provider_turn(settings: Settings) -> None:
    reply = sdk_message([THINKING_BLOCK, TOOL_USE], stop_reason="tool_use")
    sdk = StubSdk(reply)

    turn = AnthropicProvider(settings, sdk_client=sdk).send(  # type: ignore[arg-type]
        provider_request(settings, ModelRole.STRATEGY, tools=[search_spec()])
    )

    assert turn.stop_reason is StopReason.TOOL_USE
    assert turn.tool_calls == [
        ToolCall(call_id="toolu_1", name="search_evidence", arguments={"q": "x"})
    ]
    assert turn.output_text is None
    assert turn.usage == LlmUsage(input_tokens=120, output_tokens=40, cache_read_input_tokens=900)
    assert turn.provider_blocks == [THINKING_BLOCK, TOOL_USE]


def test_unlisted_stop_reason_maps_to_other() -> None:
    assert stop_reason_of("refusal") is StopReason.REFUSAL
    assert stop_reason_of("something_new") is StopReason.OTHER
    assert stop_reason_of(None) is StopReason.OTHER


def test_refusal_fallback_is_opt_in(settings: Settings) -> None:
    enabled = settings.model_copy(update={"llm_refusal_fallback": True})

    arguments = sent(provider_request(enabled, ModelRole.STRATEGY), enabled)

    assert arguments["extra_headers"] == {BETA_HEADER: REFUSAL_FALLBACK_BETA}
    assert arguments["extra_body"] == {"fallbacks": "default"}


def test_sdk_errors_become_provider_errors(settings: Settings) -> None:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    sdk = StubSdk(anthropic.APIConnectionError(request=request))

    with pytest.raises(ProviderError, match="APIConnectionError"):
        AnthropicProvider(settings, sdk_client=sdk).send(  # type: ignore[arg-type]
            provider_request(settings, ModelRole.EXTRACTION)
        )
