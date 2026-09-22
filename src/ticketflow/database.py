from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

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


def get_session(engine: Annotated[Engine, Depends(get_engine)]) -> Iterator[Session]:
    # Writes commit explicitly; close rolls back any unfinished transaction.
    with Session(engine, expire_on_commit=False) as session:
        yield session


SessionDependency = Annotated[Session, Depends(get_session)]
