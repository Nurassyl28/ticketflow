import os
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, create_engine, func, insert, select, update
from sqlalchemy.pool import NullPool

from ticketflow.auth.security import hash_password, new_token, token_digest
from ticketflow.config import Settings
from ticketflow.database import get_engine
from ticketflow.main import create_app
from ticketflow.models import AuthSession, User, UserRole
from ticketflow.seed import demo_id, seed_demo

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


@pytest.fixture(scope="session")
def api_password_hash():
    return hash_password("TicketFlow integration password")


@pytest.fixture
def api_headers(migrated_engine, api_password_hash):
    headers = {}
    with migrated_engine.begin() as connection:
        seed_demo(connection)
        now = connection.scalar(select(func.clock_timestamp()))
        for name, role in [
            ("customer", UserRole.CUSTOMER),
            ("organizer", UserRole.ORGANIZER),
            ("admin", UserRole.ADMIN),
            ("buyer2", UserRole.CUSTOMER),
            ("outsider", UserRole.ORGANIZER),
        ]:
            user_id = demo_id(name)
            if name in ("buyer2", "outsider"):
                connection.execute(
                    insert(User).values(
                        id=user_id,
                        email=f"{name}@example.com",
                        role=role,
                        password_hash=api_password_hash,
                    )
                )
            else:
                connection.execute(
                    update(User)
                    .where(User.id == user_id)
                    .values(email=f"{name}@example.com", password_hash=api_password_hash)
                )
            token = new_token()
            connection.execute(
                insert(AuthSession).values(
                    token_hash=token_digest(token),
                    user_id=user_id,
                    created_at=now,
                    expires_at=now + timedelta(hours=1),
                )
            )
            headers[name] = {"Authorization": f"Bearer {token}"}
    return headers


@pytest.fixture
def api(migrated_engine, api_headers):
    app = create_app(
        Settings(_env_file=None, database_url="postgresql+psycopg://test:test@127.0.0.1:1/test")
    )
    app.dependency_overrides[get_engine] = lambda: migrated_engine
    with TestClient(app) as client:
        yield client
