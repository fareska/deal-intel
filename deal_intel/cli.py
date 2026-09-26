import json
from collections.abc import Callable
from importlib.metadata import version
from pathlib import Path
from time import sleep
from typing import Annotated

import typer

from deal_intel.cli_client import ApiClient, ApiError, ApiUnreachable, get_api_client
from deal_intel.cli_output import (
    ExitCode,
    JsonFlag,
    exit_with_error,
    print_json,
    print_lines,
    print_result,
    raise_for_run_state,
)
from deal_intel.contracts.api import (
    ApprovalResponse,
    BriefFormat,
    BriefResponse,
    CreateRunRequest,
    DecisionRequest,
    RunAccepted,
    RunStatusResponse,
    TraceResponse,
    denial_message,
    span_depths,
)
from deal_intel.contracts.approvals import ApprovalStatus, Decision
from deal_intel.contracts.runs import WAIT_STATES, RunState

app = typer.Typer(help="Strategic Deal Intelligence Assistant", no_args_is_help=True)
runs_app = typer.Typer(help="Inspect, replay, and resume runs")
approvals_app = typer.Typer(help="List and decide approvals")
app.add_typer(runs_app, name="runs")
app.add_typer(approvals_app, name="approvals")

POLL_INTERVAL_SECONDS = 2.0
OpportunityId = Annotated[str, typer.Option("--opp", help="Opportunity id, OPP-dddd")]
UserId = Annotated[str, typer.Option("--user", help="Requesting or reading user id, USR-dddd")]
RunIdArg = Annotated[str, typer.Argument(help="Run id")]
ApprovalIdArg = Annotated[str, typer.Argument(help="Approval id")]
DataPath = Annotated[
    Path | None, typer.Option("--path", help="Folder holding the synthetic dataset")
]


@app.callback()
def main() -> None:
    """Keeps Typer in multi-command mode."""


@app.command("version")
def show_version() -> None:
    typer.echo(version("deal-intel"))


@app.command()
def generate(
    opp: OpportunityId,
    user: UserId,
    wait: Annotated[bool, typer.Option("--wait", help="Poll until the run finishes")] = False,
    fresh: Annotated[
        bool, typer.Option("--fresh", help="Bypass idempotency and the model-output cache")
    ] = False,
    as_json: JsonFlag = False,
) -> None:
    """Start a run through the API."""
    with_api(lambda client: run_generate(client, opp, user, wait, fresh, as_json))


@runs_app.command("show")
def runs_show(run_id: RunIdArg, user: UserId, as_json: JsonFlag = False) -> None:
    with_api(lambda client: show_run(client, run_id, user, as_json))


@runs_app.command("trace")
def runs_trace(run_id: RunIdArg, user: UserId, as_json: JsonFlag = False) -> None:
    with_api(lambda client: print_result(client.get_trace(run_id, user), as_json, describe_trace))


@runs_app.command("replay")
def runs_replay(run_id: RunIdArg, user: UserId, as_json: JsonFlag = False) -> None:
    with_api(lambda client: print_result(client.replay(run_id, user), as_json, describe_brief))


@runs_app.command("resume")
def runs_resume(run_id: RunIdArg, user: UserId, as_json: JsonFlag = False) -> None:
    with_api(lambda client: show_run(client, run_id, user, as_json, resume=True))


@approvals_app.command("list")
def approvals_list(
    user: UserId,
    status: Annotated[ApprovalStatus | None, typer.Option("--status")] = None,
    as_json: JsonFlag = False,
) -> None:
    with_api(lambda client: show_approvals(client, user, status, as_json))


@approvals_app.command("decide")
def approvals_decide(
    approval_id: ApprovalIdArg,
    user: UserId,
    approve: Annotated[bool, typer.Option("--approve")] = False,
    reject: Annotated[bool, typer.Option("--reject")] = False,
    note: Annotated[str, typer.Option("--note")] = "",
    as_json: JsonFlag = False,
) -> None:
    if approve == reject:
        typer.echo("exactly one of --approve or --reject is required", err=True)
        raise typer.Exit(ExitCode.INVALID_ARGUMENTS)
    request = DecisionRequest(
        user_id=user, decision=Decision.APPROVED if approve else Decision.REJECTED, note=note
    )
    with_api(
        lambda client: print_result(client.decide(approval_id, request), as_json, describe_decision)
    )


@app.command()
def ingest(path: DataPath = None) -> None:
    """Load reference tables and evidence chunks in one transaction."""
    from deal_intel.db.session import session_scope
    from deal_intel.retrieval.dataset import DEFAULT_DATA_ROOT
    from deal_intel.retrieval.ingest import load_evidence
    from deal_intel.retrieval.reference import load_reference_data

    root = DEFAULT_DATA_ROOT if path is None else path
    with session_scope() as session:
        row_counts = load_reference_data(session, root)
        report = load_evidence(session, root)
    for name, count in row_counts.items():
        typer.echo(f"{name}: {count}")
    outcome = "unchanged, nothing written" if report.skipped else "written"
    typer.echo(f"snapshot {report.snapshot_id}: {outcome}")
    for kind, count in report.chunk_counts.items():
        typer.echo(f"{kind.value}: {count}")


@app.command("generate-slack")
def generate_slack(path: DataPath = None) -> None:
    """Validate the authored Slack updates and write them as a TSV into the dataset."""
    from deal_intel.retrieval.dataset import DEFAULT_DATA_ROOT
    from deal_intel.retrieval.slack_dataset import AUTHORED_UPDATES, write_slack_dataset

    written = write_slack_dataset(DEFAULT_DATA_ROOT if path is None else path)
    typer.echo(f"wrote {len(AUTHORED_UPDATES)} updates to {written}")


def with_api(work: Callable[[ApiClient], None]) -> None:
    try:
        with get_api_client() as client:
            work(client)
    except (ApiError, ApiUnreachable) as error:
        exit_with_error(error)


def run_generate(
    client: ApiClient, opp: str, user: str, wait: bool, fresh: bool, as_json: bool
) -> None:
    accepted = client.create_run(CreateRunRequest(opportunity_id=opp, user_id=user, fresh=fresh))
    if not wait:
        print_result(accepted, as_json, describe_accepted)
        return
    finish_waited_run(client, wait_for_run(client, accepted.run_id, user), user, as_json)


def show_run(
    client: ApiClient, run_id: str, user: str, as_json: bool, *, resume: bool = False
) -> None:
    status = client.resume(run_id, user) if resume else client.get_run(run_id, user)
    print_result(status, as_json, describe_status)
    raise_for_run_state(status.state)


def show_approvals(
    client: ApiClient, user: str, status: ApprovalStatus | None, as_json: bool
) -> None:
    items = client.list_approvals(user, status)
    if as_json:
        typer.echo(json.dumps([item.model_dump(mode="json") for item in items], indent=2))
        return
    print_lines(line for item in items for line in describe_approval(item))


def wait_for_run(client: ApiClient, run_id: str, user_id: str) -> RunStatusResponse:
    while True:
        status = client.get_run(run_id, user_id)
        if status.state in WAIT_STATES:
            return status
        sleep(POLL_INTERVAL_SECONDS)


def finish_waited_run(
    client: ApiClient, status: RunStatusResponse, user_id: str, as_json: bool
) -> None:
    if status.state is RunState.DENIED:
        if as_json:
            print_json(status)
        else:
            typer.echo(status.message or denial_message())
        raise_for_run_state(status.state)
    if status.state is RunState.FAILED:
        print_result(status, as_json, describe_status)
        raise_for_run_state(status.state)
    print_result(
        client.get_brief(status.run_id, user_id, BriefFormat.MARKDOWN), as_json, describe_brief
    )


def describe_accepted(accepted: RunAccepted) -> list[str]:
    reused = "existing" if accepted.existing else "new"
    return [f"{accepted.run_id} {accepted.state.value} ({reused})"]


def describe_status(status: RunStatusResponse) -> list[str]:
    lines = [
        f"{status.run_id} {status.state.value}",
        f"opportunity {status.opportunity_id} user {status.user_id}",
        f"degraded {status.degraded} cost {status.cost_usd} tokens {status.input_tokens}",
    ]
    if status.message:
        lines.append(status.message)
    lines.extend(
        f"{usage.agent_name} in={usage.input_tokens} "
        f"out={usage.output_tokens} cost={usage.cost_usd}"
        for usage in status.usage_by_agent
    )
    return lines


def describe_brief(brief: BriefResponse) -> list[str]:
    if brief.markdown:
        return [brief.markdown]
    return [f"{brief.run_id} v{brief.version} {brief.source.value}"]


def describe_trace(trace: TraceResponse) -> list[str]:
    lines = [f"{trace.run_id}{' redacted' if trace.redacted else ''}"]
    for span, depth in zip(trace.spans, span_depths(trace.spans), strict=True):
        duration = f"{span.duration_ms}ms" if span.duration_ms is not None else "-"
        lines.append(f"{'  ' * depth}{span.kind} {span.name} {span.status} {duration}")
    return lines


def describe_approval(item: ApprovalResponse) -> list[str]:
    missing = "" if item.eligible_user_ids else " no eligible approver"
    rules = ",".join(rule.value for rule in item.rule_ids)
    return [
        f"{item.approval_id} {item.required_role.value} {item.status.value} "
        f"{rules} {item.summary} expires {item.expires_at.date().isoformat()}{missing}"
    ]


def describe_decision(item: ApprovalResponse) -> list[str]:
    return [f"{item.approval_id} {item.status.value} run {item.run_state.value}"]


if __name__ == "__main__":
    app()
