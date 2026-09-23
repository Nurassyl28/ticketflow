from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Event as ThreadEvent
from time import monotonic

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError

from scripts.demo import run_scenario
from tests.integration.helpers import old_reservation, race
from tests.integration.test_checkout import new_order, pay
from tests.integration.test_reservations import book
from ticketflow.models import (
    Event,
    Order,
    OrderStatus,
    Payment,
    ReservationStatus,
    Ticket,
    TicketReservation,
)
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


def test_complete_demo_through_api(api, migrated_engine, tmp_path):
    result = run_scenario(api, migrated_engine, tmp_path)
    assert result["status"] == "passed" and len(result["steps"]) == 12
    assert (tmp_path / "ticket.svg").read_bytes().startswith(b"<?xml")
    assert "Bearer" not in (tmp_path / "report.json").read_text()


def test_openapi_exposes_complete_contract(api):
    response = api.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for method, path in [
        ("get", "/events"),
        ("post", "/events"),
        ("patch", "/events/{event_id}"),
        ("delete", "/events/{event_id}"),
        ("get", "/events/{event_id}/seats"),
        ("post", "/reservations"),
        ("post", "/orders"),
        ("get", "/orders/me"),
        ("post", "/payments/mock"),
        ("post", "/tickets/validate"),
        ("get", "/organizer/dashboard"),
        ("get", "/admin/audit-logs"),
    ]:
        assert method in paths[path]
    assert paths["/payments/mock"]["post"]["security"]
    assert "image/svg+xml" in paths["/tickets/{ticket_id}/qr"]["get"]["responses"]["200"]["content"]


@pytest.mark.parametrize("operation", ["pay", "reserve"])
def test_expiry_checked_after_waiting_for_event_lock(api, api_headers, migrated_engine, operation):
    group = old_reservation(migrated_engine, expires_in=1.5)
    order = (
        api.post(
            "/orders", headers=api_headers["customer"], json={"reservation_id": str(group)}
        ).json()
        if operation == "pay"
        else None
    )
    with migrated_engine.connect() as blocker:
        transaction = blocker.begin()
        blocker.execute(select(Event).where(Event.id == demo_id("event/concert")).with_for_update())
        blocker_pid = blocker.scalar(select(func.pg_backend_pid()))
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lambda: (
                    pay(api, api_headers["customer"], order)
                    if operation == "pay"
                    else book(api, api_headers["buyer2"])
                )
            )
            try:
                deadline = monotonic() + 5
                blocked = False
                while monotonic() < deadline:
                    with migrated_engine.connect() as observer:
                        blocked = observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE :pid = ANY(pg_blocking_pids(pid)))"
                            ),
                            {"pid": blocker_pid},
                        )
                    if blocked:
                        break
                    ThreadEvent().wait(0.01)
                assert blocked, "Request never waited for the event lock"
                blocker.execute(select(func.pg_sleep(1.6)))
            finally:
                transaction.commit()
            response = future.result(timeout=10)
    assert response.status_code == (409 if operation == "pay" else 201), response.text
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(func.count()).select_from(Payment)) == 0
        if operation == "pay":
            assert connection.scalar(select(Order.status)) == OrderStatus.CANCELLED


def test_ticket_insert_failure_rolls_back_entire_payment(api, api_headers, migrated_engine):
    order = new_order(api, api_headers["customer"])
    with migrated_engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE FUNCTION reject_ticket() RETURNS trigger LANGUAGE plpgsql AS $$ "
            "BEGIN RAISE EXCEPTION 'test ticket storage failure'; END $$"
        )
        connection.exec_driver_sql(
            "CREATE TRIGGER reject_ticket BEFORE INSERT ON tickets "
            "FOR EACH ROW EXECUTE FUNCTION reject_ticket()"
        )
    with pytest.raises(DBAPIError):
        pay(api, api_headers["customer"], order)
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(Order.status)) == OrderStatus.PENDING
        assert connection.scalar(select(TicketReservation.status)) == ReservationStatus.ACTIVE
        assert connection.scalar(select(func.count()).select_from(Payment)) == 0
        assert connection.scalar(select(func.count()).select_from(Ticket)) == 0


def test_concurrent_layout_capacity(api, api_headers):
    headers = api_headers["organizer"]
    venue = api.post(
        "/venues",
        headers=headers,
        json={"name": "Small", "address": "One", "city": "Almaty", "capacity": 1},
    ).json()["id"]
    section = api.post(f"/venues/{venue}/sections", headers=headers, json={"name": "Main"}).json()[
        "id"
    ]
    path = f"/venues/{venue}/sections/{section}/seats"
    responses = race(
        lambda: api.post(path, headers=headers, json=[{"row_number": 1, "seat_number": 1}]),
        lambda: api.post(path, headers=headers, json=[{"row_number": 1, "seat_number": 2}]),
    )
    assert sorted(response.status_code for response in responses) == [201, 409]
    assert len(api.get(path, headers=headers).json()) == 1


def test_publication_races_layout_change_safely(api, api_headers):
    headers = api_headers["organizer"]
    venue = api.post(
        "/venues",
        headers=headers,
        json={"name": "Small", "address": "One", "city": "Almaty", "capacity": 2},
    ).json()["id"]
    section = api.post(f"/venues/{venue}/sections", headers=headers, json={"name": "Main"}).json()[
        "id"
    ]
    path = f"/venues/{venue}/sections/{section}/seats"
    assert (
        api.post(path, headers=headers, json=[{"row_number": 1, "seat_number": 1}]).status_code
        == 201
    )
    event = api.post(
        "/events",
        headers=headers,
        json={
            "venue_id": venue,
            "title": "Concurrent",
            "category": "other",
            "event_date": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
        },
    ).json()["id"]
    assert (
        api.post(
            f"/events/{event}/ticket-types",
            headers=headers,
            json={"section_id": section, "name": "Standard", "price": "10"},
        ).status_code
        == 201
    )
    published, added = race(
        lambda: api.patch(f"/events/{event}", headers=headers, json={"status": "PUBLISHED"}),
        lambda: api.post(path, headers=headers, json=[{"row_number": 1, "seat_number": 2}]),
    )
    assert published.status_code == 200 and added.status_code in (201, 409)
    assert len(api.get(f"/events/{event}/seats").json()) == len(
        api.get(path, headers=headers).json()
    )
