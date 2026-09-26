import json
import logging
from datetime import UTC, datetime

from deal_intel.observability.secrets import contains_secret
from deal_intel.observability.tracing import current_span

REDACTED_RECORD_MESSAGE = "log record dropped: secret pattern"
RUN_ID_FIELD = "run_id"
SPAN_ID_FIELD = "span_id"
# Every attribute a bare LogRecord carries; anything else on a record came from `extra=`.
STANDARD_RECORD_FIELDS = frozenset(
    vars(logging.LogRecord("", logging.INFO, "", 0, "", None, None))
) | {"message", "asctime", RUN_ID_FIELD, SPAN_ID_FIELD}


def extra_fields(record: logging.LogRecord) -> dict[str, object]:
    return {key: value for key, value in vars(record).items() if key not in STANDARD_RECORD_FIELDS}


class CorrelationFilter(logging.Filter):
    """Stamps the current span's ids on the record in the thread that logged it."""

    def filter(self, record: logging.LogRecord) -> bool:
        span = current_span()
        setattr(record, RUN_ID_FIELD, span.run_id if span else None)
        setattr(record, SPAN_ID_FIELD, span.span_id if span else None)
        return True


class SecretFilter(logging.Filter):
    """Replaces any record that mentions a secret with a fixed warning, keeping the event visible
    without its content."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record_mentions_secret(record):
            redact_record(record)
        return True


def record_mentions_secret(record: logging.LogRecord) -> bool:
    texts = [record.getMessage(), *(str(value) for value in extra_fields(record).values())]
    if record.exc_info:
        texts.append(logging.Formatter().formatException(record.exc_info))
    return any(contains_secret(text) for text in texts)


def redact_record(record: logging.LogRecord) -> None:
    for key in extra_fields(record):
        delattr(record, key)
    record.msg = REDACTED_RECORD_MESSAGE
    record.args = None
    record.exc_info = None
    record.exc_text = None
    record.levelno = logging.WARNING
    record.levelname = logging.getLevelName(logging.WARNING)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            RUN_ID_FIELD: getattr(record, RUN_ID_FIELD, None),
            SPAN_ID_FIELD: getattr(record, SPAN_ID_FIELD, None),
            **extra_fields(record),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def build_json_handler() -> logging.Handler:
    handler = logging.StreamHandler()
    handler.addFilter(SecretFilter())
    handler.addFilter(CorrelationFilter())
    handler.setFormatter(JsonFormatter())
    return handler


def configure_logging(level: str) -> None:
    """Idempotent: adds the JSON handler to the root logger once, leaving other handlers alone."""
    root = logging.getLogger()
    root.setLevel(level)
    if not any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        root.addHandler(build_json_handler())
