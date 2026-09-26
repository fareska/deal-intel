from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from deal_intel.api.dependencies import get_db_session, get_runtime
from deal_intel.api.errors import NotFound
from deal_intel.api.runtime import AppRuntime
from deal_intel.api.services.approvals import approvals_for, decide_approval
from deal_intel.api.services.catalog import opportunities_open_to
from deal_intel.api.services.runs import (
    brief_versions,
    latest_brief_response,
    load_readable_run,
    replay_run,
    run_status,
    run_trace,
    start_run,
    status_response,
)
from deal_intel.api.services.users import list_users
from deal_intel.api.templating import (
    APPROVAL_ROW_TEMPLATE,
    APPROVALS_TEMPLATE,
    BRIEF_TEMPLATE,
    FRESH_FIELD,
    NEW_RUN_TEMPLATE,
    NOT_FOUND_TEMPLATE,
    RUN_STATUS_TEMPLATE,
    TRACE_TEMPLATE,
    UI_PATH_PREFIX,
    is_htmx_request,
    templates,
    viewer_id_of,
    with_viewer,
)
from deal_intel.contracts.api import BriefFormat, CreateRunRequest, DecisionRequest, span_depths
from deal_intel.contracts.approvals import Decision
from deal_intel.contracts.reference import OPPORTUNITY_ID_PATTERN, USER_ID_PATTERN, UserProfile
from deal_intel.contracts.runs import REPLAYABLE_STATES, WAIT_STATES

LANDING_TEMPLATE = "index.html"
HX_REDIRECT_HEADER = "HX-Redirect"

router = APIRouter(prefix=UI_PATH_PREFIX, include_in_schema=False)


def load_user_choices(session: Annotated[Session, Depends(get_db_session)]) -> list[UserProfile]:
    return list_users(session)


UserChoices = Annotated[list[UserProfile], Depends(load_user_choices)]
DbSession = Annotated[Session, Depends(get_db_session)]
Runtime = Annotated[AppRuntime, Depends(get_runtime)]


@router.get("", response_class=HTMLResponse)
def landing(request: Request, users: UserChoices) -> HTMLResponse:
    return templates.TemplateResponse(request, LANDING_TEMPLATE, {"users": users})


@router.get("/runs/new", response_class=HTMLResponse)
def new_run_page(request: Request, session: DbSession, users: UserChoices) -> HTMLResponse:
    viewer = viewer_id_of(request)
    return templates.TemplateResponse(
        request,
        NEW_RUN_TEMPLATE,
        {"users": users, "opportunities": opportunities_open_to(session, viewer)},
    )


@router.post("/runs")
def create_run_from_form(
    request: Request,
    session: DbSession,
    runtime: Runtime,
    opportunity_id: Annotated[str, Form(pattern=OPPORTUNITY_ID_PATTERN)],
    fresh: Annotated[str | None, Form()] = None,
) -> RedirectResponse:
    viewer = viewer_id_of(request)
    if viewer is None:
        raise NotFound()
    accepted = start_run(
        session,
        CreateRunRequest(
            opportunity_id=opportunity_id, user_id=viewer, fresh=fresh == FRESH_FIELD
        ),
        runtime.executor,
        runtime.settings,
        runtime.clock(),
    )
    return RedirectResponse(
        with_viewer(f"{UI_PATH_PREFIX}/runs/{accepted.run_id}", viewer),
        status_code=HTTPStatus.SEE_OTHER,
    )


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def brief_page(request: Request, run_id: str, session: DbSession) -> HTMLResponse:
    reader = viewer_id_of(request)
    if reader is None:
        return not_found_page(request)
    try:
        run = load_readable_run(session, run_id, reader)
    except NotFound:
        return not_found_page(request)
    users = list_users(session)
    status = status_response(session, run)
    context: dict[str, object] = {"users": users, "status": status, "run_id": run_id}
    if status.state in REPLAYABLE_STATES:
        try:
            latest = latest_brief_response(session, run_id, reader, BriefFormat.JSON)
        except NotFound:
            latest = None
        if latest is not None:
            context["brief"] = latest.brief
            context["versions"] = brief_versions(session, run_id, reader)
    return templates.TemplateResponse(request, BRIEF_TEMPLATE, context)


@router.get("/runs/{run_id}/status", response_class=HTMLResponse)
def run_status_fragment(request: Request, run_id: str, session: DbSession) -> HTMLResponse:
    reader = viewer_id_of(request)
    if reader is None:
        return not_found_page(request)
    try:
        status = run_status(session, run_id, reader)
    except NotFound:
        return not_found_page(request)
    if is_htmx_request(request) and status.state in WAIT_STATES:
        return HTMLResponse(
            status_code=HTTPStatus.OK,
            headers={HX_REDIRECT_HEADER: with_viewer(f"{UI_PATH_PREFIX}/runs/{run_id}", reader)},
        )
    return templates.TemplateResponse(
        request,
        RUN_STATUS_TEMPLATE,
        {"users": list_users(session), "status": status, "run_id": run_id},
    )


@router.post("/runs/{run_id}/replay")
def replay_from_form(
    request: Request, run_id: str, session: DbSession, runtime: Runtime
) -> RedirectResponse:
    reader = viewer_id_of(request)
    if reader is None:
        raise NotFound()
    replay_run(session, run_id, reader, runtime.clock())
    return RedirectResponse(
        with_viewer(f"{UI_PATH_PREFIX}/runs/{run_id}", reader),
        status_code=HTTPStatus.SEE_OTHER,
    )


@router.get("/runs/{run_id}/trace", response_class=HTMLResponse)
def trace_page(request: Request, run_id: str, session: DbSession) -> HTMLResponse:
    reader = viewer_id_of(request)
    if reader is None:
        return not_found_page(request)
    try:
        status = run_status(session, run_id, reader)
        trace = run_trace(session, run_id, reader)
    except NotFound:
        return not_found_page(request)
    return templates.TemplateResponse(
        request,
        TRACE_TEMPLATE,
        {
            "users": list_users(session),
            "status": status,
            "trace": trace,
            "depths": span_depths(trace.spans),
        },
    )


@router.get("/approvals", response_class=HTMLResponse)
def approvals_page(
    request: Request, session: DbSession, users: UserChoices, runtime: Runtime
) -> HTMLResponse:
    reader = viewer_id_of(request)
    if reader is None:
        return templates.TemplateResponse(
            request, APPROVALS_TEMPLATE, {"users": users, "approvals": []}
        )
    return templates.TemplateResponse(
        request,
        APPROVALS_TEMPLATE,
        {"users": users, "approvals": approvals_for(session, reader, now=runtime.clock())},
    )


@router.post("/approvals/{approval_id}/decision", response_model=None)
def decide_from_form(
    request: Request,
    approval_id: str,
    session: DbSession,
    users: UserChoices,
    runtime: Runtime,
    user_id: Annotated[str, Form(pattern=USER_ID_PATTERN)],
    decision: Annotated[str, Form()],
    note: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    updated = decide_approval(
        session,
        approval_id,
        DecisionRequest(user_id=user_id, decision=Decision(decision), note=note),
        runtime.clock(),
    )
    if is_htmx_request(request):
        return templates.TemplateResponse(
            request, APPROVAL_ROW_TEMPLATE, {"users": users, "item": updated}
        )
    return RedirectResponse(
        with_viewer(f"{UI_PATH_PREFIX}/approvals", user_id),
        status_code=HTTPStatus.SEE_OTHER,
    )


def not_found_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, NOT_FOUND_TEMPLATE, {}, status_code=HTTPStatus.NOT_FOUND
    )
