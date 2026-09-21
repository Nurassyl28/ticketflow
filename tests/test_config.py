from pathlib import Path

import pytest
from pydantic import ValidationError

from ticketflow.config import Settings


def test_environment_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    dotenv = tmp_path / ".env"
    dotenv.write_text(
        "DATABASE_URL=postgresql+psycopg://local:local@localhost/local_db\nPOSTGRES_PORT=5432\n"
    )
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://ci:secret@localhost/ci_db")

    settings = Settings(_env_file=dotenv)

    assert settings.database_url.path == "/ci_db"
    assert "secret" not in repr(settings)


def test_dotenv_loads_database_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    dotenv = tmp_path / ".env"
    dotenv.write_text("DATABASE_URL=postgresql+psycopg://local:local@localhost/local_db\n")

    assert Settings(_env_file=dotenv).database_url.path == "/local_db"


@pytest.mark.parametrize(
    "url",
    [
        "sqlite:///local.db",
        "postgresql://user:private-secret@localhost/ticketflow",
        "postgresql+psycopg://user:private-secret@localhost/",
    ],
)
def test_invalid_database_config_fails_without_printing_password(url: str) -> None:
    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None, database_url=url)

    assert "private-secret" not in str(error.value)


def test_database_url_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(ValidationError, match="database_url"):
        Settings(_env_file=None)
