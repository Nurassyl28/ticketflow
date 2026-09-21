from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ticketflow.config import Settings
from ticketflow.main import create_app


@pytest.fixture
def client() -> Iterator[TestClient]:
    settings = Settings(
        _env_file=None,
        database_url="postgresql+psycopg://test:test@127.0.0.1:1/ticketflow_test",
    )
    with TestClient(create_app(settings)) as test_client:
        yield test_client
