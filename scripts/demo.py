"""End-to-end API demo in a temporary PostgreSQL schema."""

import argparse
import json
import secrets
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from ticketflow.config import Settings
from ticketflow.database import get_engine
from ticketflow.main import create_app
from ticketflow.manage import promote_admin

ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def demo_database(settings):
    schema = f"demo_ticketflow_{uuid4().hex}"
    admin = create_engine(str(settings.database_url), poolclass=NullPool)
    engine = create_engine(
        str(settings.database_url),
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 3},
    )
    with admin.begin() as connection:
        connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
    try:
        with engine.begin() as connection:
            config = Config(str(ROOT / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, "head")
        yield engine
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        admin.dispose()


def run_scenario(client, engine, output_dir: Path):
    steps = []

    def request(method, path, status=200, headers=None, **kwargs):
        response = client.request(method, path, headers=headers, **kwargs)
        if response.status_code != status:
            raise RuntimeError(
                f"{method} {path}: ожидался {status}, получен {response.status_code}"
            )
        return response

    def check(condition, name):
        if not condition:
            raise RuntimeError(f"Не прошёл этап: {name}")
        steps.append(name)

    suffix = uuid4().hex
    accounts = {}
    for role in ("admin", "organizer", "customer"):
        email, password = f"{role}.{suffix}@example.com", secrets.token_urlsafe(24)
        profile = request(
            "POST", "/auth/register", 201, json={"email": email, "password": password}
        ).json()
        if role == "admin":
            with Session(engine, expire_on_commit=False) as session:
                promote_admin(session, email)
        token = request("POST", "/auth/login", json={"email": email, "password": password}).json()[
            "access_token"
        ]
        accounts[role] = (profile, {"Authorization": f"Bearer {token}"})
    admin, organizer, customer = [accounts[role][1] for role in ("admin", "organizer", "customer")]
    organizer_id = accounts["organizer"][0]["id"]
    request("PATCH", f"/admin/users/{organizer_id}/role", headers=admin, json={"role": "ORGANIZER"})
    check(
        request("GET", "/auth/me", headers=organizer).json()["role"] == "ORGANIZER",
        "Регистрация и роли",
    )

    venue = request(
        "POST",
        "/venues",
        201,
        headers=organizer,
        json={"name": "Demo Hall", "address": "Demo address", "city": "Almaty", "capacity": 3},
    ).json()["id"]
    section = request(
        "POST", f"/venues/{venue}/sections", 201, headers=organizer, json={"name": "Standard"}
    ).json()["id"]
    seats = request(
        "POST",
        f"/venues/{venue}/sections/{section}/seats",
        201,
        headers=organizer,
        json=[{"row_number": 1, "seat_number": number} for number in (1, 2, 3)],
    ).json()
    event = request(
        "POST",
        "/events",
        201,
        headers=organizer,
        json={
            "venue_id": venue,
            "title": f"Demo {suffix}",
            "category": "concert",
            "event_date": (datetime.now(UTC) + timedelta(days=7)).isoformat(),
        },
    ).json()["id"]
    request(
        "POST",
        f"/events/{event}/ticket-types",
        201,
        headers=organizer,
        json={"section_id": section, "name": "Standard", "price": "2500.00"},
    )
    request("PATCH", f"/events/{event}", headers=organizer, json={"status": "PUBLISHED"})
    found = request(
        "GET",
        "/events",
        params={
            "q": suffix,
            "city": "almaty",
            "category": "concert",
            "min_price": "2500",
            "max_price": "2500",
        },
    ).json()
    check(len(found) == 1 and found[0]["id"] == event, "Создание и поиск события")
    check(len(request("GET", f"/events/{event}/seats").json()) == 3, "Схема мест и тарифы")

    def buy(seat):
        group = request(
            "POST",
            "/reservations",
            201,
            headers=customer,
            json={"event_id": event, "seat_ids": [seat["id"]]},
        ).json()
        order = request(
            "POST", "/orders", 201, headers=customer, json={"reservation_id": group["id"]}
        ).json()
        payload = {"order_id": order["id"], "idempotency_key": str(uuid4())}
        payment = request("POST", "/payments/mock", headers=customer, json=payload).json()
        repeated = request("POST", "/payments/mock", headers=customer, json=payload).json()
        if repeated != payment:
            raise RuntimeError("Повтор оплаты изменил результат")
        return order, payment["tickets"][0]

    _, ticket = buy(seats[0])
    check(ticket["status"] == "VALID", "Бронь → заказ → оплата → билет")
    output_dir.mkdir(parents=True, exist_ok=True)
    svg = request("GET", f"/tickets/{ticket['id']}/qr", headers=customer)
    (output_dir / "ticket.svg").write_bytes(svg.content)
    validation = {"event_id": event, "qr_code": ticket["qr_code"]}
    check(
        request("POST", "/tickets/validate", headers=organizer, json=validation).json()["status"]
        == "USED",
        "QR: первый вход",
    )
    repeated = request("POST", "/tickets/validate", 403, headers=organizer, json=validation)
    check(repeated.json()["detail"] == "Ticket already used", "QR: повтор отклонён")
    second, _ = buy(seats[1])
    cancelled = request("POST", f"/orders/{second['id']}/cancel", headers=customer).json()
    check(cancelled["status"] == "REFUNDED", "Полный mock-возврат")
    _, replacement = buy(seats[1])
    check(replacement["status"] == "VALID", "Повторная продажа освобождённого места")
    before = request(
        "GET", "/organizer/dashboard", headers=organizer, params={"event_id": event}
    ).json()
    check(
        before["tickets_sold"] == 2
        and before["total_revenue"] == "5000.00"
        and before["tickets_available"] == 1,
        "Сверка аналитики",
    )
    request("POST", f"/events/{event}/cancel", headers=admin)
    after = request(
        "GET", "/organizer/dashboard", headers=organizer, params={"event_id": event}
    ).json()
    check(
        after["tickets_sold"] == 0 and after["tickets_available"] == 0, "Отмена события и возвраты"
    )
    audit = request(
        "GET",
        "/admin/audit-logs",
        headers=admin,
        params={"action": "ADMIN_CANCELLED_EVENT", "entity_id": event},
    ).json()
    check(len(audit) == 1, "Аудит отмены")
    request("POST", "/auth/logout", 204, headers=customer)
    request("GET", "/auth/me", 401, headers=customer)
    steps.append("Выход отзывает сессию")
    report = {
        "status": "passed",
        "steps": steps,
        "sales_before_cancellation": before,
        "sales_after_cancellation": after,
        "database": "temporary schema; removed after demo",
    }
    (output_dir / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (output_dir / "report.md").write_text(
        "# TicketFlow: демонстрация\n\n| Проверка | Результат |\n| --- | --- |\n"
        + "".join(f"| {step} | Пройдено |\n" for step in steps)
        + "\nДанные временные; QR после демонстрации недействителен.\n",
        encoding="utf-8",
    )
    return report


def main():
    parser = argparse.ArgumentParser(description="TicketFlow: полный сценарий в изолированной БД")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "artifacts" / "demo")
    args = parser.parse_args()
    settings = Settings()
    with demo_database(settings) as engine:
        app = create_app(settings)
        app.dependency_overrides[get_engine] = lambda: engine
        with TestClient(app) as client:
            report = run_scenario(client, engine, args.output_dir)
    print(f"Пройдено {len(report['steps'])} этапов; временная схема удалена.")
    print(f"Отчёт: {args.output_dir / 'report.md'}")


if __name__ == "__main__":
    main()
