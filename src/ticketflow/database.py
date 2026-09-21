from fastapi import Request
from sqlalchemy import Engine, create_engine

from ticketflow.config import Settings


def build_engine(settings: Settings) -> Engine:
    return create_engine(
        str(settings.database_url),
        pool_pre_ping=True,
        pool_timeout=3,
        connect_args={"connect_timeout": 3, "options": "-c statement_timeout=3000"},
    )


def get_engine(request: Request) -> Engine:
    return request.app.state.engine
