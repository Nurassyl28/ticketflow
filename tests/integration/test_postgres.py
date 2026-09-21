import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from ticketflow.config import Settings
from ticketflow.main import create_app

pytestmark = pytest.mark.integration


@pytest.fixture
def database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.fail("Set TEST_DATABASE_URL or run pytest -m 'not integration'")
    return url


def test_readiness_with_real_postgres(database_url: str) -> None:
    settings = Settings(_env_file=None, database_url=database_url)

    with TestClient(create_app(settings)) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


def test_real_connection_failure_returns_503(database_url: str) -> None:
    url = make_url(database_url).set(database="ticketflow_nonexistent_readiness_test")
    settings = Settings(_env_file=None, database_url=url.render_as_string(hide_password=False))

    with TestClient(create_app(settings)) as client:
        assert client.get("/health").status_code == 200
        response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
