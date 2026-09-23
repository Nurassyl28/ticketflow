from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from tests.integration.helpers import old_reservation, race
from ticketflow.models import (
    Event,
    EventStatus,
    ReservationGroup,
    ReservationStatus,
    TicketReservation,
)
from ticketflow.reservations import expire_reservations
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


def book(api, headers, seats=("seat/VIP/1",), event="event/concert"):
    return api.post(
        "/reservations",
        headers=headers,
        json={"event_id": str(demo_id(event)), "seat_ids": [str(demo_id(seat)) for seat in seats]},
    )


def test_reservation_lifetime_scope_and_availability(api, api_headers):
    response = book(api, api_headers["customer"], ("seat/VIP/1", "seat/VIP/2"))
    assert response.status_code == 201, response.text
    reservation = response.json()
    assert reservation["status"] == "ACTIVE"
    assert (
        timedelta(minutes=9, seconds=55)
        < datetime.fromisoformat(reservation["expires_at"]) - datetime.now(UTC)
        <= timedelta(minutes=10)
    )
    assert (
        api.get(f"/reservations/{reservation['id']}", headers=api_headers["customer"]).json()
        == reservation
    )
    assert (
        api.get(f"/reservations/{reservation['id']}", headers=api_headers["buyer2"]).status_code
        == 404
    )
    assert (
        api.get(f"/reservations/{reservation['id']}", headers=api_headers["admin"]).status_code
        == 404
    )
    rows = api.get(f"/events/{demo_id('event/concert')}/seats").json()
    assert sum(row["status"] == "reserved" for row in rows) == 2
    assert book(api, api_headers["customer"]).status_code == 409


def test_conflict_rolls_back_entire_group(api, api_headers, migrated_engine):
    assert book(api, api_headers["customer"]).status_code == 201
    assert book(api, api_headers["buyer2"], ("seat/VIP/2", "seat/VIP/1")).status_code == 409
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ReservationGroup)) == 1
    assert book(api, api_headers["buyer2"], ("seat/VIP/2",)).status_code == 201


@pytest.mark.parametrize(
    "seats", [[], ["seat/VIP/1", "seat/VIP/1"], ["seat/VIP/1"] * 21, ["missing"]]
)
def test_invalid_seat_selection(api, api_headers, seats):
    assert book(api, api_headers["customer"], seats).status_code == (
        404 if seats == ["missing"] else 422
    )


@pytest.mark.parametrize("state", [EventStatus.DRAFT, EventStatus.CANCELLED, EventStatus.FINISHED])
def test_closed_event_cannot_be_reserved(api, api_headers, migrated_engine, state):
    with migrated_engine.begin() as connection:
        connection.execute(update(Event).values(status=state))
    assert book(api, api_headers["customer"]).status_code == 409


def test_started_event_and_missing_event_rejected(api, api_headers, migrated_engine):
    with migrated_engine.begin() as connection:
        connection.execute(
            update(Event).values(event_date=datetime.now(UTC) - timedelta(seconds=1))
        )
    assert book(api, api_headers["customer"]).status_code == 409
    assert (
        api.post(
            "/reservations",
            headers=api_headers["customer"],
            json={"event_id": str(uuid4()), "seat_ids": [str(demo_id("seat/VIP/1"))]},
        ).status_code
        == 404
    )
    assert book(api, {}).status_code == 401


def test_expired_reservation_is_reusable_without_worker(api, api_headers, migrated_engine):
    group = old_reservation(migrated_engine)
    seats = api.get(f"/events/{demo_id('event/concert')}/seats").json()
    assert all(seat["status"] == "available" for seat in seats)
    assert book(api, api_headers["buyer2"]).status_code == 201
    assert (
        api.get(f"/reservations/{group}", headers=api_headers["customer"]).json()["status"]
        == "EXPIRED"
    )


def test_worker_is_repeatable_and_keeps_active_reservations(api, api_headers, migrated_engine):
    old_reservation(migrated_engine)
    assert (
        book(api, api_headers["buyer2"], ("seat/Standard/1",), event="event/conference").status_code
        == 201
    )
    assert expire_reservations(migrated_engine) == 1
    assert expire_reservations(migrated_engine) == 0
    with migrated_engine.connect() as connection:
        statuses = connection.scalars(select(TicketReservation.status)).all()
        assert sorted(statuses) == [ReservationStatus.ACTIVE, ReservationStatus.EXPIRED]


@pytest.mark.parametrize("reverse", [False, True])
def test_concurrent_requests_cannot_double_book(api, api_headers, migrated_engine, reverse):
    first = ("seat/VIP/1", "seat/VIP/2") if reverse else ("seat/VIP/1",)
    responses = race(
        lambda: book(api, api_headers["customer"], first),
        lambda: book(api, api_headers["buyer2"], tuple(reversed(first))),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(ReservationGroup)) == 1
        assert connection.scalar(select(func.count()).select_from(TicketReservation)) == len(first)


def test_same_seat_at_different_events_is_independent(api, api_headers):
    responses = race(
        lambda: book(api, api_headers["customer"]),
        lambda: book(api, api_headers["buyer2"], event="event/conference"),
    )
    assert [response.status_code for response in responses] == [201, 201]


def test_worker_and_booking_agree_under_concurrency(api, api_headers, migrated_engine):
    old_reservation(migrated_engine)
    response, _ = race(
        lambda: book(api, api_headers["buyer2"]), lambda: expire_reservations(migrated_engine)
    )
    assert response.status_code == 201, response.text
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(TicketReservation)
                .where(TicketReservation.status == ReservationStatus.ACTIVE)
            )
            == 1
        )
