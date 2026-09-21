from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.exc import OperationalError, TimeoutError

from ticketflow.database import get_engine


def test_liveness_does_not_require_database(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "error",
    [
        OperationalError("SELECT 1", {}, Exception("password=private-secret")),
        TimeoutError("pool timeout password=private-secret"),
    ],
)
def test_database_failure_is_safe_and_liveness_still_works(
    client: TestClient, caplog: pytest.LogCaptureFixture, error: Exception
) -> None:
    engine = Mock(spec=Engine)
    engine.connect.side_effect = error
    client.app.dependency_overrides[get_engine] = lambda: engine

    response = client.get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"detail": "Database is unavailable"}
    assert "private-secret" not in response.text + caplog.text
    assert "Database readiness check failed" in caplog.text
    assert client.get("/health").status_code == 200
