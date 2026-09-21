# TicketFlow — Event & Ticket Booking Platform

## Идея проекта

Платформа продажи билетов на:
- концерты;
- кино;
- конференции;
- футбольные матчи;
- stand-up;
- другие мероприятия.

## Roles
- CUSTOMER
- ORGANIZER
- ADMIN

## CUSTOMER
Может:
- просматривать мероприятия;
- искать и фильтровать мероприятия;
- выбирать места;
- резервировать билеты;
- покупать билеты;
- отменять заказ;
- получать QR ticket;
- смотреть историю заказов.

## ORGANIZER
Может:
- создать событие;
- создать venue;
- настроить seating;
- установить стоимость;
- посмотреть проданные билеты;
- посмотреть revenue;
- управлять мероприятиями.

## Database

### users
- id
- email
- password_hash
- role
- created_at

### venues
- id
- name
- address
- city
- capacity

### sections
- id
- venue_id
- name

Примеры:
- VIP
- Standard
- Balcony

### seats
- id
- section_id
- row_number
- seat_number

### events
- id
- organizer_id
- venue_id
- title
- description
- event_date
- status
- created_at

Statuses:
- DRAFT
- PUBLISHED
- CANCELLED
- FINISHED

### ticket_types
- id
- event_id
- section_id
- name
- price

### ticket_reservations
- id
- user_id
- event_id
- seat_id
- expires_at
- status

Statuses:
- ACTIVE
- EXPIRED
- COMPLETED

### orders
- id
- user_id
- total_amount
- status
- created_at

Statuses:
- PENDING
- PAID
- CANCELLED
- REFUNDED

### tickets
- id
- order_id
- event_id
- seat_id
- qr_code
- status

Statuses:
- VALID
- USED
- CANCELLED

## Основная backend-задача

Пользователь выбирает Seat A10.
Backend блокирует место примерно на 10 минут.

Создается reservation с expires_at.
Пока reservation активен, другой пользователь не может купить это место.

Если пользователь оплатил:
- Reservation → COMPLETED
- Ticket → created

Если не оплатил:
- Reservation → EXPIRED
- Seat снова доступен

Для защиты от race condition использовать:
- PostgreSQL transactions
- SELECT ... FOR UPDATE
- unique constraints

## API

### Events
- GET /events
- GET /events/{id}
- POST /events
- PATCH /events/{id}
- DELETE /events/{id}

### Search
- GET /events?city=Almaty
- GET /events?category=concert
- GET /events?date_from=...
- GET /events?date_to=...
- GET /events?min_price=...
- GET /events?max_price=...

### Seats
- GET /events/{id}/seats

Статусы мест:
- available
- sold
- reserved

### Reservations
- POST /reservations

Example:
{
    "event_id": "...",
    "seat_ids": ["...", "..."]
}

### Orders
- POST /orders
- GET /orders/me
- GET /orders/{id}

### Payment
Для учебного проекта можно сделать mock payment:
- POST /payments/mock

Flow:
reservation → order → payment → tickets

## QR ticket

После оплаты backend генерирует ticket UUID / QR token.

Validation endpoint:
- POST /tickets/validate

Backend проверяет:
- ticket exists?
- correct event?
- not used?
- not cancelled?

После успешной проверки:
status = USED

Повторное сканирование:
403 Ticket already used

## Background jobs
Периодически находить reservations с expires_at < NOW()
и менять status на EXPIRED.

## Analytics
GET /organizer/dashboard

Возвращает:
- tickets_sold
- tickets_available
- total_revenue
- sales_today
- sales_by_ticket_type
- sales_by_date

## Audit log
### audit_logs
- user_id
- action
- entity
- entity_id
- timestamp
- ip

Примеры:
- ORGANIZER_CREATED_EVENT
- CUSTOMER_PURCHASED_TICKET
- ADMIN_CANCELLED_EVENT

## Что этот проект показывает
- FastAPI REST API
- PostgreSQL transactions
- Race conditions
- Concurrency
- Seat reservation
- Temporary resources
- QR verification
- RBAC
- Background processing
- Analytics
- Testing
