from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from tests.integration.helpers import race
from tests.integration.test_checkout import new_order, pay
from tests.integration.test_reservations import book
from tests.integration.test_tickets import scan
from ticketflow.models import (
    AuditLog,
    Event,
    EventStatus,
    Order,
    OrderStatus,
    Payment,
    ReservationStatus,
    Ticket,
    TicketReservation,
    TicketStatus,
)
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


def cancel(api, headers, order):
    return api.post(f"/orders/{order['id']}/cancel", headers=headers)


def cancel_event(api, api_headers):
    return api.post(f"/events/{demo_id('event/concert')}/cancel", headers=api_headers["admin"])


@pytest.mark.parametrize("paid", [False, True])
def test_cancel_is_repeatable_and_releases_seat(api, api_headers, migrated_engine, paid):
    headers = api_headers["customer"]
    order = new_order(api, headers)
    key = uuid4()
    if paid:
        assert pay(api, headers, order, key).status_code == 200
    first = cancel(api, headers, order)
    assert first.status_code == 200, first.text
    assert first.json()["status"] == ("REFUNDED" if paid else "CANCELLED")
    assert cancel(api, headers, order).json() == first.json()
    assert pay(api, headers, order).status_code == 409
    if paid:
        replay = pay(api, headers, order, key).json()
        assert replay["status"] == "REFUNDED" and replay["tickets"][0]["status"] == "CANCELLED"
        assert scan(api, api_headers["organizer"], replay["tickets"][0]).status_code == 403
    replacement = new_order(api, api_headers["buyer2"])
    assert pay(api, api_headers["buyer2"], replacement).status_code == 200
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "CUSTOMER_CANCELLED_ORDER")
            )
            == 1
        )
        if paid:
            refunded = connection.execute(
                select(Payment.refunded_at, Payment.created_at).where(Payment.status == "REFUNDED")
            ).one()
            assert refunded.refunded_at >= refunded.created_at


def test_cancel_requires_owner_and_unused_tickets(api, api_headers):
    order = new_order(api, api_headers["customer"])
    ticket = pay(api, api_headers["customer"], order).json()["tickets"][0]
    assert cancel(api, api_headers["buyer2"], order).status_code == 404
    assert cancel(api, api_headers["admin"], order).status_code == 404
    assert scan(api, api_headers["organizer"], ticket).status_code == 200
    assert cancel(api, api_headers["customer"], order).status_code == 409


def test_paid_order_cannot_be_cancelled_after_start(api, api_headers, migrated_engine):
    order = new_order(api, api_headers["customer"])
    assert pay(api, api_headers["customer"], order).status_code == 200
    with migrated_engine.begin() as connection:
        connection.execute(
            update(Event).values(event_date=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert cancel(api, api_headers["customer"], order).status_code == 409


def test_admin_cancels_event_orders_reservations_and_used_tickets(
    api, api_headers, migrated_engine
):
    paid = new_order(api, api_headers["customer"])
    ticket = pay(api, api_headers["customer"], paid).json()["tickets"][0]
    assert scan(api, api_headers["organizer"], ticket).status_code == 200
    new_order(api, api_headers["buyer2"], ("seat/VIP/2",))
    assert book(api, api_headers["customer"], ("seat/VIP/3",)).status_code == 201
    for actor in ("organizer", "customer"):
        assert (
            api.post(
                f"/events/{demo_id('event/concert')}/cancel", headers=api_headers[actor]
            ).status_code
            == 403
        )
    first = cancel_event(api, api_headers)
    assert first.status_code == 200 and first.json()["status"] == "CANCELLED"
    assert cancel_event(api, api_headers).json() == first.json()
    assert book(api, api_headers["customer"]).status_code == 409
    assert api.get(f"/events/{demo_id('event/concert')}").status_code == 404
    with migrated_engine.connect() as connection:
        assert sorted(connection.scalars(select(Order.status)).all()) == [
            OrderStatus.CANCELLED,
            OrderStatus.REFUNDED,
        ]
        assert connection.scalar(select(Ticket.status)) == TicketStatus.CANCELLED
        assert connection.scalar(select(Payment.status)) == "REFUNDED"
        assert (
            connection.scalar(
                select(func.count())
                .select_from(TicketReservation)
                .where(TicketReservation.status == ReservationStatus.ACTIVE)
            )
            == 0
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "ADMIN_CANCELLED_EVENT")
            )
            == 1
        )


def test_finished_event_preserves_history(api, api_headers, migrated_engine):
    with migrated_engine.begin() as connection:
        connection.execute(update(Event).values(status=EventStatus.FINISHED))
    assert cancel_event(api, api_headers).status_code == 409


def test_concurrent_refunds_record_once(api, api_headers, migrated_engine):
    headers = api_headers["customer"]
    order = new_order(api, headers)
    assert pay(api, headers, order).status_code == 200
    responses = race(lambda: cancel(api, headers, order), lambda: cancel(api, headers, order))
    assert [response.status_code for response in responses] == [200, 200]
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count()).select_from(Payment).where(Payment.status == "REFUNDED")
            )
            == 1
        )
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "CUSTOMER_CANCELLED_ORDER")
            )
            == 1
        )


def test_event_cancellation_races_payment_safely(api, api_headers, migrated_engine):
    order = new_order(api, api_headers["customer"])
    cancelled, paid = race(
        lambda: cancel_event(api, api_headers), lambda: pay(api, api_headers["customer"], order)
    )
    assert cancelled.status_code == 200 and paid.status_code in (200, 409)
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(Order.status)) in (
            OrderStatus.CANCELLED,
            OrderStatus.REFUNDED,
        )
        assert all(
            status == TicketStatus.CANCELLED for status in connection.scalars(select(Ticket.status))
        )
        assert all(status == "REFUNDED" for status in connection.scalars(select(Payment.status)))


def test_event_cancellation_races_scan_safely(api, api_headers, migrated_engine):
    order = new_order(api, api_headers["customer"])
    ticket = pay(api, api_headers["customer"], order).json()["tickets"][0]
    cancelled, scanned = race(
        lambda: cancel_event(api, api_headers), lambda: scan(api, api_headers["organizer"], ticket)
    )
    assert cancelled.status_code == 200 and scanned.status_code in (200, 403)
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(Ticket.status)) == TicketStatus.CANCELLED
        assert connection.scalar(select(Order.status)) == OrderStatus.REFUNDED


def test_customer_cancel_races_payment_safely(api, api_headers, migrated_engine):
    headers = api_headers["customer"]
    order = new_order(api, headers)
    cancelled, paid = race(lambda: cancel(api, headers, order), lambda: pay(api, headers, order))
    assert cancelled.status_code == 200 and paid.status_code in (200, 409)
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(Order.status)) in (
            OrderStatus.CANCELLED,
            OrderStatus.REFUNDED,
        )
        assert all(
            status == TicketStatus.CANCELLED for status in connection.scalars(select(Ticket.status))
        )
