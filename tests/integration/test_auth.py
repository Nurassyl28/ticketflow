from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.orm import Session

from ticketflow.auth.security import hash_password, token_digest
from ticketflow.config import Settings
from ticketflow.database import get_engine
from ticketflow.main import create_app
from ticketflow.manage import promote_admin
from ticketflow.models import (
    AuditLog,
    AuthSession,
    Event,
    EventCategory,
    Order,
    ReservationGroup,
    User,
    UserRole,
    Venue,
)
from ticketflow.seed import demo_id, seed_demo

pytestmark = pytest.mark.integration
PASSWORD = "TicketFlow test password 2026"


@pytest.fixture(scope="session")
def password_hash() -> str:
    return hash_password(PASSWORD)


@pytest.fixture
def accounts(migrated_engine: Engine, password_hash: str) -> dict:
    with migrated_engine.begin() as connection:
        seed_demo(connection)
        for role in UserRole:
            connection.execute(
                update(User)
                .where(User.id == demo_id(role.value.lower()))
                .values(email=f"{role.value.lower()}@example.com", password_hash=password_hash)
            )
        other_customer = connection.scalar(
            insert(User)
            .values(email="other.customer@example.com", password_hash=password_hash)
            .returning(User.id)
        )
        other_organizer = connection.scalar(
            insert(User)
            .values(
                email="other.organizer@example.com",
                password_hash=password_hash,
                role=UserRole.ORGANIZER,
            )
            .returning(User.id)
        )
        other_admin = connection.scalar(
            insert(User)
            .values(
                email="other.admin@example.com", password_hash=password_hash, role=UserRole.ADMIN
            )
            .returning(User.id)
        )
        venue = connection.scalar(
            insert(Venue)
            .values(
                organizer_id=other_organizer,
                name="Private venue",
                address="Other",
                city="Astana",
                capacity=20,
            )
            .returning(Venue.id)
        )
        event = connection.scalar(
            insert(Event)
            .values(
                organizer_id=other_organizer,
                venue_id=venue,
                title="Private draft",
                category=EventCategory.CONCERT,
                event_date=datetime.now(UTC) + timedelta(days=40),
            )
            .returning(Event.id)
        )
        orders = []
        for user_id in (demo_id("customer"), other_customer):
            booking = connection.scalar(
                insert(ReservationGroup)
                .values(user_id=user_id, event_id=demo_id("event/concert"))
                .returning(ReservationGroup.id)
            )
            orders.append(
                connection.scalar(
                    insert(Order)
                    .values(
                        user_id=user_id,
                        event_id=demo_id("event/concert"),
                        reservation_group_id=booking,
                        total_amount=Decimal("10.00"),
                    )
                    .returning(Order.id)
                )
            )
    return {
        "other_customer": other_customer,
        "other_organizer": other_organizer,
        "other_admin": other_admin,
        "other_venue": venue,
        "other_event": event,
        "orders": orders,
    }


@pytest.fixture
def auth_client(migrated_engine: Engine, accounts: dict):
    app = create_app(
        Settings(_env_file=None, database_url="postgresql+psycopg://test:test@127.0.0.1:1/test")
    )
    app.dependency_overrides[get_engine] = lambda: migrated_engine
    with TestClient(app) as client:
        yield client


def login(client: TestClient, email: str = "customer@example.com") -> dict[str, str]:
    response = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_registration_login_and_logout(auth_client, migrated_engine) -> None:
    response = auth_client.post(
        "/auth/register", json={"email": " New.User@EXAMPLE.com ", "password": PASSWORD}
    )
    assert response.status_code == 201
    user = response.json()
    assert user["email"] == "new.user@example.com"
    assert user["role"] == "CUSTOMER"
    assert set(user) == {"id", "email", "role", "created_at"}
    assert PASSWORD not in response.text

    response = auth_client.post(
        "/auth/login", json={"email": "NEW.USER@example.com", "password": PASSWORD}
    )
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"
    assert response.json()["expires_in"] == 900
    assert response.headers["cache-control"] == "no-store"
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert auth_client.get("/auth/me", headers=headers).json() == user
    with migrated_engine.connect() as connection:
        stored_hash = connection.scalar(
            select(User.password_hash).where(User.id == UUID(user["id"]))
        )
        assert stored_hash.startswith("$argon2id$")
        assert stored_hash != PASSWORD
        stored_token = connection.scalar(
            select(AuthSession.token_hash).where(AuthSession.user_id == UUID(user["id"]))
        )
        assert stored_token == token_digest(token)
        assert stored_token != token
    assert auth_client.post("/auth/logout", headers=headers).status_code == 204
    assert auth_client.get("/auth/me", headers=headers).status_code == 401


def test_duplicate_email_conflict_does_not_break_next_registration(auth_client) -> None:
    duplicate = auth_client.post(
        "/auth/register", json={"email": "CUSTOMER@example.com", "password": PASSWORD}
    )
    assert duplicate.status_code == 409
    assert PASSWORD not in duplicate.text
    assert (
        auth_client.post(
            "/auth/register", json={"email": "second@example.com", "password": PASSWORD}
        ).status_code
        == 201
    )


@pytest.mark.parametrize("field", ["role", "password_hash"])
def test_registration_cannot_assign_privileged_fields(auth_client, migrated_engine, field) -> None:
    response = auth_client.post(
        "/auth/register", json={"email": "attack@example.com", "password": PASSWORD, field: "ADMIN"}
    )
    assert response.status_code == 422
    assert PASSWORD not in response.text
    with migrated_engine.connect() as connection:
        assert connection.scalar(select(User.id).where(User.email == "attack@example.com")) is None


@pytest.mark.parametrize(
    "body",
    [
        {"email": "new@example.com", "password": "private"},
        {"email": "new@example.com", "password": "x" * 129},
        {"email": "invalid-email", "password": PASSWORD},
        [{"email": "new@example.com", "password": PASSWORD}],
    ],
)
def test_validation_errors_do_not_echo_passwords(auth_client, body) -> None:
    response = auth_client.post("/auth/register", json=body)
    assert response.status_code == 422
    password = body[0]["password"] if isinstance(body, list) else body["password"]
    assert password not in response.text
    assert all("input" not in error and "ctx" not in error for error in response.json()["detail"])


@pytest.mark.parametrize("case", ["unknown", "wrong", "disabled", "broken"])
def test_login_failures_use_same_response(auth_client, migrated_engine, case) -> None:
    if case in {"disabled", "broken"}:
        with migrated_engine.begin() as connection:
            connection.execute(
                update(User)
                .where(User.id == demo_id("customer"))
                .values(password_hash="!" if case == "disabled" else "broken")
            )
    email = "missing@example.com" if case == "unknown" else "customer@example.com"
    password = "wrong password" if case == "wrong" else PASSWORD
    response = auth_client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid email or password"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert password not in response.text


@pytest.mark.parametrize(
    "authorization", [None, "Basic abc", "Bearer malformed", "Bearer " + "x" * 43]
)
def test_missing_and_invalid_credentials_return_401(auth_client, authorization) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    for path in ("/auth/me", "/orders/me", "/organizer/events"):
        response = auth_client.get(path, headers=headers)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


def test_token_tampering_query_string_and_expiry_are_rejected(auth_client, migrated_engine) -> None:
    headers = login(auth_client)
    token = headers["Authorization"].split()[1]
    modified = ("a" if token[0] != "a" else "b") + token[1:]
    assert (
        auth_client.get("/auth/me", headers={"Authorization": f"Bearer {modified}"}).status_code
        == 401
    )
    assert auth_client.get("/auth/me", params={"access_token": token}).status_code == 401
    with migrated_engine.begin() as connection:
        connection.execute(
            update(AuthSession)
            .where(AuthSession.token_hash == token_digest(token))
            .values(
                created_at=datetime.now(UTC) - timedelta(hours=1),
                expires_at=datetime.now(UTC) - timedelta(minutes=1),
            )
        )
    assert auth_client.get("/auth/me", headers=headers).status_code == 401


def test_logout_only_revokes_current_session(auth_client) -> None:
    first, second = login(auth_client), login(auth_client)
    assert first != second
    assert auth_client.post("/auth/logout", headers=first).status_code == 204
    assert auth_client.get("/auth/me", headers=first).status_code == 401
    assert auth_client.get("/auth/me", headers=second).status_code == 200


def test_disabling_account_invalidates_existing_session(auth_client, migrated_engine) -> None:
    headers = login(auth_client)
    with migrated_engine.begin() as connection:
        connection.execute(
            update(User).where(User.id == demo_id("customer")).values(password_hash="!")
        )
    assert auth_client.get("/auth/me", headers=headers).status_code == 401


def test_deleting_user_revokes_sessions(auth_client, migrated_engine) -> None:
    response = auth_client.post(
        "/auth/register", json={"email": "delete@example.com", "password": PASSWORD}
    )
    user_id = UUID(response.json()["id"])
    headers = login(auth_client, "delete@example.com")
    with migrated_engine.begin() as connection:
        connection.execute(delete(User).where(User.id == user_id))
        assert (
            connection.scalar(
                select(func.count()).select_from(AuthSession).where(AuthSession.user_id == user_id)
            )
            == 0
        )
    assert auth_client.get("/auth/me", headers=headers).status_code == 401


@pytest.mark.parametrize(
    "email,expected",
    [
        ("customer@example.com", 0),
        ("other.customer@example.com", 1),
        ("organizer@example.com", None),
        ("admin@example.com", None),
    ],
)
def test_orders_are_scoped_to_owner(auth_client, accounts, email, expected) -> None:
    headers = login(auth_client, email)
    response = auth_client.get("/orders/me", headers=headers)
    assert response.status_code == 200
    ids = [item["id"] for item in response.json()]
    assert ids == ([] if expected is None else [str(accounts["orders"][expected])])
    for index, order_id in enumerate(accounts["orders"]):
        response = auth_client.get(f"/orders/{order_id}", headers=headers)
        assert response.status_code == (200 if expected == index else 404)
    assert auth_client.get(f"/orders/{uuid4()}", headers=headers).status_code == 404


@pytest.mark.parametrize("resource", ["events", "venues"])
@pytest.mark.parametrize(
    "email",
    [
        "customer@example.com",
        "organizer@example.com",
        "other.organizer@example.com",
        "admin@example.com",
    ],
)
def test_organizer_resources_are_scoped_and_role_protected(
    auth_client, accounts, resource, email
) -> None:
    headers = login(auth_client, email)
    response = auth_client.get(f"/organizer/{resource}", headers=headers)
    own_ids = (
        [str(demo_id("event/concert")), str(demo_id("event/conference"))]
        if resource == "events"
        else [str(demo_id("venue"))]
    )
    foreign_id = str(accounts["other_event" if resource == "events" else "other_venue"])
    if email.startswith("customer"):
        assert response.status_code == 403
        assert (
            auth_client.get(f"/organizer/{resource}/{own_ids[0]}", headers=headers).status_code
            == 403
        )
        return
    assert response.status_code == 200
    expected = [foreign_id] if email.startswith("other") else own_ids
    if email.startswith("admin"):
        expected = [*own_ids, foreign_id]
    assert {item["id"] for item in response.json()} == set(expected)
    for resource_id in [*own_ids, foreign_id]:
        response = auth_client.get(f"/organizer/{resource}/{resource_id}", headers=headers)
        assert response.status_code == (200 if resource_id in expected else 404)
    assert auth_client.get(f"/organizer/{resource}/{uuid4()}", headers=headers).status_code == 404


def test_lists_have_bounded_pagination(auth_client) -> None:
    headers = login(auth_client, "admin@example.com")
    response = auth_client.get("/organizer/events?limit=1&offset=1", headers=headers)
    assert response.status_code == 200 and len(response.json()) == 1
    assert auth_client.get("/organizer/events?limit=101", headers=headers).status_code == 422
    assert auth_client.get("/orders/me?offset=-1", headers=headers).status_code == 422


def test_role_changes_take_effect_on_existing_token_and_are_audited(
    auth_client, migrated_engine
) -> None:
    customer = login(auth_client)
    admin = login(auth_client, "admin@example.com")
    path = f"/admin/users/{demo_id('customer')}/role"
    assert auth_client.get("/organizer/events", headers=customer).status_code == 403
    assert auth_client.patch(path, json={"role": "ORGANIZER"}, headers=admin).status_code == 200
    assert auth_client.get("/auth/me", headers=customer).json()["role"] == "ORGANIZER"
    assert auth_client.get("/organizer/events", headers=customer).json() == []
    assert auth_client.patch(path, json={"role": "ORGANIZER"}, headers=admin).status_code == 200
    assert auth_client.patch(path, json={"role": "CUSTOMER"}, headers=admin).status_code == 200
    assert auth_client.get("/organizer/events", headers=customer).status_code == 403
    with migrated_engine.connect() as connection:
        actions = connection.scalars(
            select(AuditLog.action).where(AuditLog.entity_id == demo_id("customer"))
        ).all()
        assert sorted(actions) == ["ADMIN_SET_ROLE_CUSTOMER", "ADMIN_SET_ROLE_ORGANIZER"]


@pytest.mark.parametrize("email", ["customer@example.com", "organizer@example.com"])
def test_non_admin_cannot_change_roles(auth_client, email) -> None:
    headers = login(auth_client, email)
    response = auth_client.patch(
        f"/admin/users/{demo_id('customer')}/role", json={"role": "ORGANIZER"}, headers=headers
    )
    assert response.status_code == 403


def test_admin_cannot_change_self_or_other_admin_or_grant_admin(auth_client, accounts) -> None:
    headers = login(auth_client, "admin@example.com")
    for target in (demo_id("admin"), accounts["other_admin"]):
        assert (
            auth_client.patch(
                f"/admin/users/{target}/role", json={"role": "CUSTOMER"}, headers=headers
            ).status_code
            == 403
        )
    assert (
        auth_client.patch(
            f"/admin/users/{demo_id('customer')}/role", json={"role": "ADMIN"}, headers=headers
        ).status_code
        == 422
    )
    assert (
        auth_client.patch(
            f"/admin/users/{uuid4()}/role", json={"role": "ORGANIZER"}, headers=headers
        ).status_code
        == 404
    )


def test_local_admin_bootstrap_is_explicit_and_repeatable(auth_client, migrated_engine) -> None:
    with Session(migrated_engine, expire_on_commit=False) as session:
        user = promote_admin(session, "CUSTOMER@example.com")
        assert user.role == UserRole.ADMIN
        promote_admin(session, "customer@example.com")
    with migrated_engine.connect() as connection:
        assert (
            connection.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(AuditLog.action == "CLI_GRANTED_ADMIN")
            )
            == 1
        )
    assert auth_client.get("/auth/me", headers=login(auth_client)).json()["role"] == "ADMIN"


def test_local_admin_bootstrap_rejects_missing_and_disabled_users(
    auth_client, migrated_engine
) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            update(User).where(User.id == demo_id("customer")).values(password_hash="!")
        )
    with Session(migrated_engine) as session:
        with pytest.raises(ValueError, match="зарегистрируй"):
            promote_admin(session, "missing@example.com")
        with pytest.raises(ValueError, match="отключена"):
            promote_admin(session, "customer@example.com")
