from collections.abc import Callable, Sequence
from enum import StrEnum

from pydantic import ValidationError

from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.llm import (
    ConversationEntry,
    ProviderTurn,
    Tool,
    ToolCall,
    ToolResult,
    ToolResults,
    UserText,
)
from deal_intel.contracts.tracing import SpanAttribute, SpanKind
from deal_intel.guardrails.framing import frame_tool_output
from deal_intel.llm.errors import ToolBudgetExceeded
from deal_intel.llm.parsing import describe_validation_error
from deal_intel.observability.tracing import SpanHandle, Tracer

TOOL_SPAN_PREFIX = "tool."
CALL_LIMIT_MESSAGE = "Tool call limit reached. Return the final output now without tools."
UNKNOWN_TOOL_MESSAGE = "There is no tool with that name."

type TurnSender = Callable[[tuple[ConversationEntry, ...], bool], ProviderTurn]


class ToolErrorCode(StrEnum):
    CALL_LIMIT_REACHED = "CALL_LIMIT_REACHED"
    UNKNOWN_TOOL = "UNKNOWN_TOOL"
    INVALID_ARGUMENTS = "INVALID_ARGUMENTS"


class ToolLoop:
    """One agent call's conversation: the model may call read-only tools, then must answer.

    The allowance of `max_tool_calls` covers the whole agent call, feedback retries included;
    once it is spent the model is offered no tools, and a turn that still calls one fails.
    """

    def __init__(
        self,
        send: TurnSender,
        tools: Sequence[Tool],
        max_tool_calls: int,
        tracer: Tracer,
        opening_message: str,
    ) -> None:
        self._send = send
        self._tools = {tool.name: tool for tool in tools}
        self._max_tool_calls = max_tool_calls
        self._tracer = tracer
        self._conversation: list[ConversationEntry] = [UserText(text=opening_message)]
        self.tool_calls_made = 0
        self.tool_chunks: list[PackChunk] = []

    def next_output(self) -> ProviderTurn:
        """Runs turns until the model answers without calling a tool."""
        # Every turn that calls tools while they are offered spends at least one call, so the
        # allowance plus the final tool-less turn bounds the loop.
        for _ in range(self._max_tool_calls + 1):
            tools_offered = self._tools_open()
            turn = self._send(tuple(self._conversation), tools_offered)
            self._conversation.append(turn)
            if not turn.tool_calls:
                return turn
            if not tools_offered:
                raise ToolBudgetExceeded("the model called a tool after tools were withdrawn")
            self._conversation.append(ToolResults(results=self._answer_all(turn.tool_calls)))
        raise ToolBudgetExceeded(f"no final output within {self._max_tool_calls} tool calls")

    def append_user_text(self, text: str) -> None:
        self._conversation.append(UserText(text=text))

    def _tools_open(self) -> bool:
        return bool(self._tools) and self.tool_calls_made < self._max_tool_calls

    def _answer_all(self, calls: Sequence[ToolCall]) -> list[ToolResult]:
        # Every tool call needs a result, including the ones past the allowance.
        return [self._answer(call) for call in calls]

    def _answer(self, call: ToolCall) -> ToolResult:
        attributes = {SpanAttribute.TOOL_NAME: call.name, SpanAttribute.CALL_ID: call.call_id}
        with self._tracer.span(TOOL_SPAN_PREFIX + call.name, SpanKind.TOOL, attributes) as span:
            if self.tool_calls_made >= self._max_tool_calls:
                return rejected(span, call, ToolErrorCode.CALL_LIMIT_REACHED, CALL_LIMIT_MESSAGE)
            self.tool_calls_made += 1
            tool = self._tools.get(call.name)
            if tool is None:
                return rejected(span, call, ToolErrorCode.UNKNOWN_TOOL, UNKNOWN_TOOL_MESSAGE)
            try:
                arguments = tool.arguments.model_validate(call.arguments)
            except ValidationError as error:
                message = describe_validation_error(error)
                return rejected(span, call, ToolErrorCode.INVALID_ARGUMENTS, message)
            output = tool.run(arguments)
            span.set_attributes({SpanAttribute.EVIDENCE_IDS: [c.chunk_id for c in output.chunks]})
        self.tool_chunks.extend(output.chunks)
        return ToolResult(call_id=call.call_id, content=frame_tool_output(output), is_error=False)


def rejected(span: SpanHandle, call: ToolCall, code: ToolErrorCode, message: str) -> ToolResult:
    """The model sees why its call failed and can correct it; the run carries on."""
    span.record_error(code)
    return ToolResult(call_id=call.call_id, content=message, is_error=True)
