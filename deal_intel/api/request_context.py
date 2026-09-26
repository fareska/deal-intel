"""The per-request id: validated or generated at the edge, held in a context variable, and stamped
on every log record emitted while the request is handled."""

import logging
import re
import uuid
from contextvars import ContextVar, Token

from deal_intel.observability.logging import JsonFormatter

REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_FIELD = "request_id"
# Caller-supplied ids end up in logs and response headers, so only short plain tokens are kept.
WELL_FORMED_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")

CURRENT_REQUEST_ID: ContextVar[str] = ContextVar("current_request_id")


def resolve_request_id(incoming: str | None) -> str:
    if incoming is not None and WELL_FORMED_REQUEST_ID.match(incoming):
        return incoming
    return str(uuid.uuid4())


def bind_request_id(request_id: str) -> Token[str]:
    return CURRENT_REQUEST_ID.set(request_id)


def unbind_request_id(token: Token[str]) -> None:
    CURRENT_REQUEST_ID.reset(token)


def current_request_id() -> str:
    """Only valid while a request is being handled; raises `LookupError` outside one."""
    return CURRENT_REQUEST_ID.get()


class RequestIdLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        setattr(record, REQUEST_ID_FIELD, CURRENT_REQUEST_ID.get(None))
        return True


def install_request_id_log_filter() -> None:
    """Idempotent: adds the filter to the root JSON handlers that `configure_logging` installed."""
    for handler in logging.getLogger().handlers:
        already_installed = any(isinstance(f, RequestIdLogFilter) for f in handler.filters)
        if isinstance(handler.formatter, JsonFormatter) and not already_installed:
            handler.addFilter(RequestIdLogFilter())
