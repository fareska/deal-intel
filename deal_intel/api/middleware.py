"""Pure ASGI middleware for the request id, security headers, body size, and unhandled errors.

Pure ASGI rather than `BaseHTTPMiddleware`, so the request id context variable set here is the one
every handler, template, and log call below sees, and error responses can be sent directly.
"""

import logging
from collections.abc import Mapping

from fastapi import Request
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from deal_intel.api.errors import internal_error_response, payload_too_large_response
from deal_intel.api.request_context import (
    REQUEST_ID_HEADER,
    bind_request_id,
    resolve_request_id,
    unbind_request_id,
)

logger = logging.getLogger(__name__)

HTTP_SCOPE = "http"
RESPONSE_START = "http.response.start"
REQUEST_BODY = "http.request"
CONTENT_LENGTH_HEADER = "content-length"

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


class HttpMiddleware:
    """Passes non-HTTP scopes (lifespan, websockets) straight through to the app."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != HTTP_SCOPE:
            await self.app(scope, receive, send)
            return
        await self.handle(scope, receive, send)

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        raise NotImplementedError


def with_response_headers(send: Send, headers: Mapping[str, str]) -> Send:
    async def send_with_headers(message: Message) -> None:
        if message["type"] == RESPONSE_START:
            response_headers = MutableHeaders(scope=message)
            for name, value in headers.items():
                response_headers[name] = value
        await send(message)

    return send_with_headers


class RequestIdMiddleware(HttpMiddleware):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        request_id = resolve_request_id(Headers(scope=scope).get(REQUEST_ID_HEADER))
        send_with_id = with_response_headers(send, {REQUEST_ID_HEADER: request_id})
        token = bind_request_id(request_id)
        try:
            await self.app(scope, receive, send_with_id)
        finally:
            unbind_request_id(token)


class SecurityHeadersMiddleware(HttpMiddleware):
    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        await self.app(scope, receive, with_response_headers(send, SECURITY_HEADERS))


def declared_content_length(scope: Scope) -> int | None:
    value = Headers(scope=scope).get(CONTENT_LENGTH_HEADER)
    return int(value) if value is not None and value.isdigit() else None


async def read_body_within(receive: Receive, max_bytes: int) -> bytes | None:
    """The whole request body, or None as soon as it exceeds `max_bytes`."""
    chunks: list[bytes] = []
    size = 0
    while True:
        message = await receive()
        if message["type"] != REQUEST_BODY:
            return b"".join(chunks)
        chunk = message.get("body", b"")
        size += len(chunk)
        if size > max_bytes:
            return None
        chunks.append(chunk)
        if not message.get("more_body", False):
            return b"".join(chunks)


def replay_body(body: bytes, receive: Receive) -> Receive:
    replayed = False

    async def receive_replayed() -> Message:
        nonlocal replayed
        if replayed:
            return await receive()
        replayed = True
        return {"type": REQUEST_BODY, "body": body, "more_body": False}

    return receive_replayed


class BodySizeLimitMiddleware(HttpMiddleware):
    """Buffers the body up to the limit before the app runs, so the 413 never depends on how or
    whether a route reads its body, and bodies without a Content-Length are capped too."""

    def __init__(self, app: ASGIApp, max_body_bytes: int) -> None:
        super().__init__(app)
        self.max_body_bytes = max_body_bytes

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        declared = declared_content_length(scope)
        body = None
        if declared is None or declared <= self.max_body_bytes:
            body = await read_body_within(receive, self.max_body_bytes)
        if body is None:
            await payload_too_large_response(Request(scope))(scope, receive, send)
            return
        await self.app(scope, replay_body(body, receive), send)


class UnhandledErrorMiddleware(HttpMiddleware):
    """The owning boundary for unexpected exceptions: logs each once with the request id and
    answers with the generic 500 body. Must sit inside the request id and header middleware."""

    async def handle(self, scope: Scope, receive: Receive, send: Send) -> None:
        response_started = False

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            response_started = response_started or message["type"] == RESPONSE_START
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        except Exception:
            if response_started:
                raise
            logger.exception("unhandled error on %s %s", scope["method"], scope["path"])
            await internal_error_response(Request(scope))(scope, receive, send)
