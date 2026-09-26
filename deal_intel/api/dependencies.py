from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.orm import Session, sessionmaker

from deal_intel.api.runtime import AppRuntime
from deal_intel.contracts.reference import USER_ID_PATTERN
from deal_intel.db.session import get_session_factory

RUNTIME_STATE_ATTR = "runtime"


def runtime_of(request: Request) -> AppRuntime:
    runtime = getattr(request.app.state, RUNTIME_STATE_ATTR, None)
    if runtime is None:
        raise RuntimeError("app runtime is not started")
    return runtime


def session_factory_of(request: Request) -> sessionmaker[Session]:
    runtime = getattr(request.app.state, RUNTIME_STATE_ATTR, None)
    if runtime is not None:
        return runtime.session_factory
    return get_session_factory()


def get_db_session(request: Request) -> Iterator[Session]:
    with session_factory_of(request)() as session:
        yield session


def get_runtime(request: Request) -> AppRuntime:
    return runtime_of(request)


def require_reader(
    user_id: Annotated[str, Query(pattern=USER_ID_PATTERN)],
) -> str:
    return user_id


ReaderId = Annotated[str, Depends(require_reader)]
DbSession = Annotated[Session, Depends(get_db_session)]
Runtime = Annotated[AppRuntime, Depends(get_runtime)]
