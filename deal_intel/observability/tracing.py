"""Spans without the OpenTelemetry SDK: a `Tracer` protocol, a no-op tracer, and a Postgres one.

Both tracers keep the current span in a context variable, so logs carry `run_id` and `span_id`
whichever is configured, and child spans find their parent without it being passed around.
"""

import logging
import re
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.contracts.tracing import (
    ATTRIBUTE_VALUE_PATTERN,
    MAX_ATTRIBUTE_CHARS,
    AttributeValue,
    SpanAttribute,
    SpanKind,
    SpanStatus,
    TraceSpanRecord,
)
from deal_intel.db.models.tracing import TraceSpanRow
from deal_intel.db.writes import column_values
from deal_intel.observability.secrets import contains_secret

logger = logging.getLogger(__name__)

ATTRIBUTE_VALUE = re.compile(ATTRIBUTE_VALUE_PATTERN)
WHITELISTED_KEYS = frozenset(attribute.value for attribute in SpanAttribute)

type SpanWrite = Callable[[Session, "OpenSpan"], None]


@dataclass(frozen=True)
class SpanContext:
    span_id: str
    run_id: str | None


CURRENT_SPAN: ContextVar[SpanContext | None] = ContextVar("current_span", default=None)


def current_span() -> SpanContext | None:
    return CURRENT_SPAN.get()


class SpanHandle(Protocol):
    @property
    def span_id(self) -> str: ...

    def set_attributes(self, attributes: Mapping[str, object]) -> None: ...

    def record_error(self, error_code: str) -> None: ...


class Tracer(Protocol):
    def span(
        self, name: str, kind: SpanKind, attributes: Mapping[str, object] | None = None
    ) -> AbstractContextManager[SpanHandle]: ...


def safe_token(text: str) -> str | None:
    if not ATTRIBUTE_VALUE.match(text) or contains_secret(text):
        return None
    return text[:MAX_ATTRIBUTE_CHARS]


def safe_value(value: object) -> AttributeValue | None:
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return safe_token(value)
    if isinstance(value, list | tuple):
        tokens = [safe_token(item) if isinstance(item, str) else None for item in value]
        # One unsafe item drops the whole list rather than storing a misleading subset.
        return None if None in tokens else [token for token in tokens if token is not None]
    return None


def sanitize_attributes(attributes: Mapping[str, object]) -> dict[SpanAttribute, AttributeValue]:
    """Keeps whitelisted keys whose values are numbers, booleans, or short tokens."""
    clean: dict[SpanAttribute, AttributeValue] = {}
    for key, value in attributes.items():
        safe = safe_value(value) if key in WHITELISTED_KEYS else None
        if safe is not None:
            clean[SpanAttribute(key)] = safe
    return clean


def error_code_of(error: BaseException) -> str:
    return str(getattr(error, "error_code", type(error).__name__))


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class OpenSpan:
    span_id: str
    parent_span_id: str | None
    run_id: str | None
    kind: SpanKind
    name: str
    started_at: datetime
    attributes: dict[SpanAttribute, AttributeValue] = field(default_factory=dict)
    status: SpanStatus = SpanStatus.RUNNING
    ended_at: datetime | None = None

    def set_attributes(self, attributes: Mapping[str, object]) -> None:
        self.attributes.update(sanitize_attributes(attributes))

    def record_error(self, error_code: str) -> None:
        self.status = SpanStatus.ERROR
        self.set_attributes({SpanAttribute.ERROR_CODE: error_code})

    def finish(self) -> None:
        self.ended_at = utc_now()
        if self.status is SpanStatus.RUNNING:
            self.status = SpanStatus.OK

    def record(self) -> TraceSpanRecord:
        return TraceSpanRecord(
            span_id=self.span_id,
            parent_span_id=self.parent_span_id,
            run_id=self.run_id,
            kind=self.kind,
            name=self.name,
            started_at=self.started_at,
            ended_at=self.ended_at,
            status=self.status,
            attributes=self.attributes,
        )


def start_span(
    name: str, kind: SpanKind, attributes: Mapping[str, object], parent: SpanContext | None
) -> OpenSpan:
    clean = sanitize_attributes(attributes)
    own_run_id = clean.get(SpanAttribute.RUN_ID)
    inherited_run_id = parent.run_id if parent else None
    return OpenSpan(
        span_id=uuid.uuid4().hex,
        parent_span_id=parent.span_id if parent else None,
        run_id=own_run_id if isinstance(own_run_id, str) else inherited_run_id,
        kind=kind,
        name=name if ATTRIBUTE_VALUE.match(name) else kind.value,
        started_at=utc_now(),
        attributes=clean,
    )


class BaseTracer:
    """Owns the span lifecycle; subclasses decide where started and finished spans go."""

    def span(
        self, name: str, kind: SpanKind, attributes: Mapping[str, object] | None = None
    ) -> AbstractContextManager[SpanHandle]:
        return self._scope(name, kind, attributes or {})

    @contextmanager
    def _scope(
        self, name: str, kind: SpanKind, attributes: Mapping[str, object]
    ) -> Iterator[SpanHandle]:
        span = start_span(name, kind, attributes, current_span())
        self._on_start(span)
        token = CURRENT_SPAN.set(SpanContext(span_id=span.span_id, run_id=span.run_id))
        try:
            yield span
        except BaseException as error:
            if span.status is not SpanStatus.ERROR:
                span.record_error(error_code_of(error))
            raise
        finally:
            CURRENT_SPAN.reset(token)
            span.finish()
            self._on_end(span)

    def _on_start(self, span: OpenSpan) -> None:
        pass

    def _on_end(self, span: OpenSpan) -> None:
        pass


class NoopTracer(BaseTracer):
    """Stores nothing; still sets the span context so log lines stay correlated."""


class PostgresTracer(BaseTracer):
    """Writes `trace_spans`: a row on start so children can reference it, an update on end.

    Each write uses its own short session, so spans persist even when the traced work rolls back.
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._unstored_span_ids: set[str] = set()

    def _on_start(self, span: OpenSpan) -> None:
        if not self._write(span, insert_span):
            self._unstored_span_ids.add(span.span_id)

    def _on_end(self, span: OpenSpan) -> None:
        # A span whose insert failed has no row to update; skipping keeps it to one log line.
        if span.span_id in self._unstored_span_ids:
            self._unstored_span_ids.discard(span.span_id)
            return
        self._write(span, finish_span)

    def _write(self, span: OpenSpan, write: SpanWrite) -> bool:
        try:
            with self._session_factory.begin() as session:
                write(session, span)
            return True
        except Exception as error:
            # Tracing must never fail the traced work, so a failed write is dropped here.
            logger.warning(
                "span write failed",
                extra={
                    "span_kind": span.kind.value,
                    "failed_span_id": span.span_id,
                    "error_type": type(error).__name__,
                },
            )
            return False


def insert_span(session: Session, span: OpenSpan) -> None:
    session.add(TraceSpanRow(**column_values(span.record())))


def finish_span(session: Session, span: OpenSpan) -> None:
    session.execute(
        update(TraceSpanRow)
        .where(TraceSpanRow.span_id == span.span_id)
        .values(ended_at=span.ended_at, status=span.status.value, attributes=span.attributes)
    )
