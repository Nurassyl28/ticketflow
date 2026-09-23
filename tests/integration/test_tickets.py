from uuid import uuid4
from xml.etree import ElementTree

import pytest
from sqlalchemy import func, select, update

from tests.integration.helpers import race
from tests.integration.test_checkout import new_order, pay
from ticketflow.models import AuditLog, Event, EventStatus, Order, OrderStatus, Ticket, TicketStatus
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


@pytest.fixture
def bought_ticket(api, api_headers):
    order = new_order(api, api_headers["customer"])
    response = pay(api, api_headers["customer"], order)
    assert response.status_code == 200, response.text
    return response.json()["tickets"][0]


def scan(api, headers, ticket, event=None):
    return api.post(
        "/tickets/validate",
        headers=headers,
        json={"event_id": event or ticket["event_id"], "qr_code": ticket["qr_code"]},
    )


def test_ticket_retrieval_and_qr_svg(api, api_headers, bought_ticket):
    headers = api_headers["customer"]
    ticket = bought_ticket
    response = api.get("/tickets/me", headers=headers)
    assert response.json() == [ticket]
    assert response.headers["cache-control"] == "no-store"
    assert api.get(f"/tickets/{ticket['id']}", headers=headers).json() == ticket
    qr = api.get(f"/tickets/{ticket['id']}/qr", headers=headers)
    assert qr.status_code == 200 and qr.headers["content-type"] == "image/svg+xml"
    assert qr.headers["cache-control"] == "no-store"
    root = ElementTree.fromstring(qr.content)
    assert root.tag == "{http://www.w3.org/2000/svg}svg"
    assert root.findall(".//{http://www.w3.org/2000/svg}path")
    assert "@example.com" not in qr.text


@pytest.mark.parametrize("actor", ["buyer2", "organizer", "admin"])
def test_ticket_contents_only_available_to_owner(api, api_headers, bought_ticket, actor):
    assert api.get("/tickets/me", headers=api_headers[actor]).json() == []
    for suffix in ("", "/qr"):
        assert (
            api.get(
                f"/tickets/{bought_ticket['id']}{suffix}", headers=api_headers[actor]
            ).status_code
            == 404
        )


@pytest.mark.parametrize("actor,status", [("customer", 403), ("outsider", 404)])
def test_scan_requires_event_manager(api, api_headers, bought_ticket, actor, status):
    assert scan(api, api_headers[actor], bought_ticket).status_code == status


def test_wrong_event_and_unknown_token(api, api_headers, bought_ticket):
    headers = api_headers["organizer"]
    assert scan(api, headers, bought_ticket, str(demo_id("event/conference"))).status_code == 404
    assert scan(api, headers, {**bought_ticket, "qr_code": str(uuid4())}).status_code == 404
    assert scan(api, headers, bought_ticket).status_code == 200
    again = scan(api, headers, bought_ticket)
    assert again.status_code == 403 and again.json()["detail"] == "Ticket already used"
    assert (
        api.get(f"/tickets/{bought_ticket['id']}/qr", headers=api_headers["customer"]).status_code
        == 409
    )


@pytest.mark.parametrize("invalid", ["ticket", "order", "event"])
def test_invalid_admission_states(api, api_headers, bought_ticket, migrated_engine, invalid):
    with migrated_engine.begin() as connection:
        if invalid == "ticket":
            connection.execute(update(Ticket).values(status=TicketStatus.CANCELLED))
        elif invalid == "order":
            connection.execute(update(Order).values(status=OrderStatus.REFUNDED))
        else:
            connection.execute(update(Event).values(status=EventStatus.CANCELLED))
    assert scan(api, api_headers["organizer"], bought_ticket).status_code == 403


def test_concurrent_scan_has_one_success_and_one_audit(
    api, api_headers, bought_ticket, migrated_engine
):
    responses = race(
        lambda: scan(api, api_headers["organizer"], bought_ticket),
        lambda: scan(api, api_headers["admin"], bought_ticket),
    )
    assert sorted(response.status_code for response in responses) == [200, 403]
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(Ticket.status)) == TicketStatus.USED
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "ORGANIZER_VALIDATED_TICKET")
            )
            == 1
        )
