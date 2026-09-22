from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from ticketflow.models import Event, EventStatus
from ticketflow.seed import demo_id

pytestmark = pytest.mark.integration


def setup_event(api, headers, capacity=2):
    venue = api.post(
        "/venues",
        headers=headers,
        json={"name": "Hall", "city": "Astana", "address": "One", "capacity": capacity},
    )
    assert venue.status_code == 201, venue.text
    venue = venue.json()["id"]
    section = api.post(f"/venues/{venue}/sections", headers=headers, json={"name": "Main"}).json()[
        "id"
    ]
    seats = api.post(
        f"/venues/{venue}/sections/{section}/seats",
        headers=headers,
        json=[{"row_number": 1, "seat_number": i + 1} for i in range(capacity)],
    )
    assert seats.status_code == 201, seats.text
    event = api.post(
        "/events",
        headers=headers,
        json={
            "venue_id": venue,
            "title": "New concert",
            "category": "concert",
            "event_date": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        },
    )
    assert event.status_code == 201, event.text
    return venue, section, seats.json(), event.json()["id"]


def test_create_publish_and_freeze_catalog(api, api_headers):
    headers = api_headers["organizer"]
    venue, section, seats, event = setup_event(api, headers)
    assert api.get(f"/events/{event}").status_code == 404
    assert (
        api.patch(f"/events/{event}", headers=headers, json={"status": "PUBLISHED"}).status_code
        == 409
    )
    tariff = api.post(
        f"/events/{event}/ticket-types",
        headers=headers,
        json={"section_id": section, "name": "Standard", "price": "1200.50"},
    )
    assert tariff.status_code == 201, tariff.text
    assert (
        api.patch(f"/events/{event}", headers=headers, json={"status": "PUBLISHED"}).status_code
        == 200
    )
    assert api.get(f"/events/{event}").status_code == 200
    response = api.get(f"/events/{event}/seats")
    assert response.status_code == 200, response.text
    assert len(response.json()) == 2
    assert all(
        row["status"] == "available" and row["price"] == "1200.50" for row in response.json()
    )
    assert api.patch(f"/venues/{venue}", headers=headers, json={"capacity": 10}).status_code == 409
    assert api.delete(f"/venues/{venue}/seats/{seats[0]['id']}", headers=headers).status_code == 409
    assert (
        api.patch(
            f"/events/{event}/ticket-types/{tariff.json()['id']}",
            headers=headers,
            json={"price": "1"},
        ).status_code
        == 409
    )
    assert (
        api.patch(
            f"/events/{event}",
            headers=headers,
            json={"event_date": (datetime.now(UTC) + timedelta(days=5)).isoformat()},
        ).status_code
        == 409
    )
    assert api.delete(f"/events/{event}", headers=headers).status_code == 409
    assert (
        api.patch(f"/events/{event}", headers=headers, json={"title": "Corrected"}).status_code
        == 200
    )


def test_filters_combine_and_validate_ranges(api):
    response = api.get(
        "/events",
        params={
            "city": "almaty",
            "category": "concert",
            "min_price": "7000",
            "max_price": "8000",
            "q": "Demo",
            "date_from": datetime.now(UTC).isoformat(),
            "date_to": (datetime.now(UTC) + timedelta(days=40)).isoformat(),
        },
    )
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()] == [str(demo_id("event/concert"))]
    assert api.get("/events?min_price=8001&max_price=14999").json() == []
    assert api.get("/events?q=%25").json() == []
    assert api.get("/events?min_price=2&max_price=1").status_code == 422
    assert api.get("/events?min_price=NaN").status_code == 422
    assert (
        api.get("/events?date_from=2030-01-01T00:00:00Z&date_to=2020-01-01T00:00:00Z").status_code
        == 422
    )
    assert len(api.get("/events?limit=1&offset=1").json()) == 1


@pytest.mark.parametrize("status", [EventStatus.DRAFT, EventStatus.CANCELLED, EventStatus.FINISHED])
def test_non_public_events_hidden(api, migrated_engine, status):
    with migrated_engine.begin() as connection:
        connection.execute(update(Event).values(status=status))
    assert api.get("/events").json() == []
    assert api.get(f"/events/{demo_id('event/concert')}").status_code == 404
    assert api.get(f"/events/{demo_id('event/concert')}/seats").status_code == 404


def test_started_events_hidden(api, migrated_engine):
    with migrated_engine.begin() as connection:
        connection.execute(update(Event).values(event_date=datetime.now(UTC) - timedelta(hours=1)))
    assert api.get("/events").json() == []


@pytest.mark.parametrize("actor,status", [("customer", 403), ("outsider", 404)])
def test_writes_require_role_and_ownership(api, api_headers, actor, status):
    headers = api_headers[actor]
    event, venue = demo_id("event/concert"), demo_id("venue")
    assert (
        api.patch(f"/events/{event}", headers=headers, json={"title": "Hacked"}).status_code
        == status
    )
    assert api.delete(f"/events/{event}", headers=headers).status_code == status
    assert (
        api.patch(f"/venues/{venue}", headers=headers, json={"name": "Hacked"}).status_code
        == status
    )
    assert (
        api.post(
            f"/events/{event}/ticket-types",
            headers=headers,
            json={"section_id": str(demo_id("section/VIP")), "name": "Test", "price": "10"},
        ).status_code
        == status
    )


def test_capacity_uniqueness_and_atomic_batch(api, api_headers):
    headers = api_headers["organizer"]
    venue, section, seats, event = setup_event(api, headers)
    path = f"/venues/{venue}/sections/{section}/seats"
    assert (
        api.post(path, headers=headers, json=[{"row_number": 2, "seat_number": 1}]).status_code
        == 409
    )
    assert api.patch(f"/venues/{venue}", headers=headers, json={"capacity": 1}).status_code == 409
    assert api.patch(f"/venues/{venue}", headers=headers, json={"capacity": 5}).status_code == 200
    assert (
        api.post(
            path,
            headers=headers,
            json=[{"row_number": 2, "seat_number": 1}, {"row_number": 1, "seat_number": 1}],
        ).status_code
        == 409
    )
    assert len(api.get(path, headers=headers).json()) == 2
    assert (
        api.post(f"/venues/{venue}/sections", headers=headers, json={"name": "Main"}).status_code
        == 409
    )
    assert api.post(path, headers=headers, json=[]).status_code == 422


def test_draft_edit_tariff_assignment_and_delete(api, api_headers):
    headers = api_headers["organizer"]
    venue, section, seats, event = setup_event(api, headers)

    def tariff(name, price):
        response = api.post(
            f"/events/{event}/ticket-types",
            headers=headers,
            json={"section_id": section, "name": name, "price": price},
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    first, second = tariff("Standard", "1"), tariff("VIP", "2")
    assert (
        api.put(
            f"/events/{event}/seats/{seats[0]['id']}/tariff",
            headers=headers,
            json={"ticket_type_id": second},
        ).status_code
        == 204
    )
    assert (
        api.put(
            f"/events/{event}/seats/{seats[0]['id']}/tariff",
            headers=headers,
            json={"ticket_type_id": str(demo_id("tariff/concert/VIP"))},
        ).status_code
        == 404
    )
    assert (
        api.patch(
            f"/events/{event}/ticket-types/{first}", headers=headers, json={"price": "3.25"}
        ).status_code
        == 200
    )
    assert api.delete(f"/events/{event}/ticket-types/{second}", headers=headers).status_code == 204
    assert (
        api.patch(
            f"/venues/{venue}/seats/{seats[0]['id']}",
            headers=headers,
            json={"row_number": 5, "seat_number": 1},
        ).status_code
        == 200
    )
    assert (
        api.patch(
            f"/venues/{venue}/sections/{section}", headers=headers, json={"name": "Renamed"}
        ).status_code
        == 200
    )
    assert api.delete(f"/venues/{venue}/seats/{seats[1]['id']}", headers=headers).status_code == 204
    assert api.delete(f"/venues/{venue}/sections/{section}", headers=headers).status_code == 409
    assert api.delete(f"/venues/{venue}", headers=headers).status_code == 409
    assert api.delete(f"/events/{event}", headers=headers).status_code == 204
    assert api.delete(f"/venues/{venue}/sections/{section}", headers=headers).status_code == 204
    assert api.delete(f"/venues/{venue}", headers=headers).status_code == 204


def test_input_and_admin_ownership(api, api_headers, migrated_engine):
    headers = api_headers["admin"]
    data = {
        "venue_id": str(demo_id("venue")),
        "title": "Admin event",
        "category": "other",
        "event_date": (datetime.now(UTC) + timedelta(days=1)).isoformat(),
    }
    created = api.post("/events", headers=headers, json=data)
    assert created.status_code == 201, created.text
    assert created.json()["organizer_id"] == str(demo_id("organizer"))
    assert (
        api.patch(
            f"/events/{created.json()['id']}", headers=headers, json={"title": None}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/events", headers=headers, json={**data, "event_date": "2030-01-01T00:00:00"}
        ).status_code
        == 422
    )
    assert (
        api.post(
            "/events", headers=headers, json={**data, "organizer_id": str(uuid4())}
        ).status_code
        == 422
    )
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(select(Event.title).where(Event.id == demo_id("event/concert")))
            != "Hacked"
        )
