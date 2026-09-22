from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Connection, delete, insert, select, text, update
from sqlalchemy.exc import IntegrityError

from ticketflow.models import (
    AuditLog,
    Base,
    Event,
    EventSeat,
    Order,
    OrderItem,
    ReservationGroup,
    ReservationStatus,
    Seat,
    Section,
    Ticket,
    TicketReservation,
    TicketStatus,
    TicketType,
    User,
    Venue,
)
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


def add(connection: Connection, model: type[Base], **values: object) -> dict:
    statement = insert(model).values(**values).returning(*model.__table__.columns)
    return dict(connection.execute(statement).mappings().one())


def rejects(connection: Connection, statement, constraint: str) -> None:
    with pytest.raises(IntegrityError) as error, connection.begin_nested():
        connection.execute(statement)
    assert error.value.orig.diag.constraint_name == constraint


def group(connection: Connection, event: str = "concert") -> dict:
    return add(
        connection,
        ReservationGroup,
        user_id=demo_id("customer"),
        event_id=demo_id(f"event/{event}"),
    )


def reservation_values(booking: dict, seat: str = "VIP/1") -> dict:
    return {
        "group_id": booking["id"],
        "user_id": booking["user_id"],
        "event_id": booking["event_id"],
        "seat_id": demo_id(f"seat/{seat}"),
        "expires_at": booking["expires_at"],
    }


def purchase(connection: Connection, seat: str = "VIP/1", event: str = "concert") -> dict:
    booking = group(connection, event)
    reservation = add(
        connection,
        TicketReservation,
        **reservation_values(booking, seat),
        status=ReservationStatus.COMPLETED,
    )
    order = add(
        connection,
        Order,
        reservation_group_id=booking["id"],
        user_id=booking["user_id"],
        event_id=booking["event_id"],
        total_amount=Decimal("15000.00"),
    )
    item = add(
        connection,
        OrderItem,
        order_id=order["id"],
        reservation_id=reservation["id"],
        reservation_group_id=booking["id"],
        event_id=booking["event_id"],
        seat_id=reservation["seat_id"],
        ticket_type_id=demo_id(f"tariff/{event}/VIP"),
        ticket_type_name="VIP",
        unit_price=Decimal("15000.00"),
    )
    return {"group": booking, "reservation": reservation, "order": order, "item": item}


def ticket_values(purchased: dict) -> dict:
    item = purchased["item"]
    return {
        "order_item_id": item["id"],
        **{key: item[key] for key in ("order_id", "event_id", "seat_id")},
    }


def test_email_is_unique_ignoring_case(catalog: Connection) -> None:
    rejects(
        catalog,
        insert(User).values(email="CUSTOMER@DEMO.TICKETFLOW.INVALID", password_hash="!"),
        "uq_users_email_lower",
    )


def test_seat_positions_are_unique_within_section(catalog: Connection) -> None:
    rejects(
        catalog,
        insert(Seat).values(section_id=demo_id("section/VIP"), row_number=1, seat_number=1),
        "uq_seats_position",
    )


@pytest.mark.parametrize(
    ("model", "column", "value", "constraint"),
    [
        (Venue, "capacity", 0, "ck_venues_capacity_positive"),
        (Seat, "row_number", 0, "ck_seats_row_positive"),
        (Seat, "seat_number", -1, "ck_seats_number_positive"),
        (TicketType, "currency", "USD", "ck_ticket_types_currency_kzt"),
    ],
)
def test_invalid_catalog_values_are_rejected(catalog, model, column, value, constraint) -> None:
    rejects(catalog, update(model).values({column: value}), constraint)


def test_event_must_use_its_organizers_venue(catalog: Connection) -> None:
    rejects(
        catalog,
        update(Event).values(organizer_id=demo_id("customer")),
        "fk_events_venue_id_venues",
    )


def test_tariff_section_must_belong_to_event_venue(catalog: Connection) -> None:
    venue = add(
        catalog,
        Venue,
        organizer_id=demo_id("organizer"),
        name="Other",
        address="Other",
        city="Other",
        capacity=1,
    )
    section = add(catalog, Section, venue_id=venue["id"], name="Other")
    rejects(
        catalog,
        insert(TicketType).values(
            event_id=demo_id("event/concert"),
            venue_id=demo_id("venue"),
            section_id=section["id"],
            name="Wrong venue",
            price=Decimal("1.00"),
        ),
        "fk_ticket_types_section_id_sections",
    )


def test_inventory_seat_and_tariff_must_share_section(catalog: Connection) -> None:
    rejects(
        catalog,
        update(EventSeat)
        .where(
            EventSeat.event_id == demo_id("event/concert"),
            EventSeat.seat_id == demo_id("seat/VIP/1"),
        )
        .values(
            section_id=demo_id("section/Standard"),
            ticket_type_id=demo_id("tariff/concert/Standard"),
        ),
        "fk_event_seats_seat_id_seats",
    )


def test_inventory_cannot_use_another_events_tariff(catalog: Connection) -> None:
    rejects(
        catalog,
        update(EventSeat)
        .where(
            EventSeat.event_id == demo_id("event/concert"),
            EventSeat.seat_id == demo_id("seat/VIP/1"),
        )
        .values(ticket_type_id=demo_id("tariff/conference/VIP")),
        "fk_event_seats_ticket_type_id_ticket_types",
    )


def test_same_seat_can_be_reserved_for_different_events(catalog: Connection) -> None:
    first = add(catalog, TicketReservation, **reservation_values(group(catalog, "concert")))
    second = add(catalog, TicketReservation, **reservation_values(group(catalog, "conference")))
    assert first["seat_id"] == second["seat_id"]
    assert first["event_id"] != second["event_id"]


def test_active_reservation_is_unique_until_status_is_expired(catalog: Connection) -> None:
    first = add(catalog, TicketReservation, **reservation_values(group(catalog)))
    next_values = reservation_values(group(catalog))
    rejects(
        catalog,
        insert(TicketReservation).values(**next_values),
        "uq_ticket_reservations_active_seat",
    )
    catalog.execute(
        update(TicketReservation)
        .where(TicketReservation.id == first["id"])
        .values(status=ReservationStatus.EXPIRED)
    )
    replacement = add(catalog, TicketReservation, **next_values)
    assert replacement["status"] == ReservationStatus.ACTIVE


@pytest.mark.parametrize("field", ["user_id", "event_id", "expires_at"])
def test_reservation_matches_group_owner_event_and_expiry(catalog: Connection, field: str) -> None:
    values = reservation_values(group(catalog))
    values[field] = {
        "user_id": demo_id("organizer"),
        "event_id": demo_id("event/conference"),
        "expires_at": values["expires_at"] + timedelta(seconds=1),
    }[field]
    rejects(
        catalog,
        insert(TicketReservation).values(**values),
        "fk_ticket_reservations_group_id_reservation_groups",
    )


def test_reservation_expiry_must_follow_creation(catalog: Connection) -> None:
    now = datetime.now(UTC)
    rejects(
        catalog,
        insert(ReservationGroup).values(
            user_id=demo_id("customer"),
            event_id=demo_id("event/concert"),
            created_at=now,
            expires_at=now,
        ),
        "ck_reservation_groups_expiry_after_creation",
    )


def test_order_is_unique_per_group_and_matches_owner(catalog: Connection) -> None:
    booking = group(catalog)
    values = {
        "reservation_group_id": booking["id"],
        "event_id": booking["event_id"],
        "user_id": booking["user_id"],
        "total_amount": Decimal("1.00"),
    }
    rejects(
        catalog,
        insert(Order).values(**{**values, "user_id": demo_id("organizer")}),
        "fk_orders_reservation_group_id_reservation_groups",
    )
    add(catalog, Order, **values)
    rejects(catalog, insert(Order).values(**values), "uq_orders_reservation_group_id")


@pytest.mark.parametrize(
    ("field", "value", "constraint"),
    [
        ("seat_id", demo_id("seat/VIP/2"), "fk_order_items_reservation_id_ticket_reservations"),
        (
            "ticket_type_id",
            demo_id("tariff/concert/Standard"),
            "fk_order_items_event_id_event_seats",
        ),
    ],
)
def test_order_item_matches_reserved_seat_and_tariff(catalog, field, value, constraint) -> None:
    purchased = purchase(catalog)
    rejects(
        catalog,
        update(OrderItem).where(OrderItem.id == purchased["item"]["id"]).values({field: value}),
        constraint,
    )


@pytest.mark.parametrize("field", ["seat_id", "event_id", "order_id"])
def test_ticket_matches_its_order_item(catalog: Connection, field: str) -> None:
    purchased = purchase(catalog)
    values = ticket_values(purchased)
    values[field] = {
        "seat_id": demo_id("seat/VIP/2"),
        "event_id": demo_id("event/conference"),
        "order_id": uuid4(),
    }[field]
    rejects(catalog, insert(Ticket).values(**values), "fk_tickets_order_item_id_order_items")


@pytest.mark.parametrize("status", [TicketStatus.VALID, TicketStatus.USED])
def test_live_ticket_blocks_resale_until_cancelled(
    catalog: Connection, status: TicketStatus
) -> None:
    first = add(catalog, Ticket, **ticket_values(purchase(catalog)), status=status)
    next_values = ticket_values(purchase(catalog))
    rejects(catalog, insert(Ticket).values(**next_values), "uq_tickets_live_seat")
    catalog.execute(
        update(Ticket).where(Ticket.id == first["id"]).values(status=TicketStatus.CANCELLED)
    )
    replacement = add(catalog, Ticket, **next_values)
    assert replacement["qr_code"] != first["qr_code"]


def test_qr_tokens_are_unique(catalog: Connection) -> None:
    first = add(catalog, Ticket, **ticket_values(purchase(catalog)))
    rejects(
        catalog,
        insert(Ticket).values(
            **ticket_values(purchase(catalog, seat="VIP/2")), qr_code=first["qr_code"]
        ),
        "uq_tickets_qr_code",
    )


@pytest.mark.parametrize("value", [Decimal("-0.01"), Decimal("NaN")])
@pytest.mark.parametrize(
    ("model", "column", "constraint"),
    [
        (TicketType, "price", "ck_ticket_types_price_range"),
        (Order, "total_amount", "ck_orders_total_amount_range"),
        (OrderItem, "unit_price", "ck_order_items_unit_price_range"),
    ],
)
def test_money_rejects_negative_values_and_nan(catalog, model, column, constraint, value) -> None:
    purchase(catalog)
    rejects(catalog, update(model).values({column: value}), constraint)


@pytest.mark.parametrize(
    ("table", "column", "constraint"),
    [
        ("users", "role", "ck_users_userrole"),
        ("events", "category", "ck_events_eventcategory"),
        ("events", "status", "ck_events_eventstatus"),
        ("ticket_reservations", "status", "ck_ticket_reservations_reservationstatus"),
        ("orders", "status", "ck_orders_orderstatus"),
        ("tickets", "status", "ck_tickets_ticketstatus"),
    ],
)
def test_statuses_are_enforced_in_postgres(catalog, table, column, constraint) -> None:
    add(catalog, Ticket, **ticket_values(purchase(catalog)))
    rejects(catalog, text(f"UPDATE {table} SET {column} = 'INVALID'"), constraint)


def test_money_snapshot_and_timezone_round_trip(catalog: Connection) -> None:
    purchased = purchase(catalog)
    catalog.execute(update(TicketType).values(price=Decimal("0.10") + Decimal("0.20")))
    assert catalog.scalar(select(TicketType.price).limit(1)) == Decimal("0.30")
    assert catalog.scalar(select(OrderItem.unit_price)) == Decimal("15000.00")
    local_time = datetime(2030, 6, 1, 20, tzinfo=timezone(timedelta(hours=5)))
    catalog.execute(update(Event).values(event_date=local_time))
    assert catalog.scalar(select(Event.event_date).limit(1)) == local_time.astimezone(UTC)
    assert isinstance(purchased["order"]["id"], UUID)
    assert purchased["order"]["created_at"].utcoffset() is not None


def test_missing_parents_and_deletion_of_referenced_data_are_rejected(catalog: Connection) -> None:
    rejects(
        catalog,
        insert(Seat).values(section_id=uuid4(), row_number=1, seat_number=1),
        "fk_seats_section_id_sections",
    )
    rejects(catalog, delete(Venue), "fk_events_venue_id_venues")


def test_audit_logs_support_system_actions_and_ipv6(catalog: Connection) -> None:
    row = add(
        catalog,
        AuditLog,
        user_id=None,
        action="DEMO_CREATED",
        entity="event",
        entity_id=demo_id("event/concert"),
        ip="::1",
    )
    assert str(row["ip"]) == "::1"
    assert row["timestamp"].utcoffset() is not None
