import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from deal_intel.api.dependencies import get_db_session
from deal_intel.api.schemas import HEALTHZ_PATH, READYZ_PATH, HealthResponse, HealthStatus

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get(HEALTHZ_PATH)
def healthz() -> HealthResponse:
    return HealthResponse(status=HealthStatus.OK)


@router.get(READYZ_PATH)
def readyz(
    response: Response, session: Annotated[Session, Depends(get_db_session)]
) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        logger.warning("readiness check failed: %s", type(error).__name__)
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(status=HealthStatus.DATABASE_UNAVAILABLE)
    return HealthResponse(status=HealthStatus.OK)
