# TicketFlow

Учебная платформа продажи билетов: мероприятия, выбор мест, бронь на 10 минут,
mock-оплата и проверка QR-билетов. Роли: `CUSTOMER`, `ORGANIZER`, `ADMIN`.

| Документ | Содержание |
| --- | --- |
| [Техническое задание](docs/spec.md) | Исходное ТЗ без изменений |
| [План разработки](docs/plan.md) | Этапы, критерии готовности и предлагаемые уточнения |
| [Правила работы](AGENTS.md) | Язык, `uv`, issue → commit → PR → merge |

## Запуск

Нужны `uv` и работающий Docker с Compose. Проект использует Python 3.13;
`uv` установит его при необходимости. Версии зависимостей закреплены в `uv.lock`.

```sh
cp .env.example .env
uv sync --locked
docker compose up -d --wait postgres
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

Данные находятся в томе `ticketflow_postgres_data`. Изменение `POSTGRES_USER`,
`POSTGRES_PASSWORD` или `POSTGRES_DB` в `.env` не перенастраивает уже созданную БД.

## Проверки

```sh
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked --env-file .env pytest
```

Последняя команда запускает все тесты с настоящей PostgreSQL из `TEST_DATABASE_URL`.
Она должна быть запущена через Compose. На этапе 1 интеграционные тесты не меняют
данные. Если переменная отсутствует, интеграционные тесты завершаются ошибкой.
Проверки без БД можно запустить отдельно:

```sh
uv run --locked pytest -m "not integration"
```

GitHub Actions выполняет те же проверки с отдельной PostgreSQL 18 на каждом PR
и после изменений в `main`.

## Структура

| Путь | Назначение |
| --- | --- |
| `src/ticketflow/main.py` | Создание приложения и управление пулом БД |
| `src/ticketflow/config.py` | Проверяемые настройки из окружения и `.env` |
| `src/ticketflow/database.py` | Подключение SQLAlchemy / psycopg к PostgreSQL |
| `src/ticketflow/health.py` | Проверки работы API и доступности БД |
| `tests/` | Настройки, ошибки БД и интеграционные проверки |

Статус: реализован каркас этапа 1. Следующий этап — модели БД и миграции;
покупка билетов и авторизация пока не реализованы.
