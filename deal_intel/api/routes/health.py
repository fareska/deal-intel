import logging
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from deal_intel.api.dependencies import get_db_session

logger = logging.getLogger(__name__)
router = APIRouter()


class HealthStatus(StrEnum):
    OK = "ok"
    DATABASE_UNAVAILABLE = "database_unavailable"


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": HealthStatus.OK}


@router.get("/readyz")
def readyz(
    response: Response, session: Annotated[Session, Depends(get_db_session)]
) -> dict[str, str]:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        logger.warning("readiness check failed: %s", type(error).__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": HealthStatus.DATABASE_UNAVAILABLE}
    return {"status": HealthStatus.OK}
