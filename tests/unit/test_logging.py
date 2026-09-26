import io
import json
import logging
from collections.abc import Iterator

import pytest

from deal_intel.contracts.tracing import SpanAttribute, SpanKind
from deal_intel.observability.logging import (
    REDACTED_RECORD_MESSAGE,
    JsonFormatter,
    build_json_handler,
    configure_logging,
)
from deal_intel.observability.tracing import NoopTracer

API_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz"
RUN_ID = "run-7"


@pytest.fixture
def json_log() -> Iterator[tuple[logging.Logger, io.StringIO]]:
    stream = io.StringIO()
    handler = build_json_handler()
    handler.setStream(stream)  # type: ignore[attr-defined]
    logger = logging.getLogger("tests.json_log")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    yield logger, stream
    logger.removeHandler(handler)


def entries(stream: io.StringIO) -> list[dict[str, object]]:
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_lines_inside_a_span_carry_its_run_and_span_ids(
    json_log: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = json_log

    with NoopTracer().span("stage.plan", SpanKind.STAGE, {SpanAttribute.RUN_ID: RUN_ID}) as span:
        logger.info("pack built", extra={"chunk_count": 3})
    logger.info("after the run")

    inside, outside = entries(stream)
    assert inside["message"] == "pack built"
    assert inside["chunk_count"] == 3
    assert (inside["run_id"], inside["span_id"]) == (RUN_ID, span.span_id)
    assert (outside["run_id"], outside["span_id"]) == (None, None)


@pytest.mark.parametrize(
    ("message", "extra"),
    [
        (f"calling with {API_KEY}", {}),
        ("calling provider", {"header": f"x-api-key: {API_KEY}"}),
        ("connecting", {"url": "postgresql://deal:hunter2secret@localhost/deal_intel"}),
    ],
)
def test_records_mentioning_a_secret_become_a_fixed_warning(
    json_log: tuple[logging.Logger, io.StringIO], message: str, extra: dict[str, str]
) -> None:
    logger, stream = json_log

    logger.info(message, extra=extra)

    (entry,) = entries(stream)
    assert entry["message"] == REDACTED_RECORD_MESSAGE
    assert entry["level"] == logging.getLevelName(logging.WARNING)
    assert API_KEY not in stream.getvalue()
    assert "hunter2secret" not in stream.getvalue()


def test_exception_text_with_a_secret_is_dropped(
    json_log: tuple[logging.Logger, io.StringIO],
) -> None:
    logger, stream = json_log

    try:
        raise ValueError(f"bad key {API_KEY}")
    except ValueError:
        logger.exception("provider failed")

    (entry,) = entries(stream)
    assert entry["message"] == REDACTED_RECORD_MESSAGE
    assert "exception" not in entry


def test_configure_logging_installs_one_json_handler() -> None:
    root = logging.getLogger()
    before = list(root.handlers)
    level = root.level

    configure_logging("INFO")
    configure_logging("INFO")

    added = [handler for handler in root.handlers if handler not in before]
    json_handlers = [h for h in root.handlers if isinstance(h.formatter, JsonFormatter)]
    for handler in added:
        root.removeHandler(handler)
    root.setLevel(level)
    assert len(json_handlers) == 1
