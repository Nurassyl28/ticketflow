import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine
from sqlalchemy.pool import NullPool

from ticketflow.seed import seed_demo

ROOT = Path(__file__).resolve().parents[2]


def migration_config(connection: Connection) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.attributes["connection"] = connection
    return config


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL or run pytest -m 'not integration'")
    return url


@pytest.fixture
def empty_engine(database_url: str) -> Iterator[Engine]:
    # Only this generated schema is created/dropped; existing schemas stay untouched.
    schema = f"test_ticketflow_{uuid4().hex}"
    admin = create_engine(database_url, poolclass=NullPool)
    engine = create_engine(
        database_url,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 3},
    )
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


@pytest.fixture
def migrated_engine(empty_engine: Engine) -> Engine:
    with empty_engine.begin() as connection:
        command.upgrade(migration_config(connection), "head")
    return empty_engine


@pytest.fixture
def catalog(migrated_engine: Engine) -> Iterator[Connection]:
    with migrated_engine.begin() as connection:
        seed_demo(connection)
        yield connection
