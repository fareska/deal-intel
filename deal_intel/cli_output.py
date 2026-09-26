"""Exit codes and printing for the thin-client commands. Must never import database or
orchestration modules."""

from collections.abc import Callable, Iterable
from enum import IntEnum
from http import HTTPStatus
from typing import Annotated, NoReturn

import typer
from pydantic import BaseModel

from deal_intel.cli_client import ApiError, ApiUnreachable
from deal_intel.contracts.runs import RunState


class ExitCode(IntEnum):
    OK = 0
    # Typer exits with 2 on its own argument errors; the API's 4xx input errors reuse it.
    INVALID_ARGUMENTS = 2
    DENIED_OR_NOT_FOUND = 3
    # The run ended FAILED, or the API could not complete the request.
    RUN_FAILED = 4
    API_UNREACHABLE = 5


STATUS_EXIT_CODES: dict[int, ExitCode] = {
    HTTPStatus.BAD_REQUEST: ExitCode.INVALID_ARGUMENTS,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ExitCode.INVALID_ARGUMENTS,
    HTTPStatus.UNPROCESSABLE_ENTITY: ExitCode.INVALID_ARGUMENTS,
    HTTPStatus.FORBIDDEN: ExitCode.DENIED_OR_NOT_FOUND,
    HTTPStatus.NOT_FOUND: ExitCode.DENIED_OR_NOT_FOUND,
}

type CliError = ApiError | ApiUnreachable

JsonFlag = Annotated[
    bool, typer.Option("--json", help="Print the raw API response and nothing else")
]


def exit_code_for(error: CliError) -> ExitCode:
    if isinstance(error, ApiUnreachable):
        return ExitCode.API_UNREACHABLE
    return STATUS_EXIT_CODES.get(error.status_code, ExitCode.RUN_FAILED)


def describe_error(error: CliError) -> str:
    if isinstance(error, ApiError) and error.body is not None:
        body = error.body
        return f"error {body.code}: {body.message} (request id {body.request_id})"
    return f"error: {error}"


def print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        typer.echo(line)


def print_json(model: BaseModel) -> None:
    typer.echo(model.model_dump_json(indent=2))


def print_result[ModelT: BaseModel](
    model: ModelT, as_json: bool, describe: Callable[[ModelT], Iterable[str]]
) -> None:
    """`--json` prints the response model verbatim; otherwise `describe` renders it for people."""
    if as_json:
        print_json(model)
    else:
        print_lines(describe(model))


def exit_with_error(error: CliError) -> NoReturn:
    typer.echo(describe_error(error), err=True)
    raise typer.Exit(exit_code_for(error))


def raise_for_run_state(state: RunState) -> None:
    if state is RunState.DENIED:
        raise typer.Exit(ExitCode.DENIED_OR_NOT_FOUND)
    if state is RunState.FAILED:
        raise typer.Exit(ExitCode.RUN_FAILED)
