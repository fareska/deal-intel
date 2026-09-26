"""One error body for every failure: `{error: {code, message, request_id}}` as JSON, or the error
page for UI paths. Messages are fixed per code, so no handler can echo internal detail."""

from collections.abc import Mapping
from http import HTTPStatus

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.responses import Response

from deal_intel.api.request_context import current_request_id
from deal_intel.api.schemas import ErrorBody, ErrorCode, ErrorResponse
from deal_intel.api.templating import ERROR_TEMPLATE, NOT_FOUND_TEMPLATE, is_ui_path, templates
from deal_intel.orchestration.executor import DailyBudgetExceeded
from deal_intel.orchestration.persistence import RunNotFound
from deal_intel.permissions.gate import InvalidInput
from deal_intel.policy.engine import (
    ApprovalNotFound,
    ApprovalNotPending,
    NotEligible,
    RunNotAwaitingApproval,
)
from deal_intel.rendering.brief import ReplayUnavailable

ERROR_MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.INVALID_INPUT: "The request is invalid.",
    ErrorCode.NOT_FOUND: "Not found.",
    ErrorCode.FORBIDDEN: "You are not allowed to perform this action.",
    ErrorCode.METHOD_NOT_ALLOWED: "This method is not allowed here.",
    ErrorCode.CONFLICT: "The request conflicts with the current state.",
    ErrorCode.PAYLOAD_TOO_LARGE: "The request body is too large.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong. Quote the request id when reporting it.",
}

STATUS_ERROR_CODES: dict[int, ErrorCode] = {
    HTTPStatus.BAD_REQUEST: ErrorCode.INVALID_INPUT,
    HTTPStatus.FORBIDDEN: ErrorCode.FORBIDDEN,
    HTTPStatus.NOT_FOUND: ErrorCode.NOT_FOUND,
    HTTPStatus.METHOD_NOT_ALLOWED: ErrorCode.METHOD_NOT_ALLOWED,
    HTTPStatus.CONFLICT: ErrorCode.CONFLICT,
    HTTPStatus.REQUEST_ENTITY_TOO_LARGE: ErrorCode.PAYLOAD_TOO_LARGE,
    HTTPStatus.UNPROCESSABLE_ENTITY: ErrorCode.INVALID_INPUT,
}


class NotFound(HTTPException):
    """Raised for unknown and unauthorised resources alike, so callers cannot probe which exist."""

    def __init__(self) -> None:
        super().__init__(status_code=HTTPStatus.NOT_FOUND)


class Forbidden(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=HTTPStatus.FORBIDDEN)


class Conflict(HTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=HTTPStatus.CONFLICT)


def error_code_for(status_code: int) -> ErrorCode:
    if status_code in STATUS_ERROR_CODES:
        return STATUS_ERROR_CODES[status_code]
    if status_code >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return ErrorCode.INTERNAL_ERROR
    return ErrorCode.INVALID_INPUT


def error_template_for(status_code: int) -> str:
    return NOT_FOUND_TEMPLATE if status_code == HTTPStatus.NOT_FOUND else ERROR_TEMPLATE


def error_response(
    request: Request,
    status_code: int,
    code: ErrorCode,
    message: str | None = None,
    headers: Mapping[str, str] | None = None,
) -> Response:
    body = ErrorBody(
        code=code, message=message or ERROR_MESSAGES[code], request_id=current_request_id()
    )
    if is_ui_path(request.url.path):
        return templates.TemplateResponse(
            request,
            error_template_for(status_code),
            {"error": body},
            status_code=status_code,
            headers=headers,
        )
    return JSONResponse(
        ErrorResponse(error=body).model_dump(mode="json"), status_code=status_code, headers=headers
    )


def internal_error_response(request: Request) -> Response:
    return error_response(request, HTTPStatus.INTERNAL_SERVER_ERROR, ErrorCode.INTERNAL_ERROR)


def payload_too_large_response(request: Request) -> Response:
    return error_response(request, HTTPStatus.REQUEST_ENTITY_TOO_LARGE, ErrorCode.PAYLOAD_TOO_LARGE)


def invalid_fields_message(error: RequestValidationError) -> str:
    """Names the offending fields only; submitted values never appear in the response."""
    fields = dict.fromkeys(".".join(str(part) for part in item["loc"]) for item in error.errors())
    return f"{ERROR_MESSAGES[ErrorCode.INVALID_INPUT]} Invalid fields: {', '.join(fields)}."


async def handle_http_exception(request: Request, error: HTTPException) -> Response:
    return error_response(
        request, error.status_code, error_code_for(error.status_code), headers=error.headers
    )


async def handle_validation_error(request: Request, error: RequestValidationError) -> Response:
    return error_response(
        request,
        HTTPStatus.UNPROCESSABLE_ENTITY,
        ErrorCode.INVALID_INPUT,
        invalid_fields_message(error),
    )


async def handle_invalid_input(request: Request, error: InvalidInput) -> Response:
    return error_response(request, HTTPStatus.BAD_REQUEST, ErrorCode.INVALID_INPUT)


async def handle_not_found_domain(request: Request, error: Exception) -> Response:
    return error_response(request, HTTPStatus.NOT_FOUND, ErrorCode.NOT_FOUND)


async def handle_forbidden_domain(request: Request, error: NotEligible) -> Response:
    return error_response(request, HTTPStatus.FORBIDDEN, ErrorCode.FORBIDDEN)


async def handle_conflict_domain(request: Request, error: Exception) -> Response:
    return error_response(request, HTTPStatus.CONFLICT, ErrorCode.CONFLICT)


def register_error_handlers(app: FastAPI) -> None:
    """Unhandled exceptions are caught by `UnhandledErrorMiddleware` instead of a handler here,
    because Starlette runs the catch-all handler outside user middleware, where the request id and
    security headers would be missing."""
    app.add_exception_handler(HTTPException, handle_http_exception)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, handle_validation_error)  # type: ignore[arg-type]
    app.add_exception_handler(InvalidInput, handle_invalid_input)  # type: ignore[arg-type]
    app.add_exception_handler(NotEligible, handle_forbidden_domain)  # type: ignore[arg-type]
    for conflict_type in (
        ApprovalNotPending,
        RunNotAwaitingApproval,
        ReplayUnavailable,
        DailyBudgetExceeded,
    ):
        app.add_exception_handler(conflict_type, handle_conflict_domain)  # type: ignore[arg-type]
    for missing_type in (RunNotFound, ApprovalNotFound):
        app.add_exception_handler(missing_type, handle_not_found_domain)  # type: ignore[arg-type]
