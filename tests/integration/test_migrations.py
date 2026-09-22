from io import StringIO
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from ticketflow.models import Base
from ticketflow.seed import seed_demo

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def test_migration_round_trip_and_model_parity(empty_engine: Engine) -> None:
    with empty_engine.begin() as connection:
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        expected = set(Base.metadata.tables) | {"alembic_version"}

        command.upgrade(config, "head")
        assert set(inspect(connection).get_table_names()) == expected
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == ScriptDirectory.from_config(config).get_current_head()
        )
        command.check(config)

        command.downgrade(config, "base")
        assert set(inspect(connection).get_table_names()) == {"alembic_version"}
        command.upgrade(config, "head")
        command.check(config)


def test_auth_migration_preserves_existing_data(empty_engine: Engine) -> None:
    with empty_engine.begin() as connection:
        config = Config(str(ROOT / "alembic.ini"))
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        seed_demo(connection)
        users_before = connection.execute(text("SELECT * FROM users ORDER BY id")).all()
        events_before = connection.execute(text("SELECT * FROM events ORDER BY id")).all()

        command.upgrade(config, "0002")
        assert "auth_sessions" in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT * FROM users ORDER BY id")).all() == users_before
        assert connection.execute(text("SELECT * FROM events ORDER BY id")).all() == events_before

        command.downgrade(config, "0001")
        assert "auth_sessions" not in inspect(connection).get_table_names()
        assert connection.execute(text("SELECT * FROM users ORDER BY id")).all() == users_before
        assert connection.execute(text("SELECT * FROM events ORDER BY id")).all() == events_before


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
