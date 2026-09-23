from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, update
from sqlalchemy.exc import IntegrityError

from tests.integration.helpers import old_reservation, race
from tests.integration.test_reservations import book
from ticketflow.models import (
    Event,
    Order,
    OrderItem,
    OrderStatus,
    Payment,
    ReservationStatus,
    Ticket,
    TicketReservation,
)
from ticketflow.reservations import expire_reservations
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


def new_order(api, headers, seats=("seat/VIP/1",), event="event/concert"):
    reservation = book(api, headers, seats, event)
    assert reservation.status_code == 201, reservation.text
    result = api.post("/orders", headers=headers, json={"reservation_id": reservation.json()["id"]})
    assert result.status_code == 201, result.text
    return result.json()


def pay(api, headers, order, key=None):
    return api.post(
        "/payments/mock",
        headers=headers,
        json={"order_id": order["id"], "idempotency_key": str(key or uuid4())},
    )


def test_full_purchase_snapshot_and_idempotency(api, api_headers, migrated_engine):
    headers = api_headers["customer"]
    reservation = book(api, headers, ("seat/VIP/1", "seat/Standard/1")).json()
    order = api.post("/orders", headers=headers, json={"reservation_id": reservation["id"]}).json()
    assert order["total_amount"] == "22500.00"
    repeated = api.post("/orders", headers=headers, json={"reservation_id": reservation["id"]})
    assert repeated.status_code == 200 and repeated.json() == order
    items = api.get(f"/orders/{order['id']}/items", headers=headers).json()
    assert len(items) == 2 and {row["ticket_type_name"] for row in items} == {"VIP", "Standard"}
    key = uuid4()
    response = pay(api, headers, order, key)
    assert response.status_code == 200, response.text
    payment = response.json()
    assert payment["amount"] == "22500.00" and payment["status"] == "SUCCEEDED"
    assert len(payment["tickets"]) == 2
    assert len({ticket["qr_code"] for ticket in payment["tickets"]}) == 2
    assert pay(api, headers, order, key).json() == payment
    assert pay(api, headers, order).status_code == 409
    assert api.get(f"/orders/{order['id']}", headers=headers).json()["status"] == "PAID"
    assert (
        api.get(f"/reservations/{reservation['id']}", headers=headers).json()["status"]
        == "COMPLETED"
    )
    assert book(api, api_headers["buyer2"]).status_code == 409
    seats = api.get(f"/events/{demo_id('event/concert')}/seats").json()
    assert sum(row["status"] == "sold" for row in seats) == 2
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Payment)) == 1


def test_checkout_rejects_ownership_and_price_injection(api, api_headers):
    order = new_order(api, api_headers["customer"])
    assert pay(api, api_headers["buyer2"], order).status_code == 404
    assert pay(api, api_headers["admin"], order).status_code == 404
    assert api.get(f"/orders/{order['id']}/items", headers=api_headers["buyer2"]).status_code == 404
    assert (
        api.post(
            "/payments/mock",
            headers=api_headers["customer"],
            json={"order_id": order["id"], "idempotency_key": str(uuid4()), "amount": 0},
        ).status_code
        == 422
    )
    reservation = book(api, api_headers["customer"], ("seat/VIP/2",)).json()
    assert (
        api.post(
            "/orders", headers=api_headers["buyer2"], json={"reservation_id": reservation["id"]}
        ).status_code
        == 404
    )


def test_concurrent_order_creation_is_unique(api, api_headers, migrated_engine):
    headers = api_headers["customer"]
    reservation = book(api, headers).json()

    def create():
        return api.post("/orders", headers=headers, json={"reservation_id": reservation["id"]})

    first, second = race(create, create)
    assert sorted([first.status_code, second.status_code]) == [200, 201]
    assert first.json()["id"] == second.json()["id"]
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(OrderItem)) == 1


@pytest.mark.parametrize("same_key", [True, False])
def test_concurrent_payment_creates_one_ticket(api, api_headers, migrated_engine, same_key):
    headers = api_headers["customer"]
    order = new_order(api, headers)
    first_key = uuid4()
    responses = race(
        lambda: pay(api, headers, order, first_key),
        lambda: pay(api, headers, order, first_key if same_key else uuid4()),
    )
    assert sorted(response.status_code for response in responses) == (
        [200, 200] if same_key else [200, 409]
    )
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Payment)) == 1
        assert connection.scalar(select(func.count()).select_from(Ticket)) == 1
        assert connection.scalar(select(TicketReservation.status)) == ReservationStatus.COMPLETED


def test_key_cannot_pay_two_orders_even_across_events(api, api_headers, migrated_engine):
    headers = api_headers["customer"]
    first = new_order(api, headers)
    second = new_order(api, headers, event="event/conference")
    key = uuid4()
    responses = race(lambda: pay(api, headers, first, key), lambda: pay(api, headers, second, key))
    assert sorted(response.status_code for response in responses) == [200, 409]
    with migrated_engine.connect() as connection:
        assert sorted(connection.scalars(select(Order.status)).all()) == [
            OrderStatus.PAID,
            OrderStatus.PENDING,
        ]
        assert connection.scalar(select(func.count()).select_from(Ticket)) == 1


def test_expired_group_cannot_create_order(api, api_headers, migrated_engine):
    group = old_reservation(migrated_engine)
    response = api.post(
        "/orders", headers=api_headers["customer"], json={"reservation_id": str(group)}
    )
    assert response.status_code == 409
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Order)) == 0


def test_expired_order_cannot_be_paid_and_worker_agrees(api, api_headers, migrated_engine):
    group = old_reservation(migrated_engine)
    with migrated_engine.begin() as connection:
        order_id = connection.scalar(
            insert(Order)
            .values(
                user_id=demo_id("customer"),
                event_id=demo_id("event/concert"),
                reservation_group_id=group,
                total_amount=15000,
            )
            .returning(Order.id)
        )
    response, _ = race(
        lambda: pay(api, api_headers["customer"], {"id": str(order_id)}),
        lambda: expire_reservations(migrated_engine),
    )
    assert response.status_code == 409
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(Order.status)) == OrderStatus.CANCELLED
        assert connection.scalar(select(func.count()).select_from(Payment)) == 0
        assert connection.scalar(select(func.count()).select_from(Ticket)) == 0


def test_started_event_cannot_be_paid(api, api_headers, migrated_engine):
    order = new_order(api, api_headers["customer"])
    with migrated_engine.begin() as connection:
        connection.execute(
            update(Event).values(event_date=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert pay(api, api_headers["customer"], order).status_code == 409


def test_payment_constraints_reject_invalid_refund_state(api, api_headers, migrated_engine):
    order = new_order(api, api_headers["customer"])
    assert pay(api, api_headers["customer"], order).status_code == 200
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(update(Payment).values(status="REFUNDED", refunded_at=None))
    with pytest.raises(IntegrityError), migrated_engine.begin() as connection:
        connection.execute(update(Payment).values(user_id=demo_id("buyer2")))


def test_amount_overflow_does_not_create_partial_order(api, api_headers, migrated_engine):
    from ticketflow.models import TicketType

    with migrated_engine.begin() as connection:
        connection.execute(update(TicketType).values(price="9999999999.99"))
    reservation = book(api, api_headers["customer"], ("seat/VIP/1", "seat/VIP/2")).json()
    assert (
        api.post(
            "/orders", headers=api_headers["customer"], json={"reservation_id": reservation["id"]}
        ).status_code
        == 409
    )
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Order)) == 0
