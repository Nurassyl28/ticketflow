from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import update

from tests.integration.helpers import old_reservation
from tests.integration.test_cancellations import cancel, cancel_event
from tests.integration.test_catalog import setup_event
from tests.integration.test_checkout import new_order, pay
from tests.integration.test_reservations import book
from tests.integration.test_tickets import scan
from ticketflow.models import Payment
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


def dashboard(api, headers, **params):
    response = api.get("/organizer/dashboard", headers=headers, params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_all_metrics_follow_sale_hold_refund_and_cancellation(api, api_headers, migrated_engine):
    manager = api_headers["organizer"]
    baseline = dashboard(api, manager)
    assert baseline["tickets_sold"] == 0 and baseline["tickets_available"] == 24
    assert baseline["sales_by_ticket_type"] == baseline["sales_by_date"] == []
    order = new_order(api, api_headers["customer"], ("seat/VIP/1", "seat/Standard/1"))
    paid = pay(api, api_headers["customer"], order).json()
    assert book(api, api_headers["buyer2"], ("seat/VIP/2",)).status_code == 201
    old_reservation(migrated_engine, [demo_id("seat/VIP/3")])
    current = dashboard(api, manager)
    assert current["tickets_sold"] == current["sales_today"] == 2
    assert current["tickets_available"] == 21
    assert Decimal(current["total_revenue"]) == Decimal("22500.00")
    assert sum(row["tickets_sold"] for row in current["sales_by_ticket_type"]) == 2
    assert sum(Decimal(row["revenue"]) for row in current["sales_by_ticket_type"]) == Decimal(
        "22500"
    )
    assert current["sales_by_date"][0]["date"] == datetime.now(UTC).date().isoformat()
    assert current["currency"] == "KZT" and current["timezone"] == "UTC"
    assert dashboard(api, manager, event_id=str(demo_id("event/concert")))["tickets_available"] == 9
    assert cancel(api, api_headers["customer"], order).status_code == 200
    refunded = dashboard(api, manager)
    assert refunded["tickets_sold"] == refunded["sales_today"] == 0
    assert refunded["tickets_available"] == 23
    assert Decimal(refunded["total_revenue"]) == 0
    assert refunded["sales_by_date"] == []
    assert cancel_event(api, api_headers).status_code == 200
    assert dashboard(api, manager)["tickets_available"] == 12
    assert all(ticket["qr_code"] not in str(current) for ticket in paid["tickets"])


def test_utc_payment_dates_and_used_tickets(api, api_headers, migrated_engine):
    first = new_order(api, api_headers["customer"])
    payment = pay(api, api_headers["customer"], first).json()
    yesterday = datetime.now(UTC).replace(hour=23, minute=30, second=0, microsecond=0) - timedelta(
        days=1
    )
    with migrated_engine.begin() as connection:
        connection.execute(
            update(Payment).where(Payment.id == UUID(payment["id"])).values(created_at=yesterday)
        )
    second = new_order(api, api_headers["buyer2"], ("seat/Standard/1",))
    ticket = pay(api, api_headers["buyer2"], second).json()["tickets"][0]
    assert scan(api, api_headers["organizer"], ticket).status_code == 200
    data = dashboard(api, api_headers["organizer"])
    assert data["tickets_sold"] == 2 and data["sales_today"] == 1
    assert [row["date"] for row in data["sales_by_date"]] == [
        yesterday.date().isoformat(),
        datetime.now(UTC).date().isoformat(),
    ]
    assert [Decimal(row["revenue"]) for row in data["sales_by_date"]] == [
        Decimal("15000"),
        Decimal("7500"),
    ]


def test_dashboard_and_sales_are_owner_scoped(api, api_headers):
    foreign = setup_event(api, api_headers["outsider"])[3]
    assert api.get("/organizer/dashboard", headers=api_headers["customer"]).status_code == 403
    outsider = dashboard(api, api_headers["outsider"])
    assert outsider["tickets_available"] == outsider["tickets_sold"] == 0
    assert (
        api.get(
            "/organizer/dashboard", headers=api_headers["organizer"], params={"event_id": foreign}
        ).status_code
        == 404
    )
    assert dashboard(api, api_headers["admin"])["tickets_available"] == 24
    order = new_order(api, api_headers["customer"])
    paid = pay(api, api_headers["customer"], order).json()
    path = f"/organizer/events/{demo_id('event/concert')}/sales"
    assert api.get(path, headers=api_headers["outsider"]).status_code == 404
    response = api.get(path, headers=api_headers["organizer"])
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.json()[0]["order_status"] == "PAID"
    assert paid["tickets"][0]["qr_code"] not in response.text
    assert "@example.com" not in response.text
    assert cancel(api, api_headers["customer"], order).status_code == 200
    assert api.get(path, headers=api_headers["admin"]).json()[0]["order_status"] == "REFUNDED"
    assert api.get(path + "?limit=101", headers=api_headers["organizer"]).status_code == 422


def test_audit_is_admin_only_filtered_and_contains_no_secrets(api, api_headers):
    order = new_order(api, api_headers["customer"])
    payment = pay(api, api_headers["customer"], order).json()
    for actor in ("customer", "organizer"):
        assert api.get("/admin/audit-logs", headers=api_headers[actor]).status_code == 403
    response = api.get(
        "/admin/audit-logs",
        headers=api_headers["admin"],
        params={
            "action": "CUSTOMER_PURCHASED_TICKET",
            "entity": "order",
            "entity_id": order["id"],
            "user_id": str(demo_id("customer")),
            "date_from": (datetime.now(UTC) - timedelta(hours=1)).isoformat(),
        },
    )
    assert response.status_code == 200 and len(response.json()) == 1
    assert response.json()[0]["entity_id"] == order["id"]
    assert payment["tickets"][0]["qr_code"] not in response.text
    assert api_headers["customer"]["Authorization"] not in response.text
    assert "password" not in response.text
    assert (
        len(api.get("/admin/audit-logs?limit=1&offset=1", headers=api_headers["admin"]).json()) == 1
    )
    assert (
        api.get(
            "/admin/audit-logs?date_from=2030-01-01T00:00:00Z&date_to=2020-01-01T00:00:00Z",
            headers=api_headers["admin"],
        ).status_code
        == 422
    )
