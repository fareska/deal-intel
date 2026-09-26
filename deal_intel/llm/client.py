"""The one entry point for model calls: routing, output cache, tool loop, retry policy, cost,
`llm_calls` rows, spans, and fixture recording. Providers only turn one request into one turn."""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.config import Settings
from deal_intel.contracts.guardrails import OutputCheck
from deal_intel.contracts.llm import (
    CachedOutput,
    ConversationEntry,
    LlmCallRecord,
    LlmRequest,
    LlmResult,
    LlmUsage,
    ModelRoute,
    ProviderRequest,
    ProviderTurn,
    StopReason,
    ToolSpec,
)
from deal_intel.contracts.tracing import SpanAttribute, SpanKind
from deal_intel.llm import output_cache
from deal_intel.llm.call_log import write_call
from deal_intel.llm.cost import cost_usd
from deal_intel.llm.errors import ModelRefusal
from deal_intel.llm.fixtures import to_recorded, write_turns
from deal_intel.llm.keys import cache_key, input_hash
from deal_intel.llm.retry import CheckedOutput, complete_with_feedback
from deal_intel.llm.routing import route_for
from deal_intel.llm.tool_loop import ToolLoop
from deal_intel.observability.tracing import Tracer

LLM_REQUEST_SPAN = "llm_request"


class LlmProvider(Protocol):
    def send(self, request: ProviderRequest) -> ProviderTurn: ...


@dataclass
class CallLedger:
    """Everything one agent call spent, across tool turns and retries."""

    turns: list[ProviderTurn] = field(default_factory=list)
    call_ids: list[str] = field(default_factory=list)
    usage: LlmUsage = field(default_factory=LlmUsage)
    cost_usd: Decimal = Decimal(0)
    latency_ms: int = 0

    def add(self, turn: ProviderTurn, call_id: str, cost: Decimal) -> None:
        self.turns.append(turn)
        self.call_ids.append(call_id)
        self.usage = self.usage.plus(turn.usage)
        self.cost_usd += cost
        self.latency_ms += turn.latency_ms


@dataclass(frozen=True)
class CallContext[OutputT: BaseModel]:
    request: LlmRequest[OutputT]
    route: ModelRoute
    input_hash: str
    tool_specs: tuple[ToolSpec, ...]
    ledger: CallLedger


class LlmClient:
    def __init__(
        self,
        provider: LlmProvider,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        tracer: Tracer,
        record_fixtures_to: Path | None = None,
    ) -> None:
        self._provider = provider
        self._settings = settings
        self._session_factory = session_factory
        self._tracer = tracer
        self._record_fixtures_to = record_fixtures_to

    def complete[OutputT: BaseModel](
        self, request: LlmRequest[OutputT], check: OutputCheck[OutputT] | None = None
    ) -> LlmResult[OutputT]:
        """`fresh` skips the cache lookup but still stores the new output for later calls."""
        route = route_for(request.model_role, self._settings)
        request_hash = input_hash(request)
        key = cache_key(request.agent_name, request.prompt_hash, route, request_hash)
        if not request.fresh:
            cached = self._cached_result(request, route, request_hash, key)
            if cached is not None:
                return cached
        context = CallContext(
            request=request,
            route=route,
            input_hash=request_hash,
            tool_specs=tuple(tool.spec() for tool in request.tools),
            ledger=CallLedger(),
        )
        loop = ToolLoop(
            send=partial(self._send_turn, context),
            tools=request.tools,
            max_tool_calls=self._settings.max_tool_calls,
            tracer=self._tracer,
            opening_message=request.user_message,
        )
        checked = complete_with_feedback(
            loop, request.output_model, check, self._settings.llm_feedback_retries, self._tracer
        )
        tool_evidence_ids = list(dict.fromkeys(chunk.chunk_id for chunk in loop.tool_chunks))
        self._store(context, key, checked, tool_evidence_ids)
        self._record_fixture(context)
        return fresh_result(context, checked, loop.tool_calls_made, tool_evidence_ids)

    def _send_turn[OutputT: BaseModel](
        self,
        context: CallContext[OutputT],
        conversation: tuple[ConversationEntry, ...],
        tools_enabled: bool,
    ) -> ProviderTurn:
        request, route, ledger = context.request, context.route, context.ledger
        attributes = {
            **request_attributes(request, route, context.input_hash),
            SpanAttribute.TURN: len(ledger.turns),
            SpanAttribute.CACHED: False,
        }
        with self._tracer.span(LLM_REQUEST_SPAN, SpanKind.LLM_REQUEST, attributes) as span:
            turn = self._provider.send(
                ProviderRequest(
                    agent_name=request.agent_name,
                    input_hash=context.input_hash,
                    route=route,
                    system=request.system,
                    conversation=conversation,
                    tools=context.tool_specs,
                    tools_enabled=tools_enabled,
                    output_model=request.output_model,
                    max_tokens=request.max_tokens,
                )
            )
            cost = cost_usd(self._settings.model_prices_usd_per_mtok, route.model, turn.usage)
            call_id = uuid.uuid4().hex
            write_call(
                self._session_factory,
                call_record(context, turn, call_id, span.span_id, len(ledger.turns), cost),
            )
            ledger.add(turn, call_id, cost)
            span.set_attributes({**usage_attributes(turn, cost), SpanAttribute.CALL_ID: call_id})
            if turn.stop_reason is StopReason.REFUSAL:
                raise ModelRefusal(f"the model refused the {request.agent_name} request")
        return turn

    def _cached_result[OutputT: BaseModel](
        self, request: LlmRequest[OutputT], route: ModelRoute, request_hash: str, key: str
    ) -> LlmResult[OutputT] | None:
        entry = output_cache.lookup(self._session_factory, key)
        if entry is None:
            return None
        attributes = {
            **request_attributes(request, route, request_hash),
            SpanAttribute.CACHED: True,
        }
        with self._tracer.span(LLM_REQUEST_SPAN, SpanKind.LLM_REQUEST, attributes):
            output = request.output_model.model_validate(entry.output_json)
        return LlmResult[request.output_model](
            output=output,
            raw_text=entry.raw_text,
            usage=LlmUsage(),
            cost_usd=Decimal(0),
            model=route.model,
            stop_reason=StopReason.CACHED,
            latency_ms=0,
            cached=True,
            call_ids=[],
            attempts=0,
            tool_calls=0,
            tool_evidence_ids=entry.tool_evidence_ids,
            guardrail_results=entry.guardrail_results,
        )

    def _store[OutputT: BaseModel](
        self,
        context: CallContext[OutputT],
        key: str,
        checked: CheckedOutput[OutputT],
        tool_evidence_ids: list[str],
    ) -> None:
        request = context.request
        output_cache.store(
            self._session_factory,
            CachedOutput(
                cache_key=key,
                agent_name=request.agent_name,
                prompt_version=request.prompt_version,
                prompt_hash=request.prompt_hash,
                model=context.route.model,
                input_hash=context.input_hash,
                output_json=checked.output.model_dump(mode="json"),
                raw_text=checked.raw_text,
                guardrail_results=list(checked.results),
                tool_evidence_ids=tool_evidence_ids,
            ),
        )

    def _record_fixture[OutputT: BaseModel](self, context: CallContext[OutputT]) -> None:
        if self._record_fixtures_to is None:
            return
        write_turns(
            self._record_fixtures_to,
            context.request.agent_name,
            context.input_hash,
            [to_recorded(turn) for turn in context.ledger.turns],
        )


def request_attributes[OutputT: BaseModel](
    request: LlmRequest[OutputT], route: ModelRoute, request_hash: str
) -> dict[SpanAttribute, object]:
    attributes: dict[SpanAttribute, object] = {
        SpanAttribute.AGENT_NAME: request.agent_name,
        SpanAttribute.PROMPT_VERSION: request.prompt_version,
        SpanAttribute.PROMPT_HASH: request.prompt_hash,
        SpanAttribute.INPUT_HASH: request_hash,
        SpanAttribute.MODEL: route.model,
        SpanAttribute.MODEL_ROLE: request.model_role,
    }
    if request.run_id is not None:
        attributes[SpanAttribute.RUN_ID] = request.run_id
    return attributes


def usage_attributes(turn: ProviderTurn, cost: Decimal) -> dict[SpanAttribute, object]:
    return {
        SpanAttribute.STOP_REASON: turn.stop_reason,
        SpanAttribute.INPUT_TOKENS: turn.usage.input_tokens,
        SpanAttribute.OUTPUT_TOKENS: turn.usage.output_tokens,
        SpanAttribute.CACHE_READ_TOKENS: turn.usage.cache_read_input_tokens,
        SpanAttribute.CACHE_WRITE_TOKENS: turn.usage.cache_creation_input_tokens,
        SpanAttribute.COST_USD: float(cost),
        SpanAttribute.LATENCY_MS: turn.latency_ms,
    }


def call_record[OutputT: BaseModel](
    context: CallContext[OutputT],
    turn: ProviderTurn,
    call_id: str,
    span_id: str,
    turn_index: int,
    cost: Decimal,
) -> LlmCallRecord:
    request = context.request
    return LlmCallRecord(
        call_id=call_id,
        run_id=request.run_id,
        span_id=span_id,
        agent_name=request.agent_name,
        prompt_version=request.prompt_version,
        prompt_hash=request.prompt_hash,
        input_hash=context.input_hash,
        model=context.route.model,
        model_role=request.model_role,
        turn=turn_index,
        input_tokens=turn.usage.input_tokens,
        output_tokens=turn.usage.output_tokens,
        cache_creation_input_tokens=turn.usage.cache_creation_input_tokens,
        cache_read_input_tokens=turn.usage.cache_read_input_tokens,
        cost_usd=cost,
        stop_reason=turn.stop_reason,
        latency_ms=turn.latency_ms,
    )


def fresh_result[OutputT: BaseModel](
    context: CallContext[OutputT],
    checked: CheckedOutput[OutputT],
    tool_calls: int,
    tool_evidence_ids: list[str],
) -> LlmResult[OutputT]:
    ledger = context.ledger
    final_turn = ledger.turns[-1]
    return LlmResult[context.request.output_model](
        output=checked.output,
        raw_text=checked.raw_text,
        usage=ledger.usage,
        cost_usd=ledger.cost_usd,
        model=final_turn.model,
        stop_reason=final_turn.stop_reason,
        latency_ms=ledger.latency_ms,
        cached=False,
        call_ids=ledger.call_ids,
        attempts=checked.attempts,
        tool_calls=tool_calls,
        tool_evidence_ids=tool_evidence_ids,
        guardrail_results=list(checked.results),
    )
