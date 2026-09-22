from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, inspect, text

from ticketflow.models import Base

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_migration_round_trip_and_model_parity(empty_engine: Engine) -> None:
    with empty_engine.begin() as connection:
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        expected = set(Base.metadata.tables) | {"alembic_version"}

        command.upgrade(config, "head")
        assert set(inspect(connection).get_table_names()) == expected
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "0001"
        command.check(config)

        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        command.check(config)


def test_offline_migration_generates_sql_without_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://demo:password@127.0.0.1:1/offline")
    output = StringIO()
    config = Config(str(ROOT / "alembic.ini"), output_buffer=output)

    command.upgrade(config, "head", sql=True)

    sql = output.getvalue()
    assert "CREATE TABLE event_seats" in sql
    assert "CREATE UNIQUE INDEX uq_tickets_live_seat" in sql
    assert "COMMIT;" in sql
