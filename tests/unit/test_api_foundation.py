import logging
from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deal_intel.api.errors import NotFound
from deal_intel.api.main import create_app
from deal_intel.api.middleware import SECURITY_HEADERS
from deal_intel.api.request_context import (
    REQUEST_ID_FIELD,
    REQUEST_ID_HEADER,
    WELL_FORMED_REQUEST_ID,
    RequestIdLogFilter,
    current_request_id,
)
from deal_intel.api.schemas import ErrorCode, ErrorResponse
from deal_intel.config import get_settings
from deal_intel.contracts.base import StrictModel

SUPPLIED_REQUEST_ID = "test-request-0001"
INTERNAL_DETAIL = "internal detail that must stay server-side"
PROBE_LOGGER = "probe"
PROBE_LOG_MESSAGE = "probe line"
MIDDLEWARE_LOGGER = "deal_intel.api.middleware"


class ProbeBody(StrictModel):
    text: str


def build_probe_app() -> FastAPI:
    app = create_app()

    @app.get("/probe/request-id")
    def echo_request_id() -> dict[str, str]:
        return {"request_id": current_request_id()}

    @app.get("/probe/log")
    def log_line() -> None:
        logging.getLogger(PROBE_LOGGER).warning(PROBE_LOG_MESSAGE)

    @app.get("/probe/boom")
    def boom() -> None:
        raise RuntimeError(INTERNAL_DETAIL)

    @app.get("/probe/missing")
    def missing() -> None:
        raise NotFound()

    @app.post("/probe/body")
    def body_length(body: ProbeBody) -> dict[str, int]:
        return {"length": len(body.text)}

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(build_probe_app())


@pytest.fixture
def request_id_caplog(caplog: pytest.LogCaptureFixture) -> Iterator[pytest.LogCaptureFixture]:
    """caplog with the request id filter the app lifespan installs on the JSON handler."""
    log_filter = RequestIdLogFilter()
    caplog.handler.addFilter(log_filter)
    yield caplog
    caplog.handler.removeFilter(log_filter)


def error_of(response_json: object) -> ErrorResponse:
    return ErrorResponse.model_validate(response_json)


def test_generates_a_request_id_when_none_is_sent(client: TestClient) -> None:
    response = client.get("/probe/request-id")

    generated = response.headers[REQUEST_ID_HEADER]
    assert WELL_FORMED_REQUEST_ID.match(generated)
    assert response.json() == {"request_id": generated}


def test_echoes_a_well_formed_request_id(client: TestClient) -> None:
    response = client.get("/probe/request-id", headers={REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID})

    assert response.headers[REQUEST_ID_HEADER] == SUPPLIED_REQUEST_ID
    assert response.json() == {"request_id": SUPPLIED_REQUEST_ID}


@pytest.mark.parametrize("malformed", ["short", "has spaces in it", "<script>x</script>", "a" * 65])
def test_replaces_a_malformed_request_id(client: TestClient, malformed: str) -> None:
    response = client.get("/probe/request-id", headers={REQUEST_ID_HEADER: malformed})

    replacement = response.headers[REQUEST_ID_HEADER]
    assert replacement != malformed
    assert WELL_FORMED_REQUEST_ID.match(replacement)


def test_binds_the_request_id_into_log_records(
    client: TestClient, request_id_caplog: pytest.LogCaptureFixture
) -> None:
    client.get("/probe/log", headers={REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID})

    records = [r for r in request_id_caplog.records if r.getMessage() == PROBE_LOG_MESSAGE]
    assert [getattr(record, REQUEST_ID_FIELD) for record in records] == [SUPPLIED_REQUEST_ID]


def test_unknown_route_returns_the_error_body(client: TestClient) -> None:
    response = client.get("/does-not-exist", headers={REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID})

    assert response.status_code == 404
    error = error_of(response.json()).error
    assert error.code is ErrorCode.NOT_FOUND
    assert error.request_id == SUPPLIED_REQUEST_ID


def test_not_found_helper_is_indistinguishable_from_an_unknown_route(client: TestClient) -> None:
    headers = {REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID}

    raised = client.get("/probe/missing", headers=headers)
    unknown = client.get("/does-not-exist", headers=headers)

    assert raised.status_code == unknown.status_code == 404
    assert raised.content == unknown.content


def test_validation_error_names_fields_without_echoing_values(client: TestClient) -> None:
    submitted = "SUBMITTED-VALUE-SENTINEL"

    response = client.post("/probe/body", json={"text": [submitted]})

    assert response.status_code == 422
    error = error_of(response.json()).error
    assert error.code is ErrorCode.INVALID_INPUT
    assert "body.text" in error.message
    assert submitted not in response.text


def test_unhandled_error_returns_a_generic_500_and_logs_once(
    client: TestClient, request_id_caplog: pytest.LogCaptureFixture
) -> None:
    response = client.get("/probe/boom", headers={REQUEST_ID_HEADER: SUPPLIED_REQUEST_ID})

    assert response.status_code == 500
    error = error_of(response.json()).error
    assert error.code is ErrorCode.INTERNAL_ERROR
    assert error.request_id == SUPPLIED_REQUEST_ID
    for leaked in ("Traceback", "RuntimeError", INTERNAL_DETAIL, "/deal_intel/"):
        assert leaked not in response.text
    assert response.headers[REQUEST_ID_HEADER] == SUPPLIED_REQUEST_ID
    logged = [r for r in request_id_caplog.records if r.name == MIDDLEWARE_LOGGER]
    assert len(logged) == 1
    assert logged[0].exc_info is not None
    assert getattr(logged[0], REQUEST_ID_FIELD) == SUPPLIED_REQUEST_ID


def test_body_within_the_limit_reaches_the_route(client: TestClient) -> None:
    response = client.post("/probe/body", json={"text": "x" * 100})

    assert response.status_code == 200
    assert response.json() == {"length": 100}


def test_body_over_the_declared_limit_is_rejected(client: TestClient) -> None:
    oversized = "x" * get_settings().api_max_request_body_bytes

    response = client.post("/probe/body", json={"text": oversized})

    assert response.status_code == 413
    assert error_of(response.json()).error.code is ErrorCode.PAYLOAD_TOO_LARGE


def test_body_without_a_length_is_capped(client: TestClient) -> None:
    limit = get_settings().api_max_request_body_bytes
    chunk = b"x" * 1024
    chunks = iter([chunk] * (limit // len(chunk) + 1))

    response = client.post("/probe/body", content=chunks)

    assert "content-length" not in response.request.headers
    assert response.status_code == 413
    assert error_of(response.json()).error.code is ErrorCode.PAYLOAD_TOO_LARGE


@pytest.mark.parametrize(
    ("path", "content_type"),
    [
        ("/healthz", "application/json"),
        ("/does-not-exist", "application/json"),
        ("/probe/boom", "application/json"),
        ("/ui/does-not-exist", "text/html"),
    ],
)
def test_security_headers_on_every_response(
    client: TestClient, path: str, content_type: str
) -> None:
    response = client.get(path)

    assert response.headers["content-type"].startswith(content_type)
    for name, value in SECURITY_HEADERS.items():
        assert response.headers[name] == value


def test_content_security_policy_is_strict(client: TestClient) -> None:
    policy = client.get("/healthz").headers["Content-Security-Policy"]

    directives = dict(directive.split(" ", 1) for directive in policy.split("; "))
    assert directives["script-src"] == "'self'"
    assert directives["frame-ancestors"] == "'none'"
    assert "unsafe-inline" not in policy
    assert "unsafe-eval" not in policy
