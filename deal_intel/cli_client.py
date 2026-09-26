"""The CLI's only way to reach the API: one httpx client owning the base URL, timeout, and error
mapping. Must never import database or orchestration modules."""

import asyncio
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import BaseModel, ValidationError

from deal_intel.api.schemas import (
    HEALTHZ_PATH,
    ApprovalResponse,
    BriefFormat,
    BriefResponse,
    CreateRunRequest,
    DecisionRequest,
    ErrorBody,
    ErrorResponse,
    HealthResponse,
    RunAccepted,
    RunStatusResponse,
    TraceResponse,
    approval_decision_path,
    run_brief_path,
    run_path,
    run_replay_path,
    run_resume_path,
    run_trace_path,
)
from deal_intel.config import get_client_settings
from deal_intel.contracts.api import APPROVALS_PATH, RUNS_PATH
from deal_intel.contracts.approvals import ApprovalStatus

type AsgiApp = Callable[..., Awaitable[None]]
type QueryParams = dict[str, str]


class ApiError(Exception):
    """The API answered with a non-2xx status; `body` is None when it was not our error contract."""

    def __init__(self, status_code: int, body: ErrorBody | None) -> None:
        detail = f"{body.code}: {body.message}" if body else "no error body"
        super().__init__(f"API returned {status_code} ({detail})")
        self.status_code = status_code
        self.body = body


class ApiUnreachable(Exception):
    def __init__(self, base_url: str) -> None:
        super().__init__(f"cannot reach the API at {base_url}")
        self.base_url = base_url


def parse_error_body(response: httpx.Response) -> ErrorBody | None:
    try:
        return ErrorResponse.model_validate_json(response.content).error
    except ValidationError:
        return None


class InProcessTransport(httpx.BaseTransport):
    """Serves the synchronous client from an ASGI app in this process, for tests and in-process
    tooling. `httpx.ASGITransport` alone only works with the async client."""

    def __init__(self, app: AsgiApp) -> None:
        self._asgi = httpx.ASGITransport(app=app)

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return asyncio.run(self._send(request))

    async def _send(self, request: httpx.Request) -> httpx.Response:
        response = await self._asgi.handle_async_request(request)
        # Raw bytes, so the outer client decodes any content encoding exactly once.
        raw = b"".join([chunk async for chunk in response.aiter_raw()])
        return httpx.Response(
            response.status_code, headers=response.headers, content=raw, request=request
        )


class ApiClient:
    def __init__(
        self,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url
        self._http = httpx.Client(base_url=base_url, timeout=timeout_seconds, transport=transport)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def get[ModelT: BaseModel](
        self, path: str, response_model: type[ModelT], params: QueryParams | None = None
    ) -> ModelT:
        response = self._send("GET", path, params=params)
        return response_model.model_validate_json(response.content)

    def post[ModelT: BaseModel](
        self,
        path: str,
        response_model: type[ModelT],
        body: BaseModel | None = None,
        params: QueryParams | None = None,
    ) -> ModelT:
        payload = body.model_dump(mode="json") if body is not None else None
        response = self._send("POST", path, params=params, json=payload)
        return response_model.model_validate_json(response.content)

    def health(self) -> HealthResponse:
        return self.get(HEALTHZ_PATH, HealthResponse)

    def create_run(self, request: CreateRunRequest) -> RunAccepted:
        return self.post(RUNS_PATH, RunAccepted, body=request)

    def get_run(self, run_id: str, user_id: str) -> RunStatusResponse:
        return self.get(run_path(run_id), RunStatusResponse, params={"user_id": user_id})

    def get_brief(
        self, run_id: str, user_id: str, fmt: BriefFormat = BriefFormat.MARKDOWN
    ) -> BriefResponse:
        return self.get(
            run_brief_path(run_id),
            BriefResponse,
            params={"user_id": user_id, "format": fmt.value},
        )

    def get_trace(self, run_id: str, user_id: str) -> TraceResponse:
        return self.get(run_trace_path(run_id), TraceResponse, params={"user_id": user_id})

    def replay(self, run_id: str, user_id: str) -> BriefResponse:
        return self.post(run_replay_path(run_id), BriefResponse, params={"user_id": user_id})

    def resume(self, run_id: str, user_id: str) -> RunStatusResponse:
        return self.post(run_resume_path(run_id), RunStatusResponse, params={"user_id": user_id})

    def list_approvals(
        self, user_id: str, status: ApprovalStatus | None = None
    ) -> list[ApprovalResponse]:
        params: QueryParams = {"user_id": user_id}
        if status is not None:
            params["status"] = status.value
        response = self._send("GET", APPROVALS_PATH, params=params)
        return [ApprovalResponse.model_validate(item) for item in response.json()]

    def decide(self, approval_id: str, request: DecisionRequest) -> ApprovalResponse:
        return self.post(approval_decision_path(approval_id), ApprovalResponse, body=request)

    def _send(self, method: str, path: str, **options: Any) -> httpx.Response:
        try:
            response = self._http.request(method, path, **options)
        except httpx.TransportError as error:
            raise ApiUnreachable(self.base_url) from error
        if response.is_error:
            raise ApiError(response.status_code, parse_error_body(response))
        return response


def get_api_client(transport: httpx.BaseTransport | None = None) -> ApiClient:
    """The one place commands obtain a client; tests pass an `InProcessTransport`."""
    settings = get_client_settings()
    return ApiClient(settings.api_base_url, settings.api_timeout_seconds, transport)
