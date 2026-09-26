from http import HTTPStatus
from typing import Annotated

from fastapi import APIRouter, Query

from deal_intel.api.dependencies import DbSession, ReaderId, Runtime
from deal_intel.api.services import runs as run_services
from deal_intel.contracts.api import (
    RUNS_PATH,
    BriefFormat,
    BriefResponse,
    CreateRunRequest,
    RunAccepted,
    RunStatusResponse,
    TraceResponse,
)

router = APIRouter()
FormatParam = Annotated[BriefFormat, Query(alias="format")]


@router.post(RUNS_PATH, status_code=HTTPStatus.ACCEPTED)
def create_run(body: CreateRunRequest, session: DbSession, runtime: Runtime) -> RunAccepted:
    return run_services.start_run(
        session, body, runtime.executor, runtime.settings, runtime.clock()
    )


@router.get(f"{RUNS_PATH}/{{run_id}}")
def get_run(run_id: str, user_id: ReaderId, session: DbSession) -> RunStatusResponse:
    return run_services.run_status(session, run_id, user_id)


@router.get(f"{RUNS_PATH}/{{run_id}}/brief")
def get_brief(
    run_id: str, user_id: ReaderId, session: DbSession, fmt: FormatParam = BriefFormat.JSON
) -> BriefResponse:
    return run_services.latest_brief_response(session, run_id, user_id, fmt)


@router.get(f"{RUNS_PATH}/{{run_id}}/trace")
def get_trace(run_id: str, user_id: ReaderId, session: DbSession) -> TraceResponse:
    return run_services.run_trace(session, run_id, user_id)


@router.post(f"{RUNS_PATH}/{{run_id}}/replay")
def replay_run(
    run_id: str, user_id: ReaderId, session: DbSession, runtime: Runtime
) -> BriefResponse:
    return run_services.replay_run(session, run_id, user_id, runtime.clock())


@router.post(f"{RUNS_PATH}/{{run_id}}/resume")
def resume_run(
    run_id: str, user_id: ReaderId, session: DbSession, runtime: Runtime
) -> RunStatusResponse:
    return run_services.resume_run(session, run_id, user_id, runtime.executor)
