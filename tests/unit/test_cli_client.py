import json
import subprocess
import sys

import httpx
import pytest
import typer

from deal_intel.api.main import create_app
from deal_intel.api.schemas import ErrorBody, ErrorCode, HealthResponse, HealthStatus
from deal_intel.cli_client import (
    ApiClient,
    ApiError,
    ApiUnreachable,
    InProcessTransport,
    get_api_client,
)
from deal_intel.cli_output import (
    CliError,
    ExitCode,
    describe_error,
    exit_code_for,
    exit_with_error,
    print_result,
)

TEST_BASE_URL = "http://testserver"
TEST_TIMEOUT_SECONDS = 5.0
FORBIDDEN_MODULE_PREFIXES = ("deal_intel.db", "deal_intel.orchestration", "sqlalchemy")


def in_process_client() -> ApiClient:
    return ApiClient(TEST_BASE_URL, TEST_TIMEOUT_SECONDS, InProcessTransport(create_app()))


def client_with_handler(transport: httpx.MockTransport) -> ApiClient:
    return ApiClient(TEST_BASE_URL, TEST_TIMEOUT_SECONDS, transport)


def refuse_connection(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("connection refused", request=request)


def api_error(status_code: int) -> ApiError:
    return ApiError(status_code, None)


def test_health_through_the_in_process_transport() -> None:
    with in_process_client() as client:
        assert client.health() == HealthResponse(status=HealthStatus.OK)


def test_get_api_client_accepts_an_injected_transport() -> None:
    with get_api_client(InProcessTransport(create_app())) as client:
        assert client.health().status is HealthStatus.OK


def test_error_status_raises_api_error_with_the_error_body() -> None:
    with in_process_client() as client, pytest.raises(ApiError) as raised:
        client.get("/does-not-exist", HealthResponse)

    assert raised.value.status_code == 404
    assert raised.value.body is not None
    assert raised.value.body.code is ErrorCode.NOT_FOUND


def test_error_without_the_contract_body_keeps_the_status() -> None:
    proxy_error = httpx.MockTransport(lambda request: httpx.Response(502, text="Bad Gateway"))

    with client_with_handler(proxy_error) as client, pytest.raises(ApiError) as raised:
        client.health()

    assert raised.value.status_code == 502
    assert raised.value.body is None


def test_transport_failure_raises_api_unreachable() -> None:
    with (
        client_with_handler(httpx.MockTransport(refuse_connection)) as client,
        pytest.raises(ApiUnreachable) as raised,
    ):
        client.health()

    assert raised.value.base_url == TEST_BASE_URL


def test_exit_codes_match_the_plan() -> None:
    assert {code.name: code.value for code in ExitCode} == {
        "OK": 0,
        "INVALID_ARGUMENTS": 2,
        "DENIED_OR_NOT_FOUND": 3,
        "RUN_FAILED": 4,
        "API_UNREACHABLE": 5,
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (api_error(400), ExitCode.INVALID_ARGUMENTS),
        (api_error(413), ExitCode.INVALID_ARGUMENTS),
        (api_error(422), ExitCode.INVALID_ARGUMENTS),
        (api_error(403), ExitCode.DENIED_OR_NOT_FOUND),
        (api_error(404), ExitCode.DENIED_OR_NOT_FOUND),
        (api_error(409), ExitCode.RUN_FAILED),
        (api_error(500), ExitCode.RUN_FAILED),
        (ApiUnreachable(TEST_BASE_URL), ExitCode.API_UNREACHABLE),
    ],
)
def test_exit_code_for_each_error(error: CliError, expected: ExitCode) -> None:
    assert exit_code_for(error) is expected


def test_exit_with_error_prints_to_stderr_and_exits(capsys: pytest.CaptureFixture[str]) -> None:
    body = ErrorBody(code=ErrorCode.NOT_FOUND, message="Not found.", request_id="req-00000001")

    with pytest.raises(typer.Exit) as raised:
        exit_with_error(ApiError(404, body))

    assert raised.value.exit_code == ExitCode.DENIED_OR_NOT_FOUND
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.strip() == describe_error(ApiError(404, body))
    assert "req-00000001" in captured.err


def test_print_result_json_prints_only_the_model(capsys: pytest.CaptureFixture[str]) -> None:
    health = HealthResponse(status=HealthStatus.OK)

    print_result(health, as_json=True, describe=lambda model: [f"status {model.status}"])

    assert HealthResponse.model_validate(json.loads(capsys.readouterr().out)) == health


def test_print_result_human_uses_the_description(capsys: pytest.CaptureFixture[str]) -> None:
    health = HealthResponse(status=HealthStatus.OK)

    print_result(health, as_json=False, describe=lambda model: [f"status {model.status}"])

    assert capsys.readouterr().out == "status ok\n"


def test_cli_client_modules_never_import_database_or_orchestration() -> None:
    probe = (
        "import json, sys\n"
        "import deal_intel.cli_client, deal_intel.cli_output\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )

    result = subprocess.run(  # noqa: S603  fixed interpreter and argument list
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    loaded = json.loads(result.stdout)
    assert [name for name in loaded if name.startswith(FORBIDDEN_MODULE_PREFIXES)] == []
