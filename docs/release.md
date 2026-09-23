# TicketFlow: backend MVP

Все 10 этапов плана реализованы. Исходное [ТЗ](spec.md) сохранено.
Результат — REST API со Swagger, PostgreSQL и отдельным worker. Веб-интерфейс
покупателя и реальные банковские платежи остаются отдельными задачами за пределами
первого релиза, как зафиксировано в [плане](plan.md).

## Соответствие ТЗ

| Требование | Реализация |
| --- | --- |
| CUSTOMER / ORGANIZER / ADMIN | Регистрация, сессии, назначение ролей, проверка владельца |
| Площадки, секции, места | API настройки зала, вместимость, уникальные позиции, блокировка опубликованной схемы |
| Events CRUD | `/events`, `/events/{event_id}`; DRAFT → PUBLISHED → FINISHED, отдельная отмена |
| Search | Город, категория, диапазон дат и цены, подстрока заголовка; совместимые фильтры |
| Seats | `/events/{event_id}/seats`: available / reserved / sold |
| Бронь на 10 минут | `/reservations`, группа 1–20 мест, транзакции и FOR UPDATE |
| Заказы и история | `/orders`, `/orders/me`, `/orders/{order_id}`, `/orders/{order_id}/items` |
| Mock payment | `/payments/mock`, UUID идемпотентности, сумма на сервере, атомарный выпуск билетов |
| QR ticket | `/tickets/me`, `/tickets/{ticket_id}`, SVG QR, `/tickets/validate` |
| Повторное сканирование | `403 Ticket already used`; конкурентно проходит один запрос |
| Отмены | `/orders/{order_id}/cancel`, `/events/{event_id}/cancel`; полные mock-возвраты |
| Фоновое истечение | `uv run --locked python -m ticketflow.worker`, отдельный процесс |
| Аналитика | `/organizer/dashboard`, все 6 метрик, UTC, Decimal, возвраты |
| Проданные билеты | `/organizer/events/{event_id}/sales`, без QR-токенов и email |
| Аудит | Транзакционные записи действий, `/admin/audit-logs` с фильтрами |

## Демонстрация за одну команду

Из корня проекта, после `uv sync --locked` и запуска PostgreSQL:

```sh
uv run --locked python -m scripts.demo
```

Сценарий самостоятельно применяет миграции в новой схеме `demo_ticketflow_<uuid>`,
регистрирует временные учётные записи и выполняет 12 проверок через настоящий ASGI API:
создание каталога → поиск → бронь → заказ → оплата → SVG → вход → отказ повторного
входа → возврат → повторная продажа → отмена события → сверка метрик и аудита.
После выполнения схема удаляется, в том числе при ошибке; `public` не очищается.
Нужны права CREATE SCHEMA. HTTP-сервер отдельно запускать для этого сценария не нужно.

| Артефакт | Содержание |
| --- | --- |
| `artifacts/demo/report.md` | Таблица пройденных проверок |
| `artifacts/demo/report.json` | Метрики до и после отмены, результат сценария |
| `artifacts/demo/ticket.svg` | Пример QR; после удаления временной схемы недействителен |

Другая папка: `uv run --locked python -m scripts.demo --output-dir /private/tmp/ticketflow-demo`.
Пароли и Bearer-токены не выводятся и не сохраняются в отчёте. Артефакты исключены из Git.

## Проверки релиза

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked --env-file .env pytest
uv run --locked alembic upgrade head
uv run --locked alembic check
uv run --locked python -m ticketflow.seed
uv run --locked python -m ticketflow.worker --once
uv run --locked python -m scripts.demo
```

164 теста включают реальные параллельные подключения PostgreSQL: одно место,
обратный порядок мест, разные события, повтор заказа/платежа/возврата/скана,
отмену одновременно с оплатой и входом, вместимость и публикацию зала.
Отдельно проверяется истечение срока во время ожидания блокировки: оплата отклоняется,
новая бронь проходит. Принудительная ошибка записи билета откатывает платёж и статусы.
Миграции проверяются upgrade → downgrade → upgrade и сравнением с моделями.
CI выполняет тесты, миграции, seed, полный демо-сценарий и разовый worker.

## Принятые границы реализации

| Область | Решение MVP |
| --- | --- |
| Конкуренция | Короткие операции одного события сериализуются его блокировкой; разные события независимы |
| Валюта и возврат | KZT, полный mock-возврат заказа; частичного возврата нет |
| Вход на событие | PUBLISHED, PAID и VALID; отдельное временное окно прохода не задано |
| Сессии | 15 минут, отзыв при выходе; повторный вход после истечения, без refresh |
| Истёкшие сессии | Недействительны сразу; периодическое удаление пока не настроено |
| Продакшен | TLS, почта, восстановление пароля, ограничение частоты запросов и реальный эквайринг — отдельная эксплуатационная подготовка |

## GitHub

| Этап | Issue | PR |
| --- | --- | --- |
| ТЗ и план | [#1](https://github.com/Nurassyl28/ticketflow/issues/1) | [#2](https://github.com/Nurassyl28/ticketflow/pull/2) |
| 1 — каркас | [#3](https://github.com/Nurassyl28/ticketflow/issues/3) | [#4](https://github.com/Nurassyl28/ticketflow/pull/4) |
| 2 — БД | [#5](https://github.com/Nurassyl28/ticketflow/issues/5) | [#6](https://github.com/Nurassyl28/ticketflow/pull/6) |
| 3 — доступ | [#7](https://github.com/Nurassyl28/ticketflow/issues/7) | [#8](https://github.com/Nurassyl28/ticketflow/pull/8) |
| 4 — каталог | [#9](https://github.com/Nurassyl28/ticketflow/issues/9) | [#10](https://github.com/Nurassyl28/ticketflow/pull/10) |
| 5 — бронь | [#11](https://github.com/Nurassyl28/ticketflow/issues/11) | [#13](https://github.com/Nurassyl28/ticketflow/pull/13) |
| 6 — оплата | [#12](https://github.com/Nurassyl28/ticketflow/issues/12) | [#15](https://github.com/Nurassyl28/ticketflow/pull/15) |
| 7 — QR | [#14](https://github.com/Nurassyl28/ticketflow/issues/14) | [#17](https://github.com/Nurassyl28/ticketflow/pull/17) |
| 8 — отмены | [#16](https://github.com/Nurassyl28/ticketflow/issues/16) | [#19](https://github.com/Nurassyl28/ticketflow/pull/19) |
| 9 — аналитика | [#18](https://github.com/Nurassyl28/ticketflow/issues/18) | [#21](https://github.com/Nurassyl28/ticketflow/pull/21) |
| 10 — демонстрация | [#20](https://github.com/Nurassyl28/ticketflow/issues/20) | PR связан с issue |
