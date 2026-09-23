# TicketFlow

Учебная платформа продажи билетов: мероприятия, выбор мест, бронь на 10 минут,
mock-оплата и проверка QR-билетов. Роли: `CUSTOMER`, `ORGANIZER`, `ADMIN`.

Backend MVP по ТЗ завершён: 10 из 10 этапов. API доступен через Swagger;
покупательский веб-интерфейс и реальный эквайринг — отдельное продолжение проекта.

| Документ | Содержание |
| --- | --- |
| [Техническое задание](docs/spec.md) | Исходное ТЗ без изменений |
| [Готовый MVP и демонстрация](docs/release.md) | Соответствие ТЗ, запуск полного сценария и ссылки на GitHub |
| [План разработки](docs/plan.md) | Этапы, критерии готовности и предлагаемые уточнения |
| [Схема БД](docs/database.md) | Таблицы, ограничения, миграции и seed |
| [Вход и права доступа](docs/auth.md) | Сессии, роли, маршруты и назначение ADMIN |
| [Каталог](docs/catalog.md) | Площадки, места, события, тарифы и фильтры |
| [Бронирование](docs/booking.md) | Транзакции, срок удержания и worker |
| [Заказы и оплата](docs/payments.md) | Снимки цен, идемпотентность и выпуск билетов |
| [QR-билеты](docs/tickets.md) | Получение SVG и однократная проверка на входе |
| [Отмены](docs/cancellations.md) | Полный mock-возврат и отмена события |
| [Аналитика](docs/analytics.md) | Формулы dashboard, продажи и аудит |
| [Правила работы](AGENTS.md) | Язык, `uv`, issue → commit → PR → merge |

## Запуск

Нужны `uv` и работающий Docker с Compose. Проект использует Python 3.13;
`uv` установит его при необходимости. Версии зависимостей закреплены в `uv.lock`.

```sh
cp .env.example .env
uv sync --locked
docker compose up -d --wait postgres
uv run --locked alembic upgrade head
uv run --locked python -m ticketflow.seed
uv run --locked uvicorn ticketflow.main:create_app --factory --reload --reload-dir src --port 8008
```

| Адрес | Назначение |
| --- | --- |
| http://127.0.0.1:8008/docs | Swagger UI |
| http://127.0.0.1:8008/health | Работа API: `200 {"status":"ok"}`, независимо от БД |
| http://127.0.0.1:8008/health/ready | Запрос к PostgreSQL: `200`, при сбое — `503` |

PostgreSQL доступна только на `127.0.0.1:55432`. Если порт занят, поменяй
`POSTGRES_PORT` и порт в обеих переменных `DATABASE_URL` и `TEST_DATABASE_URL` в `.env`.
При изменении реквизитов БД также обнови соответствующие URL.
Пример содержит демонстрационный пароль только для локальной разработки.
Переменные окружения имеют приоритет над `.env`; `.env` не попадает в Git.

Остановка API — `Ctrl+C`. Остановка PostgreSQL с сохранением данных:

```sh
docker compose down
```

В отдельном терминале запусти worker истечения брони:

```sh
uv run --locked python -m ticketflow.worker
```

Полная демонстрация в изолированной схеме PostgreSQL:

```sh
uv run --locked python -m scripts.demo
```

Таблица результатов — `artifacts/demo/report.md`, пример QR — `artifacts/demo/ticket.svg`.
Демо удаляет только свою временную схему; рабочие данные сохраняются.

Данные находятся в томе `ticketflow_postgres_data`. Изменение `POSTGRES_USER`,
`POSTGRES_PASSWORD` или `POSTGRES_DB` в `.env` не перенастраивает уже созданную БД.

## Проверки

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked --env-file .env pytest
```

Последняя команда запускает все тесты с настоящей PostgreSQL из `TEST_DATABASE_URL`.
Она должна быть запущена через Compose. Тесты схемы создают и удаляют только свои
временные схемы; существующие таблицы приложения не очищаются. Учётной записи
нужны права CREATE SCHEMA. Если переменная отсутствует, интеграционные тесты завершаются ошибкой.
Проверки без БД можно запустить отдельно:

```sh
uv run --locked pytest -m "not integration"
```

GitHub Actions выполняет те же проверки с отдельной PostgreSQL 18 на каждом PR
и после изменений в `main`, а также проверяет миграции, seed, worker и демо-сценарий.

## Миграции и данные

```sh
uv run --locked alembic current
uv run --locked alembic check
```

Seed — необязательный демонстрационный каталог: 2 события, 12 мест, 4 тарифа.
Повторный запуск сохраняет существующие данные. Демонстрационные пользователи
не имеют рабочего пароля. Подробности и схема связей — в [описании БД](docs/database.md).

## Регистрация и вход

В Swagger UI вызови `POST /auth/register` с email и паролем длиной 12–128 символов,
затем `POST /auth/login`. Скопируй `access_token` в кнопку **Authorize**.
`GET /auth/me` покажет профиль; `POST /auth/logout` завершит текущую сессию.
Срок токена — 15 минут; настройка `AUTH_TOKEN_TTL_MINUTES` принимает 1–120.

Новый пользователь получает роль `CUSTOMER`. Первого администратора назначай
локально, указав email уже зарегистрированной учётной записи:

```sh
uv run --locked python -m ticketflow.manage promote-admin your-email@example.com
```

ADMIN назначает `ORGANIZER` через `PATCH /admin/users/{user_id}/role`.
Список маршрутов и ограничения — в [описании доступа](docs/auth.md).

## Структура

| Путь | Назначение |
| --- | --- |
| `src/ticketflow/main.py` | Создание приложения и управление пулом БД |
| `src/ticketflow/config.py` | Проверяемые настройки из окружения и `.env` |
| `src/ticketflow/database.py` | Подключение SQLAlchemy / psycopg к PostgreSQL |
| `src/ticketflow/models/` | 15 моделей с ограничениями и индексами |
| `src/ticketflow/auth/` | Регистрация, пароли, сессии и проверка ролей |
| `src/ticketflow/access.py` | Чтение заказов и ресурсов с проверкой владельца |
| `src/ticketflow/manage.py` | Локальное назначение администратора |
| `src/ticketflow/catalog.py` | Каталог, публикация, схема мест и тарифы |
| `src/ticketflow/reservations.py` | Транзакционная бронь и истечение |
| `src/ticketflow/checkout.py` | Заказы, снимки цен и mock-оплата |
| `src/ticketflow/tickets.py` | QR SVG и однократная проверка |
| `src/ticketflow/cancellations.py` | Отмены и mock-возвраты |
| `src/ticketflow/analytics.py` | Dashboard, продажи и чтение аудита |
| `src/ticketflow/worker.py` | Фоновая обработка истечения |
| `scripts/demo.py` | Сквозная демонстрация в отдельной схеме |
| `migrations/` | Версионируемая схема Alembic |
| `src/ticketflow/seed.py` | Повторяемый демонстрационный каталог |
| `src/ticketflow/health.py` | Проверки работы API и доступности БД |
| `tests/` | Настройки, ошибки БД и интеграционные проверки |

Проверки: 164 теста PostgreSQL/настроек/API, включая гонки; CI и воспроизводимая демонстрация.
