"""Repeatable demo catalog; accounts have disabled password hashes."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import Connection
from sqlalchemy.dialects.postgresql import insert

from ticketflow.config import Settings
from ticketflow.database import build_engine
from ticketflow.models import (
    Base,
    Event,
    EventCategory,
    EventSeat,
    EventStatus,
    Seat,
    Section,
    TicketType,
    User,
    UserRole,
    Venue,
)


def demo_id(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://ticketflow.local/demo/{name}")


def seed_demo(connection: Connection) -> dict[str, int]:
    """Insert missing rows in the caller's transaction, preserving existing data."""
    counts: dict[str, int] = {}

    def add(model: type[Base], **values: object) -> None:
        table = model.__table__
        statement = (
            insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=list(table.primary_key.columns))
        )
        statement = statement.returning(next(iter(table.primary_key.columns)))
        inserted = connection.execute(statement).all()
        counts[table.name] = counts.get(table.name, 0) + len(inserted)

    for role in UserRole:
        add(
            User,
            id=demo_id(role.value.lower()),
            email=f"{role.value.lower()}@demo.ticketflow.invalid",
            password_hash="!",
            role=role,
        )

    venue_id = demo_id("venue")
    organizer_id = demo_id("organizer")
    add(
        Venue,
        id=venue_id,
        organizer_id=organizer_id,
        name="TicketFlow Demo Hall",
        address="Демонстрационная площадка, 1",
        city="Almaty",
        capacity=12,
    )

    for section in ("VIP", "Standard"):
        section_id = demo_id(f"section/{section}")
        add(Section, id=section_id, venue_id=venue_id, name=section)
        for seat_number in range(1, 7):
            add(
                Seat,
                id=demo_id(f"seat/{section}/{seat_number}"),
                section_id=section_id,
                row_number=1,
                seat_number=seat_number,
            )

    for index, category in enumerate((EventCategory.CONCERT, EventCategory.CONFERENCE)):
        event_id = demo_id(f"event/{category.value}")
        add(
            Event,
            id=event_id,
            organizer_id=organizer_id,
            venue_id=venue_id,
            title=f"TicketFlow Demo {category.value.title()}",
            description="Демонстрационное событие для разработки.",
            category=category,
            event_date=datetime.now(UTC) + timedelta(days=30 + index),
            status=EventStatus.PUBLISHED,
        )
        for section, price in (("VIP", Decimal("15000.00")), ("Standard", Decimal("7500.00"))):
            section_id = demo_id(f"section/{section}")
            ticket_type_id = demo_id(f"tariff/{category.value}/{section}")
            add(
                TicketType,
                id=ticket_type_id,
                event_id=event_id,
                venue_id=venue_id,
                section_id=section_id,
                name=section,
                price=price,
            )
            for seat_number in range(1, 7):
                add(
                    EventSeat,
                    event_id=event_id,
                    seat_id=demo_id(f"seat/{section}/{seat_number}"),
                    section_id=section_id,
                    ticket_type_id=ticket_type_id,
                )

    return counts


def main() -> None:
    engine = build_engine(Settings())
    try:
        with engine.begin() as connection:
            counts = seed_demo(connection)
        print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
