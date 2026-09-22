import pytest
from sqlalchemy import Engine, func, select, update

from ticketflow.models import Base, User, Venue
from ticketflow.seed import demo_id, seed_demo

pytestmark = pytest.mark.integration


def test_seed_is_repeatable_and_preserves_existing_data(migrated_engine: Engine) -> None:
    expected = {
        "users": 3,
        "venues": 1,
        "sections": 2,
        "seats": 12,
        "events": 2,
        "ticket_types": 4,
        "event_seats": 24,
    }
    with migrated_engine.begin() as connection:
        assert seed_demo(connection) == expected
        connection.execute(update(Venue).where(Venue.id == demo_id("venue")).values(name="Edited"))
        assert all(count == 0 for count in seed_demo(connection).values())
        assert connection.scalar(select(Venue.name)) == "Edited"
        assert connection.scalars(select(User.password_hash)).all() == ["!", "!", "!"]
        for name, count in expected.items():
            assert (
                connection.scalar(select(func.count()).select_from(Base.metadata.tables[name]))
                == count
            )
