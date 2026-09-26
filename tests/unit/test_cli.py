import json
import os
import subprocess
import sys

from typer.testing import CliRunner

from deal_intel.cli import app
from deal_intel.cli_client import ApiClient, InProcessTransport
from deal_intel.cli_output import ExitCode
from deal_intel.contracts.access import DENIED_MESSAGE
from deal_intel.contracts.api import RunStatusResponse
from deal_intel.contracts.brief import SECTION_HEADINGS
from tests.unit.api_harness import (
    DEAL_DESK,
    NARROW_READER,
    OPP_1001,
    OPP_1003,
    OUTSIDER,
    REQUESTER_1001,
    REQUESTER_1003,
)
from tests.unit.test_cli_client import FORBIDDEN_MODULE_PREFIXES

runner = CliRunner()
TEST_BASE_URL = "http://testserver"
TEST_TIMEOUT_SECONDS = 5.0


def bind_in_process_client(monkeypatch, api_app) -> None:
    monkeypatch.setattr("deal_intel.cli.POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(
        "deal_intel.cli.get_api_client",
        lambda transport=None: ApiClient(
            TEST_BASE_URL, TEST_TIMEOUT_SECONDS, InProcessTransport(api_app)
        ),
    )


def test_generate_wait_prints_nine_headings(api_app, monkeypatch) -> None:
    bind_in_process_client(monkeypatch, api_app)

    result = runner.invoke(app, ["generate", "--opp", OPP_1001, "--user", REQUESTER_1001, "--wait"])

    assert result.exit_code == ExitCode.OK, result.output
    for heading in SECTION_HEADINGS:
        assert heading in result.output


def test_generate_denied_exits_3(api_app, monkeypatch) -> None:
    bind_in_process_client(monkeypatch, api_app)

    result = runner.invoke(app, ["generate", "--opp", OPP_1003, "--user", NARROW_READER, "--wait"])

    assert result.exit_code == ExitCode.DENIED_OR_NOT_FOUND
    assert DENIED_MESSAGE in result.output


def test_runs_show_json_parses(api_app, monkeypatch) -> None:
    bind_in_process_client(monkeypatch, api_app)
    created = runner.invoke(app, ["generate", "--opp", OPP_1001, "--user", REQUESTER_1001])
    run_id = created.output.split()[0]

    result = runner.invoke(app, ["runs", "show", run_id, "--user", REQUESTER_1001, "--json"])

    assert result.exit_code == ExitCode.OK
    assert RunStatusResponse.model_validate(json.loads(result.output))


def test_approvals_decide_as_outsider_exits_3(api_app, monkeypatch) -> None:
    bind_in_process_client(monkeypatch, api_app)
    runner.invoke(app, ["generate", "--opp", OPP_1003, "--user", REQUESTER_1003, "--wait"])
    listed = runner.invoke(app, ["approvals", "list", "--user", DEAL_DESK, "--status", "pending"])
    approval_id = listed.output.split()[0]

    result = runner.invoke(
        app, ["approvals", "decide", approval_id, "--user", OUTSIDER, "--approve", "--note", "no"]
    )

    assert result.exit_code == ExitCode.DENIED_OR_NOT_FOUND


def test_approvals_decide_requires_approve_or_reject() -> None:
    result = runner.invoke(app, ["approvals", "decide", "x", "--user", DEAL_DESK])

    assert result.exit_code == ExitCode.INVALID_ARGUMENTS


def test_cli_run_commands_never_import_database_or_orchestration() -> None:
    probe = "import json, sys\nimport deal_intel.cli\nprint(json.dumps(sorted(sys.modules)))\n"
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    loaded = json.loads(result.stdout)
    assert [name for name in loaded if name.startswith(FORBIDDEN_MODULE_PREFIXES)] == []


def test_http_commands_run_without_database_url(tmp_path) -> None:
    env = {key: value for key, value in os.environ.items() if key != "DATABASE_URL"}
    probe = (
        "from deal_intel.cli import app\n"
        "from typer.testing import CliRunner\n"
        "result = CliRunner().invoke(app, ['generate', '--help'])\n"
        "raise SystemExit(result.exit_code)\n"
    )
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr + result.stdout
