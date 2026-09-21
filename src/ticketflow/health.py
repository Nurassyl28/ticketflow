import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from ticketflow.database import get_engine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/health", tags=["Health"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(HealthResponse):
    database: Literal["ok"] = "ok"


@router.get("", response_model=HealthResponse)
def health() -> HealthResponse:
    """Check that the API process is alive, independently of PostgreSQL."""
    return HealthResponse()


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"description": "Database is unavailable"}},
)
def readiness(engine: Annotated[Engine, Depends(get_engine)]) -> ReadinessResponse:
    """Check that PostgreSQL accepts a query."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # Connection errors can contain credentials; keep them out of responses and logs.
        logger.warning("Database readiness check failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        ) from None
    return ReadinessResponse()
