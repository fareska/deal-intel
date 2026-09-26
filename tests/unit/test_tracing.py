import logging

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.contracts.tracing import (
    MAX_ATTRIBUTE_CHARS,
    SpanAttribute,
    SpanKind,
    SpanStatus,
    TraceSpanRecord,
)
from deal_intel.db.models import TraceSpanRow
from deal_intel.llm.errors import LlmErrorCode, ModelRefusal
from deal_intel.observability.tracing import (
    NoopTracer,
    PostgresTracer,
    current_span,
    sanitize_attributes,
)

RUN_ID = "run-1"
# Nothing listens on port 1, so every connection is refused at once.
UNREACHABLE_DATABASE_URL = "postgresql+psycopg://nobody@127.0.0.1:1/none"
SPAN_WRITE_FAILED = "span write failed"


def stored_spans(session_factory: sessionmaker[Session]) -> list[TraceSpanRecord]:
    with session_factory() as session:
        rows = session.scalars(select(TraceSpanRow).order_by(TraceSpanRow.started_at))
        return [TraceSpanRecord.model_validate(row, from_attributes=True) for row in rows]


def stored_span(session_factory: sessionmaker[Session], span_id: str) -> TraceSpanRecord:
    return next(span for span in stored_spans(session_factory) if span.span_id == span_id)


def test_span_row_is_inserted_on_start_and_finished_on_end(
    session_factory: sessionmaker[Session],
) -> None:
    tracer = PostgresTracer(session_factory)

    with tracer.span("stage.retrieve", SpanKind.STAGE, {SpanAttribute.RUN_ID: RUN_ID}) as span:
        running = stored_span(session_factory, span.span_id)
        span.set_attributes({SpanAttribute.TOOL_NAME: "search_evidence"})
    finished = stored_span(session_factory, span.span_id)

    assert running.status is SpanStatus.RUNNING
    assert running.ended_at is None
    assert finished.status is SpanStatus.OK
    assert finished.ended_at is not None
    assert finished.ended_at >= finished.started_at
    assert finished.attributes[SpanAttribute.TOOL_NAME] == "search_evidence"


def test_child_spans_link_their_parent_and_inherit_its_run_id(
    session_factory: sessionmaker[Session],
) -> None:
    tracer = PostgresTracer(session_factory)

    with tracer.span("run", SpanKind.RUN, {SpanAttribute.RUN_ID: RUN_ID}) as parent:
        with tracer.span("stage.plan", SpanKind.STAGE) as child:
            assert current_span() is not None
            assert current_span().span_id == child.span_id  # type: ignore[union-attr]
    assert current_span() is None

    stored = stored_span(session_factory, child.span_id)
    assert stored.parent_span_id == parent.span_id
    assert stored.run_id == RUN_ID


def test_failed_work_marks_the_span_as_an_error_and_still_raises(
    session_factory: sessionmaker[Session],
) -> None:
    tracer = PostgresTracer(session_factory)

    with pytest.raises(ModelRefusal):
        with tracer.span("agent", SpanKind.AGENT_CALL) as span:
            raise ModelRefusal("refused")

    stored = stored_span(session_factory, span.span_id)
    assert stored.status is SpanStatus.ERROR
    assert stored.attributes[SpanAttribute.ERROR_CODE] == LlmErrorCode.MODEL_REFUSAL


def test_span_write_failure_never_fails_the_work_and_logs_one_line(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = create_engine(UNREACHABLE_DATABASE_URL)
    tracer = PostgresTracer(sessionmaker(bind=engine))

    with caplog.at_level(logging.WARNING):
        with tracer.span("stage.plan", SpanKind.STAGE) as span:
            outcome = "done"
    engine.dispose()

    failures = [record for record in caplog.records if record.getMessage() == SPAN_WRITE_FAILED]
    assert outcome == "done"
    assert len(failures) == 1
    assert failures[0].failed_span_id == span.span_id  # type: ignore[attr-defined]


def test_only_whitelisted_keys_with_token_values_are_kept() -> None:
    chunk_text = "Legal approval requires a signed DPA before any discount is final."
    clean = sanitize_attributes(
        {
            "text": chunk_text,
            SpanAttribute.ERROR_CODE: chunk_text,
            SpanAttribute.MODEL: "sk-ant-api03-abcdefghijklmnop",
            SpanAttribute.EVIDENCE_IDS: ["slack:SLK-1003-02", "not a token"],
            SpanAttribute.TOOL_NAME: "search_evidence",
            SpanAttribute.INPUT_TOKENS: 1200,
            SpanAttribute.CACHED: False,
        }
    )

    assert clean == {
        SpanAttribute.TOOL_NAME: "search_evidence",
        SpanAttribute.INPUT_TOKENS: 1200,
        SpanAttribute.CACHED: False,
    }


def test_long_token_values_are_capped() -> None:
    clean = sanitize_attributes({SpanAttribute.INPUT_HASH: "a" * (MAX_ATTRIBUTE_CHARS + 100)})

    assert clean[SpanAttribute.INPUT_HASH] == "a" * MAX_ATTRIBUTE_CHARS


def test_span_name_that_is_not_a_token_falls_back_to_the_kind(
    session_factory: sessionmaker[Session],
) -> None:
    with PostgresTracer(session_factory).span("free text name", SpanKind.TOOL) as span:
        pass

    assert stored_span(session_factory, span.span_id).name == SpanKind.TOOL.value


def test_noop_tracer_sets_the_span_context_and_stores_nothing(
    session_factory: sessionmaker[Session],
) -> None:
    with NoopTracer().span("run", SpanKind.RUN, {SpanAttribute.RUN_ID: RUN_ID}) as span:
        context = current_span()

    assert context is not None
    assert (context.span_id, context.run_id) == (span.span_id, RUN_ID)
    assert stored_spans(session_factory) == []
