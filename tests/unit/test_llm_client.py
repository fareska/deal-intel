import json
import re
from collections.abc import Callable, Sequence
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import Field, JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.config import (
    DEFAULT_MODEL_EXTRACTION,
    DEFAULT_MODEL_STRATEGY,
    Settings,
    get_settings,
)
from deal_intel.contracts.access import Allowed
from deal_intel.contracts.agents.common import AgentName
from deal_intel.contracts.agents.conversation_intelligence import ConversationFindings
from deal_intel.contracts.agents.negotiation_strategy import StrategyOutput
from deal_intel.contracts.agents.stakeholder_map import StakeholderMap
from deal_intel.contracts.base import StrictModel
from deal_intel.contracts.evidence import PackChunk
from deal_intel.contracts.guardrails import (
    Confidence,
    EvidenceBacked,
    GuardrailCheck,
    GuardrailOutcome,
    GuardrailResult,
    OutputCheck,
)
from deal_intel.contracts.llm import (
    Effort,
    LlmRequest,
    LlmUsage,
    ModelRole,
    ProviderRequest,
    ProviderTurn,
    RecordedTurn,
    StopReason,
    Tool,
    ToolCall,
    ToolOutput,
    ToolResults,
    UserText,
)
from deal_intel.contracts.tracing import ATTRIBUTE_VALUE_PATTERN, SpanAttribute, SpanKind
from deal_intel.db.models import EvidenceChunkRow, LlmCallRow, TraceSpanRow
from deal_intel.guardrails.validators import FINDING_CHECKS, evidence_check
from deal_intel.llm.client import LlmClient, LlmProvider
from deal_intel.llm.cost import cost_usd
from deal_intel.llm.errors import (
    FixtureMissing,
    LlmErrorCode,
    ModelRefusal,
    OutputNotSalvageable,
    SchemaValidationError,
    ToolBudgetExceeded,
    UnknownModelPrice,
)
from deal_intel.llm.fake_client import FakeLlmProvider
from deal_intel.llm.fixtures import fixture_path, load_turns, write_turns
from deal_intel.llm.keys import cache_key, input_hash
from deal_intel.llm.parsing import parse_output
from deal_intel.llm.routing import route_for
from deal_intel.llm.tool_loop import CALL_LIMIT_MESSAGE, ToolErrorCode
from deal_intel.observability.tracing import PostgresTracer
from deal_intel.permissions.gate import authorize
from deal_intel.retrieval.retriever import ScopedRetriever

type PackChunkFactory = Callable[[str, str], PackChunk]

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMITTED_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "llm"
AGENT = "test_agent"
TOOL_AGENT = "test_tool_agent"
RUN_ID = "run-test-1"
SYSTEM = "Answer in the given schema."
CITY_QUESTION = "Name one European capital and its population."
LISBON = {"city": "Lisbon", "population": 545923}
USAGE = LlmUsage(input_tokens=1000, output_tokens=500)
HAIKU_COST = Decimal("0.0035")

SEARCH_TOOL = "search_evidence"
VERBAL_APPROVAL_QUERY = "verbally okayed discount"
VERBAL_APPROVAL_CHUNK = "slack:SLK-1003-02"
VERBAL_APPROVAL_QUESTION = "What did the team hear about discount approval on Eclipse?"
CALL = "gong_summary:CALL-027"
CALL_TEXT = "Procurement asked for a 22% discount on the renewal."
UNKNOWN_CHUNK = "policy:legal-signoff"


class CityFacts(StrictModel):
    city: str
    population: int


class Claim(EvidenceBacked):
    statement: str


class Claims(StrictModel):
    claims: list[Claim] = Field(min_length=1, max_length=3)


class SearchArgs(StrictModel):
    query: str = Field(min_length=1)
    k: int = Field(default=3, ge=1, le=5)


class ProviderSpy:
    """Passes requests through and keeps them, so tests can read what the model was sent."""

    def __init__(self, inner: LlmProvider) -> None:
        self.inner = inner
        self.requests: list[ProviderRequest] = []

    def send(self, request: ProviderRequest) -> ProviderTurn:
        self.requests.append(request)
        return self.inner.send(request)


class ScriptedProvider:
    def __init__(self, turns: Sequence[ProviderTurn]) -> None:
        self._turns = iter(turns)

    def send(self, request: ProviderRequest) -> ProviderTurn:
        return next(self._turns)


@pytest.fixture
def settings() -> Settings:
    return get_settings().model_copy(update={"max_tool_calls": 4, "llm_feedback_retries": 2})


@pytest.fixture
def tracer(session_factory: sessionmaker[Session]) -> PostgresTracer:
    return PostgresTracer(session_factory)


def build_client(
    provider: LlmProvider,
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    record_to: Path | None = None,
) -> LlmClient:
    return LlmClient(
        provider,
        settings=settings,
        session_factory=session_factory,
        tracer=tracer,
        record_fixtures_to=record_to,
    )


def city_request(
    *, fresh: bool = False, role: ModelRole = ModelRole.EXTRACTION, prompt_hash: str = "p1"
) -> LlmRequest[CityFacts]:
    return LlmRequest[CityFacts](
        agent_name=AGENT,
        prompt_version="v1",
        prompt_hash=prompt_hash,
        model_role=role,
        system=SYSTEM,
        user_message=CITY_QUESTION,
        output_model=CityFacts,
        max_tokens=256,
        run_id=RUN_ID,
        fresh=fresh,
    )


def claims_request(
    user_message: str, *, agent: str = AGENT, tools: Sequence[Tool] = ()
) -> LlmRequest[Claims]:
    return LlmRequest[Claims](
        agent_name=agent,
        prompt_version="v1",
        prompt_hash="p1",
        model_role=ModelRole.EXTRACTION,
        system=SYSTEM,
        user_message=user_message,
        output_model=Claims,
        max_tokens=512,
        tools=tuple(tools),
        run_id=RUN_ID,
    )


def answer(output: JsonValue) -> RecordedTurn:
    return RecordedTurn(
        model=DEFAULT_MODEL_EXTRACTION, stop_reason=StopReason.END_TURN, usage=USAGE, output=output
    )


def tool_turn(*calls: ToolCall) -> RecordedTurn:
    return RecordedTurn(
        model=DEFAULT_MODEL_EXTRACTION,
        stop_reason=StopReason.TOOL_USE,
        usage=USAGE,
        tool_calls=list(calls),
    )


def search_call(call_id: str, query: str, k: int = 3) -> ToolCall:
    return ToolCall(call_id=call_id, name=SEARCH_TOOL, arguments={"query": query, "k": k})


def claims_output(*claims: tuple[str, str]) -> dict[str, JsonValue]:
    return {
        "claims": [
            {
                "statement": statement,
                "evidence_ids": [chunk_id],
                "confidence": Confidence.HIGH.value,
            }
            for statement, chunk_id in claims
        ]
    }


def with_fixture(root: Path, request: LlmRequest, turns: Sequence[RecordedTurn]) -> Path:
    return write_turns(root, request.agent_name, input_hash(request), turns)


def evidence_tool(run: Callable[[SearchArgs], ToolOutput]) -> Tool[SearchArgs]:
    return Tool[SearchArgs](
        name=SEARCH_TOOL,
        description="Search the evidence you are allowed to see.",
        arguments=SearchArgs,
        run=run,
    )


def fixed_evidence_tool(chunks: Sequence[PackChunk]) -> Tool[SearchArgs]:
    return evidence_tool(lambda _: ToolOutput(chunks=list(chunks)))


def scoped_search_tool(session: Session) -> Tool[SearchArgs]:
    access = authorize(session, "USR-5003", "OPP-1003")
    assert isinstance(access, Allowed)
    retriever = ScopedRetriever(session, access.scope)
    return evidence_tool(
        lambda args: ToolOutput(chunks=retriever.search(args.query, k=args.k).chunks)
    )


def llm_calls(session_factory: sessionmaker[Session]) -> list[LlmCallRow]:
    with session_factory() as session:
        return list(session.scalars(select(LlmCallRow).order_by(LlmCallRow.created_at)))


def spans_of(session_factory: sessionmaker[Session], kind: SpanKind) -> list[TraceSpanRow]:
    with session_factory() as session:
        statement = select(TraceSpanRow).where(TraceSpanRow.kind == kind.value)
        return list(session.scalars(statement.order_by(TraceSpanRow.started_at)))


def outcomes(results: Sequence[GuardrailResult]) -> set[tuple[GuardrailCheck, GuardrailOutcome]]:
    return {(result.check, result.outcome) for result in results}


def call_evidence_check(pack_chunk: PackChunkFactory) -> OutputCheck[Claims]:
    return evidence_check([pack_chunk(CALL, CALL_TEXT)], FINDING_CHECKS)


def test_committed_fixture_replays_into_the_output_model(
    settings: Settings, session_factory: sessionmaker[Session], tracer: PostgresTracer
) -> None:
    client = build_client(FakeLlmProvider(COMMITTED_FIXTURES), settings, session_factory, tracer)

    result = client.complete(city_request())

    assert result.output == CityFacts.model_validate(LISBON)
    assert (result.cached, result.attempts, result.stop_reason) == (False, 1, StopReason.END_TURN)
    assert result.usage == USAGE
    assert result.cost_usd == HAIKU_COST


def test_each_provider_call_writes_one_row_linked_to_its_span(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
) -> None:
    request = city_request()
    with_fixture(tmp_path, request, [answer({"city": "Lisbon"}), answer(LISBON)])
    client = build_client(FakeLlmProvider(tmp_path), settings, session_factory, tracer)

    result = client.complete(request)

    rows = llm_calls(session_factory)
    request_spans = {span.span_id: span for span in spans_of(session_factory, SpanKind.LLM_REQUEST)}
    assert [row.call_id for row in rows] == result.call_ids
    assert [row.turn for row in rows] == [0, 1]
    assert all(row.span_id in request_spans for row in rows)
    assert all(row.run_id == RUN_ID and row.cost_usd == HAIKU_COST for row in rows)
    assert result.cost_usd == 2 * HAIKU_COST


def test_schema_failure_is_sent_back_and_retried(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
) -> None:
    request = city_request()
    with_fixture(tmp_path, request, [answer({"city": "Lisbon"}), answer(LISBON)])
    spy = ProviderSpy(FakeLlmProvider(tmp_path))

    result = build_client(spy, settings, session_factory, tracer).complete(request)

    feedback = spy.requests[1].conversation[-1]
    assert isinstance(feedback, UserText)
    assert "population" in feedback.text
    assert result.attempts == 2
    assert (GuardrailCheck.RETRY, GuardrailOutcome.RETRIED) in outcomes(result.guardrail_results)


def test_schema_failure_on_every_attempt_raises(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
) -> None:
    request = city_request()
    with_fixture(tmp_path, request, [answer("not json")] * 3)
    client = build_client(FakeLlmProvider(tmp_path), settings, session_factory, tracer)

    with pytest.raises(SchemaValidationError):
        client.complete(request)
    assert len(llm_calls(session_factory)) == 3


def test_guardrail_findings_are_sent_back_and_retried(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
    pack_chunk: PackChunkFactory,
) -> None:
    request = claims_request("What did procurement ask for?")
    good = ("Procurement asked for a 22% discount.", CALL)
    with_fixture(
        tmp_path,
        request,
        [answer(claims_output(("Legal signed off.", UNKNOWN_CHUNK))), answer(claims_output(good))],
    )
    spy = ProviderSpy(FakeLlmProvider(tmp_path))

    client = build_client(spy, settings, session_factory, tracer)
    result = client.complete(request, call_evidence_check(pack_chunk))

    feedback = spy.requests[1].conversation[-1]
    assert isinstance(feedback, UserText)
    assert UNKNOWN_CHUNK in feedback.text
    assert result.output == Claims.model_validate(claims_output(good))
    assert {
        (GuardrailCheck.RETRY, GuardrailOutcome.RETRIED),
        (GuardrailCheck.CITATIONS, GuardrailOutcome.PASSED),
    } <= outcomes(result.guardrail_results)


def test_exhausted_retries_drop_offending_items_and_keep_a_record(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
    pack_chunk: PackChunkFactory,
) -> None:
    request = claims_request("What did procurement ask for?")
    good = ("Procurement asked for a 22% discount.", CALL)
    mixed = claims_output(good, ("Procurement asked for 30%.", CALL))
    with_fixture(tmp_path, request, [answer(mixed)] * 3)

    client = build_client(FakeLlmProvider(tmp_path), settings, session_factory, tracer)
    result = client.complete(request, call_evidence_check(pack_chunk))

    assert result.output == Claims.model_validate(claims_output(good))
    assert result.attempts == 3
    assert {
        (GuardrailCheck.NUMBERS, GuardrailOutcome.DROPPED),
        (GuardrailCheck.RETRY, GuardrailOutcome.DROPPED),
    } <= outcomes(result.guardrail_results)


def test_output_that_breaks_its_contract_once_cleaned_raises(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
    pack_chunk: PackChunkFactory,
) -> None:
    request = claims_request("What did procurement ask for?")
    with_fixture(
        tmp_path, request, [answer(claims_output(("Legal signed off.", UNKNOWN_CHUNK)))] * 3
    )

    client = build_client(FakeLlmProvider(tmp_path), settings, session_factory, tracer)
    with pytest.raises(OutputNotSalvageable):
        client.complete(request, call_evidence_check(pack_chunk))


def test_identical_request_is_served_from_the_cache(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
) -> None:
    provider = FakeLlmProvider(COMMITTED_FIXTURES)
    client = build_client(provider, settings, session_factory, tracer)

    first = client.complete(city_request())
    second = client.complete(city_request())

    assert provider.calls[AGENT] == 1
    assert len(llm_calls(session_factory)) == 1
    assert second.output == first.output
    assert (second.cached, second.cost_usd, second.stop_reason) == (True, 0, StopReason.CACHED)
    assert second.guardrail_results == first.guardrail_results
    cached_spans = [
        span
        for span in spans_of(session_factory, SpanKind.LLM_REQUEST)
        if span.attributes[SpanAttribute.CACHED]
    ]
    assert len(cached_spans) == 1


def test_fresh_request_skips_the_cache(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
) -> None:
    provider = FakeLlmProvider(COMMITTED_FIXTURES)
    client = build_client(provider, settings, session_factory, tracer)

    client.complete(city_request())
    fresh = client.complete(city_request(fresh=True))

    assert provider.calls[AGENT] == 2
    assert not fresh.cached
    assert len(llm_calls(session_factory)) == 2


def test_cache_key_changes_with_prompt_and_effort(settings: Settings) -> None:
    extraction = route_for(ModelRole.EXTRACTION, settings)
    strategy = route_for(ModelRole.STRATEGY, settings)
    lower_effort = strategy.model_copy(update={"effort": Effort.LOW})

    keys = {
        cache_key(AGENT, "p1", extraction, "hash"),
        cache_key(AGENT, "p2", extraction, "hash"),
        cache_key(AGENT, "p1", strategy, "hash"),
        cache_key(AGENT, "p1", lower_effort, "hash"),
    }

    assert len(keys) == 4


def test_routing_gives_thinking_and_effort_to_strategy_only(settings: Settings) -> None:
    strategy = route_for(ModelRole.STRATEGY, settings)
    extraction = route_for(ModelRole.EXTRACTION, settings)

    assert (strategy.model, strategy.adaptive_thinking, strategy.effort) == (
        settings.model_strategy,
        True,
        settings.strategy_effort,
    )
    assert (extraction.model, extraction.adaptive_thinking, extraction.effort) == (
        settings.model_extraction,
        False,
        None,
    )


def test_cost_prices_all_four_token_counts(settings: Settings) -> None:
    prices = settings.model_prices_usd_per_mtok
    usage = LlmUsage(
        input_tokens=1000,
        output_tokens=500,
        cache_creation_input_tokens=2000,
        cache_read_input_tokens=10000,
    )

    assert cost_usd(prices, DEFAULT_MODEL_EXTRACTION, USAGE) == HAIKU_COST
    # 1000 * 4 + 500 * 20 + 2000 * 5 + 10000 * 0.20, per million tokens.
    assert cost_usd(prices, DEFAULT_MODEL_STRATEGY, usage) == Decimal("0.026")


def test_unknown_model_price_raises(settings: Settings) -> None:
    with pytest.raises(UnknownModelPrice, match="claude-unknown"):
        cost_usd(settings.model_prices_usd_per_mtok, "claude-unknown", USAGE)


def test_refusal_raises_and_still_records_the_call(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
) -> None:
    provider = FakeLlmProvider(tmp_path, fail_agents=[AGENT])
    client = build_client(provider, settings, session_factory, tracer)

    with pytest.raises(ModelRefusal):
        client.complete(city_request())

    (row,) = llm_calls(session_factory)
    (span,) = spans_of(session_factory, SpanKind.LLM_REQUEST)
    assert row.stop_reason == StopReason.REFUSAL
    assert row.span_id == span.span_id
    assert span.attributes[SpanAttribute.ERROR_CODE] == LlmErrorCode.MODEL_REFUSAL


def test_missing_fixture_names_the_path_to_record(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
) -> None:
    request = city_request()
    expected = fixture_path(tmp_path, AGENT, input_hash(request))
    client = build_client(FakeLlmProvider(tmp_path), settings, session_factory, tracer)

    with pytest.raises(FixtureMissing, match=re.escape(str(expected))):
        client.complete(request)


def test_recording_writes_fixtures_the_fake_replays(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
) -> None:
    thinking_block: dict[str, JsonValue] = {"type": "thinking", "thinking": "...", "signature": "s"}
    recorded = ProviderTurn(
        model=DEFAULT_MODEL_EXTRACTION,
        stop_reason=StopReason.END_TURN,
        usage=USAGE,
        output_text=json.dumps(LISBON),
        provider_blocks=[thinking_block],
    )
    recorder = build_client(
        ScriptedProvider([recorded]), settings, session_factory, tracer, record_to=tmp_path
    )

    recorder.complete(city_request())
    replayed = build_client(FakeLlmProvider(tmp_path), settings, session_factory, tracer).complete(
        city_request(fresh=True)
    )

    (turn,) = load_turns(tmp_path, AGENT, input_hash(city_request()))
    assert turn == answer(LISBON)
    assert replayed.output == CityFacts.model_validate(LISBON)


def test_tool_loop_replays_a_scoped_search_then_the_answer(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    ingested_session: Session,
) -> None:
    request = claims_request(
        VERBAL_APPROVAL_QUESTION, agent=TOOL_AGENT, tools=[scoped_search_tool(ingested_session)]
    )
    spy = ProviderSpy(FakeLlmProvider(COMMITTED_FIXTURES))

    client = build_client(spy, settings, session_factory, tracer)
    result = client.complete(request, evidence_check([], FINDING_CHECKS))

    tool_results = spy.requests[1].conversation[-1]
    assert isinstance(tool_results, ToolResults)
    assert f'chunk_id="{VERBAL_APPROVAL_CHUNK}"' in tool_results.results[0].content
    assert result.tool_calls == 1
    assert VERBAL_APPROVAL_CHUNK in result.tool_evidence_ids
    assert result.output.claims[0].evidence_ids == [VERBAL_APPROVAL_CHUNK]
    (tool_span,) = spans_of(session_factory, SpanKind.TOOL)
    assert tool_span.attributes[SpanAttribute.TOOL_NAME] == SEARCH_TOOL
    assert VERBAL_APPROVAL_CHUNK in tool_span.attributes[SpanAttribute.EVIDENCE_IDS]


def test_calls_past_the_limit_get_errors_and_tools_are_withdrawn(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
    pack_chunk: PackChunkFactory,
) -> None:
    request = claims_request(
        "Search twice.", tools=[fixed_evidence_tool([pack_chunk(CALL, CALL_TEXT)])]
    )
    good = claims_output(("Procurement asked for a 22% discount.", CALL))
    with_fixture(
        tmp_path,
        request,
        [tool_turn(search_call("c1", "discount"), search_call("c2", "renewal")), answer(good)],
    )
    spy = ProviderSpy(FakeLlmProvider(tmp_path))
    limited = settings.model_copy(update={"max_tool_calls": 1})

    result = build_client(spy, limited, session_factory, tracer).complete(request)

    first, second = spy.requests
    tool_results = second.conversation[-1]
    assert isinstance(tool_results, ToolResults)
    assert [r.is_error for r in tool_results.results] == [False, True]
    assert tool_results.results[1].content == CALL_LIMIT_MESSAGE
    assert (first.tools_enabled, second.tools_enabled) == (True, False)
    assert result.tool_calls == 1
    error_codes = [
        s.attributes.get(SpanAttribute.ERROR_CODE) for s in spans_of(session_factory, SpanKind.TOOL)
    ]
    assert error_codes == [None, ToolErrorCode.CALL_LIMIT_REACHED]


def test_tool_call_after_tools_are_withdrawn_fails(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
    pack_chunk: PackChunkFactory,
) -> None:
    request = claims_request(
        "Keep searching.", tools=[fixed_evidence_tool([pack_chunk(CALL, CALL_TEXT)])]
    )
    with_fixture(
        tmp_path,
        request,
        [tool_turn(search_call("c1", "discount")), tool_turn(search_call("c2", "renewal"))],
    )
    limited = settings.model_copy(update={"max_tool_calls": 1})

    client = build_client(FakeLlmProvider(tmp_path), limited, session_factory, tracer)
    with pytest.raises(ToolBudgetExceeded):
        client.complete(request)


def test_invalid_tool_arguments_go_back_to_the_model(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
    pack_chunk: PackChunkFactory,
) -> None:
    request = claims_request(
        "Search badly.", tools=[fixed_evidence_tool([pack_chunk(CALL, CALL_TEXT)])]
    )
    good = claims_output(("Procurement asked for a 22% discount.", CALL))
    with_fixture(tmp_path, request, [tool_turn(search_call("c1", "", k=99)), answer(good)])
    spy = ProviderSpy(FakeLlmProvider(tmp_path))

    build_client(spy, settings, session_factory, tracer).complete(request)

    tool_results = spy.requests[1].conversation[-1]
    assert isinstance(tool_results, ToolResults)
    (rejected,) = tool_results.results
    assert rejected.is_error
    assert "query" in rejected.content
    (tool_span,) = spans_of(session_factory, SpanKind.TOOL)
    assert tool_span.attributes[SpanAttribute.ERROR_CODE] == ToolErrorCode.INVALID_ARGUMENTS


HARNESS_FLAG = "no_evidence"
HARNESS_FLAGGED_OUTPUTS = [ConversationFindings, StakeholderMap, StrategyOutput]


@pytest.mark.parametrize("output_model", HARNESS_FLAGGED_OUTPUTS)
def test_harness_flag_is_absent_from_the_model_schema(output_model: type[StrictModel]) -> None:
    assert HARNESS_FLAG not in output_model.model_json_schema()["properties"]
    assert HARNESS_FLAG in output_model.model_fields


@pytest.mark.parametrize("output_model", HARNESS_FLAGGED_OUTPUTS)
def test_model_output_setting_the_harness_flag_is_rejected(
    output_model: type[StrictModel],
) -> None:
    harness_output = output_model.empty().model_dump(mode="json")  # type: ignore[attr-defined]

    with pytest.raises(SchemaValidationError, match=HARNESS_FLAG):
        parse_output(output_model, json.dumps(harness_output))
    assert output_model.model_validate(harness_output) == output_model.empty()  # type: ignore[attr-defined]


def test_strategy_output_claiming_no_evidence_fails_after_retries(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    tmp_path: Path,
) -> None:
    request = LlmRequest[StrategyOutput](
        agent_name=AgentName.NEGOTIATION_STRATEGY,
        prompt_version="v1",
        prompt_hash="p1",
        model_role=ModelRole.STRATEGY,
        system=SYSTEM,
        user_message="Plan the negotiation.",
        output_model=StrategyOutput,
        max_tokens=1024,
    )
    claimed = {"executive_summary": [], "negotiation_state": None, HARNESS_FLAG: True}
    with_fixture(tmp_path, request, [answer(claimed)] * 3)
    spy = ProviderSpy(FakeLlmProvider(tmp_path))

    with pytest.raises(SchemaValidationError, match=HARNESS_FLAG):
        build_client(spy, settings, session_factory, tracer).complete(request)
    feedback = spy.requests[1].conversation[-1]
    assert isinstance(feedback, UserText)
    assert HARNESS_FLAG in feedback.text


def test_no_evidence_text_reaches_any_span(
    settings: Settings,
    session_factory: sessionmaker[Session],
    tracer: PostgresTracer,
    ingested_session: Session,
) -> None:
    request = claims_request(
        VERBAL_APPROVAL_QUESTION, agent=TOOL_AGENT, tools=[scoped_search_tool(ingested_session)]
    )
    client = build_client(FakeLlmProvider(COMMITTED_FIXTURES), settings, session_factory, tracer)
    result = client.complete(request, evidence_check([], FINDING_CHECKS))
    chunk_texts = list(ingested_session.scalars(select(EvidenceChunkRow.text)))
    leaked = chunk_texts[0]
    with tracer.span(leaked, SpanKind.STAGE, {"text": leaked, SpanAttribute.ERROR_CODE: leaked}):
        pass

    with session_factory() as session:
        spans = list(session.scalars(select(TraceSpanRow)))
    stored = [
        text
        for span in spans
        for value in [span.name, *span.attributes.values()]
        for text in (value if isinstance(value, list) else [value])
        if isinstance(text, str)
    ]
    assert result.tool_calls == 1
    assert all(re.fullmatch(ATTRIBUTE_VALUE_PATTERN, text) for text in stored)
    assert not any(chunk in text for chunk in chunk_texts for text in stored)
